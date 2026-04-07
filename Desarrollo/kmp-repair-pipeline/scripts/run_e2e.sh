#!/usr/bin/env bash
# =============================================================================
# run_e2e.sh — Full end-to-end pipeline run for one repair case.
#
# Usage:
#   ./scripts/run_e2e.sh <case_id>            # normal run
#   ./scripts/run_e2e.sh <case_id> --fresh    # soft-reset execution evidence first
#
# What this does (all 13 phases):
#   Phase 6  run-before-after  — Gradle before/after execution
#   Phase 7  analyze-case      — KMP structural analysis
#   Phase 8  localize          — Impact localization + LLM re-ranking
#   Phase 9  repair            — All 4 baseline modes (raw_error, context_rich,
#                                iterative_agentic, full_thesis) with in-loop
#                                validation after each applied patch
#   Phase 10 validate          — Final multi-target validation (run by repair loop)
#   Phase 11 explain           — ExplanationAgent structured + Markdown output
#   Phase 12 metrics           — BSR / CTSR / FFSR / EFR / Hit@k
#   Phase 13 report            — CSV / JSON / Markdown report across all cases
#
# Prerequisites:
#   - Docker Compose Postgres running  (docker compose up -d postgres)
#   - .env file at project root        (sets JAVA_HOME, GCP creds, DB URL, etc.)
#   - Case already built               (kmp-repair build-case <case_id>)
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <case_id> [--fresh]" >&2
  exit 1
fi

CASE_ID="$1"
FRESH_FLAG=""
if [[ "${2:-}" == "--fresh" ]]; then
  FRESH_FLAG="--fresh"
fi

# ---------------------------------------------------------------------------
# Environment bootstrap
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT"

# Resolve the Python interpreter that kmp-repair uses.
# The kmp-repair entrypoint shebang tells us exactly which python3 has the
# kmp_repair_pipeline package installed (homebrew python, not /usr/bin/python3).
KMP_PYTHON="$(head -1 "$(command -v kmp-repair)" 2>/dev/null | sed 's|^#!||')"
if [[ -z "$KMP_PYTHON" ]] || ! "$KMP_PYTHON" -c "import kmp_repair_pipeline" &>/dev/null; then
  # Fallback: check common homebrew python paths
  for candidate in \
      /opt/homebrew/opt/python@3.12/bin/python3.12 \
      /opt/homebrew/bin/python3.12 \
      python3 python; do
    if command -v "$candidate" &>/dev/null && \
       "$candidate" -c "import kmp_repair_pipeline" &>/dev/null 2>&1; then
      KMP_PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$KMP_PYTHON" ]]; then
  echo "ERROR: Could not find a Python with kmp_repair_pipeline installed." >&2
  echo "       Run: pip install -e . from $PROJECT_ROOT" >&2
  exit 1
fi

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  export KMP_LLM_PROVIDER KMP_LLM_MODEL KMP_VERTEX_PROJECT KMP_VERTEX_LOCATION \
         GOOGLE_APPLICATION_CREDENTIALS JAVA_HOME KMP_DATABASE_URL
else
  echo "ERROR: .env not found at $PROJECT_ROOT/.env" >&2
  exit 1
fi

# Verify Java
if [[ -z "${JAVA_HOME:-}" ]]; then
  echo "ERROR: JAVA_HOME is not set. Source .env first." >&2
  exit 1
fi
echo "Using Java: $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
step() {
  local name="$1"; shift
  echo ""
  echo "================================================================"
  echo "  STEP: $name"
  echo "================================================================"
}

check_status() {
  local case_id="$1"
  # Run a quick status check via python. Suppress all log noise (logger writes
  # to stdout via rich), take only the last printed line. Fall back to "UNKNOWN"
  # if python fails (DB unreachable, module not found, etc.).
  "$KMP_PYTHON" -c "
import sys
try:
    from kmp_repair_pipeline.storage.db import get_session
    from kmp_repair_pipeline.storage.models import RepairCase
    with get_session() as s:
        c = s.get(RepairCase, '$case_id')
        print(c.status if c else 'UNKNOWN (case not found)')
except Exception as e:
    print(f'UNKNOWN ({e})', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null | tail -1 || echo "UNKNOWN"
}

# ---------------------------------------------------------------------------
# Verify case exists and is in the right state
# ---------------------------------------------------------------------------
STATUS="$(check_status "$CASE_ID")"
echo "Case $CASE_ID — initial status: $STATUS"

if [[ "$STATUS" == "UNKNOWN"* ]]; then
  echo "ERROR: Case $CASE_ID not found in DB. Run 'kmp-repair build-case' first." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 6 — run-before-after
# ---------------------------------------------------------------------------
step "Phase 6 — run-before-after $FRESH_FLAG"
kmp-repair run-before-after "$CASE_ID" $FRESH_FLAG

STATUS="$(check_status "$CASE_ID")"
echo "Status after Phase 6: $STATUS"
if [[ "$STATUS" == "NO_ERRORS_TO_FIX" ]]; then
  echo ""
  echo ">>> Non-breaking update: after-state compiled with 0 errors."
  echo ">>> Skipping repair/validate — jumping directly to metrics."
  step "Phase 12 — metrics (no-op case)"
  kmp-repair metrics "$CASE_ID"
  step "Phase 13 — report"
  kmp-repair report --format all
  echo ""
  echo "================================================================"
  echo "  DONE (no-op case) — $CASE_ID"
  echo "================================================================"
  exit 0
fi

if [[ "$STATUS" != "EXECUTED" ]]; then
  echo "ERROR: Expected EXECUTED after Phase 6, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 7 — analyze-case
# ---------------------------------------------------------------------------
step "Phase 7 — analyze-case"
kmp-repair analyze-case "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "ANALYZED" ]]; then
  echo "ERROR: Expected ANALYZED after Phase 7, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 8 — localize
# ---------------------------------------------------------------------------
step "Phase 8 — localize"
kmp-repair localize "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "LOCALIZED" ]]; then
  echo "ERROR: Expected LOCALIZED after Phase 8, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 9 + 10 — repair --all-baselines (includes in-loop validate)
# ---------------------------------------------------------------------------
step "Phase 9+10 — repair --all-baselines (includes in-loop validation)"
kmp-repair repair "$CASE_ID" --all-baselines

STATUS="$(check_status "$CASE_ID")"
echo "Status after repair: $STATUS"
# VALIDATED is expected when at least one baseline validated successfully.
# PATCH_ATTEMPTED means all baselines exhausted without full validation — continue to explain.
if [[ "$STATUS" != "VALIDATED" && "$STATUS" != "PATCH_ATTEMPTED" ]]; then
  echo "ERROR: Unexpected status after repair: $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 11 — explain
# ---------------------------------------------------------------------------
step "Phase 11 — explain"
kmp-repair explain "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "EXPLAINED" ]]; then
  echo "ERROR: Expected EXPLAINED after Phase 11, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 12 — metrics
# ---------------------------------------------------------------------------
step "Phase 12 — metrics"
kmp-repair metrics "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "EVALUATED" ]]; then
  echo "ERROR: Expected EVALUATED after Phase 12, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Phase 13 — report
# ---------------------------------------------------------------------------
step "Phase 13 — report (all cases)"
kmp-repair report --format all

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo "  DONE — $CASE_ID  (status: $STATUS)"
echo "  Reports: $PROJECT_ROOT/data/reports/"
echo "================================================================"
