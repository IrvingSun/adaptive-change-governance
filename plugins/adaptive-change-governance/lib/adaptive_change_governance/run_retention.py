from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class CleanupResult:
    output_root: Path
    retain_latest: int
    retain_days: int
    dry_run: bool
    kept: list[Path]
    deleted: list[Path]
    skipped: list[Path]


def cleanup_runs(output_root: Path, policy: dict[str, Any], dry_run: bool = False) -> CleanupResult:
    retain_latest = _positive_int(policy.get("retain_latest"), 20)
    retain_days = _positive_int(policy.get("retain_days"), 30)
    runs = _run_dirs(output_root)
    newest_kept = set(runs[:retain_latest])
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    kept: list[Path] = []
    deleted: list[Path] = []
    skipped: list[Path] = []

    for run_dir in runs:
        if run_dir in newest_kept:
            kept.append(run_dir)
            continue
        if _is_active_run(run_dir):
            skipped.append(run_dir)
            continue
        if _mtime(run_dir) >= cutoff:
            kept.append(run_dir)
            continue
        deleted.append(run_dir)
        if not dry_run:
            shutil.rmtree(run_dir)

    return CleanupResult(
        output_root=output_root,
        retain_latest=retain_latest,
        retain_days=retain_days,
        dry_run=dry_run,
        kept=kept,
        deleted=deleted,
        skipped=skipped,
    )


def render_cleanup_summary(result: CleanupResult) -> str:
    action = "Would delete" if result.dry_run else "Deleted"
    lines = [
        "Run cleanup",
        f"Output root: {result.output_root}",
        f"Policy: retain_latest={result.retain_latest}, retain_days={result.retain_days}",
        f"Kept: {len(result.kept)}",
        f"Skipped active: {len(result.skipped)}",
        f"{action}: {len(result.deleted)}",
    ]
    for path in result.deleted[:20]:
        lines.append(f"  - {path.name}")
    if len(result.deleted) > 20:
        lines.append(f"  - ... {len(result.deleted) - 20} more")
    return "\n".join(lines) + "\n"


def _run_dirs(output_root: Path) -> list[Path]:
    if not output_root.exists():
        return []
    runs = [path for path in output_root.iterdir() if path.is_dir()]
    return sorted(runs, key=_mtime, reverse=True)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _is_active_run(run_dir: Path) -> bool:
    if (run_dir / ".workflow-approved").exists():
        return False
    return (run_dir / "workflow-plan.md").exists() and (run_dir / "human-review.yaml").exists()


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default
