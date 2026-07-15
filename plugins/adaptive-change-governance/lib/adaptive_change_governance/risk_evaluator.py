from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LEVEL_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
LEVEL_BY_ORDER = {value: key for key, value in LEVEL_ORDER.items()}

WEIGHTS = {
    "business_criticality": 1.3,
    "production_impact": 1.4,
    "change_scope": 0.8,
    "dependency_coupling": 1.0,
    "uncertainty": 1.1,
    "reversibility": 1.2,
    "data_risk": 1.3,
    "security_risk": 1.3,
    "testability_risk": 1.0,
    "observability_risk": 0.9,
}


def render_risk_markdown(risk: dict[str, Any]) -> str:
    explanation = risk.get("risk_explanation", {})
    lines = [
        "# Risk Assessment",
        "",
        f"- FACT: weighted_score={risk.get('weighted_score')}",
        f"- DECISION: calculated_level={risk.get('calculated_level')}",
        f"- DECISION: guardrail_minimum_level={risk.get('guardrail_minimum_level')}",
        f"- DECISION: final_level={risk.get('final_level')}",
        "",
        "## Dimension Scores",
    ]
    for item in explanation.get("dimension_explanations", []):
        lines.append(f"- DECISION: {item.get('dimension')} score={item.get('score')} weight={item.get('weight')}")
        for fact in item.get("evidence", [])[:4]:
            lines.append(f"  - {fact}")
    lines.extend(["", "## Guardrail Evaluations"])
    for item in explanation.get("guardrail_evaluations", []):
        lines.append(
            f"- DECISION: {item.get('id')} status={item.get('status')} strength={item.get('strength')} "
            f"needs_human_confirmation={item.get('needs_human_confirmation')}"
        )
        for fact in item.get("evidence", [])[:3]:
            lines.append(f"  - {fact.get('text', fact)}")
        lines.append(f"  - {item.get('decision')}")
    lines.extend(["", "## Decision Trace"])
    lines.extend(f"- {item}" for item in explanation.get("decision_trace", []) or risk.get("judgments", []))
    lines.extend(["", "## Required By Guardrails"])
    lines.extend(f"- DECISION: {item}" for item in risk.get("required_by_guardrails", []) or ["none"])
    lines.extend(["", "## Prohibited"])
    lines.extend(f"- DECISION: {item}" for item in risk.get("prohibited", []) or ["none"])
    return "\n".join(lines) + "\n"


