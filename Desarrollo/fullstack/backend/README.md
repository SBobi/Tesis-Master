# kmp-repair-webapi -- Backend Adapter

Integration layer between the frontend and the canonical pipeline.
All repair, localization, validation, and evaluation logic lives in:

```
../../kmp-repair-pipeline/   <-- source of truth (never duplicate from here)
```

This package exposes a FastAPI + RQ web interface over that logic.

---

## System architecture

```
 Browser / Frontend
       |
       | HTTP / SSE
       v
+------------------+
|  FastAPI app     |  app.py -- 14 REST + SSE endpoints
|  (kmp_repair_    |
|   webapi)        |
+--------+---------+
         |  enqueue job
         v
+------------------+     +------------------+
|  Redis queue     |<--->|  RQ Worker       |  worker.py
|  (kmp_pipeline)  |     |  (kmp-repair-    |
+------------------+     |   worker)        |
                         +--------+---------+
                                  | imports
                                  v
                    +-------------------------------+
                    |  kmp_repair_pipeline.*        |
                    |  (canonical pipeline,         |
                    |   editable local dep)         |
                    +-------------------------------+
                                  |
                         reads/writes
                                  v
                    +-------------------------------+
                    |  PostgreSQL 15                |
                    |  (same DB as CLI pipeline)    |
                    +-------------------------------+
                    |  Local artifact store         |
                    |  data/artifacts/<case_id>/    |
                    +-------------------------------+
```

---

## Request flow for a pipeline run

```
 POST /api/cases/{id}/jobs/pipeline
         |
         v
   app.py: validate case exists (RepairCaseRepo)
         |
         v
   job_runner.py: sanitize params (stages.py)
         |         create PipelineJob row in DB
         |         enqueue RQ job (get_queue().enqueue)
         |
         v
   Redis queue <-- job waiting
         |
         v (worker picks up)
   job_runner.py: execute_pipeline_job()
         |
         for stage in planned_stages:
           |
           v
       orchestrator.py: run_stage_with_audit()
           |  writes STAGE_STARTED transition
           |
           v
       orchestrator.py: _run_stage_impl()
           |  calls kmp_repair_pipeline.<stage>()
           |
           v
       orchestrator.py: writes STAGE_COMPLETED transition
         |
         v
   PipelineJob.status = SUCCEEDED / FAILED
         |
         v
   GET /api/jobs/{job_id}  <-- frontend polls this
```

---

## Package layout

```
fullstack/backend/
|-- scripts/
|   |-- run_e2e.sh        end-to-end test script (no mocking)
|   `-- reset_case.py     reset a case to INGESTED status
|-- src/kmp_repair_webapi/
|   |-- app.py            FastAPI routes (14 endpoints)
|   |-- worker.py         RQ worker entrypoint
|   |-- job_runner.py     job enqueueing + RQ execution loop
|   |-- orchestrator.py   stage dispatch -> kmp_repair_pipeline.*
|   |-- queries.py        DB read queries for web responses
|   |-- stages.py         stage vocabulary + param validation
|   |-- schemas.py        Pydantic request/response schemas
|   |-- settings.py       environment-driven configuration
|   |-- queue.py          Redis/RQ connection helpers
|   `-- env_loader.py     .env loader (web-layer utility)
|-- tests/
|   |-- test_webapi.py    unit tests (stages, orchestrator -- no DB)
|   `-- test_endpoints.py endpoint tests (all 14 routes -- no DB, no Redis)
|-- docker-compose.yml    Postgres 15 + Redis 7
|-- .env.example          environment variable template
`-- pyproject.toml
```

Pipeline modules imported (read-only, never duplicated here):

```
kmp_repair_pipeline.ingest.*
kmp_repair_pipeline.storage.*
kmp_repair_pipeline.case_builder.*
kmp_repair_pipeline.runners.*
kmp_repair_pipeline.static_analysis.*
kmp_repair_pipeline.localization.*
kmp_repair_pipeline.repair.*
kmp_repair_pipeline.baselines.*
kmp_repair_pipeline.validation.*
kmp_repair_pipeline.explanation.*
kmp_repair_pipeline.evaluation.*
kmp_repair_pipeline.reporting.*
kmp_repair_pipeline.utils.*
```

---

## Installation

### 1. Start infrastructure

