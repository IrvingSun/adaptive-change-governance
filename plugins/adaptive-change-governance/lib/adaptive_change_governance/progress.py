from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import dump_yaml, load_yaml


STATUS_LABELS = {
    "pending": "未执行",
    "in_progress": "执行中",
    "done": "已执行",
    "blocked": "已阻塞",
}

STATUS_COLORS = {
    "pending": "\033[90m",
    "in_progress": "\033[33m",
    "done": "\033[32m",
    "blocked": "\033[31m",
}

RESET = "\033[0m"


@dataclass
class ProgressTracker:
    workflow_modules: dict[str, Any]

    def initialize(self, run_dir: Path, modules: list[str], current: str | None = None) -> dict[str, Any]:
        now = _now()
        steps = []
        for module in modules:
            status = "in_progress" if module == current else "pending"
            steps.append({
                "id": module,
                "name": self._module_name(module),
                "status": status,
                "started_at": now if status == "in_progress" else "",
                "completed_at": "",
                "duration_seconds": None,
                "agent": "",
                "artifacts": [],
                "notes": [],
            })
        data = {
            "version": 1,
            "updated_at": now,
            "steps": steps,
        }
        self._save(run_dir, data)
        return data

    def mark_done(
        self,
        run_dir: Path,
        module: str,
        *,
        artifacts: list[str] | None = None,
        agent: str | None = None,
        notes: list[str] | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        data = self._load(run_dir)
        now = _now()
        found = False
        for step in data.get("steps", []):
            if step.get("id") != module:
                continue
            found = True
            if not step.get("started_at"):
                step["started_at"] = now
            step["status"] = "done"
            step["completed_at"] = now
            step["duration_seconds"] = _duration_seconds(step.get("started_at"), now)
            self._merge_metadata(step, artifacts=artifacts, agent=agent, notes=notes)
        if not found and strict:
            raise ValueError(f"workflow module is not in progress tracker: {module}")
        data["updated_at"] = now
        self._save(run_dir, data)
        return data

    def mark_current(
        self,
        run_dir: Path,
        module: str,
        *,
        agent: str | None = None,
        notes: list[str] | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        data = self._load(run_dir)
        now = _now()
        found = False
        for step in data.get("steps", []):
            if step.get("status") == "in_progress" and step.get("id") != module:
                step["status"] = "pending"
                step["started_at"] = ""
            if step.get("id") == module and step.get("status") != "done":
                found = True
                step["status"] = "in_progress"
                step["started_at"] = step.get("started_at") or now
                self._merge_metadata(step, agent=agent, notes=notes)
            elif step.get("id") == module:
                found = True
                self._merge_metadata(step, agent=agent, notes=notes)
        if not found and strict:
            raise ValueError(f"workflow module is not in progress tracker: {module}")
        data["updated_at"] = now
        self._save(run_dir, data)
        return data

    def mark_blocked(
        self,
        run_dir: Path,
        module: str,
        *,
        artifacts: list[str] | None = None,
        agent: str | None = None,
        notes: list[str] | None = None,
        strict: bool = True,
    ) -> dict[str, Any]:
        data = self._load(run_dir)
        now = _now()
        found = False
        for step in data.get("steps", []):
            if step.get("id") != module:
                continue
            found = True
            step["status"] = "blocked"
            step["completed_at"] = ""
            step["duration_seconds"] = None
            self._merge_metadata(step, artifacts=artifacts, agent=agent, notes=notes)
        if not found and strict:
            raise ValueError(f"workflow module is not in progress tracker: {module}")
        data["updated_at"] = now
        self._save(run_dir, data)
        return data

    def render(self, run_dir: Path, color: bool = True) -> str:
        data = self._load(run_dir)
        lines = ["流程状态栏:"]
        for index, step in enumerate(data.get("steps", []), start=1):
            status = step.get("status", "pending")
            duration = step.get("duration_seconds")
            duration_text = f"{duration:.1f}s" if isinstance(duration, (int, float)) else "-"
            text = f"  {index}. [{STATUS_LABELS.get(status, status)}] {step.get('name', step.get('id'))} ({step.get('id')}) 用时: {duration_text}"
            agent = step.get("agent")
            artifacts = step.get("artifacts") or []
            if agent:
                text += f" 执行者: {agent}"
            if artifacts:
                text += " 产物: " + ", ".join(str(item) for item in artifacts)
            if color:
                text = f"{STATUS_COLORS.get(status, '')}{text}{RESET}"
            lines.append(text)
        return "\n".join(lines) + "\n"

    def _module_name(self, module: str) -> str:
        return self.workflow_modules.get("modules", {}).get(module, {}).get("description", module)

    def _load(self, run_dir: Path) -> dict[str, Any]:
        path = run_dir / "progress.yaml"
        if path.exists():
            return load_yaml(path)
        workflow_path = run_dir / "workflow-recommendation.yaml"
        if workflow_path.exists():
            workflow = load_yaml(workflow_path)
            modules = workflow.get("workflow_recommendation", {}).get("required_modules", [])
            return self.initialize(run_dir, modules)
        return {"version": 1, "updated_at": _now(), "steps": []}

    def _save(self, run_dir: Path, data: dict[str, Any]) -> None:
        dump_yaml(run_dir / "progress.yaml", data)

    def _merge_metadata(
        self,
        step: dict[str, Any],
        *,
        artifacts: list[str] | None = None,
        agent: str | None = None,
        notes: list[str] | None = None,
    ) -> None:
        if agent:
            step["agent"] = agent
        if artifacts:
            existing = list(step.get("artifacts") or [])
            for artifact in artifacts:
                if artifact and artifact not in existing:
                    existing.append(artifact)
            step["artifacts"] = existing
        if notes:
            existing_notes = list(step.get("notes") or [])
            for note in notes:
                if note:
                    existing_notes.append(note)
            step["notes"] = existing_notes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(started_at: str | None, completed_at: str) -> float:
    if not started_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
    except ValueError:
        return 0.0
    return round(max(0.0, (end - start).total_seconds()), 3)
