#!/usr/bin/env bash
# =============================================================================
# run_e2e.sh — Full end-to-end pipeline run for one repair case.
#
# Usage:
#   ./scripts/run_e2e.sh <case_id>                        # human console (default)
#   ./scripts/run_e2e.sh <case_id> --fresh                # human console + fresh reset
#   ./scripts/run_e2e.sh <case_id> --fresh --verbose      # full technical logs
#
# What this does:
#   run-before-after  — Gradle before/after execution
#   analyze-case      — KMP structural analysis
#   localize          — Impact localization + LLM re-ranking
#   repair            — All 4 baseline modes (raw_error, context_rich,
#                       iterative_agentic, full_thesis) with in-loop
#                       validation after each applied patch
#   explain           — ExplanationAgent structured + Markdown output
#   metrics           — BSR / CTSR / FFSR / EFR / Hit@k
#   report            — CSV / JSON / Markdown report across all cases
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

if [[ -t 1 ]]; then
  C_RESET="$(printf '\033[0m')"
  C_BOLD="$(printf '\033[1m')"
  C_DIM="$(printf '\033[2m')"
  C_RED="$(printf '\033[31m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_BLUE="$(printf '\033[34m')"
  C_CYAN="$(printf '\033[36m')"
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_RED=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_CYAN=""
fi

PHASE_PASS_COUNT=0
PHASE_FAIL_COUNT=0
PHASE_SKIP_COUNT=0
PHASE_EVENT_COUNT=0
PHASE_CURRENT_SCOPE=""
PHASE_CURRENT_MODE=""
PHASE_CURRENT_MODEL=""
PHASE_MODEL_ANNOUNCED=0
PHASE_BEFORE_PASS=0
PHASE_BEFORE_FAIL=0
PHASE_AFTER_PASS=0
PHASE_AFTER_FAIL=0
PHASE_EXPECT_RUN_CONTEXT=0
PHASE_PENDING_RUN_TASK=""
PHASE_PENDING_RUN_CWD=""
PHASE_EXPECT_FINISH=0
PHASE_PENDING_FINISH_TASK=""
PHASE_PENDING_FINISH_STATUS=""
PHASE_PENDING_FINISH_DURATION=""
PHASE_PENDING_FINISH_ERRORS=""
PHASE_PENDING_FINISH_TTL=0
PHASE_EXPECT_VALIDATION=0
PHASE_PENDING_PATCH_STATUS=""
PHASE_PENDING_OVERALL=""
PHASE_PENDING_VALIDATION_TTL=0
PHASE_EXPECT_EXPLAIN=0
PHASE_PENDING_EXPLAIN_JSON=""
PHASE_PENDING_EXPLAIN_MD=""
PHASE_PENDING_EXPLAIN_TOKENS=""
PHASE_PENDING_EXPLAIN_TTL=0
PHASE_LAST_AGENT_LATENCY=""
PHASE_EXPECT_REPAIR_DONE=0
PHASE_PENDING_REPAIR_MODE=""
PHASE_PENDING_REPAIR_ATTEMPT=""
PHASE_PENDING_REPAIR_STATUS=""
PHASE_PENDING_REPAIR_TTL=0
PHASE_LAST_APPLIED_MODE=""
PHASE_LAST_APPLIED_ATTEMPT=""

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <case_id> [--fresh] [--verbose]" >&2
  exit 1
fi

CASE_ID=""
FRESH_FLAG=""
CONSOLE_MODE="human"

for arg in "$@"; do
  case "$arg" in
    --fresh)
      FRESH_FLAG="--fresh"
      ;;
    --verbose)
      CONSOLE_MODE="verbose"
      ;;
    --human)
      CONSOLE_MODE="human"
      ;;
    -h|--help)
      echo "Usage: $0 <case_id> [--fresh] [--verbose]"
      exit 0
      ;;
    -* )
      echo "ERROR: Unknown flag: $arg" >&2
      echo "Usage: $0 <case_id> [--fresh] [--verbose]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$CASE_ID" ]]; then
        CASE_ID="$arg"
      else
        echo "ERROR: Unexpected extra argument: $arg" >&2
        echo "Usage: $0 <case_id> [--fresh] [--verbose]" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$CASE_ID" ]]; then
  echo "ERROR: Missing <case_id>." >&2
  echo "Usage: $0 <case_id> [--fresh] [--verbose]" >&2
  exit 1
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

# Console UX mode:
#   human   -> reduce logger noise (warnings/errors + explicit summaries)
#   verbose -> full technical logs
if [[ "$CONSOLE_MODE" == "human" ]]; then
  export KMP_LOG_LEVEL="${KMP_LOG_LEVEL:-WARNING}"
else
  export KMP_LOG_LEVEL="${KMP_LOG_LEVEL:-INFO}"
fi

RUN_TS="$(date +%Y%m%d-%H%M%S)"
RUN_LOG_DIR="$PROJECT_ROOT/data/reports/console-runs/${CASE_ID}-${RUN_TS}"
mkdir -p "$RUN_LOG_DIR"

echo "Console mode: $CONSOLE_MODE (KMP_LOG_LEVEL=$KMP_LOG_LEVEL)"
echo "Detailed phase logs: $RUN_LOG_DIR"

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

phase_uses_build_counters() {
  local phase_key="$1"
  [[ "$phase_key" == "run_before_after" || "$phase_key" == "repair" ]]
}

