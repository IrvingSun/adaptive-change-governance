from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LEVEL_MODULES = {
    "L1": ["code_fact_scan", "regression_test"],
    "L2": ["requirement_confirmation", "code_fact_scan", "technical_design", "test_design", "regression_test"],
    "L3": ["requirement_confirmation", "code_fact_scan", "dependency_analysis", "technical_design", "test_design", "regression_test", "independent_review", "rollback_plan"],
    "L4": ["requirement_confirmation", "code_fact_scan", "dependency_analysis", "data_impact_analysis", "technical_design", "test_design", "regression_test", "independent_review", "adversarial_review", "rollback_plan", "staged_release", "post_release_monitoring"],
}


@dataclass
class WorkflowComposer:
    project_risk: dict[str, Any]
    workflow_modules: dict[str, Any]

    def compose(self, evidence: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
        final_level = risk["final_level"]
        request_goal = evidence.get("request", {}).get("request_goal", {})
        required = list(LEVEL_MODULES[final_level])
        required.extend(risk.get("required_by_guardrails", []))
        required = self._dedupe_existing(required)
        required = self._modules_for_goal(required, request_goal)
        optional = self._optional_modules(evidence, risk, required)
        skipped = self._skipped_modules(required, optional)
        human_gates = self._human_gates(risk, request_goal)
        escalation_triggers = self._escalation_triggers(evidence)
        return {
            "version": 1,
            "workflow_recommendation": {
                "request_goal": request_goal,
                "default_stop_gate": request_goal.get("default_stop_gate", "workflow_plan_approval"),
                "baseline_level": risk["baseline_level"],
                "calculated_level": risk["calculated_level"],
                "final_level": final_level,
                "triggered_guardrails": risk["triggered_guardrails"],
                "required_modules": required,
                "optional_modules": optional,
                "skipped_modules": skipped,
                "human_gates": human_gates,
                "escalation_triggers": escalation_triggers,
                "prohibited": risk.get("prohibited", []),
            },
            "judgments": self._judgments(evidence, risk, required, skipped),
        }

    def render_markdown(self, evidence: dict[str, Any], risk: dict[str, Any], workflow: dict[str, Any]) -> str:
        rec = workflow["workflow_recommendation"]
        lines = [
            "# Workflow Summary",
            "",
            f"Risk level: {rec['final_level']}",
            f"Required modules: {', '.join(rec.get('required_modules', [])) or 'none'}",
            f"Skipped modules: {len(rec.get('skipped_modules', []))} (see section 7 below)",
            f"Next gate: {rec.get('default_stop_gate', 'workflow_plan_approval')}",
            "",
            "---",
            "",
            "# Workflow Plan",
            "",
            "## 1. 任务摘要",
            f"- FACT: 用户原始诉求：{evidence['request']['original']}",
            f"- INFERENCE: 归一化目标：{evidence['request']['normalized_intent']}",
            f"- INFERENCE: 请求目标类型：{rec.get('request_goal', {}).get('type', 'implementation')}；requires_code_change={rec.get('request_goal', {}).get('requires_code_change', 'unknown')}",
            f"- DECISION: 默认停止节点：{rec.get('default_stop_gate', 'workflow_plan_approval')}",
            "",
            "## 2. 当前代码事实",
            f"- FACT: branch={evidence['repository']['branch']}, commit={evidence['repository']['commit']}, dirty={evidence['repository']['dirty']}",
            f"- FACT: direct_files={self._paths(evidence['code_findings']['direct_files']) or 'none'}",
            f"- FACT: related_files={self._paths(evidence['code_findings']['related_files']) or 'none'}",
            f"- FACT: affected_domains={evidence['code_findings']['affected_domains']}",
            f"- FACT: change_types={evidence['code_findings']['change_types']}",
            "",
            "## 3. 风险评估",
            f"- FACT: 项目基线等级：{rec['baseline_level']}",
            f"- FACT: 本次任务评分：{risk['weighted_score']}",
            f"- DECISION: 计算等级：{rec['calculated_level']}",
            f"- DECISION: 最终建议等级：{rec['final_level']}",
            f"- FACT: 命中的硬围栏：{rec['triggered_guardrails'] or []}",
            "",
            "## 4. 建议流程",
        ]
        lines.extend(f"- DECISION: {module}" for module in rec["required_modules"])
        lines.extend([
            "",
            "## 5. 必须执行的流程模块",
        ])
        lines.extend(self._module_lines(rec["required_modules"]))
        lines.extend([
            "",
            "## 6. 可选模块",
        ])
        lines.extend(self._module_lines(rec["optional_modules"]) or ["- DECISION: none"])
        lines.extend([
            "",
            "## 7. 明确跳过的模块",
        ])
        for item in rec["skipped_modules"]:
            lines.append(f"- DECISION: {item['module']} skipped. {item['reason']}")
        lines.extend([
            "",
            "## 8. 人工确认节点",
        ])
        lines.extend(f"- DECISION: {gate}" for gate in rec["human_gates"])
        lines.extend([
            "",
            "## 9. 流程升级条件",
        ])
        lines.extend(f"- DECISION: {trigger}" for trigger in rec["escalation_triggers"])
        lines.extend([
            "",
            "## 10. 未知信息",
        ])
        lines.extend(f"- UNKNOWN: {item.replace('UNKNOWN: ', '')}" for item in evidence["unknowns"])
        lines.extend([
            "",
            "## 11. 待调查问题",
        ])
        questions = evidence.get("investigation_questions", {}).get("questions", [])
        if questions:
            for item in questions:
                lines.append(
                    f"- DECISION: [{item.get('priority')}] {item.get('module')} -> "
                    f"{item.get('expected_artifact')}; {item.get('question')}"
                )
                lines.append(f"  - {item.get('reason')}")
        else:
            lines.append("- DECISION: none")
        lines.extend([
            "",
            "## 12. 判断依据",
        ])
        lines.extend(f"- {judgment}" for judgment in risk["judgments"])
        lines.extend(f"- {judgment}" for judgment in workflow["judgments"])
        lines.extend([
            "",
            "## 13. 阶段边界",
            "- DECISION: 在 workflow-plan 获得人工确认前，不得生成 technical-plan。",
            "- DECISION: 在 technical-plan 获得人工确认前，不得修改业务代码。",
        ])
        return "\n".join(lines) + "\n"

    def _dedupe_existing(self, modules: list[str]) -> list[str]:
        available = self.workflow_modules.get("modules", {})
        result = []
        for module in modules:
            if module in available and module not in result:
                result.append(module)
        return result

    def _modules_for_goal(self, required: list[str], request_goal: dict[str, Any]) -> list[str]:
        goal_type = request_goal.get("type", "implementation")
        if goal_type in {"analysis_only", "decision_support"}:
            excluded = {
                "technical_design",
                "test_design",
                "regression_test",
                "rollback_plan",
                "staged_release",
                "post_release_monitoring",
            }
            return [module for module in required if module not in excluded]
        if goal_type == "planning_only":
            excluded = {
                "regression_test",
                "staged_release",
                "post_release_monitoring",
            }
            return [module for module in required if module not in excluded]
        return required

    def _optional_modules(self, evidence: dict[str, Any], risk: dict[str, Any], required: list[str]) -> list[str]:
        optional = []
        if risk["final_level"] == "L3":
            optional.extend(["adversarial_review", "staged_release", "post_release_monitoring"])
        if evidence["code_findings"]["public_api_changes"] and "compatibility_analysis" not in required:
            optional.append("compatibility_analysis")
        return self._dedupe_existing([module for module in optional if module not in required])

    def _skipped_modules(self, required: list[str], optional: list[str]) -> list[dict[str, str]]:
        skipped = []
        for module in sorted(self.workflow_modules.get("modules", {}).keys()):
            if module not in required and module not in optional:
                skipped.append({"module": module, "reason": "DECISION: not required by calculated level, hard guardrail, or current evidence."})
        return skipped

    def _human_gates(self, risk: dict[str, Any], request_goal: dict[str, Any]) -> list[str]:
        if request_goal.get("type") in {"analysis_only", "decision_support"}:
            return [request_goal.get("default_stop_gate", "analysis_complete")]
        gates = list(self.project_risk.get("default_human_gates", []))
        if "workflow_plan_approval" not in gates:
            gates.insert(0, "workflow_plan_approval")
        if risk.get("triggered_guardrails") and "hard_guardrail_review" not in gates:
            gates.append("hard_guardrail_review")
        return gates

    def _escalation_triggers(self, evidence: dict[str, Any]) -> list[str]:
        triggers = [
            "discovers_cross_module_dependency",
            "discovers_database_schema_change",
            "discovers_message_schema_change",
            "discovers_public_api_change",
            "discovers_historical_data_impact",
            "no_safe_rollback",
            "test_failure_with_unclear_cause",
            "actual_change_scope_exceeds_initial_evidence",
        ]
        if evidence["repository"]["commit"] == "unknown":
            triggers.append("git_baseline_becomes_available_or_changes")
        return triggers

    def _judgments(self, evidence: dict[str, Any], risk: dict[str, Any], required: list[str], skipped: list[dict[str, str]]) -> list[str]:
        return [
            f"DECISION: required workflow modules are derived from final level {risk['final_level']} plus hard guardrail requirements.",
            "DECISION: hard guardrail modules can only be appended or strengthened, never removed by model judgment.",
            f"FACT: {len(skipped)} workflow modules were explicitly skipped with reasons.",
            "INFERENCE: keyword scanning has limited confidence for dynamic routes and implicit dependencies, so unknowns remain visible.",
        ]

    def _module_lines(self, modules: list[str]) -> list[str]:
        lines = []
        for module in modules:
            meta = self.workflow_modules.get("modules", {}).get(module, {})
            lines.append(f"- DECISION: {module} -> {meta.get('output', 'unknown output')}；{meta.get('description', '')}")
        return lines

    def _paths(self, findings: list[dict[str, str]]) -> list[str]:
        return [item["path"] for item in findings]