@dataclass
class RiskEvaluator:
    project_risk: dict[str, Any]
    guardrails: dict[str, Any]
    calibration: dict[str, Any] | None = None

    def evaluate(self, evidence: dict[str, Any]) -> dict[str, Any]:
        triggered_details = self._triggered_guardrail_details(evidence)
        triggered = self._triggered_guardrails(evidence, triggered_details)
        weak_candidates = [item for item in triggered_details if item.get("strength") == "weak"]
        dimensions = self._score_dimensions(evidence, triggered)
        weights = self._weights()
        weighted_score = round(sum(dimensions[key] * weight for key, weight in weights.items()), 2)
        calculated_level = self._level_from_score(weighted_score)
        guardrail_minimum = self._minimum_guardrail_level(triggered)
        final_level = self._max_level(calculated_level, guardrail_minimum)
        required_by_guardrails = sorted({module for item in triggered for module in item.get("require", [])})
        prohibited = sorted({module for item in triggered for module in item.get("prohibit", [])})
        explanation = self._risk_explanation(evidence, triggered_details, triggered, dimensions)
        return {
            "version": 1,
            "risk_dimensions": dimensions,
            "weights": weights,
            "calibration": self._calibration_summary(),
            "weighted_score": weighted_score,
            "baseline_level": self.project_risk["project"]["baseline_level"],
            "calculated_level": calculated_level,
            "guardrail_minimum_level": guardrail_minimum,
            "final_level": final_level,
            "triggered_guardrails": [item["id"] for item in triggered],
            "triggered_guardrail_details": triggered_details,
            "weak_guardrail_candidates": weak_candidates,
            "required_by_guardrails": required_by_guardrails,
            "prohibited": prohibited,
            "risk_explanation": explanation,
            "judgments": self._judgments(evidence, triggered, dimensions, calculated_level, final_level),
        }

    def _triggered_guardrails(self, evidence: dict[str, Any], details: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        # Guardrail suppression is only valid for goals that stop at an
        # analysis/decision gate. planning_only keeps guardrails so required
        # risk modules reach the technical plan, and a contradictory
        # implementation goal must never disarm them.
        goal = evidence.get("request", {}).get("request_goal", {})
        if goal.get("type") in {"analysis_only", "decision_support"} and goal.get("requires_code_change") is False:
            return []
        facts = self._fact_index(evidence)
        strong_ids = {item["id"] for item in details or [] if item.get("strength") == "strong"}
        triggered = []
        for guardrail in self.guardrails.get("hard_guardrails", []):
            conditions = guardrail.get("when", {}).get("any", [])
            if guardrail["id"] in strong_ids and any(self._condition_matches(condition, facts) for condition in conditions):
                triggered.append(guardrail)
        return triggered

    def _triggered_guardrail_details(self, evidence: dict[str, Any]) -> list[dict[str, Any]]:
        facts = self._fact_index(evidence)
        details = []
        for guardrail in self.guardrails.get("hard_guardrails", []):
            matches = []
            for condition in guardrail.get("when", {}).get("any", []):
                for key, value in condition.items():
                    if value in facts.get(key, set()):
                        matches.append({
                            "condition": {key: value},
                            "evidence": self._evidence_for_condition(evidence, key, value),
                        })
            if matches:
                strength = self._match_strength(matches)
                details.append({
                    "id": guardrail["id"],
                    "matches": matches,
                    "strength": strength,
                    "needs_human_confirmation": strength == "weak",
                    "require": guardrail.get("require", []),
                    "prohibit": guardrail.get("prohibit", []),
                    "decision": self._guardrail_decision(guardrail["id"], strength),
                })
        return details

    def _fact_index(self, evidence: dict[str, Any]) -> dict[str, set[str]]:
        code = evidence.get("code_findings", {})
        return {
            "affected_domain": set(code.get("affected_domains", [])),
            "change_type": set(code.get("change_types", [])),
            "operation": set(code.get("operations", [])),
        }

    def _condition_matches(self, condition: dict[str, str], facts: dict[str, set[str]]) -> bool:
        return any(value in facts.get(key, set()) for key, value in condition.items())

    def _evidence_for_condition(self, evidence: dict[str, Any], key: str, value: str) -> list[dict[str, str]]:
        code = evidence.get("code_findings", {})
        evidence_key = {
            "affected_domain": "domain_evidence",
            "change_type": "change_type_evidence",
            "operation": "operation_evidence",
        }.get(key)
        facts = []
        if evidence_key:
            for item in code.get(evidence_key, []):
                if item.get("value") == value:
                    facts.append({
                        "strength": item.get("strength", "strong"),
                        "text": item.get("fact", f"FACT: {key}={value}."),
                    })
        if not facts:
            facts.append({
                "strength": "weak",
                "text": f"WEAK SIGNAL: evidence-pack.yaml code_findings contains {key}={value}, but no direct keyword evidence was recorded.",
            })
        return facts[:5]

    def _match_strength(self, matches: list[dict[str, Any]]) -> str:
        strengths = [item.get("strength", "weak") for match in matches for item in match.get("evidence", [])]
        return "strong" if "strong" in strengths else "weak"

    def _guardrail_decision(self, guardrail_id: str, strength: str) -> str:
        if strength == "strong":
            return f"DECISION: triggered {guardrail_id} because strong evidence matched at least one hard-guardrail condition."
        return f"DECISION: triggered {guardrail_id} from weak signals; keep guardrail active and require human confirmation."

    def _risk_explanation(
        self,
        evidence: dict[str, Any],
        details: list[dict[str, Any]],
        triggered: list[dict[str, Any]],
        dimensions: dict[str, int],
    ) -> dict[str, Any]:
        triggered_ids = {str(item["id"]) for item in triggered}
        return {
            "dimension_explanations": self._dimension_explanations(evidence, dimensions),
            "guardrail_evaluations": self._guardrail_evaluations(details, triggered_ids),
            "decision_trace": self._decision_trace(evidence, details, triggered_ids),
        }

    def _dimension_explanations(self, evidence: dict[str, Any], dimensions: dict[str, int]) -> list[dict[str, Any]]:
        code = evidence.get("code_findings", {})
        tests = evidence.get("test_findings", {})
        file_risk = code.get("file_risk", {})
        boundary = code.get("feature_boundary", {})
        boundary_summary = boundary.get("summary", {}) if isinstance(boundary, dict) else {}
        artifact_context = evidence.get("artifact_context", {}) if isinstance(evidence.get("artifact_context"), dict) else {}
        unknowns = evidence.get("unknowns", [])
        inputs = {
            "business_criticality": [
                f"FACT: affected_domains={code.get('affected_domains', [])}",
                f"FACT: file_risk_effective_level={file_risk.get('effective_level', 'unknown')}",
            ],
            "production_impact": [
                f"FACT: public_api_changes={code.get('public_api_changes')}",
                f"FACT: database_changes={code.get('database_changes')}",
                f"FACT: message_schema_changes={code.get('message_schema_changes')}",
            ],
            "change_scope": [
                f"FACT: direct_files={len(code.get('direct_files', []))}",
                f"FACT: affected_modules={code.get('affected_modules', [])}",
                f"FACT: ambiguous_important_files={boundary_summary.get('ambiguous_important_files', 0)}",
            ],
            "dependency_coupling": [
                f"FACT: dependency complexity comes from project-risk.yaml engineering_health.dependency_complexity={self.project_risk.get('engineering_health', {}).get('dependency_complexity', 'unknown')}",
            ],
            "uncertainty": [
                f"UNKNOWN: unknown_count={len(unknowns)}",
                f"FACT: feature_boundary_confidence={boundary_summary.get('confidence', 'unknown')}",
                f"FACT: validated_artifact_confidence={self._artifact_context_confidence(artifact_context)}",
            ],
            "reversibility": [
                f"FACT: rollback capability comes from project-risk.yaml engineering_health.rollback_capability={self.project_risk.get('engineering_health', {}).get('rollback_capability', 'unknown')}",
            ],
            "data_risk": [
                f"FACT: database_changes={code.get('database_changes')}",
                f"FACT: operations={code.get('operations', [])}",
            ],
            "security_risk": [
                f"FACT: affected_domains={code.get('affected_domains', [])}",
            ],
            "testability_risk": [
                f"FACT: coverage_confidence={tests.get('coverage_confidence', 'unknown')}",
                f"FACT: existing_tests={tests.get('existing_tests', [])[:5]}",
            ],
            "observability_risk": [
                f"FACT: observability comes from project-risk.yaml engineering_health.observability={self.project_risk.get('engineering_health', {}).get('observability', 'unknown')}",
            ],
        }
        explanations = []
        for name, score in dimensions.items():
            explanations.append({
                "dimension": name,
                "score": score,
                "weight": self._weights().get(name),
                "evidence": inputs.get(name, []),
                "decision": self._dimension_decision(name, score),
            })
        return explanations

    def _dimension_decision(self, name: str, score: int) -> str:
        if score <= 1:
            band = "low"
        elif score <= 3:
            band = "medium"
        else:
            band = "high"
        return f"DECISION: {name} scored {score} ({band}) from code facts, project risk profile, and explicit UNKNOWN items."

    def _guardrail_evaluations(self, details: list[dict[str, Any]], triggered_ids: set[str]) -> list[dict[str, Any]]:
        by_id = {item.get("id"): item for item in details}
        evaluations = []
        for guardrail in self.guardrails.get("hard_guardrails", []):
            guardrail_id = guardrail.get("id")
            detail = by_id.get(guardrail_id)
            if guardrail_id in triggered_ids:
                status = "triggered"
                decision = f"DECISION: hard guardrail {guardrail_id} is active; required modules and prohibitions are mandatory."
            elif detail:
                status = "weak_candidate"
                decision = f"DECISION: hard guardrail {guardrail_id} is a weak candidate only; it requires human confirmation and does not set the hard minimum level."
            else:
                status = "not_matched"
                decision = f"DECISION: hard guardrail {guardrail_id} did not match current evidence."
            evaluations.append({
                "id": guardrail_id,
                "status": status,
                "strength": detail.get("strength") if detail else "none",
                "needs_human_confirmation": bool(detail.get("needs_human_confirmation")) if detail else False,
                "matched_conditions": [match.get("condition") for match in detail.get("matches", [])] if detail else [],
                "evidence": [
                    fact
                    for match in detail.get("matches", [])
                    for fact in match.get("evidence", [])
                ][:10] if detail else [],
                "required_modules": guardrail.get("require", []),
                "prohibited": guardrail.get("prohibit", []),
                "decision": decision,
            })
        return evaluations

    def _decision_trace(self, evidence: dict[str, Any], details: list[dict[str, Any]], triggered_ids: set[str]) -> list[str]:
        code = evidence.get("code_findings", {})
        boundary = code.get("feature_boundary", {})
        summary = boundary.get("summary", {}) if isinstance(boundary, dict) else {}
        artifact_context = evidence.get("artifact_context", {}) if isinstance(evidence.get("artifact_context"), dict) else {}
        trace = [
            f"FACT: feature boundary confidence={summary.get('confidence', 'unknown')}; confirmed_files={summary.get('confirmed_files', 0)}; ambiguous_important_files={summary.get('ambiguous_important_files', 0)}.",
            f"FACT: file risk inherent={code.get('file_risk', {}).get('highest_level', 'unknown')}; effective={code.get('file_risk', {}).get('effective_level', 'unknown')}.",
            f"FACT: validated artifact context confidence={self._artifact_context_confidence(artifact_context)}.",
            f"FACT: strong guardrails={sorted(triggered_ids)}.",
        ]
        weak = [item.get("id") for item in details if item.get("strength") == "weak"]
        if weak:
            trace.append(f"WEAK SIGNAL: weak guardrail candidates={weak}; they do not lower or remove hard gates and require human confirmation.")
        if evidence.get("unknowns"):
            trace.append("UNKNOWN: " + "; ".join(item.replace("UNKNOWN: ", "", 1) for item in evidence.get("unknowns", [])[:5]))
        trace.append("DECISION: final risk level is calculated from weighted dimensions, then raised if any strong hard guardrail minimum is higher.")
        return trace

    def _score_dimensions(self, evidence: dict[str, Any], triggered: list[dict[str, Any]]) -> dict[str, int]:
        business = self.project_risk.get("business_risk", {})
        engineering = self.project_risk.get("engineering_health", {})
        code = evidence.get("code_findings", {})
        tests = evidence.get("test_findings", {})
        unknowns = evidence.get("unknowns", [])
        domains = set(code.get("affected_domains", []))
        change_types = set(code.get("change_types", []))
        file_risk = code.get("file_risk", {})
        file_risk_score = int(file_risk.get("effective_score", file_risk.get("highest_score", 1)) or 1)
        feature_boundary = code.get("feature_boundary", {}) if isinstance(code.get("feature_boundary"), dict) else {}
        boundary_summary = feature_boundary.get("summary", {}) if isinstance(feature_boundary.get("summary"), dict) else {}
        boundary_confidence = str(boundary_summary.get("confidence", "unknown"))
        ambiguous_important_files = int(boundary_summary.get("ambiguous_important_files", 0) or 0)
        artifact_context = evidence.get("artifact_context", {}) if isinstance(evidence.get("artifact_context"), dict) else {}
        artifact_confidence = self._artifact_context_confidence(artifact_context)
        critical_domains = set(self.project_risk.get("critical_domains", []))
        intrinsically_sensitive = {
            "financial-calculation",
            "data-integrity",
            "database-schema",
            "message-contract",
            "public-interface",
            "authentication",
            "authorization",
            "credentials",
            "privacy-data",
            "external-system-control",
            "physical-device-control",
            "billing",
            "refund",
            "settlement",
            "reconciliation",
            "charging-control",
            "device-command",
        }
        sensitive_change = bool(domains & (critical_domains | intrinsically_sensitive))
        doc_only = change_types and change_types <= {"documentation"} and not sensitive_change
        text_only = bool(code.get("text_only_change")) and not code.get("database_changes") and not code.get("public_api_changes") and not code.get("message_schema_changes")
        public_or_data_change = code.get("database_changes") or code.get("public_api_changes") or code.get("message_schema_changes")
        if doc_only or text_only:
            business_criticality = 1
            production_impact = 1
            change_scope = 1
            uncertainty = 1 + (1 if not code.get("direct_files") else 0)
            reversibility = 1
            data_risk = 1
            testability_risk = 1
            observability_risk = 1
        else:
            business_criticality = max(1, int(business.get("criticality", 3))) if sensitive_change else 2
            production_impact = max(1, int(business.get("customer_impact", 3))) if (sensitive_change or public_or_data_change) else 2
            change_scope = 1 + min(4, len(code.get("direct_files", [])) // 5 + len(code.get("affected_modules", [])))
            uncertainty = 2 + min(3, len(unknowns) // 2)
            if boundary_confidence == "low":
                uncertainty = max(uncertainty, 4)
            elif boundary_confidence == "medium":
                uncertainty = max(uncertainty, 3)
            if ambiguous_important_files:
                uncertainty = max(uncertainty, 4)
            if artifact_confidence == "high" and not ambiguous_important_files:
                uncertainty = max(1, uncertainty - 1)
            elif artifact_confidence == "medium" and uncertainty > 3:
                uncertainty -= 1
            reversibility = 6 - max(1, int(engineering.get("rollback_capability", 3)))
            data_risk = max(1, int(business.get("data_integrity", 3))) if code.get("database_changes") else (3 if sensitive_change else 1)
            testability_risk = 5 if tests.get("coverage_confidence") == "low" else 3
            observability_risk = 6 - max(1, int(engineering.get("observability", 3)))
        dimensions = {
            "business_criticality": business_criticality,
            "production_impact": production_impact,
            "change_scope": change_scope,
            "dependency_coupling": max(1, int(engineering.get("dependency_complexity", 3))),
            "uncertainty": uncertainty,
            "reversibility": reversibility,
            "data_risk": data_risk,
            "security_risk": max(1, int(business.get("compliance", 3))) if self._has_security_domain(code) else 1,
            "testability_risk": testability_risk,
            "observability_risk": observability_risk,
        }
        if triggered:
            dimensions["uncertainty"] = max(dimensions["uncertainty"], 4)
        if code.get("public_api_changes") or code.get("message_schema_changes"):
            dimensions["production_impact"] = max(dimensions["production_impact"], 4)
        if file_risk_score >= 4:
            dimensions["business_criticality"] = max(dimensions["business_criticality"], 3)
            dimensions["production_impact"] = max(dimensions["production_impact"], 3)
            dimensions["uncertainty"] = max(dimensions["uncertainty"], 3)
            dimensions["reversibility"] = max(dimensions["reversibility"], 3)
        if file_risk_score >= 5:
            dimensions["data_risk"] = max(dimensions["data_risk"], 4)
            dimensions["production_impact"] = max(dimensions["production_impact"], 4)
        if ambiguous_important_files:
            dimensions["change_scope"] = max(dimensions["change_scope"], min(5, 2 + ambiguous_important_files))
        self._apply_blast_radius(dimensions, code, doc_only or text_only)
        return {key: min(5, max(1, value)) for key, value in dimensions.items()}

    def _apply_blast_radius(self, dimensions: dict[str, int], code: dict[str, Any], display_only: bool) -> None:
        # Blast radius is a code-grounded fact: a tiny edit to a widely referenced
        # symbol is high risk. Reference fan-out drives change_scope and makes
        # dependency_coupling change-specific instead of a flat project constant.
        # It may only raise risk (monotonic), never lower a guardrail.
        reference = code.get("reference_findings", {})
        if not isinstance(reference, dict) or not reference.get("changed_symbols"):
            return
        inbound = int(reference.get("inbound_reference_count", 0) or 0)
        modules = len(reference.get("referencing_modules", []) or [])
        fan_out = self._fan_out_score(inbound, modules)
        dimensions["dependency_coupling"] = fan_out
        if not display_only:
            dimensions["change_scope"] = max(dimensions["change_scope"], fan_out)
        if reference.get("is_shared_contract") and not display_only:
            dimensions["change_scope"] = max(dimensions["change_scope"], 4)
            dimensions["production_impact"] = max(dimensions["production_impact"], 4)

    def _fan_out_score(self, inbound: int, modules: int) -> int:
        if inbound >= 200 or modules >= 5:
            return 5
        if inbound >= 50 or modules >= 3:
            return 4
        if inbound >= 10 or modules >= 2:
            return 3
        if inbound >= 1:
            return 2
        return 1

    def _has_security_domain(self, code: dict[str, Any]) -> bool:
        security = {"authentication", "authorization", "credential", "token"}
        return bool(security & set(code.get("affected_domains", [])))

    def _artifact_context_confidence(self, artifact_context: dict[str, Any]) -> str:
        values = {str(item.get("confidence", "")) for item in artifact_context.get("artifacts", []) if isinstance(item, dict)}
        if "high" in values:
            return "high"
        if "medium" in values:
            return "medium"
        if "low" in values:
            return "low"
        return "unknown"

    def _level_from_score(self, score: float) -> str:
        thresholds = self._thresholds()
        if score < thresholds["L2"]:
            return "L1"
        if score < thresholds["L3"]:
            return "L2"
        if score < thresholds["L4"]:
            return "L3"
        return "L4"

    def _weights(self) -> dict[str, float]:
        weights = dict(WEIGHTS)
        overrides = self._calibration_section("dimension_weight_overrides")
        for key, value in overrides.items():
            if key in weights and isinstance(value, (int, float)):
                weights[key] = float(value)
        return weights

    def _thresholds(self) -> dict[str, float]:
        thresholds = {"L2": 15.0, "L3": 27.0, "L4": 40.0}
        configured = self._calibration_section("level_thresholds")
        for key in thresholds:
            value = configured.get(key)
            if isinstance(value, (int, float)):
                thresholds[key] = float(value)
        return thresholds

    def _calibration_section(self, key: str) -> dict[str, Any]:
        if not isinstance(self.calibration, dict):
            return {}
        value = self.calibration.get(key)
        return value if isinstance(value, dict) else {}

    def _calibration_summary(self) -> dict[str, Any]:
        return {
            "source": self.calibration.get("source", "default") if isinstance(self.calibration, dict) else "default",
            "level_thresholds": self._thresholds(),
            "dimension_weight_overrides": self._calibration_section("dimension_weight_overrides"),
        }

    def _minimum_guardrail_level(self, triggered: list[dict[str, Any]]) -> str:
        level = "L1"
        overrides = self.guardrails.get("level_overrides", {})
        for guardrail in triggered:
            minimum = overrides.get(guardrail["id"], {}).get("minimum_level", "L1")
            level = self._max_level(level, minimum)
        return level

    def _max_level(self, left: str, right: str) -> str:
        return LEVEL_BY_ORDER[max(LEVEL_ORDER[left], LEVEL_ORDER[right])]

    def _judgments(self, evidence: dict[str, Any], triggered: list[dict[str, Any]], dimensions: dict[str, int], calculated: str, final: str) -> list[str]:
        judgments = [
            f"FACT: project baseline level is {self.project_risk['project']['baseline_level']}.",
            f"FACT: repository branch is {evidence['repository']['branch']} and commit is {evidence['repository']['commit']}.",
            f"FACT: risk dimensions were scored with rule weights: {dimensions}.",
            f"DECISION: calculated level is {calculated}.",
        ]
        file_risk = evidence.get("code_findings", {}).get("file_risk", {})
        if file_risk.get("matches"):
            judgments.append(
                f"FACT: file risk inherent level is {file_risk.get('highest_level')} and effective level is {file_risk.get('effective_level')}."
            )
            if file_risk.get("risk_adjustment") != "none":
                judgments.append("DECISION: file risk was adjusted by change nature, subject to diff verification.")
        feature_boundary = evidence.get("code_findings", {}).get("feature_boundary", {})
        if feature_boundary:
            summary = feature_boundary.get("summary", {})
            judgments.append(
                "FACT: feature boundary confidence is "
                f"{summary.get('confidence', 'unknown')} with "
                f"{summary.get('confirmed_files', 0)} confirmed file(s) and "
                f"{summary.get('ambiguous_important_files', 0)} ambiguous important file(s)."
            )
            if summary.get("ambiguous_important_files", 0):
                judgments.append("UNKNOWN: important files with weak request relation increase uncertainty risk.")
        if triggered:
            judgments.append("FACT: hard guardrails matched: " + ", ".join(item["id"] for item in triggered) + ".")
            for detail in self._triggered_guardrail_details(evidence):
                for match in detail["matches"]:
                    judgments.extend(item["text"] for item in match["evidence"][:2])
                judgments.append(detail["decision"])
            judgments.append("DECISION: final level cannot be lower than hard guardrail minimum.")
        weak_candidates = [item["id"] for item in self._triggered_guardrail_details(evidence) if item.get("strength") == "weak"]
        if weak_candidates:
            judgments.append("WEAK SIGNAL: guardrail candidates need human confirmation and do not set hard minimum level: " + ", ".join(weak_candidates) + ".")
        if evidence.get("unknowns"):
            normalized_unknowns = [item.replace("UNKNOWN: ", "", 1) for item in evidence["unknowns"][:3]]
            judgments.append("UNKNOWN: " + "; ".join(normalized_unknowns))
            judgments.append("INFERENCE: unresolved unknowns increase uncertainty risk.")
        judgments.append(f"DECISION: final level is {final}.")
        return judgments
