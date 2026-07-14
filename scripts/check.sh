#!/usr/bin/env bash
# Local pre-commit check: same gates as CI (.github/workflows/ci.yml).
# Runs the static type check first, then the test suite (which includes the
# plugin sync/drift check). Fails fast so type regressions surface locally
# instead of only on CI.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

echo "==> mypy"
"$PYTHON" -m mypy lib/adaptive_change_governance plugins/adaptive-change-governance/hooks/implementation_gate.py

echo "==> tests"
"$PYTHON" test/test_phase1.py

echo "all checks passed"
