#!/usr/bin/env bash
# Sync the canonical runtime (root lib/bin/config) into the plugin package.
# The root tree is the single source of truth; run this after changing
# lib/, bin/, or .ai-governance/*.yaml, then commit both copies together.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN="$ROOT/plugins/adaptive-change-governance"

rsync -a --delete --exclude '__pycache__' "$ROOT/lib/" "$PLUGIN/lib/"
rsync -a "$ROOT/bin/change-assess" "$PLUGIN/bin/change-assess"

for name in assessment-schema workflow-modules artifact-schemas project-risk guardrails risk-calibration risk-scenarios; do
  rsync -a "$ROOT/.ai-governance/$name.yaml" "$PLUGIN/.ai-governance/$name.yaml"
done

rsync -a --delete "$ROOT/.ai-governance/profiles/" "$PLUGIN/.ai-governance/profiles/"
rsync -a --delete "$ROOT/.ai-governance/templates/" "$PLUGIN/.ai-governance/templates/"

# Bytecode is a local build artifact, and running the tests or the hook recreates it
# inside the package. rsync's --exclude also shields it from --delete, so clear it
# explicitly and leave a clean package behind.
find "$PLUGIN" -type d -name '__pycache__' -prune -exec rm -rf {} +

echo "plugin runtime synced from root tree"