fetch_patch_files_for_attempt() {
  local case_id="$1"
  local mode="$2"
  local attempt="$3"

  "$KMP_PYTHON" - <<PY 2>/dev/null || true
from sqlalchemy import select

from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.models import PatchAttempt

case_id = "$case_id"
mode = "$mode"
attempt = int("$attempt")

with get_session() as s:
  row = s.scalar(
    select(PatchAttempt).where(
      PatchAttempt.repair_case_id == case_id,
      PatchAttempt.repair_mode == mode,
      PatchAttempt.attempt_number == attempt,
    )
  )
  if not row:
    print("")
  else:
    files = list(row.touched_files or [])
    print("|".join(files))
PY
}

fetch_latest_patch_details_for_mode() {
  local case_id="$1"
  local mode="$2"

  "$KMP_PYTHON" - <<PY 2>/dev/null || true
from sqlalchemy import select

from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.models import PatchAttempt

case_id = "$case_id"
mode = "$mode"

with get_session() as s:
  row = s.scalars(
    select(PatchAttempt)
    .where(
      PatchAttempt.repair_case_id == case_id,
      PatchAttempt.repair_mode == mode,
    )
    .order_by(PatchAttempt.attempt_number.desc())
  ).first()

  if not row:
    print("")
  else:
    files = list(row.touched_files or [])
    print("|".join([str(row.attempt_number), *files]))
PY
}

print_explain_summary() {
  local case_id="$1"
  "$KMP_PYTHON" - <<PY 2>/dev/null || true
import json
from pathlib import Path

from sqlalchemy import select

from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.models import Explanation

case_id = "$case_id"

def _trim(text: str, size: int = 180) -> str:
  text = " ".join((text or "").split())
  if len(text) <= size:
    return text
  return text[: size - 3].rstrip() + "..."

with get_session() as s:
  exp = s.scalars(
    select(Explanation)
    .where(Explanation.repair_case_id == case_id)
    .order_by(Explanation.created_at.desc())
  ).first()

if not exp or not exp.json_path:
  print("Explain summary: no disponible")
  raise SystemExit(0)

path = Path(exp.json_path)
if not path.exists():
  print(f"Explain summary: archivo no encontrado ({path})")
  raise SystemExit(0)

data = json.loads(path.read_text(encoding="utf-8"))

what = _trim(str(data.get("what_was_updated", "")))
why = _trim(str(data.get("patch_rationale", "")))
validation = _trim(str(data.get("validation_summary", "")))

uncertainty = ""
uncertainties = data.get("uncertainties") or []
if uncertainties:
  first = uncertainties[0]
  if isinstance(first, dict):
    uncertainty = _trim(str(first.get("description", "")))
  else:
    uncertainty = _trim(str(first))

print("Explain summary:")
if what:
  print(f"  - Cambio principal: {what}")
if why:
  print(f"  - Racional del parche: {why}")
if validation:
  print(f"  - Resultado validación: {validation}")
if uncertainty:
  print(f"  - Incertidumbre reportada: {uncertainty}")
print(f"  - JSON: {path}")
PY
}

