"""Deterministic code-signal scanner (logic-grounded domain floor).

Risk should come from what the located code actually does, not from words in the
request. This scanner reads the localized files and detects hard, reproducible
*behavior* signals that the keyword dictionary misses — money arithmetic,
device-protocol calls, route/auth decorators, message publish/consume — and maps
them to strong domain evidence that hard guardrails can fire on. A `round()` on a
money field or a `power_off()` call is caught every run regardless of wording.

Scope is deliberately narrow:

- It emits *domains only*. Destructive operations (delete/truncate) keep their
  existing relation-aware grading in ``_operation_findings``; treating any
  incidental ``delete from`` string as a requested operation would over-escalate.
- Callers must skip it for display-text-only changes: coarse localization can
  pull an unrelated file into scope, and scoring its behavior would over-escalate
  a copy edit.

The host model refines semantics on top and may only add domains, never remove
this evidence.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# kind -> (line regex, domains). Only behavior signals not already covered by the
# keyword dictionary; operations and change types are intentionally not emitted.
_LINE_RULES: list[tuple[str, re.Pattern[str], list[str]]] = [
    ("route_definition", re.compile(r"@(app|router|blueprint)\.(get|post|put|delete|patch)\b|@(Get|Post|Put|Delete|Patch|Request)Mapping\b|@RestController\b|@app\.route\b"), ["public-interface"]),
    ("auth_guard", re.compile(r"\b(login_required|permission_required|IsAuthenticated|PreAuthorize|roles_required|check_permission|has_perm)\b"), ["authentication", "authorization"]),
    ("device_protocol", re.compile(r"\b(power_off|power_on|send_command|device_protocol|stop_charging|start_charging|actuator|firmware)\b"), ["physical-device-control"]),
    ("message_pub_sub", re.compile(r"\bKafkaListener\b|\.(publish|emit)\(|producer\.send\b|consumer\.(poll|subscribe)\b"), ["message-contract"]),
]

_MONEY_TOKEN = re.compile(r"\b(amount|price|fee|refund|balance|payment|settlement|reconciliation)\b|金额|退款|费用|价格|结算|对账|计费", re.IGNORECASE)
# Precise rounding/decimal markers only. A bare [*/%] matched any division or a
# comment slash anywhere in a file that merely mentioned "price", which flagged
# unrelated files as money code.
_MONEY_ARITH = re.compile(r"round\(|Decimal\(|\.quantize\(")

MAX_FILE_BYTES = 400_000
MAX_SIGNALS = 200

# Behavior signals only mean something in source code. Prose and fixtures mention
# `power_off` or `round()` without doing them: scanning a .md or a test-fixture
# .yaml produced false money/device domains on this tool's own repository.
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".java", ".kt", ".scala", ".go",
    ".rb", ".php", ".cs", ".rs", ".swift", ".m", ".mm", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".sql", ".sh", ".bash",
}


def is_code_file(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


def scan_code_signals(root: Path, direct_files: list[str]) -> dict[str, Any]:
    """Return behavior signals and derived strong domain evidence."""
    signals: list[dict[str, Any]] = []
    for path in direct_files:
        if not is_code_file(path):
            continue
        text = _read(root / path)
        if not text:
            continue
        lines = text.splitlines()
        for number, line in enumerate(lines, start=1):
            for kind, pattern, domains in _LINE_RULES:
                if pattern.search(line):
                    signals.append(_signal(kind, path, number, line, domains))
                    break
            if len(signals) >= MAX_SIGNALS:
                break
        money = _money_signal(path, lines)
        if money:
            signals.append(money)
        if len(signals) >= MAX_SIGNALS:
            break

    domain_set: set[str] = set()
    domain_evidence: list[dict[str, str]] = []
    for signal in signals:
        for value in signal["domains"]:
            domain_set.add(value)
            domain_evidence.append(_evidence(value, signal))

    return {
        "signals": signals,
        "domains": sorted(domain_set),
        "domain_evidence": domain_evidence,
    }


def _signal(kind: str, path: str, line: int, text: str, domains: list[str]) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "line": line,
        "snippet": text.strip()[:160],
        "strength": "strong",
        "domains": domains,
    }


def _money_signal(path: str, lines: list[str]) -> dict[str, Any] | None:
    token_line = 0
    has_token = False
    has_arith = False
    for number, line in enumerate(lines, start=1):
        if _MONEY_TOKEN.search(line):
            has_token = True
            if not token_line:
                token_line = number
        if _MONEY_ARITH.search(line):
            has_arith = True
    if has_token and has_arith:
        snippet = lines[token_line - 1] if token_line else ""
        return _signal("money_arithmetic", path, token_line or 1, snippet, ["financial-calculation"])
    return None


def _evidence(value: str, signal: dict[str, Any]) -> dict[str, str]:
    return {
        "value": value,
        "source": "code_signal",
        "path": signal["path"],
        "keyword": signal["kind"],
        "strength": "strong",
        "fact": f"FACT: {signal['path']}:{signal['line']} has code signal '{signal['kind']}' -> affected_domain {value}.",
    }


def _read(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
