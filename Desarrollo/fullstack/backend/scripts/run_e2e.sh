#!/usr/bin/env bash
# =============================================================================
# run_e2e.sh -- Backend end-to-end test for kmp-repair-webapi.
#
# Runs a complete pipeline cycle through the FastAPI + RQ layer against a
# real PostgreSQL database and Redis instance.  No mocking.
#
# Usage:
#   ./scripts/run_e2e.sh                          # Ktor case (default)
#   ./scripts/run_e2e.sh <case_id>                # explicit case
#   ./scripts/run_e2e.sh --start-from localize    # skip earlier stages
#   ./scripts/run_e2e.sh --mode raw_error         # single repair mode
#   ./scripts/run_e2e.sh --keep                   # keep API + worker running
#
# What this does:
#   1. Verify prerequisites (Docker containers, .env, packages installed)
#   2. Reset the target case to INGESTED status via scripts/reset_case.py
#   3. Kill any process already bound to port 8000
#   4. Start the FastAPI server (kmp-repair-api) in the background
#   5. Start the RQ worker (kmp-repair-worker) in the background
#   6. POST /api/cases/{case_id}/jobs/pipeline via curl
#   7. Poll GET /api/jobs/{job_id} until terminal status
#   8. Fetch and print stage-by-stage result summary
#   9. Verify all pipeline stages show COMPLETED in the timeline
#  10. Stop API server and worker (unless --keep is passed)
#
# Prerequisites:
#   - kmp_repair_db (Postgres 15) container running on port 5432
#   - kmp_repair_redis container running on port 6379
#   - fullstack/backend/.env configured (see .env.example)
#   - pip install -e ../../kmp-repair-pipeline && pip install -e .
#
# Default case: 3407b237 (Ktor 3.1.3 -> 3.4.1, PR #1)
#   This case is always available after running seed_real_cases.py in the
#   canonical pipeline and its repos are already cloned to data/artifacts/.
#   The e2e starts from run-before-after (skips re-clone) by default.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PIPELINE_DIR="$(cd "$BACKEND_DIR/../../kmp-repair-pipeline" && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_CASE_ID="3407b237-981f-40da-9623-4c4ac3c2087b"
DEFAULT_START_STAGE="run-before-after"
DEFAULT_REPAIR_MODE="full_thesis"
ARTIFACT_BASE="$PIPELINE_DIR/data/artifacts"
REPORT_DIR="$PIPELINE_DIR/data/reports"
API_PORT=8000
API_URL="http://localhost:$API_PORT"
POLL_INTERVAL_S=10
POLL_MAX_ITERATIONS=120   # 20 minutes max
KEEP_RUNNING=0
WORKER_BIN=""
PYTHON_SCRIPTS_DIR=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
CASE_ID="$DEFAULT_CASE_ID"
START_STAGE="$DEFAULT_START_STAGE"
REPAIR_MODE="$DEFAULT_REPAIR_MODE"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start-from)
            START_STAGE="$2"; shift 2 ;;
        --mode)
            REPAIR_MODE="$2"; shift 2 ;;
        --keep)
            KEEP_RUNNING=1; shift ;;
        --help|-h)
            sed -n '3,35p' "$0" | sed 's/^# //; s/^#//'
            exit 0 ;;
        -*)
            echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            CASE_ID="$1"; shift ;;
    esac
done

# ---------------------------------------------------------------------------
# Color output (only when stdout is a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET="\033[0m"
    C_BOLD="\033[1m"
    C_DIM="\033[2m"
    C_GREEN="\033[32m"
    C_RED="\033[31m"
    C_YELLOW="\033[33m"
    C_CYAN="\033[36m"
else
    C_RESET="" C_BOLD="" C_DIM="" C_GREEN="" C_RED="" C_YELLOW="" C_CYAN=""
fi