render_phase_event() {
  local phase_key="$1"
  local line="$2"
  local stripped line_starts_new_run

  line="${line//$'\r'/}"
  stripped="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  [[ -z "${stripped//[[:space:]]/}" ]] && return 1

  # Wrapped "Running:" blocks arrive over multiple lines.
  if [[ "$stripped" == *"Running:"* ]]; then
    PHASE_EXPECT_RUN_CONTEXT=1
    PHASE_PENDING_RUN_TASK=""
    PHASE_PENDING_RUN_CWD=""
  fi

  if [[ "$stripped" =~ Running:.*[[:space:]]([[:alnum:]_:.-]+)[[:space:]]\(cwd=([^\)]+)\) ]]; then
    PHASE_PENDING_RUN_TASK="${BASH_REMATCH[1]}"
    PHASE_EXPECT_RUN_CONTEXT=1
    PHASE_PENDING_RUN_CWD="${BASH_REMATCH[2]}"
    stripped=")"
  fi

  if [[ $PHASE_EXPECT_RUN_CONTEXT -eq 1 ]]; then
    if [[ "$stripped" =~ task=([[:alnum:]_:.-]+) ]]; then
      PHASE_PENDING_RUN_TASK="${BASH_REMATCH[1]}"
    fi

    if [[ "$stripped" =~ --continue[[:space:]]+([[:alnum:]_:.-]+)$ ]]; then
      PHASE_PENDING_RUN_TASK="${BASH_REMATCH[1]}"
    fi

    if [[ "$stripped" =~ ^[[:alnum:]_:.-]+$ ]]; then
      PHASE_PENDING_RUN_TASK="$stripped"
    elif [[ "$stripped" =~ [[:space:]]([[:alnum:]_:.-]+)$ ]]; then
      candidate_task="${BASH_REMATCH[1]}"
      if [[ "$candidate_task" != "Running:" && "$candidate_task" != "INFO" && "$candidate_task" != "WARNING" && "$candidate_task" != "ERROR" ]]; then
        PHASE_PENDING_RUN_TASK="$candidate_task"
      fi
    fi

    if [[ "$stripped" == *"(cwd="* ]]; then
      PHASE_PENDING_RUN_CWD="${stripped#*(cwd=}"
    elif [[ -n "$PHASE_PENDING_RUN_CWD" ]]; then
      PHASE_PENDING_RUN_CWD+="$stripped"
    fi

    if [[ -n "$PHASE_PENDING_RUN_CWD" && "$PHASE_PENDING_RUN_CWD" == *")"* ]]; then
      cwd="${PHASE_PENDING_RUN_CWD%%)*}"
      task="${PHASE_PENDING_RUN_TASK:-task}"

      if [[ "$cwd" == *"/workspace/before"* ]]; then
        if [[ "$PHASE_CURRENT_SCOPE" != "before" ]]; then
          PHASE_CURRENT_SCOPE="before"
          printf "  %b[REVISION]%b BEFORE\n" "$C_BOLD" "$C_RESET"
        fi
      elif [[ "$cwd" == *"/workspace/after"* ]]; then
        if [[ "$PHASE_CURRENT_SCOPE" != "after" ]]; then
          PHASE_CURRENT_SCOPE="after"
          printf "  %b[REVISION]%b AFTER\n" "$C_BOLD" "$C_RESET"
        fi
      fi

      PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
      if [[ -n "$PHASE_CURRENT_MODE" && "$phase_key" == "repair" ]]; then
        printf "    %b[TAREA ]%b [%s] %s\n" "$C_CYAN" "$C_RESET" "$PHASE_CURRENT_MODE" "$task"
      elif [[ -n "$PHASE_CURRENT_SCOPE" ]]; then
        printf "    %b[TAREA ]%b [%s] %s\n" "$C_CYAN" "$C_RESET" "$PHASE_CURRENT_SCOPE" "$task"
      else
        printf "  %b[TAREA ]%b %s\n" "$C_CYAN" "$C_RESET" "$task"
      fi

      PHASE_EXPECT_RUN_CONTEXT=0
      PHASE_PENDING_RUN_TASK=""
      PHASE_PENDING_RUN_CWD=""
      return 0
    fi
  fi

  # Wrapped "Task ... finished" blocks also span several lines.
  if [[ "$stripped" =~ Task[[:space:]]+([^[:space:]]+)[[:space:]]+finished: ]]; then
    PHASE_EXPECT_FINISH=1
    PHASE_PENDING_FINISH_TASK="${BASH_REMATCH[1]}"
    PHASE_PENDING_FINISH_STATUS=""
    PHASE_PENDING_FINISH_DURATION=""
    PHASE_PENDING_FINISH_ERRORS=""
    PHASE_PENDING_FINISH_TTL=6
  fi

  if [[ $PHASE_EXPECT_FINISH -eq 1 ]]; then
    [[ "$stripped" =~ status=([A-Z_]+) ]] && PHASE_PENDING_FINISH_STATUS="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ duration=([0-9.]+)s ]] && PHASE_PENDING_FINISH_DURATION="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ errors=([0-9]+) ]] && PHASE_PENDING_FINISH_ERRORS="${BASH_REMATCH[1]}"

    line_starts_new_run=0
    [[ "$stripped" == *"Running:"* ]] && line_starts_new_run=1

    PHASE_PENDING_FINISH_TTL=$((PHASE_PENDING_FINISH_TTL - 1))
    if [[ -n "$PHASE_PENDING_FINISH_STATUS" && ( -n "$PHASE_PENDING_FINISH_DURATION" || $PHASE_PENDING_FINISH_TTL -le 0 || $line_starts_new_run -eq 1 ) ]]; then
      task="$PHASE_PENDING_FINISH_TASK"
      status="$PHASE_PENDING_FINISH_STATUS"
      duration="${PHASE_PENDING_FINISH_DURATION:-?}"
      errors="${PHASE_PENDING_FINISH_ERRORS:-?}"

      PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
      if [[ "$status" == SUCCESS* ]]; then
        PHASE_PASS_COUNT=$((PHASE_PASS_COUNT + 1))
        if [[ "$PHASE_CURRENT_SCOPE" == "before" ]]; then
          PHASE_BEFORE_PASS=$((PHASE_BEFORE_PASS + 1))
        elif [[ "$PHASE_CURRENT_SCOPE" == "after" ]]; then
          PHASE_AFTER_PASS=$((PHASE_AFTER_PASS + 1))
        fi
        if [[ -n "$PHASE_CURRENT_SCOPE" ]]; then
          printf "    %b[OK   ]%b [%s] %s (%ss)\n" "$C_GREEN" "$C_RESET" "$PHASE_CURRENT_SCOPE" "$task" "$duration"
        elif [[ -n "$PHASE_CURRENT_MODE" && "$phase_key" == "repair" ]]; then
          printf "    %b[OK   ]%b [%s] %s (%ss)\n" "$C_GREEN" "$C_RESET" "$PHASE_CURRENT_MODE" "$task" "$duration"
        else
          printf "  %b[OK   ]%b %s (%ss)\n" "$C_GREEN" "$C_RESET" "$task" "$duration"
        fi
      elif [[ "$status" == FAILED* ]]; then
        PHASE_FAIL_COUNT=$((PHASE_FAIL_COUNT + 1))
        if [[ "$PHASE_CURRENT_SCOPE" == "before" ]]; then
          PHASE_BEFORE_FAIL=$((PHASE_BEFORE_FAIL + 1))
        elif [[ "$PHASE_CURRENT_SCOPE" == "after" ]]; then
          PHASE_AFTER_FAIL=$((PHASE_AFTER_FAIL + 1))
        fi
        if [[ -n "$PHASE_CURRENT_SCOPE" ]]; then
          printf "    %b[FALLO]%b [%s] %s (%ss, errores=%s)\n" "$C_RED" "$C_RESET" "$PHASE_CURRENT_SCOPE" "$task" "$duration" "$errors"
        elif [[ -n "$PHASE_CURRENT_MODE" && "$phase_key" == "repair" ]]; then
          printf "    %b[FALLO]%b [%s] %s (%ss, errores=%s)\n" "$C_RED" "$C_RESET" "$PHASE_CURRENT_MODE" "$task" "$duration" "$errors"
        else
          printf "  %b[FALLO]%b %s (%ss, errores=%s)\n" "$C_RED" "$C_RESET" "$task" "$duration" "$errors"
        fi
      else
        PHASE_SKIP_COUNT=$((PHASE_SKIP_COUNT + 1))
        printf "    %b[SKIP ]%b %s (%s)\n" "$C_YELLOW" "$C_RESET" "$task" "$status"
      fi

      PHASE_EXPECT_FINISH=0
      PHASE_PENDING_FINISH_TASK=""
      PHASE_PENDING_FINISH_STATUS=""
      PHASE_PENDING_FINISH_DURATION=""
      PHASE_PENDING_FINISH_ERRORS=""
      PHASE_PENDING_FINISH_TTL=0

      if [[ $line_starts_new_run -eq 0 ]]; then
        return 0
      fi
    else
      return 0
    fi
  fi

  if [[ "$stripped" =~ Calling[[:space:]]+RepairAgent[[:space:]]+\(model=([^[:space:]]+)[[:space:]]+mode=([^[:space:]]+)[[:space:]]+attempt=([0-9]+)\) ]]; then
    PHASE_CURRENT_MODEL="${BASH_REMATCH[1]}"
    mode="${BASH_REMATCH[2]}"
    attempt="${BASH_REMATCH[3]}"

    if [[ $PHASE_MODEL_ANNOUNCED -eq 0 ]]; then
      PHASE_MODEL_ANNOUNCED=1
      printf "  %b[MODELO]%b RepairAgent=%s\n" "$C_BLUE" "$C_RESET" "$PHASE_CURRENT_MODEL"
    fi

    if [[ "$mode" != "$PHASE_CURRENT_MODE" ]]; then
      PHASE_CURRENT_MODE="$mode"
      printf "  %b[BASELINE]%b %s\n" "$C_BOLD" "$C_RESET" "$PHASE_CURRENT_MODE"
    fi

    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "    %b[INTENTO]%b baseline=%s intento=%s\n" "$C_CYAN" "$C_RESET" "$mode" "$attempt"
    return 0
  fi

  if [[ "$stripped" =~ latency=([0-9.]+)s ]]; then
    PHASE_LAST_AGENT_LATENCY="${BASH_REMATCH[1]}"
  fi

  if [[ "$stripped" == *"repair done:"* ]]; then
    PHASE_EXPECT_REPAIR_DONE=1
    PHASE_PENDING_REPAIR_MODE=""
    PHASE_PENDING_REPAIR_ATTEMPT=""
    PHASE_PENDING_REPAIR_STATUS=""
    PHASE_PENDING_REPAIR_TTL=4
  fi

  if [[ $PHASE_EXPECT_REPAIR_DONE -eq 1 ]]; then
    [[ "$stripped" =~ mode=([a-z_]+) ]] && PHASE_PENDING_REPAIR_MODE="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ attempt=([0-9]+) ]] && PHASE_PENDING_REPAIR_ATTEMPT="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ status=([A-Z_]+) ]] && PHASE_PENDING_REPAIR_STATUS="${BASH_REMATCH[1]}"

    PHASE_PENDING_REPAIR_TTL=$((PHASE_PENDING_REPAIR_TTL - 1))
    if [[ -n "$PHASE_PENDING_REPAIR_MODE" && -n "$PHASE_PENDING_REPAIR_STATUS" && ( -n "$PHASE_PENDING_REPAIR_ATTEMPT" || $PHASE_PENDING_REPAIR_TTL -le 0 ) ]]; then
      if [[ "$PHASE_PENDING_REPAIR_STATUS" == "APPLIED" ]]; then
        PHASE_LAST_APPLIED_MODE="$PHASE_PENDING_REPAIR_MODE"
        PHASE_LAST_APPLIED_ATTEMPT="$PHASE_PENDING_REPAIR_ATTEMPT"
      fi

      PHASE_EXPECT_REPAIR_DONE=0
      PHASE_PENDING_REPAIR_MODE=""
      PHASE_PENDING_REPAIR_ATTEMPT=""
      PHASE_PENDING_REPAIR_STATUS=""
      PHASE_PENDING_REPAIR_TTL=0
      return 0
    fi
  fi

  if [[ "$stripped" =~ validating[[:space:]]+attempt[[:space:]]+#[0-9]+[[:space:]]+\(mode=([^[:space:]]+) ]]; then
    mode="${BASH_REMATCH[1]}"
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "    %b[VALIDAR]%b baseline=%s\n" "$C_CYAN" "$C_RESET" "$mode"
    return 0
  fi

  if [[ "$stripped" =~ attempt[[:space:]]+([0-9]+):[[:space:]]+patch[[:space:]]+failed[[:space:]]+to[[:space:]]+apply ]]; then
    attempt="${BASH_REMATCH[1]}"
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    PHASE_FAIL_COUNT=$((PHASE_FAIL_COUNT + 1))
    printf "    %b[FALLO ]%b no se pudo aplicar parche en intento=%s\n" "$C_RED" "$C_RESET" "$attempt"
    return 0
  fi

  if [[ "$stripped" =~ Baseline[[:space:]]+([a-z_]+):[[:space:]]+patch[[:space:]]+APPLIED[[:space:]]+on[[:space:]]+attempt[[:space:]]+([0-9]+)/([0-9]+) ]]; then
    mode="${BASH_REMATCH[1]}"
    attempt="${BASH_REMATCH[2]}"
    max_attempts="${BASH_REMATCH[3]}"
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "    %b[BASELINE]%b %s aplicó parche (%s/%s)\n" "$C_BLUE" "$C_RESET" "$mode" "$attempt" "$max_attempts"

    files=""
    global_attempt=""
    if [[ "$PHASE_LAST_APPLIED_MODE" == "$mode" && -n "$PHASE_LAST_APPLIED_ATTEMPT" ]]; then
      global_attempt="$PHASE_LAST_APPLIED_ATTEMPT"
      files="$(fetch_patch_files_for_attempt "$CASE_ID" "$mode" "$global_attempt")"
    else
      details="$(fetch_latest_patch_details_for_mode "$CASE_ID" "$mode")"
      if [[ -n "$details" ]]; then
        IFS='|' read -r -a detail_parts <<< "$details"
        global_attempt="${detail_parts[0]:-}"
        if (( ${#detail_parts[@]} > 1 )); then
          files="${details#*|}"
        fi
      fi
    fi

    [[ -n "$global_attempt" ]] && printf "      - intento global: %s\n" "$global_attempt"
    [[ -n "$PHASE_LAST_AGENT_LATENCY" ]] && printf "      - tiempo modelo: %ss\n" "$PHASE_LAST_AGENT_LATENCY"

    if [[ -n "$files" ]]; then
      IFS='|' read -r -a touched_files <<< "$files"
      for f in "${touched_files[@]}"; do
        [[ -n "$f" ]] && printf "      - archivo: %s\n" "$f"
      done
    else
      echo "      - archivo: (pendiente; se mostrará al validar baseline)"
    fi

    PHASE_LAST_AGENT_LATENCY=""
    return 0
  fi

  if [[ "$stripped" =~ Baseline[[:space:]]+([a-z_]+):[[:space:]]+VALIDATED ]]; then
    mode="${BASH_REMATCH[1]}"
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    PHASE_PASS_COUNT=$((PHASE_PASS_COUNT + 1))
    printf "    %b[VALIDO]%b baseline=%s (parche validado)\n" "$C_GREEN" "$C_RESET" "$mode"

    validated_attempt=""
    files=""

    if [[ "$PHASE_LAST_APPLIED_MODE" == "$mode" && -n "$PHASE_LAST_APPLIED_ATTEMPT" ]]; then
      validated_attempt="$PHASE_LAST_APPLIED_ATTEMPT"
      files="$(fetch_patch_files_for_attempt "$CASE_ID" "$mode" "$validated_attempt")"
    fi

    if [[ -z "$files" ]]; then
      details="$(fetch_latest_patch_details_for_mode "$CASE_ID" "$mode")"
      if [[ -n "$details" ]]; then
        IFS='|' read -r -a detail_parts <<< "$details"
        [[ -z "$validated_attempt" ]] && validated_attempt="${detail_parts[0]:-}"
        if (( ${#detail_parts[@]} > 1 )); then
          files="${details#*|}"
        fi
      fi
    fi

    [[ -n "$validated_attempt" ]] && printf "      - intento: %s\n" "$validated_attempt"
    if [[ -n "$files" ]]; then
      IFS='|' read -r -a touched_files <<< "$files"
      for f in "${touched_files[@]}"; do
        [[ -n "$f" ]] && printf "      - archivo: %s\n" "$f"
      done
    fi

    return 0
  fi

  if [[ "$stripped" =~ Baseline[[:space:]]+([a-z_]+):[[:space:]]+budget[[:space:]]+exhausted.*final=([A-Z_]+) ]]; then
    mode="${BASH_REMATCH[1]}"
    status="${BASH_REMATCH[2]}"
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    PHASE_FAIL_COUNT=$((PHASE_FAIL_COUNT + 1))
    printf "    %b[AGOTA ]%b baseline=%s (resultado=%s)\n" "$C_YELLOW" "$C_RESET" "$mode" "$status"
    return 0
  fi

  # Wrapped validation summary: first line has "validation complete:", then
  # patch_status / overall may appear on subsequent wrapped lines.
  if [[ "$stripped" == *"validation complete:"* ]]; then
    PHASE_EXPECT_VALIDATION=1
    PHASE_PENDING_PATCH_STATUS=""
    PHASE_PENDING_OVERALL=""
    PHASE_PENDING_VALIDATION_TTL=4
  fi

  if [[ $PHASE_EXPECT_VALIDATION -eq 1 ]]; then
    [[ "$stripped" =~ patch_status=([A-Z_]+) ]] && PHASE_PENDING_PATCH_STATUS="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ overall=([A-Z_]+) ]] && PHASE_PENDING_OVERALL="${BASH_REMATCH[1]}"

    PHASE_PENDING_VALIDATION_TTL=$((PHASE_PENDING_VALIDATION_TTL - 1))
    if [[ -n "$PHASE_PENDING_PATCH_STATUS" && ( -n "$PHASE_PENDING_OVERALL" || $PHASE_PENDING_VALIDATION_TTL -le 0 ) ]]; then
      patch_status="$PHASE_PENDING_PATCH_STATUS"
      overall="${PHASE_PENDING_OVERALL:-N/A}"
      PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))

      if [[ "$patch_status" == "VALIDATED" ]]; then
        PHASE_PASS_COUNT=$((PHASE_PASS_COUNT + 1))
        printf "    %b[VALIDO]%b validación in-loop: parche=%s repositorio=%s\n" "$C_GREEN" "$C_RESET" "$patch_status" "$overall"
      else
        PHASE_FAIL_COUNT=$((PHASE_FAIL_COUNT + 1))
        printf "    %b[FALLO ]%b validación in-loop: parche=%s repositorio=%s\n" "$C_RED" "$C_RESET" "$patch_status" "$overall"
      fi

      PHASE_EXPECT_VALIDATION=0
      PHASE_PENDING_PATCH_STATUS=""
      PHASE_PENDING_OVERALL=""
      PHASE_PENDING_VALIDATION_TTL=0
      return 0
    fi
  fi

  if [[ "$stripped" =~ localized:[[:space:]]+([0-9]+)[[:space:]]+candidates ]]; then
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "  %b[INFO ]%b candidatos localizados=%s\n" "$C_BLUE" "$C_RESET" "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$stripped" =~ execution[[:space:]]+complete:[[:space:]]+status=([A-Z_]+)[[:space:]]+errors=([0-9]+) ]]; then
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "  %b[INFO ]%b ejecución before/after: estado=%s errores_after=%s\n" "$C_BLUE" "$C_RESET" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi

  if [[ "$stripped" =~ errors[[:space:]]+\(after\)[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "  %b[INFO ]%b errores en AFTER=%s\n" "$C_BLUE" "$C_RESET" "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$stripped" == *"explanation complete:"* ]]; then
    PHASE_EXPECT_EXPLAIN=1
    PHASE_PENDING_EXPLAIN_JSON=""
    PHASE_PENDING_EXPLAIN_MD=""
    PHASE_PENDING_EXPLAIN_TOKENS=""
    PHASE_PENDING_EXPLAIN_TTL=6
  fi

  if [[ $PHASE_EXPECT_EXPLAIN -eq 1 ]]; then
    [[ "$stripped" =~ json=([^[:space:]]+) ]] && PHASE_PENDING_EXPLAIN_JSON="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ md=([^[:space:]]+) ]] && PHASE_PENDING_EXPLAIN_MD="${BASH_REMATCH[1]}"
    [[ "$stripped" =~ tokens=([0-9+]+) ]] && PHASE_PENDING_EXPLAIN_TOKENS="${BASH_REMATCH[1]}"

    PHASE_PENDING_EXPLAIN_TTL=$((PHASE_PENDING_EXPLAIN_TTL - 1))
    if [[ -n "$PHASE_PENDING_EXPLAIN_TOKENS" || $PHASE_PENDING_EXPLAIN_TTL -le 0 ]]; then
      PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
      printf "  %b[INFO ]%b explain listo (tokens=%s)\n" "$C_BLUE" "$C_RESET" "${PHASE_PENDING_EXPLAIN_TOKENS:-N/A}"
      [[ -n "$PHASE_PENDING_EXPLAIN_JSON" ]] && printf "    - json: %s\n" "$PHASE_PENDING_EXPLAIN_JSON"
      [[ -n "$PHASE_PENDING_EXPLAIN_MD" ]] && printf "    - md  : %s\n" "$PHASE_PENDING_EXPLAIN_MD"

      PHASE_EXPECT_EXPLAIN=0
      PHASE_PENDING_EXPLAIN_JSON=""
      PHASE_PENDING_EXPLAIN_MD=""
      PHASE_PENDING_EXPLAIN_TOKENS=""
      PHASE_PENDING_EXPLAIN_TTL=0
      return 0
    fi
  fi

  if [[ "$stripped" =~ ^Report:[[:space:]]+([0-9]+)[[:space:]]+row ]]; then
    PHASE_EVENT_COUNT=$((PHASE_EVENT_COUNT + 1))
    printf "  %b[INFO ]%b filas en reporte=%s\n" "$C_BLUE" "$C_RESET" "${BASH_REMATCH[1]}"
    return 0
  fi

  return 1
}

run_phase_cmd() {
  local phase_key="$1"; shift
  local logfile="$RUN_LOG_DIR/${phase_key}.log"
  local rc=0
  local started_at=0
  local elapsed=0
  local last_line=0
  local total_lines=0
  local now=0
  local heartbeat_every=10
  local next_heartbeat=0
  local use_build_counters=0

  if phase_uses_build_counters "$phase_key"; then
    use_build_counters=1
  fi

  if [[ "$CONSOLE_MODE" == "verbose" ]]; then
    set +e
    "$@" 2>&1 | tee "$logfile"
    rc=${PIPESTATUS[0]}
    set -e
  else
    : > "$logfile"
    started_at="$(date +%s)"
    PHASE_PASS_COUNT=0
    PHASE_FAIL_COUNT=0
    PHASE_SKIP_COUNT=0
    PHASE_EVENT_COUNT=0
    PHASE_CURRENT_SCOPE=""
    PHASE_CURRENT_MODE=""
    PHASE_CURRENT_MODEL=""
    PHASE_MODEL_ANNOUNCED=0
    PHASE_BEFORE_PASS=0
    PHASE_BEFORE_FAIL=0
    PHASE_AFTER_PASS=0
    PHASE_AFTER_FAIL=0
    PHASE_EXPECT_RUN_CONTEXT=0
    PHASE_PENDING_RUN_TASK=""
    PHASE_PENDING_RUN_CWD=""
    PHASE_EXPECT_FINISH=0
    PHASE_PENDING_FINISH_TASK=""
    PHASE_PENDING_FINISH_STATUS=""
    PHASE_PENDING_FINISH_DURATION=""
    PHASE_PENDING_FINISH_ERRORS=""
    PHASE_PENDING_FINISH_TTL=0
    PHASE_EXPECT_VALIDATION=0
    PHASE_PENDING_PATCH_STATUS=""
    PHASE_PENDING_OVERALL=""
    PHASE_PENDING_VALIDATION_TTL=0
    PHASE_EXPECT_EXPLAIN=0
    PHASE_PENDING_EXPLAIN_JSON=""
    PHASE_PENDING_EXPLAIN_MD=""
    PHASE_PENDING_EXPLAIN_TOKENS=""
    PHASE_PENDING_EXPLAIN_TTL=0
    PHASE_LAST_AGENT_LATENCY=""
    PHASE_EXPECT_REPAIR_DONE=0
    PHASE_PENDING_REPAIR_MODE=""
    PHASE_PENDING_REPAIR_ATTEMPT=""
    PHASE_PENDING_REPAIR_STATUS=""
    PHASE_PENDING_REPAIR_TTL=0
    PHASE_LAST_APPLIED_MODE=""
    PHASE_LAST_APPLIED_ATTEMPT=""
    next_heartbeat=$((started_at + heartbeat_every))

    KMP_LOG_LEVEL="${KMP_CONSOLE_SOURCE_LEVEL:-INFO}" "$@" >"$logfile" 2>&1 &
    local cmd_pid=$!

    printf "  %b[RUN ]%b %s\n" "$C_CYAN" "$C_RESET" "$phase_key"
    while kill -0 "$cmd_pid" 2>/dev/null; do
      sleep 2

      total_lines="$(wc -l < "$logfile" | tr -d '[:space:]')"
      [[ -z "$total_lines" ]] && total_lines=0
      if (( total_lines > last_line )); then
        while IFS= read -r line; do
          render_phase_event "$phase_key" "$line" || true
        done < <(sed -n "$((last_line + 1)),${total_lines}p" "$logfile")
        last_line=$total_lines
      fi

      if ! kill -0 "$cmd_pid" 2>/dev/null; then
        break
      fi

      now="$(date +%s)"
      if (( now >= next_heartbeat )); then
        elapsed=$(( now - started_at ))
        if (( use_build_counters == 1 )); then
          printf "  %b[ALIVE]%b %s elapsed=%ss ok=%s fallo=%s\n" \
            "$C_DIM" "$C_RESET" "$phase_key" "$elapsed" "$PHASE_PASS_COUNT" "$PHASE_FAIL_COUNT"
        else
          printf "  %b[ALIVE]%b %s elapsed=%ss eventos=%s\n" \
            "$C_DIM" "$C_RESET" "$phase_key" "$elapsed" "$PHASE_EVENT_COUNT"
        fi
        next_heartbeat=$((next_heartbeat + heartbeat_every))
      fi
    done

    set +e
    wait "$cmd_pid"
    rc=$?
    set -e

    total_lines="$(wc -l < "$logfile" | tr -d '[:space:]')"
    [[ -z "$total_lines" ]] && total_lines=0
    if (( total_lines > last_line )); then
      while IFS= read -r line; do
        render_phase_event "$phase_key" "$line" || true
      done < <(sed -n "$((last_line + 1)),${total_lines}p" "$logfile")
      last_line=$total_lines
    fi

    elapsed=$(( $(date +%s) - started_at ))
    if [[ $rc -eq 0 ]]; then
      if (( use_build_counters == 1 )); then
        printf "  %b[DONE ]%b %s (%ss, ok=%s, fallo=%s, eventos=%s)\n" \
          "$C_GREEN" "$C_RESET" "$phase_key" "$elapsed" "$PHASE_PASS_COUNT" "$PHASE_FAIL_COUNT" "$PHASE_EVENT_COUNT"
        if [[ "$phase_key" == "run_before_after" ]]; then
          printf "    BEFORE: ok=%s fallo=%s\n" "$PHASE_BEFORE_PASS" "$PHASE_BEFORE_FAIL"
          printf "    AFTER : ok=%s fallo=%s\n" "$PHASE_AFTER_PASS" "$PHASE_AFTER_FAIL"
        fi
      else
        printf "  %b[DONE ]%b %s (%ss, eventos=%s)\n" \
          "$C_GREEN" "$C_RESET" "$phase_key" "$elapsed" "$PHASE_EVENT_COUNT"
      fi
      local last_line
      last_line="$(grep -v '^[[:space:]]*$' "$logfile" | tail -n 1 || true)"
      if [[ -n "$last_line" ]]; then
        printf "  %b[CIERRE]%b %s\n" "$C_BLUE" "$C_RESET" "$last_line"
      fi
    fi
  fi

  if [[ $rc -ne 0 ]]; then
    echo "ERROR: Phase command failed (exit=$rc)." >&2
    echo "       See full log: $logfile" >&2
    if [[ "$CONSOLE_MODE" == "human" ]]; then
      echo "------- last log lines (${phase_key}) -------" >&2
      tail -n 20 "$logfile" >&2 || true
      echo "---------------------------------------------" >&2
    fi
    return $rc
  fi
}

print_repair_change_summary() {
  local case_id="$1"
  "$KMP_PYTHON" - <<PY 2>/dev/null || true
from sqlalchemy import select

from kmp_repair_pipeline.storage.db import get_session
from kmp_repair_pipeline.storage.models import PatchAttempt, ValidationRun

case_id = "$case_id"
ordered_modes = ("raw_error", "context_rich", "iterative_agentic", "full_thesis")

with get_session() as s:
  attempts = s.scalars(
    select(PatchAttempt)
    .where(PatchAttempt.repair_case_id == case_id)
    .order_by(PatchAttempt.repair_mode, PatchAttempt.attempt_number.desc())
  ).all()

  latest_by_mode = {}
  for attempt in attempts:
    if attempt.repair_mode not in latest_by_mode:
      latest_by_mode[attempt.repair_mode] = attempt

  print("Repair summary (what changed / what failed):")
  for mode in ordered_modes:
    attempt = latest_by_mode.get(mode)
    if attempt is None:
      print(f"  [{mode}] no attempt")
      continue

    touched = list(attempt.touched_files or [])
    preview = ", ".join(touched[:2]) if touched else "none"
    if len(touched) > 2:
      preview += f", +{len(touched) - 2} more"

    validations = s.scalars(
      select(ValidationRun).where(ValidationRun.patch_attempt_id == attempt.id)
    ).all()

    if not validations:
      validation_text = "validation=no"
    else:
      ok = sum(1 for v in validations if str(v.status) == "SUCCESS_REPOSITORY_LEVEL")
      fail = sum(1 for v in validations if str(v.status).startswith("FAILED"))
      validation_text = f"validation(ok={ok}, fail={fail}, total={len(validations)})"

    print(
      f"  [{mode}] status={attempt.status} attempt={attempt.attempt_number} "
      f"touched={len(touched)} ({preview}) {validation_text}"
    )
PY
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
# run-before-after
# ---------------------------------------------------------------------------
step "run-before-after $FRESH_FLAG"
run_phase_cmd "run_before_after" kmp-repair run-before-after "$CASE_ID" $FRESH_FLAG

STATUS="$(check_status "$CASE_ID")"
echo "Status after run-before-after: $STATUS"
if [[ "$STATUS" == "NO_ERRORS_TO_FIX" ]]; then
  echo ""
  echo ">>> Non-breaking update: after-state compiled with 0 errors."
  echo ">>> Skipping repair/validate — jumping directly to metrics."
  step "metrics (no-op case)"
  run_phase_cmd "metrics_noop" kmp-repair metrics "$CASE_ID"
  step "report"
  run_phase_cmd "report_noop" kmp-repair report --format all
  echo ""
  echo "================================================================"
  echo "  DONE (no-op case) — $CASE_ID"
  echo "  Detailed logs: $RUN_LOG_DIR"
  echo "================================================================"
  exit 0
fi

if [[ "$STATUS" != "EXECUTED" ]]; then
  echo "ERROR: Expected EXECUTED after run-before-after, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# analyze-case
# ---------------------------------------------------------------------------
step "analyze-case"
run_phase_cmd "analyze_case" kmp-repair analyze-case "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "ANALYZED" ]]; then
  echo "ERROR: Expected ANALYZED after analyze-case, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# localize
# ---------------------------------------------------------------------------
step "localize"
run_phase_cmd "localize" kmp-repair localize "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "LOCALIZED" ]]; then
  echo "ERROR: Expected LOCALIZED after localize, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# repair --all-baselines (includes in-loop validation)
# ---------------------------------------------------------------------------
step "repair --all-baselines (includes in-loop validation)"
echo "  Modelo RepairAgent esperado: ${KMP_LLM_MODEL:-desconocido}"
run_phase_cmd "repair" kmp-repair repair "$CASE_ID" --all-baselines

STATUS="$(check_status "$CASE_ID")"
echo "Status after repair: $STATUS"
print_repair_change_summary "$CASE_ID"
# VALIDATED is expected when at least one baseline validated successfully.
# PATCH_ATTEMPTED means all baselines exhausted without full validation — continue to explain.
if [[ "$STATUS" != "VALIDATED" && "$STATUS" != "PATCH_ATTEMPTED" ]]; then
  echo "ERROR: Unexpected status after repair: $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------
step "explain"
run_phase_cmd "explain" kmp-repair explain "$CASE_ID"
print_explain_summary "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "EXPLAINED" ]]; then
  echo "ERROR: Expected EXPLAINED after explain, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
step "metrics"
run_phase_cmd "metrics" kmp-repair metrics "$CASE_ID"

STATUS="$(check_status "$CASE_ID")"
if [[ "$STATUS" != "EVALUATED" ]]; then
  echo "ERROR: Expected EVALUATED after metrics, got $STATUS" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
step "report (all cases)"
run_phase_cmd "report" kmp-repair report --format all

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "================================================================"
echo "  DONE — $CASE_ID  (status: $STATUS)"
echo "  Reports: $PROJECT_ROOT/data/reports/"
echo "  Detailed logs: $RUN_LOG_DIR"
echo "================================================================"
