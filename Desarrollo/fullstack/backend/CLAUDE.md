# kmp-repair-webapi -- CLAUDE.md

## What this package is

A web API adapter only.  It does NOT contain pipeline business logic.

The canonical pipeline lives at:
```
../../kmp-repair-pipeline/   <-- DO NOT duplicate from here
```

Installed as editable dependency: `pip install -e ../../kmp-repair-pipeline`

---

## Rules (non-negotiable)

### Do NOT duplicate pipeline core

The following modules must NEVER be copied into this package:
- `kmp_repair_pipeline.ingest`
- `kmp_repair_pipeline.case_builder`
- `kmp_repair_pipeline.runners`
- `kmp_repair_pipeline.static_analysis`
- `kmp_repair_pipeline.localization`
- `kmp_repair_pipeline.repair`
- `kmp_repair_pipeline.baselines`
- `kmp_repair_pipeline.validation`
- `kmp_repair_pipeline.explanation`
- `kmp_repair_pipeline.evaluation`
- `kmp_repair_pipeline.reporting`
- `kmp_repair_pipeline.domain`
- `kmp_repair_pipeline.case_bundle`
- `kmp_repair_pipeline.storage`
- `kmp_repair_pipeline.cli`

If you need to call pipeline logic, import it.  Never copy it.

### Canonical imports pattern

All pipeline imports in this package use:
```python
from kmp_repair_pipeline.X.Y import Z
```

Never use relative imports to reach pipeline code.

### What belongs in this package

Only the web/orchestration layer:
```
app.py          -- FastAPI routes (14 endpoints)
worker.py       -- RQ worker entrypoint (macOS bootstrap + Java 21 detection)
job_runner.py   -- job enqueueing + RQ execution loop
orchestrator.py -- dispatches stages to pipeline functions
queries.py      -- DB read queries for web responses (uses pipeline models)
stages.py       -- stage vocabulary + param validation (no business logic)
schemas.py      -- Pydantic request/response schemas
settings.py     -- environment-driven configuration
queue.py        -- Redis/RQ connection helpers
env_loader.py   -- .env loader (web-layer utility, not in canonical pipeline)

scripts/
  reset_case.py -- standalone tool: clears all pipeline-run data for a case
  run_e2e.sh    -- end-to-end test: starts services, runs pipeline, verifies
```

### Database and migrations

- All schema changes go through Alembic in `../../kmp-repair-pipeline/migrations/`
- Never add migration files here
- Run migrations from the canonical pipeline directory:
  `cd ../../kmp-repair-pipeline && alembic upgrade head`

### Adding new endpoints

1. Add route to `app.py`
2. Add query helpers to `queries.py` if needed (import models from
   `kmp_repair_pipeline.storage.models`)
3. Add Pydantic schemas to `schemas.py`
4. Add endpoint test to `tests/test_endpoints.py` (mock at
   `kmp_repair_webapi.app.*` level)
5. Never implement pipeline logic inside route handlers

### Adding new stage support

1. Add stage name to `stages.py` vocabulary lists (`PIPELINE_STAGES`,
   `CASE_RUNNABLE_STAGES`)
2. Add param validation block to `sanitize_stage_params()`
3. Add `command_for_stage()` branch
4. Add dispatch block to `_run_stage_impl()` in `orchestrator.py`
5. The dispatch calls the canonical pipeline function -- never reimplements it

---

## Scripts

### scripts/reset_case.py

Clears all pipeline-run data for a case, resetting it to INGESTED status.
Preserves: ingest record, dependency event, repository row, cloned workspaces.
Deletes: execution_runs, task_results, error_observations, patch_attempts,
         validation_runs, explanations, evaluation_metrics, source_entities,
         localization_candidates, agent_logs, pipeline_jobs,
         case_status_transitions.

Handles all foreign-key constraints in the correct order.

```bash
python scripts/reset_case.py <case_id>
python scripts/reset_case.py <case_id> --dry-run
```

### scripts/run_e2e.sh

Runs the full pipeline through the real API + worker without mocking.
Handles all setup and teardown automatically -- no manual steps required.

```bash
./scripts/run_e2e.sh                          # Ktor default case
./scripts/run_e2e.sh <case_id>               # specific case
./scripts/run_e2e.sh --start-from localize   # start from a later stage
./scripts/run_e2e.sh --mode raw_error        # single repair mode
./scripts/run_e2e.sh --keep                  # leave API + worker running
```

Steps performed:
1. Verify Docker containers + .env + installed packages
2. Reset case to INGESTED (calls reset_case.py)
3. Kill any process on port 8000
4. Start API server in background
5. Start RQ worker in background
6. POST /api/cases/{id}/jobs/pipeline
7. Poll GET /api/jobs/{id} until SUCCEEDED / FAILED
8. Print stage-by-stage duration table
9. Verify timeline COMPLETED + metrics recorded
10. Smoke-test all 14 endpoints with real data
11. Stop API + worker

---

## FK deletion order (discovered in production schema)

When deleting pipeline-run data for a case, this order is mandatory:

```
error_observations  (references task_results)
task_results        (references execution_runs)
validation_runs     (references execution_runs AND patch_attempts)
explanations        (references patch_attempts)
execution_runs
patch_attempts
localization_candidates  (references source_entities)
expect_actual_links      (references source_entities)
source_entities
agent_logs
evaluation_metrics
case_status_transitions  (references pipeline_jobs)
pipeline_jobs (nullify parent_job_id first -- self-reference)
pipeline_jobs DELETE
repair_cases UPDATE status = 'INGESTED'
```

See `scripts/reset_case.py` for the canonical implementation.

---

## Allowed minimal changes in canonical pipeline

If a canonical pipeline change is strictly required for web integration:
1. Document the change here with: what, why, impact, validation
2. Keep the change minimal and non-breaking
3. The change must not duplicate web logic into the CLI path

Known necessary gap:
- `utils/env_loader.py` is not in canonical pipeline v2.
  Resolution: bundled as `kmp_repair_webapi/env_loader.py`
  (not a pipeline concern; web-layer startup only).

---

## Development commands

```bash
# Start infrastructure (use pipeline's docker-compose to share DB)
cd ../../kmp-repair-pipeline && docker compose up -d

# Install deps
pip install -e ../../kmp-repair-pipeline
pip install -e .
pip install pytest httpx

# Run DB migrations
cd ../../kmp-repair-pipeline && alembic upgrade head

# Start API server
uvicorn kmp_repair_webapi.app:app --reload --port 8000

# Start worker (separate terminal)
kmp-repair-worker

# Run unit + endpoint tests (no Docker)
pytest tests/ -v

# Run full e2e (requires Docker + .env)
./scripts/run_e2e.sh
```

---

## Test coverage

### Unit tests (test_webapi.py)

- `sanitize_stage_params` allowlist enforcement
- `sanitize_pipeline_request` stage normalization
- `command_for_stage` CLI preview generation
- `_run_stage_impl` metrics serialization (mocked pipeline)
- `GET /api/health`

### Endpoint tests (test_endpoints.py)

All 14 endpoints covered with mocked DB and pipeline:
- Happy path and error cases (404, 400, 422)
- Path traversal blocked by artifact-content endpoint
- SSE stream content-type verification
- Query param validation (tail range, max_bytes range)
- Filter forwarding (status, repo, update_class)

Run: `pytest tests/ -v` (50 tests, no Docker required)