```bash
# From the canonical pipeline directory (shares DB + Redis with CLI):
cd ../../kmp-repair-pipeline
docker compose up -d

# Or from this directory (standalone containers):
cd fullstack/backend
docker compose up -d
```

### 2. Create a virtual environment (Python 3.11+)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
# Canonical pipeline must come first (all business logic lives here):
pip install -e ../../kmp-repair-pipeline

# Then the web adapter:
pip install -e .

# Test dependencies:
pip install pytest httpx
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env -- minimum required keys:
#   KMP_DATABASE_URL   postgresql+psycopg2://...
#   KMP_REDIS_URL      redis://localhost:6379/0
#   KMP_ARTIFACT_BASE  absolute path to pipeline data/artifacts/
#   KMP_LLM_PROVIDER   vertex  (or anthropic)
#   GOOGLE_APPLICATION_CREDENTIALS  path to service-account.json
```

### 5. Run DB migrations (managed by canonical pipeline)

```bash
cd ../../kmp-repair-pipeline
alembic upgrade head
```

---

## Running locally

### API server

```bash
# From fullstack/backend/:
uvicorn kmp_repair_webapi.app:app --host 0.0.0.0 --port 8000

# or via entry point:
kmp-repair-api
```

### RQ worker (separate terminal)

```bash
kmp-repair-worker
```

### Health check

```bash
curl http://localhost:8000/api/health
```

---

## End-to-end test

`scripts/run_e2e.sh` runs a complete pipeline cycle against real infrastructure
with no mocking.  It starts and stops the API server and worker automatically.

```bash
cd fullstack/backend

# Run with the default Ktor case (pull/1):
./scripts/run_e2e.sh

# Explicit case:
./scripts/run_e2e.sh 3407b237-981f-40da-9623-4c4ac3c2087b

# Start from a later stage (case must be at least INGESTED):
./scripts/run_e2e.sh --start-from localize

# Different repair mode:
./scripts/run_e2e.sh --mode raw_error

# Keep API + worker running after test:
./scripts/run_e2e.sh --keep
```

The script performs steps in order:

```
1. Check prerequisites (Docker containers, .env, packages)
2. Reset case to INGESTED via scripts/reset_case.py
3. Kill any existing process on port 8000
4. Start API server in background
5. Start RQ worker in background
6. POST /api/cases/{id}/jobs/pipeline
7. Poll GET /api/jobs/{id} every 10s until SUCCEEDED / FAILED
8. Print stage-by-stage duration table
9. Verify all stages COMPLETED + metrics recorded
   Smoke-test all 14 endpoints with real data
10. Stop API + worker (unless --keep)
```

### Case reset utility

`scripts/reset_case.py` clears all pipeline-run data for a case (execution
runs, patch attempts, validations, explanations, metrics, localization
candidates, source entities, agent logs, job records, transitions) while
preserving the ingest record and cloned revision workspaces.

```bash
# Reset a case:
python scripts/reset_case.py 3407b237-981f-40da-9623-4c4ac3c2087b

# Dry run (show what would be deleted):
python scripts/reset_case.py 3407b237-981f-40da-9623-4c4ac3c2087b --dry-run
```

---

## API endpoints

```
Method  Path                                    Description
------  --------------------------------------  ---------------------------
GET     /api/health                             Health check
POST    /api/cases                              Create case from PR URL
GET     /api/cases                              List cases (filterable)
GET     /api/cases/{case_id}                    Case detail + full evidence
GET     /api/cases/{case_id}/history            Job + transition history
POST    /api/cases/{case_id}/jobs/stage         Enqueue a single stage
POST    /api/cases/{case_id}/jobs/pipeline      Enqueue full pipeline
GET     /api/jobs/{job_id}                      Job detail
POST    /api/jobs/{job_id}/cancel               Request job cancellation
GET     /api/jobs/{job_id}/logs                 Tail job log file
GET     /api/jobs/{job_id}/stream               SSE stream (status + logs)
GET     /api/stream/active                      SSE stream of active jobs
GET     /api/cases/{case_id}/artifact-content   Read artifact file content
GET     /api/reports/compare                    Compare metrics by mode
```

### Query parameters

`GET /api/cases` accepts:
- `status` -- filter by case status (e.g. `EVALUATED`)
- `update_class` -- filter by dependency update class
- `repo` -- substring match on repository URL / name / owner
- `date_from` -- ISO 8601 datetime, created_at lower bound
- `repair_mode` -- filter to cases that have a patch attempt in this mode

`GET /api/jobs/{job_id}/logs` accepts:
- `tail` -- number of lines to return (20..2000, default 200)

`GET /api/cases/{case_id}/artifact-content` accepts:
- `path` -- relative path within the case artifact directory (required)
- `max_bytes` -- truncation limit in bytes (1000..500000, default 100000)

`GET /api/reports/compare` accepts:
- `modes` -- comma-separated repair mode names
- `case_id` -- restrict to a single case

---

## Environment variables

```
Variable                    Default                              Description
--------------------------  -----------------------------------  --------------------------
KMP_DATABASE_URL            postgresql+psycopg2://kmp_repair:    PostgreSQL DSN (psycopg2)
                            kmp_repair_dev@localhost:5432/
                            kmp_repair
