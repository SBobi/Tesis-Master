#!/usr/bin/env bash

# cd /Users/sbobi/Desktop/Tesis-Master/Desarrollo/fullstack/frontend
# ./scripts/run_fullstack.sh 

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$(cd "$FRONTEND_DIR/../backend" && pwd)"

FRONT_PORT="${FRONT_PORT:-3000}"
BACK_PORT="${BACK_PORT:-8000}"
API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:${BACK_PORT}}"
START_INFRA="${START_INFRA:-1}"
START_WORKER="${START_WORKER:-1}"
API_LOG="${API_LOG:-/tmp/kmp_stack_api.log}"
WORKER_LOG="${WORKER_LOG:-/tmp/kmp_stack_worker.log}"

API_PID=""
WORKER_PID=""
PYTHON_BIN=""

log() {
  echo "[stack] $*"
}

fail() {
  echo "[stack][error] $*" >&2
  exit 1
}

resolve_python() {
  local candidates=()
  local candidate
  local pipeline_venv="$FRONTEND_DIR/../../kmp-repair-pipeline/.venv/bin/python"

  if [[ -n "${KMP_PYTHON_BIN:-}" ]]; then
    [[ -x "$KMP_PYTHON_BIN" ]] || fail "KMP_PYTHON_BIN no es ejecutable: $KMP_PYTHON_BIN"
    candidates+=("$KMP_PYTHON_BIN")
  fi

  [[ -x "$BACKEND_DIR/.venv/bin/python" ]] && candidates+=("$BACKEND_DIR/.venv/bin/python")
  [[ -x "$pipeline_venv" ]] && candidates+=("$pipeline_venv")
  [[ -x "/opt/homebrew/opt/python@3.12/bin/python3.12" ]] && candidates+=("/opt/homebrew/opt/python@3.12/bin/python3.12")
  [[ -x "/opt/homebrew/bin/python3.12" ]] && candidates+=("/opt/homebrew/bin/python3.12")

  if command -v python3.12 >/dev/null 2>&1; then
    candidates+=("$(command -v python3.12)")
  fi
  if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
  fi
  if command -v python >/dev/null 2>&1; then
    candidates+=("$(command -v python)")
  fi

  for candidate in "${candidates[@]}"; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" -c "import kmp_repair_webapi" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      return
    fi
  done

  fail "No encontré un Python con kmp_repair_webapi. Instala deps en backend/.venv o exporta KMP_PYTHON_BIN"
}

wait_for_api() {
  local retries=40
  local delay=0.5

  for _ in $(seq 1 "$retries"); do
    if curl -sf "$API_BASE_URL/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done

  return 1
}

port_listening() {
  local port="$1"
  lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

infra_ready() {
  port_listening 5432 && port_listening 6379
}

cleanup() {
  local exit_code=$?

  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    log "Deteniendo worker (PID $WORKER_PID)"
    kill "$WORKER_PID" 2>/dev/null || true
  fi

  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    log "Deteniendo API (PID $API_PID)"
    kill "$API_PID" 2>/dev/null || true
  fi

  wait 2>/dev/null || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

log "Frontend: $FRONTEND_DIR"
log "Backend:  $BACKEND_DIR"
log "API URL:  $API_BASE_URL"

if [[ "$START_INFRA" == "1" ]]; then
  if infra_ready; then
    log "Postgres + Redis ya están activos en 5432/6379 (se reutilizan)"
  else
    command -v docker >/dev/null 2>&1 || fail "Docker no está disponible y START_INFRA=1"
    log "Levantando Postgres + Redis (docker compose up -d)"
    if ! (cd "$BACKEND_DIR" && docker compose up -d); then
      if infra_ready; then
        log "docker compose devolvió error, pero 5432/6379 ya están ocupados; se reutiliza infraestructura existente"
      else
        fail "No se pudo levantar infraestructura y no se detectó Postgres/Redis activos en 5432/6379"
      fi
    fi
  fi
fi

resolve_python

if ! "$PYTHON_BIN" -c "import kmp_repair_webapi" >/dev/null 2>&1; then
  fail "El runtime $PYTHON_BIN no tiene kmp_repair_webapi instalado. Ejecuta: pip install -e $BACKEND_DIR"
fi

if curl -sf "$API_BASE_URL/api/health" >/dev/null 2>&1; then
  log "API ya está corriendo en $API_BASE_URL (se reutiliza)"
else
  if port_listening "$BACK_PORT"; then
    fail "El puerto $BACK_PORT está ocupado por otro proceso que no responde /api/health"
  fi

  log "Iniciando API backend en puerto $BACK_PORT"
  (
    cd "$BACKEND_DIR"
    "$PYTHON_BIN" -m uvicorn kmp_repair_webapi.app:app --host 0.0.0.0 --port "$BACK_PORT" >"$API_LOG" 2>&1
  ) &
  API_PID=$!
  log "API PID: $API_PID (log: $API_LOG)"

  wait_for_api || fail "La API no respondió a tiempo. Revisa: $API_LOG"
fi

if [[ "$START_WORKER" == "1" ]]; then
  if pgrep -f "kmp-repair-worker|kmp_repair_webapi.worker" >/dev/null 2>&1; then
    log "Worker existente detectado (se reutiliza)"
  else
    WORKER_ENTRY="$($PYTHON_BIN -c "import sysconfig; print(sysconfig.get_path('scripts'))")/kmp-repair-worker"

    log "Iniciando worker backend"
    if [[ -x "$WORKER_ENTRY" ]]; then
      (
        cd "$BACKEND_DIR"
        "$WORKER_ENTRY" >"$WORKER_LOG" 2>&1
      ) &
    else
      (
        cd "$BACKEND_DIR"
        "$PYTHON_BIN" -c "from kmp_repair_webapi.worker import run; run()" >"$WORKER_LOG" 2>&1
      ) &
    fi

    WORKER_PID=$!
    log "Worker PID: $WORKER_PID (log: $WORKER_LOG)"
  fi
fi

log "Iniciando frontend en puerto $FRONT_PORT"
PORT="$FRONT_PORT" NEXT_PUBLIC_API_BASE_URL="$API_BASE_URL" "$FRONTEND_DIR/scripts/run_front.sh"
