import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "lib"))

from adaptive_change_governance.config_loader import load_yaml
import adaptive_change_governance.cli as cli_module
from adaptive_change_governance.context_adjuster import apply_user_context
from adaptive_change_governance.human_review import HumanReviewGate, ReviewError
from adaptive_change_governance.repository_analyzer import RepositoryAnalyzer
from adaptive_change_governance.run_retention import cleanup_runs
from adaptive_change_governance.risk_evaluator import RiskEvaluator
from adaptive_change_governance.schema_validator import ValidationError, validate_all, validate_risk_calibration
from adaptive_change_governance.workflow_composer import WorkflowComposer


class Phase1Test(unittest.TestCase):
    def setUp(self):
        self.project_risk = load_yaml(ROOT / ".ai-governance/project-risk.yaml")
        self.guardrails = load_yaml(ROOT / ".ai-governance/guardrails.yaml")
        self.modules = load_yaml(ROOT / ".ai-governance/workflow-modules.yaml")
        self.risk_calibration = load_yaml(ROOT / ".ai-governance/risk-calibration.yaml")
        self.charging_project_risk = load_yaml(ROOT / ".ai-governance/profiles/charging-platform/project-risk.yaml")
        self.charging_guardrails = load_yaml(ROOT / ".ai-governance/profiles/charging-platform/guardrails.yaml")

    def test_config_files_validate(self):
        validate_all(self.project_risk, self.guardrails, self.modules)
        validate_risk_calibration(self.risk_calibration)
        validate_all(self.charging_project_risk, self.charging_guardrails, self.modules)

    def test_bad_config_reports_clear_error(self):
        bad = dict(self.project_risk)
        bad["project"] = {"name": "bad", "baseline_level": "LX"}
        with self.assertRaisesRegex(ValidationError, "baseline_level"):
            validate_all(bad, self.guardrails, self.modules)

    def test_audit_retention_policy_validates(self):
        bad = dict(self.project_risk)
        bad["audit_retention"] = {"audit_mode": "git", "retain_latest": 0, "retain_days": 30}
        with self.assertRaisesRegex(ValidationError, "audit_retention"):
            validate_all(bad, self.guardrails, self.modules)

    def test_bad_risk_calibration_reports_clear_error(self):
        bad = {"version": 1, "level_thresholds": {"L2": 20, "L3": 10, "L4": 40}}
        with self.assertRaisesRegex(ValidationError, "level_thresholds.L3"):
            validate_risk_calibration(bad)

    def test_money_change_triggers_hard_guardrail_and_required_modules(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("调整退款金额保留两位小数的计算方式。", self.project_risk)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
        required = workflow["workflow_recommendation"]["required_modules"]
        self.assertIn("financial-calculation-change", risk["triggered_guardrails"])
        self.assertGreaterEqual({"L1": 1, "L2": 2, "L3": 3, "L4": 4}[risk["final_level"]], 3)
        for module in ("business_rule_confirmation", "boundary_test", "independent_review", "reconciliation_test"):
            self.assertIn(module, required)

    def test_charging_profile_keeps_charging_specific_money_guardrail(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("调整退款金额保留两位小数的计算方式。", self.charging_project_risk)
        risk = RiskEvaluator(self.charging_project_risk, self.charging_guardrails).evaluate(evidence)
        self.assertIn("money-change", risk["triggered_guardrails"])
        self.assertGreaterEqual({"L1": 1, "L2": 2, "L3": 3, "L4": 4}[risk["final_level"]], 3)

    def test_low_risk_copy_change_can_remain_lightweight(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("修改后台订单页面上的提示文案。", self.project_risk)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
        rec = workflow["workflow_recommendation"]
        self.assertEqual("L1", risk["final_level"])
        self.assertEqual(["code_fact_scan", "regression_test"], rec["required_modules"])
        self.assertTrue(any(item["module"] == "technical_design" for item in rec["skipped_modules"]))

    def test_menu_label_change_remains_lightweight_despite_unrelated_keywords(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "frontend/src/layouts").mkdir(parents=True)
            (temp / "frontend/src/layouts/BotLayout.vue").write_text("<span>群配置</span>\n", encoding="utf-8")
            (temp / "app/bot").mkdir(parents=True)
            (temp / "app/bot/handler.py").write_text(
                "def clean_group_config_cache():\n"
                "    sql = 'delete from cache_items where name = 群配置'\n"
                "    return {'api_schema': 'message'}\n",
                encoding="utf-8",
            )
            evidence = RepositoryAnalyzer(temp).analyze("把后台「群配置」相关的菜单修改为「业务群配置」", self.project_risk)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
            self.assertTrue(evidence["code_findings"]["text_only_change"])
            boundary = evidence["code_findings"]["feature_boundary"]
            self.assertEqual("high", boundary["summary"]["confidence"])
            self.assertTrue(any(item["path"] == "frontend/src/layouts/BotLayout.vue" for item in boundary["included_files"]))
            self.assertEqual([], risk["triggered_guardrails"])
            self.assertEqual("L1", risk["final_level"])
            self.assertEqual(["code_fact_scan", "regression_test"], workflow["workflow_recommendation"]["required_modules"])
            self.assertNotIn("direct_production_execution", workflow["workflow_recommendation"]["prohibited"])
        finally:
            shutil.rmtree(temp)

    def test_model_intent_can_route_menu_label_change_lightweight(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "frontend/src/layouts").mkdir(parents=True)
            (temp / "frontend/src/layouts/BotLayout.vue").write_text("<span>群配置</span>\n", encoding="utf-8")
            (temp / "app/bot").mkdir(parents=True)
            (temp / "app/bot/shared.py").write_text(
                "def delete_group_config_cache():\n"
                "    return {'api_schema': 'message'}\n",
                encoding="utf-8",
            )
            intent = {
                "version": 1,
                "change_kind": "menu_label_change",
                "summary": "rename menu display text only",
                "confidence": "high",
                "scope": {
                    "included": ["rename the backend menu display label"],
                    "excluded": ["database changes", "public API changes", "permission key changes"],
                    "unknowns": [],
                },
                "risk_hints": {
                    "data_operation": False,
                    "database_schema_change": False,
                    "public_interface_change": False,
                    "permission_change": False,
                    "security_change": False,
                    "financial_change": False,
                },
            }
            evidence = RepositoryAnalyzer(temp).analyze(
                "把后台「群配置」相关的菜单修改为「业务群配置」",
                self.project_risk,
                intent=intent,
            )
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertTrue(evidence["code_findings"]["text_only_change"])
            self.assertEqual("menu_label_change", evidence["request"]["model_intent"]["change_kind"])
            self.assertEqual("L1", risk["final_level"])
            self.assertEqual([], risk["triggered_guardrails"])
        finally:
            shutil.rmtree(temp)

    def test_file_risk_makes_database_file_higher_risk_than_ui_copy(self):
        ui_temp = Path(tempfile.mkdtemp())
        db_temp = Path(tempfile.mkdtemp())
        try:
            (ui_temp / "frontend/src/layouts").mkdir(parents=True)
            (ui_temp / "frontend/src/layouts/BotLayout.vue").write_text("<span>群配置</span>\n", encoding="utf-8")
            ui_evidence = RepositoryAnalyzer(ui_temp).analyze("把后台「群配置」相关的菜单修改为「业务群配置」", self.project_risk)
            ui_risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(ui_evidence)

            (db_temp / "app").mkdir(parents=True)
            (db_temp / "app/database.py").write_text("DATABASE_NAME = 'main'\n", encoding="utf-8")
            db_evidence = RepositoryAnalyzer(db_temp).analyze("修改数据库连接名称 database", self.project_risk)
            db_risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(db_evidence)

            self.assertEqual("low", ui_evidence["code_findings"]["file_risk"]["highest_level"])
            self.assertEqual("high", db_evidence["code_findings"]["file_risk"]["highest_level"])
            self.assertTrue(any(item.get("pattern") == "semantic:data_access" for item in db_evidence["code_findings"]["file_risk"]["matches"]))
            self.assertEqual("L1", ui_risk["final_level"])
            self.assertGreater({"L1": 1, "L2": 2, "L3": 3, "L4": 4}[db_risk["final_level"]], {"L1": 1, "L2": 2, "L3": 3, "L4": 4}[ui_risk["final_level"]])
        finally:
            shutil.rmtree(ui_temp)
            shutil.rmtree(db_temp)

    def test_semantic_file_role_can_raise_unconfigured_database_file_risk(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "src/persistence").mkdir(parents=True)
            (temp / "src/persistence/connection.ts").write_text(
                "export const databaseUrl = process.env.DATABASE_URL\n"
                "export function query(sql: string) { return sql }\n",
                encoding="utf-8",
            )
            evidence = RepositoryAnalyzer(temp).analyze("修改 database 连接名称", self.project_risk)
            file_risk = evidence["code_findings"]["file_risk"]
            boundary = evidence["code_findings"]["feature_boundary"]
            self.assertEqual("high", file_risk["highest_level"])
            self.assertTrue(any(item.get("pattern") == "semantic:data_access" for item in file_risk["matches"]))
            self.assertTrue(any(item["role"] == "data_access" for item in boundary["file_roles"]))
        finally:
            shutil.rmtree(temp)

    def test_comment_only_change_lowers_effective_file_risk_but_keeps_inherent_risk(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir(parents=True)
            (temp / "app/database.py").write_text("# database connection name docs\nDATABASE_NAME = 'main'\n", encoding="utf-8")
            intent = {
                "version": 1,
                "change_kind": "comment_change",
                "change_nature": "comment_only",
                "summary": "update a comment in database.py",
                "confidence": "high",
                "scope": {"included": ["comment only"], "excluded": ["executable code"], "unknowns": ["diff must confirm comment-only"]},
                "risk_hints": {
                    "data_operation": False,
                    "database_schema_change": False,
                    "public_interface_change": False,
                    "permission_change": False,
                    "security_change": False,
                    "financial_change": False,
                },
            }
            evidence = RepositoryAnalyzer(temp).analyze("修改 app/database.py 中的一行注释", self.project_risk, intent=intent)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            file_risk = evidence["code_findings"]["file_risk"]
            self.assertEqual("high", file_risk["highest_level"])
            self.assertEqual("low", file_risk["effective_level"])
            self.assertEqual("lowered_by_change_nature", file_risk["risk_adjustment"])
            self.assertTrue(any("diff is comment" in item for item in evidence["unknowns"]))
            self.assertEqual("L1", risk["final_level"])
        finally:
            shutil.rmtree(temp)

    def test_analysis_only_goal_stops_without_implementation_modules(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir(parents=True)
            (temp / "app/database.py").write_text("DATABASE_NAME = 'main'\n", encoding="utf-8")
            intent = {
                "version": 1,
                "change_kind": "risk_analysis",
                "change_nature": "analysis_only",
                "summary": "分析删除数据库配置的风险，不修改代码",
                "confidence": "high",
                "request_goal": {
                    "type": "analysis_only",
                    "requires_code_change": False,
                    "default_stop_gate": "analysis_complete",
                    "rationale": "INFERENCE: user asks for risk analysis only.",
                },
                "scope": {"included": ["risk analysis"], "excluded": ["code changes"], "unknowns": []},
                "risk_hints": {
                    "data_operation": False,
                    "database_schema_change": False,
                    "public_interface_change": False,
                    "permission_change": False,
                    "security_change": False,
                    "financial_change": False,
                },
            }
            evidence = RepositoryAnalyzer(temp).analyze("分析删除数据库配置会有什么风险", self.project_risk, intent=intent)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
            rec = workflow["workflow_recommendation"]
            self.assertEqual("analysis_only", rec["request_goal"]["type"])
            self.assertFalse(rec["request_goal"]["requires_code_change"])
            self.assertEqual("analysis_complete", rec["default_stop_gate"])
            self.assertEqual([], risk["triggered_guardrails"])
            self.assertIn("code_fact_scan", rec["required_modules"])
            self.assertNotIn("technical_design", rec["required_modules"])
            self.assertNotIn("test_design", rec["required_modules"])
            self.assertNotIn("regression_test", rec["required_modules"])
        finally:
            shutil.rmtree(temp)

    def test_analysis_only_cli_blocks_technical_plan_generation(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            (temp / "app").mkdir()
            (temp / "app/database.py").write_text("DATABASE_NAME = 'main'\n", encoding="utf-8")
            intent_path = temp / "intent.yaml"
            intent_path.write_text(
                "\n".join([
                    "version: 1",
                    "change_kind: risk_analysis",
                    "change_nature: analysis_only",
                    "summary: 分析风险，不修改代码",
                    "confidence: high",
                    "request_goal:",
                    "  type: analysis_only",
                    "  requires_code_change: false",
                    "  default_stop_gate: analysis_complete",
                    "scope:",
                    "  included: [risk analysis]",
                    "  excluded: [code changes]",
                    "  unknowns: []",
                    "risk_hints:",
                    "  data_operation: false",
                    "  database_schema_change: false",
                    "  public_interface_change: false",
                    "  permission_change: false",
                    "  security_change: false",
                    "  financial_change: false",
                ]),
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            assess = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "分析删除数据库配置会有什么风险", "--intent-file", str(intent_path)],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            self.assertIn("Request goal: analysis_only", assess.stdout)
            self.assertIn("Next gate: analysis_complete", assess.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            review = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--review-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr + review.stdout)
            self.assertIn("Current gate: analysis_complete", review.stdout)
            self.assertIn("--generate-analysis-report", review.stdout)
            next_action = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(next_action.returncode, 0, next_action.stderr + next_action.stdout)
            self.assertIn("Requires user confirmation: no", next_action.stdout)
            self.assertIn("Recommended action: generate_analysis_report", next_action.stdout)
            next_execute = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", run_dir.name, "--execute-next"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(next_execute.returncode, 0, next_execute.stderr + next_execute.stdout)
            self.assertIn("Analysis report generated", next_execute.stdout)
            self.assertTrue((run_dir / "analysis-report.yaml").exists())
            (run_dir / "analysis-report.yaml").unlink()
            (run_dir / "analysis-report.md").unlink()
            (run_dir / ".analysis-complete").unlink()
            report = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--generate-analysis-report", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(report.returncode, 0, report.stderr + report.stdout)
            self.assertIn("Analysis report generated", report.stdout)
            self.assertTrue((run_dir / "analysis-report.yaml").exists())
            self.assertTrue((run_dir / "analysis-report.md").exists())
            self.assertTrue((run_dir / ".analysis-complete").exists())
            report_data = load_yaml(run_dir / "analysis-report.yaml")
            self.assertEqual("analysis_only", report_data["request"]["goal"]["type"])
            self.assertEqual("analysis_complete", report_data["request"]["default_stop_gate"])
            self.assertIn("do not generate technical-plan", " ".join(report_data["recommended_next_actions"]))
            blocked = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--propose-technical-plan", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(blocked.returncode, 3, blocked.stderr + blocked.stdout)
            self.assertIn("request goal is analysis_only", blocked.stdout)
        finally:
            shutil.rmtree(temp)

    def test_risk_scenario_cli_validates_default_calibration(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs", "risk-scenario-report.*"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--validate-risk-scenarios"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Status: pass", result.stdout)
            self.assertTrue((temp / ".ai-governance/risk-scenario-report.yaml").exists())
            report = load_yaml(temp / ".ai-governance/risk-scenario-report.yaml")
            self.assertEqual("pass", report["status"])
            self.assertGreaterEqual(report["summary"]["passed"], 4)
        finally:
            shutil.rmtree(temp)

    def test_user_context_can_remove_weak_risk_signal_but_not_strong_evidence(self):
        weak_evidence = {
            "version": 1,
            "request": {"original": "修改菜单文案", "request_goal": {"requires_code_change": True}},
            "repository": {"branch": "unknown", "commit": "unknown"},
            "code_findings": {
                "direct_files": [{"path": "app/api/auth.py", "reason": "WEAK SIGNAL: generic API file"}],
                "related_files": [],
                "affected_modules": ["app"],
                "affected_domains": [],
                "change_types": ["public_api"],
                "operations": [],
                "domain_evidence": [],
                "change_type_evidence": [{
                    "value": "public_api",
                    "source": "code_search",
                    "path": "app/api/auth.py",
                    "keyword": "api",
                    "strength": "weak",
                    "fact": "WEAK SIGNAL: generic api keyword",
                }],
                "operation_evidence": [],
                "database_changes": False,
                "message_schema_changes": False,
                "public_api_changes": True,
                "file_risk": {"highest_score": 1, "effective_score": 1},
            },
            "test_findings": {"coverage_confidence": "low"},
            "unknowns": [],
            "evidence_sources": ["code_search"],
        }
        context = {"scope": {"excluded": ["public API changes"]}, "corrections": []}
        adjusted = apply_user_context(weak_evidence, context)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(adjusted)
        self.assertNotIn("public_api", adjusted["code_findings"]["change_types"])
        self.assertFalse(adjusted["code_findings"]["public_api_changes"])
        self.assertNotIn("public-interface-change", risk["triggered_guardrails"])
        self.assertTrue(adjusted["context_adjustments"]["applied"])

        strong_evidence = dict(weak_evidence)
        strong_evidence["code_findings"] = dict(weak_evidence["code_findings"])
        strong_evidence["code_findings"]["change_type_evidence"] = [dict(weak_evidence["code_findings"]["change_type_evidence"][0], strength="strong")]
        strong_adjusted = apply_user_context(strong_evidence, context)
        self.assertIn("public_api", strong_adjusted["code_findings"]["change_types"])
        self.assertTrue(strong_adjusted["context_adjustments"]["conflicts"])

    def test_destructive_database_operation_cannot_drop_hard_gate(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("删除重复的设备端口状态数据。", self.project_risk)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
        rec = workflow["workflow_recommendation"]
        self.assertIn("destructive-database-operation", risk["triggered_guardrails"])
        self.assertNotIn("physical-device-control", risk["triggered_guardrails"])
        self.assertEqual("L4", risk["final_level"])
        self.assertIn("dry_run", rec["required_modules"])
        self.assertIn("manual_approval", rec["required_modules"])
        self.assertIn("direct_production_execution", rec["prohibited"])
        self.assertTrue(risk["triggered_guardrail_details"])
        self.assertTrue(any("删除" in fact["text"] for detail in risk["triggered_guardrail_details"] for match in detail["matches"] for fact in match["evidence"]))

    def test_code_and_config_removal_does_not_imply_destructive_database_operation(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("将IoT设备调试功能从管理系统中移除，并删除对应的代码和配置", self.project_risk)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        self.assertNotIn("destructive-database-operation", risk["triggered_guardrails"])
        self.assertNotIn("delete", evidence["code_findings"]["operations"])
        self.assertTrue(any(item.get("value") == "code_or_config_removal" for item in evidence["code_findings"]["operation_evidence"]))

    def test_related_orm_mapping_delete_triggers_destructive_database_operation(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app/iot_debug").mkdir(parents=True)
            (temp / "app/iot_debug/models.py").write_text(
                "from sqlalchemy import Column\n"
                "class IotDebugRecord:\n"
                "    __tablename__ = 'iot_debug_records'\n"
                "    def delete_debug_data(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            evidence = RepositoryAnalyzer(temp).analyze("删除 IoT debug 数据", self.project_risk)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertIn("destructive-database-operation", risk["triggered_guardrails"])
            self.assertIn("delete", evidence["code_findings"]["operations"])
        finally:
            shutil.rmtree(temp)

    def test_unrelated_orm_mapping_delete_is_weak_signal_only(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app/auth").mkdir(parents=True)
            (temp / "app/auth/models.py").write_text(
                "from sqlalchemy import Column\n"
                "class AuthToken:\n"
                "    __tablename__ = 'auth_tokens'\n"
                "    def delete_token_data(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            evidence = RepositoryAnalyzer(temp).analyze("移除 IoT 调试功能", self.project_risk)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertNotIn("destructive-database-operation", risk["triggered_guardrails"])
            self.assertNotIn("delete", evidence["code_findings"]["operations"])
        finally:
            shutil.rmtree(temp)

    def test_distant_feature_token_does_not_make_delete_file_strong(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app/api").mkdir(parents=True)
            (temp / "app/api/auth.py").write_text(
                "API_TITLE = 'IoT management system'\n\n"
                "def delete_session(session_id):\n"
                "    sql = 'delete from auth_sessions where id = ?'\n"
                "    return sql\n",
                encoding="utf-8",
            )
            evidence = RepositoryAnalyzer(temp).analyze("将IoT设备调试功能从管理系统中移除，并删除对应的代码和配置", self.project_risk)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertNotIn("destructive-database-operation", risk["triggered_guardrails"])
            self.assertNotIn("delete", evidence["code_findings"]["operations"])
            weak_delete = [
                item for item in evidence["code_findings"]["operation_evidence"]
                if item.get("path") == "app/api/auth.py" and item.get("keyword") == "delete"
            ]
            self.assertTrue(weak_delete)
            self.assertTrue(all(item.get("strength") == "weak" for item in weak_delete))
        finally:
            shutil.rmtree(temp)

    def test_charging_profile_distinguishes_device_data_from_device_control(self):
        data_evidence = RepositoryAnalyzer(ROOT).analyze("删除重复的设备端口状态数据。", self.charging_project_risk)
        data_risk = RiskEvaluator(self.charging_project_risk, self.charging_guardrails).evaluate(data_evidence)
        self.assertIn("destructive-database-operation", data_risk["triggered_guardrails"])
        self.assertNotIn("device-control", data_risk["triggered_guardrails"])

        control_evidence = RepositoryAnalyzer(ROOT).analyze("修改设备停止充电指令的重试逻辑。", self.charging_project_risk)
        control_risk = RiskEvaluator(self.charging_project_risk, self.charging_guardrails).evaluate(control_evidence)
        self.assertIn("device-control", control_risk["triggered_guardrails"])

    def test_non_git_scan_records_unknowns(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "README.md").write_text("hello", encoding="utf-8")
            evidence = RepositoryAnalyzer(temp).analyze("修改提示文案", self.project_risk)
            self.assertFalse(evidence["repository"]["git_available"])
            self.assertTrue(any("git metadata" in item for item in evidence["unknowns"]))
            self.assertTrue(any("dynamic calls" in item for item in evidence["unknowns"]))
        finally:
            shutil.rmtree(temp)

    def test_project_root_falls_back_to_pwd_when_cwd_fails(self):
        temp = Path(tempfile.mkdtemp())
        original_cwd = cli_module.Path.cwd
        original_pwd = os.environ.get("PWD")
        try:
            cli_module.Path.cwd = classmethod(lambda cls: (_ for _ in ()).throw(OSError("cwd unavailable")))
            os.environ["PWD"] = str(temp)
            self.assertEqual(temp.resolve(), cli_module._project_root())
        finally:
            cli_module.Path.cwd = original_cwd
            if original_pwd is None:
                os.environ.pop("PWD", None)
            else:
                os.environ["PWD"] = original_pwd
            shutil.rmtree(temp)

    def test_cli_assess_generates_phase1_artifacts_and_stops(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "修改后台订单页面上的提示文案。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Review command: change-assess --review-workflow", result.stdout)
            runs = list((temp / ".ai-governance/runs").iterdir())
            self.assertEqual(1, len(runs))
            status = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--status", runs[0].name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
            self.assertIn("Run Status", status.stdout)
            self.assertIn("Current gate: workflow_plan_approval", status.stdout)
            self.assertIn("workflow not approved", status.stdout)
            self.assertIn("change-assess --approve-workflow", status.stdout)
            next_action = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", runs[0].name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(next_action.returncode, 0, next_action.stderr + next_action.stdout)
            self.assertIn("Next Action", next_action.stdout)
            self.assertIn("Requires user confirmation: yes", next_action.stdout)
            self.assertIn("Recommended action: approve_workflow", next_action.stdout)
            execute_next = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", runs[0].name, "--execute-next"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(execute_next.returncode, 3, execute_next.stderr + execute_next.stdout)
            self.assertIn("requires user confirmation", execute_next.stdout)
            for artifact in ("evidence-pack.yaml", "risk-assessment.yaml", "risk-assessment.md", "workflow-plan.md"):
                self.assertTrue((runs[0] / artifact).exists(), artifact)
            risk_data = load_yaml(runs[0] / "risk-assessment.yaml")
            self.assertIn("risk_explanation", risk_data)
            self.assertTrue(risk_data["risk_explanation"]["dimension_explanations"])
            self.assertTrue(risk_data["risk_explanation"]["guardrail_evaluations"])
            risk_md = (runs[0] / "risk-assessment.md").read_text(encoding="utf-8")
            self.assertIn("## Dimension Scores", risk_md)
            self.assertIn("## Guardrail Evaluations", risk_md)
            for artifact in ("review.md", "human-review.yaml"):
                self.assertTrue((runs[0] / artifact).exists(), artifact)
            progress = load_yaml(runs[0] / "progress.yaml")
            self.assertTrue(progress["steps"])
            self.assertEqual("done", progress["steps"][0]["status"])
            self.assertIsNotNone(progress["steps"][0]["duration_seconds"])
            review_md = (runs[0] / "review.md").read_text(encoding="utf-8")
            self.assertIn("Review Commands", review_md)
            self.assertNotIn("Editable File", review_md)
            self.assertNotIn("Edit `human-review.yaml`", review_md)
            self.assertFalse((runs[0] / "technical-plan.md").exists())
            workflow_plan = (runs[0] / "workflow-plan.md").read_text(encoding="utf-8")
            self.assertIn("不得生成 technical-plan", workflow_plan)
        finally:
            shutil.rmtree(temp)

    def test_human_review_can_approve_and_add_modules(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "修改后台订单页面上的提示文案。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            review = load_yaml(run_dir / "human-review.yaml")
            review["decision"] = "approve"
            review["module_changes"]["add_required"] = ["requirement_confirmation"]
            with (run_dir / "human-review.yaml").open("w", encoding="utf-8") as handle:
                import yaml

                yaml.safe_dump(review, handle, sort_keys=False, allow_unicode=True)
            approval = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--approve-workflow",
                    run_dir.name,
                    "--add-required",
                    "threat_analysis",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(approval.returncode, 0, approval.stderr + approval.stdout)
            approved = load_yaml(run_dir / "approved-workflow.yaml")
            self.assertIn("requirement_confirmation", approved["workflow_recommendation"]["required_modules"])
            self.assertIn("threat_analysis", approved["workflow_recommendation"]["required_modules"])
            self.assertEqual("human_cli_approval", approved["approval"]["reviewer"])
            self.assertTrue((run_dir / "approved-workflow-plan.md").exists())
            self.assertTrue((run_dir / ".workflow-approved").exists())
            self.assertFalse((run_dir / "technical-plan.md").exists())
        finally:
            shutil.rmtree(temp)

    def test_technical_plan_gate_requires_workflow_then_approval(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            assess = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "修改后台订单页面上的提示文案。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())

            blocked_plan = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--propose-technical-plan", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, blocked_plan.returncode)
            self.assertIn("workflow approval is required", blocked_plan.stdout)

            approval = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--approve-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(approval.returncode, 0, approval.stderr + approval.stdout)
            context = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--add-context",
                    run_dir.name,
                    "--include",
                    "edit copy text",
                    "--exclude",
                    "business logic changes",
                    "--user-fact",
                    "copy-only change confirmed",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(context.returncode, 0, context.stderr + context.stdout)

            proposed = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--propose-technical-plan", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proposed.returncode, 0, proposed.stderr + proposed.stdout)
            self.assertTrue((run_dir / "technical-plan.yaml").exists())
            plan = load_yaml(run_dir / "technical-plan.yaml")
            self.assertEqual("pass", plan["validation"]["status"])
            self.assertIn("edit copy text", plan["scope"]["included"])
            for module in load_yaml(run_dir / "approved-workflow.yaml")["workflow_recommendation"]["required_modules"]:
                self.assertIn(module, plan["module_coverage"])

            blocked_gate = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--check-gate", run_dir.name, "--stage", "implementation"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, blocked_gate.returncode)
            self.assertIn("technical plan has not been approved", blocked_gate.stdout)

            technical_approval = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--approve-technical-plan", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(technical_approval.returncode, 0, technical_approval.stderr + technical_approval.stdout)
            self.assertTrue((run_dir / ".technical-plan-approved").exists())

            gate = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--check-gate", run_dir.name, "--stage", "implementation"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(gate.returncode, 0, gate.stderr + gate.stdout)
            self.assertIn("GATE OK", gate.stdout)
        finally:
            shutil.rmtree(temp)

    def test_agent_tasks_are_generated_from_approved_workflow_level(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            assess = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "删除重复配置数据。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            approval = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--approve-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(approval.returncode, 0, approval.stderr + approval.stdout)
            tasks = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--generate-agent-tasks", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(tasks.returncode, 0, tasks.stderr + tasks.stdout)
            artifact = load_yaml(run_dir / "agent-tasks.yaml")
            self.assertTrue(artifact["policy"]["subagents_required"])
            task_ids = {task["id"] for task in artifact["tasks"]}
            self.assertIn("code_fact_scan", task_ids)
            self.assertIn("data_impact_analysis", task_ids)
            self.assertIn("adversarial_review", task_ids)
            self.assertIn("implementation_gate", task_ids)
            dependency_task = next(task for task in artifact["tasks"] if task["id"] == "dependency_analysis")
            self.assertIn("--complete-step", dependency_task["completion_command"])
            self.assertIn("--module dependency_analysis", dependency_task["completion_command"])
            self.assertTrue((run_dir / "agent-tasks.md").exists())

            started = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--start-step",
                    run_dir.name,
                    "--module",
                    "dependency_analysis",
                    "--agent",
                    "dependency-analyzer",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(started.returncode, 0, started.stderr + started.stdout)

            (run_dir / "dependency-analysis.yaml").write_text(
                "upstream: []\n"
                "downstream: []\n",
                encoding="utf-8",
            )
            invalid_completed = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--complete-step",
                    run_dir.name,
                    "--module",
                    "dependency_analysis",
                    "--artifact",
                    "dependency-analysis.yaml",
                    "--agent",
                    "dependency-analyzer",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(invalid_completed.returncode, 3, invalid_completed.stderr + invalid_completed.stdout)
            self.assertIn("Artifact validation blocked", invalid_completed.stdout)
            self.assertIn("missing required field: external_consumers", invalid_completed.stdout)
            invalid_progress = load_yaml(run_dir / "progress.yaml")
            invalid_dependency_step = next(step for step in invalid_progress["steps"] if step["id"] == "dependency_analysis")
            self.assertEqual("blocked", invalid_dependency_step["status"])
            self.assertTrue((run_dir / "artifact-validation-dependency_analysis.yaml").exists())
            blocked_status = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--status", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(blocked_status.returncode, 0, blocked_status.stderr + blocked_status.stdout)
            self.assertIn("artifact-validation-dependency_analysis.yaml blocked", blocked_status.stdout)
            self.assertIn("workflow step blocked: dependency_analysis", blocked_status.stdout)
            self.assertIn("--validate-artifact", blocked_status.stdout)

            (run_dir / "dependency-analysis.yaml").write_text(
                "upstream: []\n"
                "downstream: []\n"
                "external_consumers: []\n"
                "unknowns: []\n"
                "evidence:\n"
                "  - FACT: call graph checked\n",
                encoding="utf-8",
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--validate-artifact",
                    run_dir.name,
                    "--module",
                    "dependency_analysis",
                    "--artifact",
                    "dependency-analysis.yaml",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validation.returncode, 0, validation.stderr + validation.stdout)
            self.assertIn("Artifact validation: pass", validation.stdout)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--complete-step",
                    run_dir.name,
                    "--module",
                    "dependency_analysis",
                    "--artifact",
                    "dependency-analysis.yaml",
                    "--agent",
                    "dependency-analyzer",
                    "--note",
                    "call graph checked",
                ],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("Step completed: dependency_analysis", completed.stdout)
            self.assertIn("产物: dependency-analysis.yaml", completed.stdout)
            progress = load_yaml(run_dir / "progress.yaml")
            dependency_step = next(step for step in progress["steps"] if step["id"] == "dependency_analysis")
            self.assertEqual(dependency_step["status"], "done")
            self.assertEqual(dependency_step["agent"], "dependency-analyzer")
            self.assertIn("dependency-analysis.yaml", dependency_step["artifacts"])
            self.assertIn("call graph checked", dependency_step["notes"])
            self.assertIsNotNone(dependency_step["duration_seconds"])

            review = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--review-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr + review.stdout)
            self.assertIn("dependency-analysis.yaml", review.stdout)
        finally:
            shutil.rmtree(temp)

    def test_diff_verification_blocks_executable_change_for_comment_only_intent(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            (temp / "app").mkdir()
            database = temp / "app/database.py"
            database.write_text("# old comment\nDATABASE_NAME = 'main'\n", encoding="utf-8")
            intent_path = temp / "intent.yaml"
            intent_path.write_text(
                "\n".join([
                    "version: 1",
                    "change_kind: comment_change",
                    "change_nature: comment_only",
                    "summary: update a database.py comment only",
                    "confidence: high",
                    "request_goal:",
                    "  type: implementation",
                    "  requires_code_change: true",
                    "  default_stop_gate: workflow_plan_approval",
                    "scope:",
                    "  included: [comment only]",
                    "  excluded: [executable code]",
                    "  unknowns: [diff must confirm comment-only]",
                    "risk_hints:",
                    "  data_operation: false",
                    "  database_schema_change: false",
                    "  public_interface_change: false",
                    "  permission_change: false",
                    "  security_change: false",
                    "  financial_change: false",
                ]),
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], cwd=temp, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline"],
                cwd=temp,
                check=True,
                capture_output=True,
                text=True,
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            assess = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "修改 app/database.py 中的一行注释", "--intent-file", str(intent_path)],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            for command in (
                [sys.executable, str(temp / "bin/change-assess"), "--approve-workflow", run_dir.name],
                [sys.executable, str(temp / "bin/change-assess"), "--propose-technical-plan", run_dir.name],
                [sys.executable, str(temp / "bin/change-assess"), "--approve-technical-plan", run_dir.name],
            ):
                result = subprocess.run(command, cwd=temp, env=env, check=False, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            database.write_text("# new comment\nDATABASE_NAME = 'main'\n", encoding="utf-8")
            pass_result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--verify-diff", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(pass_result.returncode, 0, pass_result.stderr + pass_result.stdout)
            self.assertIn("Status: pass", pass_result.stdout)
            self.assertEqual("pass", load_yaml(run_dir / "diff-verification.yaml")["status"])
            approved_required = load_yaml(run_dir / "approved-workflow.yaml")["workflow_recommendation"]["required_modules"]
            for module in approved_required:
                completed_module = subprocess.run(
                    [sys.executable, str(temp / "bin/change-assess"), "--complete-step", run_dir.name, "--module", module],
                    cwd=temp,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed_module.returncode, 0, completed_module.stderr + completed_module.stdout)
            reassess = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--reassess", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(reassess.returncode, 0, reassess.stderr + reassess.stdout)
            self.assertTrue((run_dir / "post-evidence-pack.yaml").exists())
            self.assertTrue((run_dir / "post-risk-assessment.yaml").exists())
            self.assertTrue((run_dir / "post-risk-assessment.md").exists())
            self.assertTrue((run_dir / "reassessment.yaml").exists())
            self.assertIn("Requires human reapproval: False", reassess.stdout)
            verification = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--generate-verification-report", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verification.returncode, 0, verification.stderr + verification.stdout)
            self.assertIn("Status: pass", verification.stdout)
            self.assertTrue((run_dir / "verification-report.yaml").exists())
            self.assertTrue((run_dir / ".verification-complete").exists())
            self.assertEqual("COMPLETED", load_yaml(run_dir / "run-state.yaml")["state"])

            database.write_text("# new comment\nDATABASE_NAME = 'other'\n", encoding="utf-8")
            blocked = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--verify-diff", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(blocked.returncode, 3, blocked.stderr + blocked.stdout)
            self.assertIn("low-risk intent diff includes executable-looking changes", blocked.stdout)
            gate = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--check-gate", run_dir.name, "--stage", "implementation"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(gate.returncode, 3, gate.stderr + gate.stdout)
            self.assertIn("diff verification is blocked", gate.stdout)
        finally:
            shutil.rmtree(temp)

    def test_cli_review_workflow_lists_user_actions(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "修改后台订单页面上的提示文案。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            review = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--review-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr + review.stdout)
            self.assertIn("Allowed user changes", review.stdout)
            self.assertIn("流程状态栏", review.stdout)
            self.assertIn("已执行", review.stdout)
            self.assertIn("Guardrail evidence", review.stdout)
            self.assertIn("Recommended execution steps", review.stdout)
            self.assertIn("扫描当前代码", review.stdout)
            self.assertIn("--add-required", review.stdout)
            self.assertIn("--review-decision", review.stdout)
            self.assertNotIn("Required modules:", review.stdout)
        finally:
            shutil.rmtree(temp)

    def test_cli_review_workflow_shows_guardrail_evidence(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            subprocess.run(["git", "init"], cwd=temp, check=True, capture_output=True, text=True)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "删除重复配置数据。"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_dir = next((temp / ".ai-governance/runs").iterdir())
            review = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--review-workflow", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(review.returncode, 0, review.stderr + review.stdout)
            self.assertIn("Guardrail evidence", review.stdout)
            self.assertIn("DECISION: triggered destructive-database-operation", review.stdout)
            self.assertIn("FACT: user_request contains keyword '删除'", review.stdout)
        finally:
            shutil.rmtree(temp)

    def test_cleanup_runs_deletes_only_old_inactive_runs(self):
        temp = Path(tempfile.mkdtemp())
        try:
            runs = temp / ".ai-governance" / "runs"
            old_done = runs / "20260101-000000-old-done"
            old_active = runs / "20260101-000001-old-active"
            fresh_done = runs / "20260713-000000-fresh-done"
            for run_dir in (old_done, old_active, fresh_done):
                run_dir.mkdir(parents=True)
                (run_dir / "workflow-plan.md").write_text("plan", encoding="utf-8")
                (run_dir / "human-review.yaml").write_text("version: 1\n", encoding="utf-8")
            (old_done / ".workflow-approved").write_text("approved\n", encoding="utf-8")
            (fresh_done / ".workflow-approved").write_text("approved\n", encoding="utf-8")
            old_time = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
            fresh_time = datetime.now(timezone.utc).timestamp()
            for path in (old_done, old_active):
                os.utime(path, (old_time, old_time))
            os.utime(fresh_done, (fresh_time, fresh_time))

            dry = cleanup_runs(runs, {"retain_latest": 1, "retain_days": 30}, dry_run=True)
            self.assertIn(old_done, dry.deleted)
            self.assertIn(old_active, dry.skipped)
            self.assertTrue(old_done.exists())

            result = cleanup_runs(runs, {"retain_latest": 1, "retain_days": 30}, dry_run=False)
            self.assertIn(old_done, result.deleted)
            self.assertFalse(old_done.exists())
            self.assertTrue(old_active.exists())
            self.assertTrue(fresh_done.exists())
        finally:
            shutil.rmtree(temp)

    def test_weak_file_signal_requires_human_confirmation(self):
        evidence = {
            "version": 1,
            "request": {"original": "移除 IoT 调试功能", "normalized_intent": "移除 IoT 调试功能", "acceptance_criteria": []},
            "repository": {"branch": "main", "commit": "abc", "dirty": False},
            "code_findings": {
                "direct_files": [{"path": "app/api/auth.py", "reason": "WEAK SIGNAL: generic API file"}],
                "related_files": [],
                "affected_modules": ["app"],
                "affected_domains": [],
                "change_types": ["public_api"],
                "operations": [],
                "domain_evidence": [],
                "change_type_evidence": [{
                    "value": "public_api",
                    "source": "code_search",
                    "path": "app/api/auth.py",
                    "keyword": "api",
                    "strength": "weak",
                    "fact": "WEAK SIGNAL: app/api/auth.py contains keyword 'api' mapped to change_type public_api but does not match request-specific tokens.",
                }],
                "operation_evidence": [],
                "database_changes": False,
                "message_schema_changes": False,
                "public_api_changes": True,
                "scheduled_jobs_affected": False,
                "configuration_changes": False,
            },
            "dependency_findings": {"upstream": [], "downstream": [], "external_dependencies": []},
            "test_findings": {"existing_tests": [], "coverage_confidence": "low", "missing_test_areas": []},
            "runtime_findings": {"production_usage": "unknown", "traffic_level": "unknown", "observability": "low", "rollback_capability": "low"},
            "unknowns": ["UNKNOWN: weak public API signal requires human confirmation"],
            "evidence_sources": ["code_search", "guardrails"],
        }
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        public_detail = next(item for item in risk["triggered_guardrail_details"] if item["id"] == "public-interface-change")
        self.assertNotIn("public-interface-change", risk["triggered_guardrails"])
        self.assertEqual("weak", public_detail["strength"])
        self.assertTrue(public_detail["needs_human_confirmation"])

    def test_human_review_cannot_remove_hard_guardrail_module(self):
        evidence = RepositoryAnalyzer(ROOT).analyze("调整退款金额保留两位小数的计算方式。", self.project_risk)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        composer = WorkflowComposer(self.project_risk, self.modules)
        workflow = composer.compose(evidence, risk)
        temp = Path(tempfile.mkdtemp())
        try:
            from adaptive_change_governance.config_loader import dump_yaml

            dump_yaml(temp / "evidence-pack.yaml", evidence)
            dump_yaml(temp / "risk-assessment.yaml", risk)
            dump_yaml(temp / "workflow-recommendation.yaml", workflow)
            gate = HumanReviewGate(self.modules)
            gate.write_review_files(temp, evidence, risk, workflow)
            review = load_yaml(temp / "human-review.yaml")
            review["decision"] = "approve"
            review["module_changes"]["remove_required"] = ["business_rule_confirmation"]
            with (temp / "human-review.yaml").open("w", encoding="utf-8") as handle:
                import yaml

                yaml.safe_dump(review, handle, sort_keys=False, allow_unicode=True)
            with self.assertRaisesRegex(ReviewError, "cannot remove"):
                gate.approve_workflow(temp, self.project_risk)
        finally:
            shutil.rmtree(temp)


if __name__ == "__main__":
    unittest.main()
