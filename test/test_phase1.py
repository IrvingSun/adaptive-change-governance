import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "lib"))

from adaptive_change_governance.config_loader import load_yaml
import adaptive_change_governance.cli as cli_module
from adaptive_change_governance.human_review import HumanReviewGate, ReviewError
from adaptive_change_governance.repository_analyzer import RepositoryAnalyzer
from adaptive_change_governance.risk_evaluator import RiskEvaluator
from adaptive_change_governance.schema_validator import ValidationError, validate_all
from adaptive_change_governance.workflow_composer import WorkflowComposer


class Phase1Test(unittest.TestCase):
    def setUp(self):
        self.project_risk = load_yaml(ROOT / ".ai-governance/project-risk.yaml")
        self.guardrails = load_yaml(ROOT / ".ai-governance/guardrails.yaml")
        self.modules = load_yaml(ROOT / ".ai-governance/workflow-modules.yaml")
        self.charging_project_risk = load_yaml(ROOT / ".ai-governance/profiles/charging-platform/project-risk.yaml")
        self.charging_guardrails = load_yaml(ROOT / ".ai-governance/profiles/charging-platform/guardrails.yaml")

    def test_config_files_validate(self):
        validate_all(self.project_risk, self.guardrails, self.modules)
        validate_all(self.charging_project_risk, self.charging_guardrails, self.modules)

    def test_bad_config_reports_clear_error(self):
        bad = dict(self.project_risk)
        bad["project"] = {"name": "bad", "baseline_level": "LX"}
        with self.assertRaisesRegex(ValidationError, "baseline_level"):
            validate_all(bad, self.guardrails, self.modules)

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
        self.assertTrue(any("删除" in fact for detail in risk["triggered_guardrail_details"] for match in detail["matches"] for fact in match["evidence"]))

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
            runs = list((temp / ".ai-governance/runs").iterdir())
            self.assertEqual(1, len(runs))
            for artifact in ("evidence-pack.yaml", "risk-assessment.yaml", "workflow-plan.md"):
                self.assertTrue((runs[0] / artifact).exists(), artifact)
            for artifact in ("review.md", "human-review.yaml"):
                self.assertTrue((runs[0] / artifact).exists(), artifact)
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
            review["approval"]["reviewer"] = "test-reviewer"
            with (run_dir / "human-review.yaml").open("w", encoding="utf-8") as handle:
                import yaml

                yaml.safe_dump(review, handle, sort_keys=False, allow_unicode=True)
            approval = subprocess.run(
                [
                    sys.executable,
                    str(temp / "bin/change-assess"),
                    "--approve-workflow",
                    run_dir.name,
                    "--reviewer",
                    "cli-reviewer",
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
            self.assertEqual("cli-reviewer", approved["approval"]["reviewer"])
            self.assertTrue((run_dir / "approved-workflow-plan.md").exists())
            self.assertTrue((run_dir / ".workflow-approved").exists())
            self.assertFalse((run_dir / "technical-plan.md").exists())
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