DATABASE_URL                postgresql://...                     Alternate DSN (no driver)
KMP_REDIS_URL               redis://localhost:6379/0             Redis URL for RQ
KMP_RQ_QUEUE                kmp_pipeline                        Queue name
KMP_RQ_DEFAULT_TIMEOUT_S    10800                               Job timeout in seconds
KMP_WEB_API_HOST            0.0.0.0                             API bind address
KMP_WEB_API_PORT            8000                                API port
KMP_WEB_CORS_ORIGINS        http://localhost:3000               Allowed CORS origins
KMP_ARTIFACT_BASE           data/artifacts                      Artifact store root (abs)
KMP_REPORT_OUTPUT_DIR       data/reports                        Report output root (abs)
KMP_LLM_PROVIDER            vertex                              LLM provider
KMP_LLM_MODEL               gemini-2.5-flash                    Model ID
KMP_VERTEX_PROJECT          (required for vertex)               GCP project ID
KMP_VERTEX_LOCATION         us-central1                         GCP region
GOOGLE_APPLICATION_CREDENTIALS  (required for vertex)           Path to service account JSON
ANTHROPIC_API_KEY           (required for anthropic)            Anthropic API key
JAVA_HOME                   (auto-detected on macOS)            Path to JDK 21
```

The worker auto-detects macOS and sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`
and `JAVA_HOME` via `/usr/libexec/java_home -v 21`.  Override `JAVA_HOME` in
`.env` if auto-detection fails.

---

## Tests

### Unit tests (no Docker required)

```bash
pytest tests/ -v
```

`tests/test_webapi.py` covers stage param validation and orchestrator dispatch.
`tests/test_endpoints.py` covers all 14 API endpoints with mocked dependencies.

### End-to-end test (requires Docker + .env)

```bash
./scripts/run_e2e.sh
```

---

## Stage parameter reference

Each stage accepts a specific set of params via `params_by_stage`.
Unknown keys are rejected with HTTP 400.

```
Stage             Accepted params
----------------  ---------------------------------------------------
build-case        artifact_base, work_dir, overwrite
run-before-after  artifact_base, targets, timeout_s
analyze-case      (none)
localize          artifact_base, no_agent, top_k, provider, model
repair            artifact_base, mode, top_k, patch_strategy,
                  force_patch_attempt, provider, model
validate          artifact_base, attempt_id, targets, timeout_s
explain           artifact_base, provider, model
metrics           ground_truth
report            output_dir, format, modes, cases
```

Repair modes: `raw_error`, `context_rich`, `iterative_agentic`, `full_thesis`
Patch strategies: `single_diff`, `chain_by_file`
Targets: `shared`, `android`, `ios`, `jvm`

---

## Troubleshooting

**ModuleNotFoundError: kmp_repair_pipeline**
Run: `pip install -e ../../kmp-repair-pipeline`

**redis.exceptions.ConnectionError**
Redis is not running. Run: `docker compose up -d redis`

**macOS fork safety error in worker**
The worker auto-sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`.
If the error persists, set it in `.env` before starting the worker.

**DB migration not found**
Migrations are managed by the canonical pipeline:
`cd ../../kmp-repair-pipeline && alembic upgrade head`

**Port 8000 already in use**
`./scripts/run_e2e.sh` kills the existing process automatically.
Manual: `lsof -ti tcp:8000 | xargs kill`

**Job stuck in QUEUED**
The worker is not running. Check `/tmp/kmp_worker_e2e.log` or start
`kmp-repair-worker` in a separate terminal.
