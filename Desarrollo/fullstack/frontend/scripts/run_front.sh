#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT="${PORT:-3000}"
API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}"

cd "$FRONTEND_DIR"

if [[ ! -d node_modules ]]; then
  echo "[front] Instalando dependencias..."
  npm install
fi

echo "[front] Iniciando frontend en http://localhost:${PORT}"
echo "[front] Backend API: ${API_BASE_URL}"

NEXT_PUBLIC_API_BASE_URL="$API_BASE_URL" npm run dev -- --port "$PORT"
