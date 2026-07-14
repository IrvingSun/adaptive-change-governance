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


@dataclass
class RiskEvaluator:
    project_risk: dict[str, Any]
    guardrails: dict[str, Any]

    def evaluate(self, evidence: dict[str, Any]) -> dict[str, Any]:
        triggered_details = self._triggered_guardrail_details(evidence)
        triggered = self._triggered_guardrails(evidence, triggered_details)
        weak_candidates = [item for item in triggered_details if item.get("strength") == "weak"]
        dimensions = self._score_dimensions(evidence, triggered)
        weighted_score = round(sum(dimensions[key] * weight for key, weight in WEIGHTS.items()), 2)
        calculated_level = self._level_from_score(weighted_score)
        guardrail_minimum = self._minimum_guardrail_level(triggered)
        final_level = self._max_level(calculated_level, guardrail_minimum)
        required_by_guardrails = sorted({module for item in triggered for module in item.get("require", [])})
        prohibited = sorted({module for item in triggered for module in item.get("prohibit", [])})
        return {
            "version": 1,
            "risk_dimensions": dimensions,
            "weights": WEIGHTS,
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
            "judgments": self._judgments(evidence, triggered, dimensions, calculated_level, final_level),
        }

    def _triggered_guardrails(self, evidence: dict[str, Any], details: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if evidence.get("request", {}).get("request_goal", {}).get("requires_code_change") is False:
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
        return {key: min(5, max(1, value)) for key, value in dimensions.items()}

    def _has_security_domain(self, code: dict[str, Any]) -> bool:
        security = {"authentication", "authorization", "credential", "token"}
        return bool(security & set(code.get("affected_domains", [])))

    def _level_from_score(self, score: float) -> str:
        if score < 15:
            return "L1"
        if score < 27:
            return "L2"
        if score < 40:
            return "L3"
        return "L4"

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