log()  { echo -e "${C_BOLD}[e2e]${C_RESET} $*"; }
ok()   { echo -e "${C_GREEN}[OK ]${C_RESET} $*"; }
fail() { echo -e "${C_RED}[ERR]${C_RESET} $*"; }
dim()  { echo -e "${C_DIM}      $*${C_RESET}"; }

kill_stale_processes() {
    local label="$1"
    local pattern="$2"
    local pids

    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [[ -z "$pids" ]] && return

    log "Found stale $label process(es) -- stopping to avoid DB lock contention"
    while IFS= read -r pid; do
        [[ -z "$pid" ]] && continue
        dim "killing PID $pid"
        kill "$pid" 2>/dev/null || true
    done <<< "$pids"

    sleep 0.5
    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
        while IFS= read -r pid; do
            [[ -z "$pid" ]] && continue
            dim "force killing PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        done <<< "$pids"
    fi
}

# ---------------------------------------------------------------------------
# Python runtime resolution
# ---------------------------------------------------------------------------
PYTHON_BIN=""
PYTHON_CANDIDATES=()

add_python_candidate() {
    local candidate="$1"
    [[ -z "$candidate" ]] && return
    [[ ! -x "$candidate" ]] && return

    local existing
    for existing in "${PYTHON_CANDIDATES[@]:-}"; do
        [[ "$existing" == "$candidate" ]] && return
    done
    PYTHON_CANDIDATES+=("$candidate")
}

python_supports_backend() {
    local py="$1"
    "$py" -c "import kmp_repair_pipeline, kmp_repair_webapi" >/dev/null 2>&1
}

resolve_python_runtime() {
    PYTHON_CANDIDATES=()
    PYTHON_BIN=""

    # Explicit override wins when provided.
    add_python_candidate "${KMP_PYTHON_BIN:-}"

    # Prefer project virtualenvs when they exist.
    add_python_candidate "$BACKEND_DIR/.venv/bin/python"
    add_python_candidate "$PIPELINE_DIR/.venv/bin/python"

    # Common Homebrew Python locations.
    add_python_candidate "/opt/homebrew/opt/python@3.12/bin/python3.12"
    add_python_candidate "/opt/homebrew/bin/python3.12"

    # Shell-discoverable fallbacks.
    local candidate
    for candidate in python3.12 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            add_python_candidate "$(command -v "$candidate")"
        fi
    done

    if [[ "${#PYTHON_CANDIDATES[@]}" -eq 0 ]]; then
        return 1
    fi

    for candidate in "${PYTHON_CANDIDATES[@]}"; do
        if python_supports_backend "$candidate"; then
            PYTHON_BIN="$candidate"
            return 0
        fi
    done

    local bootstrap_python="${PYTHON_CANDIDATES[0]}"
    log "No Python runtime with required packages found. Installing editable deps with $bootstrap_python"

    if ! "$bootstrap_python" -m pip install -e "$PIPELINE_DIR" > /tmp/kmp_e2e_pip.log 2>&1; then
        fail "Failed installing kmp_repair_pipeline with $bootstrap_python"
        dim "See /tmp/kmp_e2e_pip.log"
        return 1
    fi
    if ! "$bootstrap_python" -m pip install -e "$BACKEND_DIR" >> /tmp/kmp_e2e_pip.log 2>&1; then
        fail "Failed installing kmp_repair_webapi with $bootstrap_python"
        dim "See /tmp/kmp_e2e_pip.log"
        return 1
    fi

    for candidate in "${PYTHON_CANDIDATES[@]}"; do
        if python_supports_backend "$candidate"; then
            PYTHON_BIN="$candidate"
            return 0
        fi
    done

    return 1
}

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
API_PID=""
WORKER_PID=""

