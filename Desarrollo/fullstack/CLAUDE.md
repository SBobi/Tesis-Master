# fullstack — CLAUDE.md

## What this directory is

The web layer of the kmp-repair thesis system.  Contains two packages:

```
fullstack/
├── backend/    FastAPI + RQ adapter over the canonical pipeline
└── frontend/   Next.js 14 editorial frontend
```

Each package has its own CLAUDE.md.  See those files for detailed rules.

---

## Critical boundary

```
Frontend (port 3000)  →  Backend API (port 8000)  →  kmp_repair_pipeline.*
```

- The frontend only communicates with the backend via `lib/api.ts`.
- The backend only invokes pipeline logic via canonical imports
  (`from kmp_repair_pipeline.X import Y`).
- The frontend never imports Python or calls pipeline endpoints directly.

---

## Shared infrastructure

Both packages share the same Docker Compose infrastructure:

```bash
# Start from the backend directory (Postgres 15 + Redis 7):
cd fullstack/backend
docker compose up -d
```

The canonical pipeline's docker-compose (in `../../kmp-repair-pipeline/`) starts
Postgres 15 only (no Redis).  Use the backend's docker-compose for the full stack.

---

## Starting the full local stack

```bash
# 1. Infrastructure
cd fullstack/backend && docker compose up -d

# 2. DB migrations (canonical pipeline owns all migrations)
cd ../../kmp-repair-pipeline && alembic upgrade head

# 3. Backend API (terminal 1)
cd fullstack/backend
source .venv/bin/activate
kmp-repair-api          # → http://localhost:8000

# 4. RQ worker (terminal 2)
kmp-repair-worker

# 5. Frontend (terminal 3)
cd fullstack/frontend
npm run dev             # → http://localhost:3000
```

---

## Environment variables

Backend `.env` is the single source of truth for shared configuration.  The
frontend reads only `NEXT_PUBLIC_API_BASE_URL` (set in `.env.local`).

See `fullstack/backend/.env.example` for all backend variables.

---

## Do not

- Add pipeline business logic to the backend package.
- Call the backend from the frontend outside of `lib/api.ts`.
- Add Alembic migrations to the backend directory.
- Commit secrets (`.env`, `.secrets/`) to version control.
