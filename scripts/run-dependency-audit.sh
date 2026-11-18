#!/usr/bin/env bash
# run-dependency-audit.sh: human-run report path for the dependency audit (ADR-003).
# Runs the four allowlisted npm commands, saves their output in OUT_DIR, and passes
# it to scripts/audit_dependencies_deterministic.py.
# This runs outside the dependency-auditor agent, so the agent's Bash hook does not
# apply and the agent's tools and permissions are unchanged. Run it as a human, not
# from the Orchestrator session, whose Bash is not granted by the policy (D14).
# Real results need registry access and each manifest's package-lock.json.
# Usage: bash scripts/run-dependency-audit.sh [OUT_DIR]   (default: a new mktemp -d)
set -uo pipefail

OUT="${1:-$(mktemp -d)}"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)" || exit 1

# Run from the repository root, not the caller's cwd.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# npm exits non-zero when it finds issues, so its exit status is ignored and the
# deterministic script decides from the saved content. Real failures still become
# errors: missing npm leaves empty files, a crash leaves empty or non-JSON output,
# and npm error objects (e.g. ENOLOCK) are parsed as errors, never as zero counts.
if command -v npm >/dev/null 2>&1; then
  npm audit --json --prefix tracker-api > "$OUT/audit-api.json" 2> "$OUT/audit-api.stderr"
  npm audit --json --prefix tracker-client > "$OUT/audit-client.json" 2> "$OUT/audit-client.stderr"
  npm outdated --json --prefix tracker-api > "$OUT/outdated-api.json" 2> "$OUT/outdated-api.stderr"
  npm outdated --json --prefix tracker-client > "$OUT/outdated-client.json" 2> "$OUT/outdated-client.stderr"
else
  echo "run-dependency-audit.sh: npm not found on PATH; all inputs are left empty and will be reported as errors." >&2
  for name in audit-api audit-client outdated-api outdated-client; do
    : > "$OUT/$name.json"
  done
fi

LOCKS=()
[ -f tracker-api/package-lock.json ] && LOCKS+=( --lock-api tracker-api/package-lock.json )
[ -f tracker-client/package-lock.json ] && LOCKS+=( --lock-client tracker-client/package-lock.json )

python3 scripts/audit_dependencies_deterministic.py \
  --audit-api "$OUT/audit-api.json" --audit-client "$OUT/audit-client.json" \
  --outdated-api "$OUT/outdated-api.json" --outdated-client "$OUT/outdated-client.json" \
  ${LOCKS[@]+"${LOCKS[@]}"} \
  --output "$OUT/dependency-audit-report.json" --report "$OUT/dependency-audit-report.md"
STATUS=$?

echo "JSON report: $OUT/dependency-audit-report.json"
echo "Markdown report: $OUT/dependency-audit-report.md"
exit "$STATUS"
