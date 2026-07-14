from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml
from .repository_analyzer import RepositoryAnalyzer
from .risk_evaluator import LEVEL_ORDER, RiskEvaluator


@dataclass
class RiskScenarioValidator:
    root: Path
    project_risk: dict[str, Any]
    guardrails: dict[str, Any]
    calibration: dict[str, Any] | None = None

    def validate(self, scenarios_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
        scenarios = load_yaml(scenarios_path)
        results = []
        for scenario in scenarios.get("scenarios", []):
            results.append(self._validate_one(scenario))
        status = "pass" if all(item["status"] == "pass" for item in results) else "fail"
        report = {
            "version": 1,
            "source": str(scenarios_path),
            "status": status,
            "summary": {
                "total": len(results),
                "passed": sum(1 for item in results if item["status"] == "pass"),
                "failed": sum(1 for item in results if item["status"] != "pass"),
            },
            "results": results,
        }
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            dump_yaml(output_dir / "risk-scenario-report.yaml", report)
            (output_dir / "risk-scenario-report.md").write_text(self.render_markdown(report), encoding="utf-8")
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Risk Scenario Report",
            "",
            f"- DECISION: status={report.get('status')}",
            f"- FACT: total={report.get('summary', {}).get('total')}",
            f"- FACT: passed={report.get('summary', {}).get('passed')}",
            f"- FACT: failed={report.get('summary', {}).get('failed')}",
            "",
            "## Results",
        ]
        for item in report.get("results", []):
            lines.append(f"- DECISION: {item.get('id')} -> {item.get('status')}; expected={item.get('expected_level')}; actual={item.get('actual_level')}")
            for error in item.get("errors", []):
                lines.append(f"  - {error}")
        return "\n".join(lines) + "\n"

    def _validate_one(self, scenario: dict[str, Any]) -> dict[str, Any]:
        temp = Path(tempfile.mkdtemp())
        try:
            for path, content in scenario.get("repository", {}).get("files", {}).items():
                target = temp / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
            evidence = RepositoryAnalyzer(temp).analyze(str(scenario.get("request", "")), self.project_risk, intent=scenario.get("intent", {}))
            risk = RiskEvaluator(self.project_risk, self.guardrails, self.calibration).evaluate(evidence)
            errors = self._errors(scenario, risk)
            return {
                "id": scenario.get("id", "unknown"),
                "status": "pass" if not errors else "fail",
                "request": scenario.get("request", ""),
                "expected_level": scenario.get("expected", {}).get("level"),
                "actual_level": risk.get("final_level"),
                "expected_guardrails": scenario.get("expected", {}).get("triggered_guardrails", []),
                "actual_guardrails": risk.get("triggered_guardrails", []),
                "errors": errors,
            }
        finally:
            shutil.rmtree(temp)

    def _errors(self, scenario: dict[str, Any], risk: dict[str, Any]) -> list[str]:
        expected = scenario.get("expected", {})
        errors = []
        level = expected.get("level")
        if level and risk.get("final_level") != level:
            errors.append(f"DECISION: expected final level {level}, got {risk.get('final_level')}.")
        min_level = expected.get("minimum_level")
        if min_level and LEVEL_ORDER.get(risk.get("final_level", "L1"), 1) < LEVEL_ORDER.get(min_level, 1):
            errors.append(f"DECISION: expected minimum level {min_level}, got {risk.get('final_level')}.")
        expected_guardrails = set(expected.get("triggered_guardrails", []))
        actual_guardrails = set(risk.get("triggered_guardrails", []))
        missing = sorted(expected_guardrails - actual_guardrails)
        if missing:
            errors.append("DECISION: missing expected guardrails: " + ", ".join(missing))
        absent = set(expected.get("absent_guardrails", []))
        unexpected = sorted(absent & actual_guardrails)
        if unexpected:
            errors.append("DECISION: unexpected guardrails triggered: " + ", ".join(unexpected))
        return errors