cleanup() {
    if [[ "$KEEP_RUNNING" -eq 1 ]]; then
        echo ""
        log "API server and worker left running (--keep)"
        dim "API   PID $API_PID   -- kill with: kill $API_PID"
        dim "Worker PID $WORKER_PID  -- kill with: kill $WORKER_PID"
        return
    fi
    echo ""
    log "Stopping API server and worker..."
    [[ -n "$API_PID" ]]    && kill "$API_PID"    2>/dev/null || true
    [[ -n "$WORKER_PID" ]] && kill "$WORKER_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    ok "Cleanup done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1 -- Prerequisites
# ---------------------------------------------------------------------------
echo ""
log "Step 1/9: Checking prerequisites"

# Docker containers
for container in kmp_repair_db kmp_repair_redis; do
    if ! docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | grep -q "true"; then
        # Fallback: accept any container name containing 'postgres'/'redis' on expected ports
        if [[ "$container" == "kmp_repair_db" ]]; then
            nc -z localhost 5432 2>/dev/null || { fail "PostgreSQL not reachable on port 5432. Start it with: docker compose up -d"; exit 1; }
        else
            nc -z localhost 6379 2>/dev/null || { fail "Redis not reachable on port 6379. Start it with: docker compose up -d"; exit 1; }
        fi
    fi
done
ok "PostgreSQL + Redis reachable"

# .env file
ENV_FILE="$BACKEND_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    fail ".env not found at $ENV_FILE"
    dim "Copy and configure: cp $BACKEND_DIR/.env.example $ENV_FILE"
    dim "Required keys: KMP_DATABASE_URL, KMP_REDIS_URL, KMP_ARTIFACT_BASE, KMP_REPORT_OUTPUT_DIR"
    dim "LLM keys: KMP_LLM_PROVIDER, KMP_VERTEX_PROJECT (or ANTHROPIC_API_KEY)"
    exit 1
fi
ok ".env found at $ENV_FILE"

# Python packages
if ! resolve_python_runtime; then
    fail "Could not find/configure a Python runtime with kmp_repair_pipeline + kmp_repair_webapi"
    dim "Set KMP_PYTHON_BIN or create .venv in backend/pipeline, then retry"
    exit 1
fi
PYTHON_VERSION="$($PYTHON_BIN -c "import sys; print(sys.version.split()[0])")"
ok "Python runtime ready: $PYTHON_BIN (v$PYTHON_VERSION)"

# Clear stale helper processes from previously aborted runs.
kill_stale_processes "worker" "kmp-repair-worker"
kill_stale_processes "reset-case" "scripts/reset_case.py"

WORKER_BIN="$(cd "$(dirname "$PYTHON_BIN")" && pwd)/kmp-repair-worker"
PYTHON_SCRIPTS_DIR="$($PYTHON_BIN -c "import sysconfig; print(sysconfig.get_path('scripts'))")"
WORKER_BIN="$PYTHON_SCRIPTS_DIR/kmp-repair-worker"
if [[ ! -x "$WORKER_BIN" ]]; then
    fail "Worker entrypoint not found for selected runtime: $WORKER_BIN"
    dim "Run: $PYTHON_BIN -m pip install -e $BACKEND_DIR"
    exit 1
fi

# Kill any existing process on the API port
EXISTING_PIDS="$(lsof -ti tcp:"$API_PORT" 2>/dev/null || true)"
if [[ -n "$EXISTING_PIDS" ]]; then
    log "Port $API_PORT in use -- terminating existing process(es)"
    while IFS= read -r pid; do
        [[ -z "$pid" ]] && continue
        dim "killing PID $pid"
        kill "$pid" 2>/dev/null || true
    done <<< "$EXISTING_PIDS"
    sleep 1

    STILL_BOUND="$(lsof -ti tcp:"$API_PORT" 2>/dev/null || true)"
    if [[ -n "$STILL_BOUND" ]]; then
        while IFS= read -r pid; do
            [[ -z "$pid" ]] && continue
            dim "force killing PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        done <<< "$STILL_BOUND"
        sleep 0.5
    fi
fi

# ---------------------------------------------------------------------------
# Step 2 -- Reset case to INGESTED
# ---------------------------------------------------------------------------
echo ""
log "Step 2/9: Resetting case $CASE_ID to INGESTED"
cd "$BACKEND_DIR"
"$PYTHON_BIN" scripts/reset_case.py "$CASE_ID"
ok "Case reset"

# ---------------------------------------------------------------------------
# Step 3 -- Start API server
# ---------------------------------------------------------------------------
echo ""
log "Step 3/9: Starting API server on port $API_PORT"
cd "$BACKEND_DIR"
"$PYTHON_BIN" -m uvicorn kmp_repair_webapi.app:app \
    --host 0.0.0.0 --port "$API_PORT" \
    > /tmp/kmp_api_e2e.log 2>&1 &
API_PID=$!
dim "API PID: $API_PID  log: /tmp/kmp_api_e2e.log"

# Wait for the server to be ready (up to 10s)
for i in $(seq 1 20); do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        fail "API server exited during startup. Check /tmp/kmp_api_e2e.log"
        exit 1
    fi
    if curl -sf "$API_URL/api/health" > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
curl -sf "$API_URL/api/health" > /dev/null 2>&1 \
    || { fail "API server did not become healthy within 10s. Check /tmp/kmp_api_e2e.log"; exit 1; }
ok "API server healthy at $API_URL"

# ---------------------------------------------------------------------------
# Step 4 -- Start RQ worker
# ---------------------------------------------------------------------------
echo ""
log "Step 4/9: Starting RQ worker"
cd "$BACKEND_DIR"
"$WORKER_BIN" > /tmp/kmp_worker_e2e.log 2>&1 &
WORKER_PID=$!
dim "Worker PID: $WORKER_PID  log: /tmp/kmp_worker_e2e.log"
sleep 2  # allow worker bootstrap to complete
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    fail "Worker exited during startup. Check /tmp/kmp_worker_e2e.log"
    exit 1
fi
ok "Worker started"

# ---------------------------------------------------------------------------
# Step 5 -- Verify case detail via API
# ---------------------------------------------------------------------------
echo ""
log "Step 5/9: Verifying case detail via GET /api/cases/$CASE_ID"
CASE_INFO=$(curl -sf "$API_URL/api/cases/$CASE_ID")
CASE_STATUS=$(echo "$CASE_INFO" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['case']['status'])")
CASE_PR=$(echo "$CASE_INFO" | "$PYTHON_BIN" -c "import json,sys; d=json.load(sys.stdin); print(d['case']['event']['pr_ref'],'--',d['case']['event']['pr_title'])")
ok "case status=$CASE_STATUS  pr=$CASE_PR"

# ---------------------------------------------------------------------------
# Step 6 -- POST pipeline job
# ---------------------------------------------------------------------------
echo ""
log "Step 6/9: Enqueueing pipeline via POST /api/cases/$CASE_ID/jobs/pipeline"

PAYLOAD=$("$PYTHON_BIN" -c "
import json
payload = {
    'start_from_stage': '$START_STAGE',
    'requested_by': 'e2e-run',
    'params_by_stage': {
        'run-before-after': {
            'artifact_base': '$ARTIFACT_BASE',
            'timeout_s': 900
        },
        'analyze-case': {},
        'localize': {
            'artifact_base': '$ARTIFACT_BASE',
            'top_k': 10,
            'no_agent': False
        },
        'repair': {
            'artifact_base': '$ARTIFACT_BASE',
            'mode': '$REPAIR_MODE',
            'top_k': 5,
            'patch_strategy': 'single_diff',
            'force_patch_attempt': True
        },
        'validate': {
            'artifact_base': '$ARTIFACT_BASE',
            'timeout_s': 600
        },
        'explain': {
            'artifact_base': '$ARTIFACT_BASE'
        },
        'metrics': {},
        'report': {
            'output_dir': '$REPORT_DIR',
            'format': 'all'
        }
    }
}
print(json.dumps(payload))
")

JOB_RESPONSE=$(curl -sf -X POST "$API_URL/api/cases/$CASE_ID/jobs/pipeline" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
JOB_ID=$(echo "$JOB_RESPONSE" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['job']['job_id'])")
JOB_STATUS=$(echo "$JOB_RESPONSE" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['job']['status'])")
ok "Job enqueued  job_id=$JOB_ID  status=$JOB_STATUS"

# ---------------------------------------------------------------------------
# Step 7 -- Poll until terminal state
# ---------------------------------------------------------------------------
echo ""
log "Step 7/9: Polling GET /api/jobs/$JOB_ID every ${POLL_INTERVAL_S}s"

TERMINAL_STATUS=""
for i in $(seq 1 "$POLL_MAX_ITERATIONS"); do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        fail "API process exited while job is in progress. Check /tmp/kmp_api_e2e.log"
        exit 1
    fi

    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        fail "Worker process exited while job is in progress. Check /tmp/kmp_worker_e2e.log"
        exit 1
    fi

    JOB_INFO=$(curl -sf "$API_URL/api/jobs/$JOB_ID")
    POLL_STATUS=$(echo "$JOB_INFO" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['job']['status'])")
    POLL_STAGE=$(echo "$JOB_INFO" | "$PYTHON_BIN" -c "import json,sys; j=json.load(sys.stdin)['job']; print(j.get('current_stage') or '-')")
    echo -e "  ${C_DIM}[$i]${C_RESET} status=${C_CYAN}${POLL_STATUS}${C_RESET}  stage=${POLL_STAGE}"

    case "$POLL_STATUS" in
        SUCCEEDED|FAILED|CANCELED)
            TERMINAL_STATUS="$POLL_STATUS"
            break
            ;;
    esac
    sleep "$POLL_INTERVAL_S"
done

if [[ -z "$TERMINAL_STATUS" ]]; then
    fail "Job did not reach terminal state within $((POLL_MAX_ITERATIONS * POLL_INTERVAL_S))s"
    exit 1
fi

if [[ "$TERMINAL_STATUS" != "SUCCEEDED" ]]; then
    fail "Pipeline $TERMINAL_STATUS"
    ERROR_MSG=$(curl -sf "$API_URL/api/jobs/$JOB_ID" | "$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['job'].get('error_message') or 'none')")
    dim "error: $ERROR_MSG"
    dim "worker log: /tmp/kmp_worker_e2e.log"
    exit 1
fi

ok "Pipeline SUCCEEDED"

# ---------------------------------------------------------------------------
# Step 8 -- Print stage-by-stage result summary
# ---------------------------------------------------------------------------
echo ""
log "Step 8/9: Stage result summary"

curl -sf "$API_URL/api/jobs/$JOB_ID" | "$PYTHON_BIN" -c "
import json, sys
j = json.load(sys.stdin)['job']
rs = j.get('result_summary') or {}
total_s = sum(v.get('duration_s', 0) for v in rs.values())
rows = []
for stage, info in rs.items():
    d = info.get('duration_s', 0)
    s = info.get('case_status', '?')
    summ = info.get('summary', {})
    rows.append((stage, d, s, summ))
rows.sort(key=lambda r: r[1], reverse=True)  # slowest first

print(f'  {\"STAGE\":<22} {\"STATUS\":<22} {\"DURATION\":>10}')
print('  ' + '-' * 58)
for stage, d, s, _ in rows:
    print(f'  {stage:<22} {s:<22} {d:>8.1f}s')
print('  ' + '-' * 58)
print(f'  {\"TOTAL\":<22} {\"SUCCEEDED\":<22} {total_s:>8.1f}s  ({total_s/60:.1f} min)')
"

# ---------------------------------------------------------------------------
# Step 9 -- Verify timeline and metrics
# ---------------------------------------------------------------------------
echo ""
log "Step 9/9: Verifying timeline and metrics"

CASE_DETAIL=$(curl -sf "$API_URL/api/cases/$CASE_ID")

# Timeline check
"$PYTHON_BIN" -c "
import json, sys
d = json.load(sys.stdin)
timeline = d['timeline']
not_done = [t['stage'] for t in timeline if t['status'] not in ('COMPLETED', 'NOT_STARTED')]
if not_done:
    print('FAIL: stages not COMPLETED:', not_done)
    sys.exit(1)
print('  Timeline: all run stages show COMPLETED')

metrics = d['evidence']['metrics']
if not metrics:
    print('  Metrics: none recorded')
else:
    for m in metrics:
        bsr = m.get('bsr'); ctsr = m.get('ctsr'); ffsr = m.get('ffsr'); efr = m.get('efr')
        mode = m.get('repair_mode', '?')
        print(f\"  Metrics [{mode}]: BSR={bsr} CTSR={ctsr} FFSR={ffsr} EFR={efr}\")
" <<< "$CASE_DETAIL" || exit 1

ok "All checks passed"

# ---------------------------------------------------------------------------
# Endpoint smoke check -- exercise every API route with real data
# ---------------------------------------------------------------------------
echo ""
log "Endpoint smoke check (all routes, real data)"

check_endpoint() {
    local label="$1"; local url="$2"; local method="${3:-GET}"; local body="${4:-}"; local expected="${5:-2xx}"
    local code
    if [[ -n "$body" ]]; then
        code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" \
               -H "Content-Type: application/json" -d "$body" 2>/dev/null || echo "000")
    else
        code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" 2>/dev/null || echo "000")
    fi
    if [[ "$expected" == "2xx" ]]; then
        if [[ "$code" == 2* ]]; then
            ok "$label -> $code"
        else
            fail "$label -> $code"
            ENDPOINT_FAILURES=$((ENDPOINT_FAILURES + 1))
        fi
    else
        if [[ "$code" == "$expected" ]]; then
            ok "$label -> $code"
        else
            fail "$label -> $code"
            ENDPOINT_FAILURES=$((ENDPOINT_FAILURES + 1))
        fi
    fi
}

ENDPOINT_FAILURES=0

check_endpoint "GET  /api/health"                         "$API_URL/api/health"
check_endpoint "GET  /api/cases"                          "$API_URL/api/cases"
check_endpoint "GET  /api/cases?status=EVALUATED"         "$API_URL/api/cases?status=EVALUATED"
check_endpoint "GET  /api/cases/{id}"                     "$API_URL/api/cases/$CASE_ID"
check_endpoint "GET  /api/cases/{id}/history"             "$API_URL/api/cases/$CASE_ID/history"
check_endpoint "GET  /api/jobs/{id}"                      "$API_URL/api/jobs/$JOB_ID"
check_endpoint "GET  /api/jobs/{id}/logs"                 "$API_URL/api/jobs/$JOB_ID/logs"
check_endpoint "GET  /api/reports/compare"                "$API_URL/api/reports/compare"
check_endpoint "GET  /api/reports/compare?modes=full_thesis" "$API_URL/api/reports/compare?modes=full_thesis"
check_endpoint "GET  /api/cases/{id}/artifact-content"    "$API_URL/api/cases/$CASE_ID/artifact-content?path=explanations%2Fexplanation.md"
check_endpoint "GET  /api/cases/nonexistent -> 404"       "$API_URL/api/cases/nonexistent-x" "GET" "" "404"
check_endpoint "GET  /api/jobs/nonexistent -> 404"        "$API_URL/api/jobs/nonexistent-x" "GET" "" "404"

if [[ "$ENDPOINT_FAILURES" -gt 0 ]]; then
    fail "$ENDPOINT_FAILURES endpoint(s) returned unexpected status codes"
    exit 1
fi
ok "All endpoints returned expected status codes"

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${C_BOLD}================================================================${C_RESET}"
echo -e "${C_BOLD} Backend E2E Complete${C_RESET}"
echo -e "${C_BOLD}================================================================${C_RESET}"
echo "  Case   : $CASE_ID"
echo "  PR     : $CASE_PR"
echo "  Mode   : $REPAIR_MODE"
echo "  Job    : $JOB_ID"
echo "  Result : SUCCEEDED"
echo "  API    : $API_URL"
echo -e "${C_BOLD}================================================================${C_RESET}"
