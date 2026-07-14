from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml


QUESTION_MODULES = {
    "dynamic_dependency_check": ("dependency_analysis", "dependency-analysis.yaml"),
    "weak_guardrail_confirmation": ("hard_guardrail_review", "hard-guardrail-review.yaml"),
    "consumer_check": ("consumer_analysis", "consumer-analysis.yaml"),
    "data_impact_check": ("data_impact_analysis", "data-impact-analysis.yaml"),
    "feature_boundary_check": ("code_fact_scan", "code-fact-report.yaml"),
    "test_coverage_check": ("test_design", "test-design.yaml"),
}


@dataclass
class InvestigationQuestionComposer:
    def compose(self, evidence: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
        questions: list[dict[str, Any]] = []
        self._add_unknown_questions(questions, evidence)
        self._add_weak_guardrail_questions(questions, risk)
        self._add_feature_boundary_questions(questions, evidence)
        self._add_sensitive_change_questions(questions, evidence)
        deduped = self._dedupe(questions)
        return {
            "version": 1,
            "status": "open" if deduped else "none",
            "policy": {
                "agent_artifacts_do_not_directly_downgrade_risk": True,
                "strong_guardrail_evidence_cannot_be_removed_by_agent_artifact": True,
                "user_context_has_higher_priority_than_agent_artifact": True,
                "low_confidence_answers_remain_unknown": True,
            },
            "questions": deduped,
        }

    def write(self, run_dir: Path, questions: dict[str, Any]) -> None:
        dump_yaml(run_dir / "investigation-questions.yaml", questions)
        (run_dir / "investigation-questions.md").write_text(self.render_markdown(questions), encoding="utf-8")

    def render_markdown(self, questions: dict[str, Any]) -> str:
        lines = [
            "# Investigation Questions",
            "",
            f"- DECISION: status={questions.get('status', 'unknown')}",
            "",
            "## Policy",
        ]
        for key, value in questions.get("policy", {}).items():
            lines.append(f"- DECISION: {key}={value}")
        lines.extend(["", "## Questions"])
        for item in questions.get("questions", []) or []:
            lines.extend([
                f"### {item.get('id')}",
                f"- module: {item.get('module')}",
                f"- priority: {item.get('priority')}",
                f"- expected_artifact: {item.get('expected_artifact')}",
                f"- question: {item.get('question')}",
                f"- reason: {item.get('reason')}",
                f"- status: {item.get('status')}",
                "- evidence:",
            ])
            lines.extend(f"  - {entry}" for entry in item.get("evidence", []) or ["none"])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _add_unknown_questions(self, questions: list[dict[str, Any]], evidence: dict[str, Any]) -> None:
        for unknown in evidence.get("unknowns", []):
            text = str(unknown)
            lower = text.lower()
            if any(token in lower for token in ["dynamic", "route", "implicit", "dependency", "framework", "调用", "路由", "依赖"]):
                self._append(questions, "dynamic_dependency_check", "high", "核实是否存在动态调用、框架路由、生成代码或隐式依赖影响本次变更。", text, [text])
            elif any(token in lower for token in ["test", "coverage", "测试"]):
                self._append(questions, "test_coverage_check", "medium", "核实受影响行为是否存在自动化测试或需要新增测试设计。", text, [text])

    def _add_weak_guardrail_questions(self, questions: list[dict[str, Any]], risk: dict[str, Any]) -> None:
        for detail in risk.get("weak_guardrail_candidates", []):
            guardrail_id = detail.get("id", "unknown")
            evidence = [
                fact.get("text", str(fact))
                for match in detail.get("matches", [])
                for fact in match.get("evidence", [])
            ][:5]
            question = f"确认弱信号护栏 {guardrail_id} 是否真实适用于本次变更，还是仅为泛关键词/弱相关文件命中。"
            self._append(questions, "weak_guardrail_confirmation", "high", question, detail.get("decision", f"weak guardrail candidate: {guardrail_id}"), evidence)

    def _add_feature_boundary_questions(self, questions: list[dict[str, Any]], evidence: dict[str, Any]) -> None:
        boundary = evidence.get("code_findings", {}).get("feature_boundary", {})
        summary = boundary.get("summary", {}) if isinstance(boundary, dict) else {}
        if summary.get("confidence") in {"low", "medium"} or summary.get("ambiguous_important_files", 0):
            evidence_lines = [item.get("fact", str(item)) for item in boundary.get("ambiguous_files", [])[:5]]
            self._append(
                questions,
                "feature_boundary_check",
                "high",
                "确认本次变更的真实功能边界，区分应修改文件、弱信号文件和不应触碰的共享基础设施。",
                f"feature_boundary confidence={summary.get('confidence', 'unknown')}, ambiguous_important_files={summary.get('ambiguous_important_files', 0)}",
                evidence_lines,
            )

    def _add_sensitive_change_questions(self, questions: list[dict[str, Any]], evidence: dict[str, Any]) -> None:
        code = evidence.get("code_findings", {})
        if code.get("public_api_changes") or code.get("message_schema_changes"):
            self._append(
                questions,
                "consumer_check",
                "high",
                "核实受影响接口、路由或消息结构是否存在外部消费者、前端消费者或跨服务调用方。",
                "public_api_changes or message_schema_changes is true",
                [str(item) for item in code.get("change_type_evidence", [])[:5]],
            )
        if code.get("database_changes") or code.get("operations"):
            self._append(
                questions,
                "data_impact_check",
                "high",
                "核实是否存在真实数据变更、影响行数、dry-run 查询和回滚/恢复方案。",
                "database_changes or data operations were detected",
                [str(item) for item in code.get("operation_evidence", [])[:5]],
            )

    def _append(self, questions: list[dict[str, Any]], kind: str, priority: str, question: str, reason: str, evidence: list[str]) -> None:
        module, artifact = QUESTION_MODULES[kind]
        questions.append({
            "id": kind,
            "module": module,
            "priority": priority,
            "question": question,
            "reason": reason,
            "expected_artifact": artifact,
            "status": "open",
            "evidence": evidence or [],
        })

    def _dedupe(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        order = {"high": 3, "medium": 2, "low": 1}
        for item in questions:
            key = (item["id"], item["expected_artifact"])
            existing = by_key.get(key)
            if not existing:
                by_key[key] = item
                continue
            existing["evidence"] = _unique(existing.get("evidence", []) + item.get("evidence", []))[:10]
            if order.get(item.get("priority", ""), 0) > order.get(existing.get("priority", ""), 0):
                existing["priority"] = item["priority"]
        return sorted(by_key.values(), key=lambda item: (-order.get(item.get("priority", ""), 0), item["id"]))


def _unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
