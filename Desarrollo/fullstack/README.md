# kmp-repair — Fullstack Web Layer

Web interface for the KMP Repair Pipeline thesis system.  Contains two packages
that together expose the canonical pipeline as a usable web application.

```
fullstack/
├── backend/    FastAPI + RQ adapter (port 8000)
└── frontend/   Next.js 14 editorial UI (port 3000)
```

The pipeline itself (`../../kmp-repair-pipeline/`) is the source of truth for
all repair, localization, validation, and evaluation logic.  These packages
only adapt and display that work.

---

## Architecture

```
Browser (port 3000)
      |
      | HTTP / SSE  (via lib/api.ts only)
      v
FastAPI app (port 8000)
      |
      | enqueue job
      v
Redis queue  ←→  RQ Worker
                      |
                      | imports (editable dep)
                      v
            kmp_repair_pipeline.*
                      |
                 reads / writes
                      v
              PostgreSQL 15
              Artifact store
```

**Critical boundary:** the frontend never calls pipeline code directly, and the
backend never duplicates pipeline logic.  Every business-logic call flows
through `kmp_repair_pipeline.*` imports in the worker.

---

## Starting the full local stack

```bash
# 1. Infrastructure (Postgres 15 + Redis 7)
cd fullstack/backend
docker compose up -d

# 2. DB migrations (owned by canonical pipeline — never run here)
cd ../../kmp-repair-pipeline
alembic upgrade head

# 3. Backend API  (terminal 1)
cd fullstack/backend
source .venv/bin/activate
kmp-repair-api          # → http://localhost:8000

# 4. RQ worker  (terminal 2)
kmp-repair-worker

# 5. Frontend  (terminal 3)
cd fullstack/frontend
npm run dev             # → http://localhost:3000
```

---

## Package summaries

### `backend/` — FastAPI + RQ adapter

| File | Role |
|------|------|
| `app.py` | 15 REST + SSE endpoints |
| `worker.py` | RQ worker entrypoint |
| `job_runner.py` | job enqueueing + execution loop |
| `orchestrator.py` | stage dispatch → pipeline functions |
| `queries.py` | DB read queries for web responses |
| `stages.py` | stage vocabulary + param validation |
| `schemas.py` | Pydantic request/response schemas |

See [backend/README.md](backend/README.md) for the full API reference,
environment variables, and troubleshooting guide.

### `frontend/` — Next.js 14 editorial UI

| Route | Purpose |
|-------|---------|
| `/` | Hero + active pipeline status (SSE) + recent cases |
| `/process` | Ingest PR URL, auto-select newly ingested case, run pipeline, live console |
| `/cases` | Full case listing with filters |
| `/cases/[caseId]` | Evidence timeline, patch diffs, validation matrix |
| `/results` | Aggregated metrics comparison + D3 charts + export |
| `/environment` | Runtime health checks + backend configuration |
| `/about` | Static thesis framing |

All backend calls go through `lib/api.ts`.  Never call the API directly from
pages or components.

See [frontend/README.md](frontend/README.md) for the full component reference
and design system documentation.

---

## Environment configuration

| File | Owner | Contents |
|------|-------|---------|
| `backend/.env` | backend | DB URL, Redis URL, LLM credentials, artifact paths |
| `frontend/.env.local` | frontend | `NEXT_PUBLIC_API_BASE_URL` only |

Copy `backend/.env.example` to `backend/.env` and fill in the required values
before starting the stack.

---

## What NOT to do

- Do not add pipeline logic to `backend/` — import it from the pipeline.
- Do not call the backend from `frontend/` outside of `lib/api.ts`.
- Do not add Alembic migrations anywhere in this directory.
- Do not commit `.env` or credential files.
