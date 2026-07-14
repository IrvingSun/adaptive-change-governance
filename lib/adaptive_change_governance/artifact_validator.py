from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import ConfigError, dump_yaml, load_yaml


class ArtifactValidationError(ValueError):
    pass


def _count_lines(path: Path) -> int | None:
    """Line count of a text file, or None when it cannot be read."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


@dataclass
class ArtifactValidator:
    schemas: dict[str, Any]

    def validate(
        self,
        run_dir: Path,
        module: str,
        artifact: str,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        schema = self._schema_for(module)
        artifact_path = self._artifact_path(run_dir, artifact)
        errors = []
        if not artifact_path.exists():
            errors.append(f"artifact file not found: {artifact}")
        elif schema:
            try:
                data = load_yaml(artifact_path)
            except ConfigError as exc:
                errors.append(str(exc))
            else:
                errors.extend(self._validate_required_fields(data, schema.get("required_fields", [])))
                errors.extend(self._validate_evidence(data, schema))
                errors.extend(self._validate_evidence_locations(data, schema, project_root))
        report = {
            "version": 1,
            "run_id": run_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "module": module,
            "artifact": artifact,
            "schema_applied": bool(schema),
            "required_fields": list(schema.get("required_fields", [])) if schema else [],
            "schema_rules": self._schema_rules(schema),
            "status": "pass" if not errors else "blocked",
            "errors": errors,
        }
        self._write_report(run_dir, report)
        return report

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Artifact Validation",
            "",
            f"- FACT: run_id={report.get('run_id')}",
            f"- FACT: module={report.get('module')}",
            f"- FACT: artifact={report.get('artifact')}",
            f"- FACT: schema_applied={report.get('schema_applied')}",
            f"- DECISION: status={report.get('status')}",
            "",
            "## Required Fields",
        ]
        lines.extend(f"- FACT: {item}" for item in report.get("required_fields", []) or ["none"])
        lines.extend(["", "## Schema Rules"])
        for key, value in report.get("schema_rules", {}).items():
            lines.append(f"- FACT: {key}={value}")
        if report.get("errors"):
            lines.extend(["", "## Blocking Errors"])
            lines.extend(f"- DECISION: {item}" for item in report["errors"])
        return "\n".join(lines) + "\n"

    def _schema_for(self, module: str) -> dict[str, Any]:
        schemas = self.schemas.get("schemas", {}) if isinstance(self.schemas, dict) else {}
        schema = schemas.get(module, {})
        return schema if isinstance(schema, dict) else {}

    def _artifact_path(self, run_dir: Path, artifact: str) -> Path:
        path = Path(artifact)
        if path.is_absolute():
            return path
        return run_dir / path

    def _validate_required_fields(self, data: dict[str, Any], required_fields: list[str]) -> list[str]:
        errors = []
        for field in required_fields:
            if field not in data:
                errors.append(f"missing required field: {field}")
            elif data[field] in ("", None):
                errors.append(f"required field is empty: {field}")
        return errors

    def _validate_evidence(self, data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not schema.get("evidence_required") and not schema.get("evidence_path_line_required") and not schema.get("confidence_required"):
            return errors
        evidence = data.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append("evidence must be a non-empty list")
            return errors
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] must be a mapping")
                continue
            if schema.get("evidence_path_line_required"):
                for field in ("path", "line", "fact"):
                    if item.get(field) in ("", None):
                        errors.append(f"evidence[{index}].{field} is required")
                if "line" in item and not isinstance(item.get("line"), int):
                    errors.append(f"evidence[{index}].line must be an integer")
            if schema.get("confidence_required") and item.get("confidence") not in {"high", "medium", "low"}:
                errors.append(f"evidence[{index}].confidence must be high, medium, or low")
            fact = str(item.get("fact", ""))
            if fact and not fact.startswith(("FACT:", "INFERENCE:", "UNKNOWN:", "WEAK SIGNAL:", "DECISION:")):
                errors.append(f"evidence[{index}].fact must start with FACT:, INFERENCE:, UNKNOWN:, WEAK SIGNAL:, or DECISION:")
        return errors

    def _validate_evidence_locations(
        self, data: dict[str, Any], schema: dict[str, Any], project_root: Path | None
    ) -> list[str]:
        """Spot-check that FACT evidence cites a location that actually resolves.

        Confirms a cited file:line exists inside the governed project; it does
        NOT verify that the location supports the claim (see docs/threat-model.md,
        O3 fabricated-but-plausible evidence). Fails open: skipped when the check
        is not requested, the project root is unknown, or a file cannot be read.
        Only FACT evidence is checked; INFERENCE/UNKNOWN/WEAK SIGNAL/DECISION may
        reference planned or hypothetical locations.
        """
        if not schema.get("evidence_location_must_resolve") or project_root is None:
            return []
        evidence = data.get("evidence")
        if not isinstance(evidence, list):
            return []
        try:
            root = project_root.resolve()
        except OSError:
            return []
        errors: list[str] = []
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                continue
            if not str(item.get("fact", "")).startswith("FACT:"):
                continue
            raw_path = item.get("path")
            line = item.get("line")
            if raw_path in ("", None) or not isinstance(line, int):
                continue  # missing/mistyped fields are reported by _validate_evidence
            target = (root / str(raw_path)).resolve()
            if not target.is_relative_to(root):
                errors.append(f"evidence[{index}] path escapes the project: {raw_path}")
                continue
            if not target.is_file():
                errors.append(f"evidence[{index}] cites a file that does not exist: {raw_path}")
                continue
            line_count = _count_lines(target)
            if line_count is None:
                continue  # unreadable: cannot verify, do not block
            if line < 1 or line > line_count:
                errors.append(
                    f"evidence[{index}] cites {raw_path}:{line} but the file has {line_count} lines"
                )
        return errors

    def _schema_rules(self, schema: dict[str, Any]) -> dict[str, Any]:
        if not schema:
            return {}
        return {
            "evidence_required": bool(schema.get("evidence_required")),
            "evidence_path_line_required": bool(schema.get("evidence_path_line_required")),
            "confidence_required": bool(schema.get("confidence_required")),
            "evidence_location_must_resolve": bool(schema.get("evidence_location_must_resolve")),
        }

    def _write_report(self, run_dir: Path, report: dict[str, Any]) -> None:
        module = report.get("module", "artifact")
        yaml_path = run_dir / f"artifact-validation-{module}.yaml"
        md_path = run_dir / f"artifact-validation-{module}.md"
        dump_yaml(yaml_path, report)
        md_path.write_text(self.render_markdown(report), encoding="utf-8")
