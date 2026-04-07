# kmp-repair — Root CLAUDE.md

## Project layout

```
Desarrollo/
├── kmp-repair-pipeline/     ← Canonical pipeline (core engine, thesis implementation)
└── fullstack/
    ├── backend/             ← Web API adapter (FastAPI + RQ)
    └── frontend/            ← Editorial frontend (Next.js 14)
```

Each sub-project has its own CLAUDE.md with detailed rules.  This file records
project-wide rules that apply everywhere.

---

## Separation of concerns (critical)

| Layer | Directory | What belongs here |
|-------|-----------|-------------------|
| Core engine | `kmp-repair-pipeline/` | All repair, localization, validation, evaluation, and agent logic |
| Web adapter | `fullstack/backend/` | FastAPI routes, RQ worker, job orchestration — imports from pipeline |
| Frontend | `fullstack/frontend/` | Next.js UI — calls backend REST/SSE only, never pipeline directly |

**Never duplicate logic across layers.**  If pipeline logic appears in the
backend, it is a bug.  If the backend is called directly from the frontend
without going through `lib/api.ts`, it is a bug.

---

## Shared database

Both the canonical pipeline CLI and the backend web adapter share the same
PostgreSQL 15 database.  Migrations are managed exclusively by the pipeline:

```bash
cd kmp-repair-pipeline && alembic upgrade head
```

Never add migration files to `fullstack/backend/`.

---

## Development stack

- Python ≥ 3.10 (pinned to 3.12 via `.python-version`)
- Node.js / npm for the frontend
- PostgreSQL 15 + Redis 7 (via Docker Compose)
- Java 21 (JDK, for Gradle builds — Java 25 is not supported)
- Android SDK (optional — targets marked `NOT_RUN_ENVIRONMENT_UNAVAILABLE` when absent)

---

## What NOT to do (project-wide)

- Do not add a fourth LLM agent to the pipeline.
- Do not duplicate repair/validation/evaluation logic into the backend.
- Do not call the pipeline Python code directly from the frontend.
- Do not run schema changes by hand — always use Alembic.
- Do not lower version aliases in `libs.versions.toml` when applying patches.
- Do not use Java 25 — Kotlin compiler has a hard incompatibility.
- Do not push to main without running the unit test suites.
