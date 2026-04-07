# kmp-repair

**A Multi-Agent System to Repair Breaking Changes Caused by Dependency Updates in Kotlin Multiplatform**

Master's thesis implementation — Santiago Bobadilla Suarez

---

## Overview

When a Dependabot pull request updates a dependency in a Kotlin Multiplatform (KMP) repository,
the resulting build failure propagates differently across shared, Android, and iOS source sets.
A patch that compiles in `commonMain` may still break `androidMain` or `iosMain` because
`expect`/`actual` contracts must stay aligned across all declared targets.

This system addresses that problem through a five-stage evidence-and-decision pipeline:
ingest and type the update, capture before/after execution evidence, localize impact,
synthesize a patch, and validate plus explain outcomes without relying on conversational memory.

---

## System Architecture

```
 Dependabot PR (GitHub)
         |
         | GitHub API
         v
+========================================+
|         kmp-repair-pipeline            |
|   Canonical pipeline — core engine     |
|                                        |
|  Stage 1: Ingest & Type Update         |  UpdateEvidence
|    discover -> ingest -> build-case    |
|                                        |
|  Stage 2: Before/After Execution       |  ExecutionEvidence
|    run-before-after                    |
|    shared | android | ios targets      |
|                                        |
|  Stage 3: Hybrid Localization          |  StructuralEvidence
|    analyze-case -> localize            |  LocalizationResult
|    [LocalizationAgent]                 |
|                                        |
|  Stage 4: Patch Synthesis              |  PatchAttempt x 4
|    repair (4 baselines)                |
|    [RepairAgent]                       |
|                                        |
|  Stage 5: Validation & Explanation     |  ValidationEvidence
|    validate -> explain                 |  ExplanationEvidence
|    [ExplanationAgent]                  |
|                                        |
|  Eval: metrics -> report               |  EvaluationMetric rows
+========================================+
         |                   |
         | editable install  | shared PostgreSQL + artifact store
         |                   |
+====================+   +=====================+
|  fullstack/backend |   |  PostgreSQL 15       |
|  FastAPI + RQ      |   |  (managed by         |
|                    |   |   Alembic)           |
|  REST: 13 routes   |   +=====================+
|  SSE:   2 streams  |
+========+===========+
         |
         | HTTP / SSE
         |
+====================+
|  fullstack/frontend |
|  Next.js 14         |
|                     |
|  /           Home   |
|  /process    Run    |
|  /cases      List   |
|  /results    Metrics|
|  /environment Env   |
|  /about      Thesis |
+====================+
```

---

## Repository Layout

```
Desarrollo/
|
|-- kmp-repair-pipeline/          Core engine (thesis implementation)
|   |-- src/kmp_repair_pipeline/
|   |   |-- cli/                  CLI entry points (kmp-repair)
|   |   |-- ingest/               Stage 1: update ingestion & typing
|   |   |-- case_builder/         Stage 1/2: workspace construction
|   |   |-- runners/              Stage 2: Gradle execution + error parsing
|   |   |-- static_analysis/      Stage 3: KMP-aware AST analysis
|   |   |-- localization/         Stage 3: hybrid localization + LocalizationAgent
|   |   |-- repair/               Stage 4: patch synthesis + RepairAgent
|   |   |-- baselines/            Stage 4: four repair baseline modes
|   |   |-- validation/           Stage 5: multi-target validation
|   |   |-- explanation/          Stage 5: ExplanationAgent
|   |   |-- evaluation/           Metrics: BSR, CTSR, FFSR, EFR, Hit@k
|   |   |-- reporting/            CSV / JSON / Markdown export
|   |   |-- domain/               Pure domain types (no I/O)
|   |   |-- case_bundle/          Typed Case Bundle model
|   |   |-- storage/              DB layer (SQLAlchemy 2.0 + Alembic)
|   |   `-- utils/                LLM provider, logging, hashing, JSON I/O
|   |-- migrations/               Alembic migration files (schema source of truth)
|   |-- tests/
|   |   |-- unit/                 Unit tests (no Docker required)
|   |   `-- integration/          Integration tests (requires Docker)
|   |-- data/
|   |   |-- artifacts/            Runtime: cloned workspaces + execution output (gitignored)
|   |   `-- reports/              Runtime: generated CSV/JSON/MD reports (gitignored)
|   |-- scripts/
|   |   |-- bootstrap_env.sh      Auto-detect JAVA_HOME, ANDROID_HOME, GCP credentials
|   |   `-- run_e2e.sh            Full end-to-end pipeline runner
|   |-- docker-compose.yml        PostgreSQL 15 only
|   |-- pyproject.toml
|   `-- alembic.ini
|
|-- fullstack/
|   |-- backend/                  Web API adapter
|   |   |-- src/kmp_repair_webapi/
|   |   |   |-- app.py            FastAPI routes (13 REST + 2 SSE)
|   |   |   |-- worker.py         RQ worker entrypoint
|   |   |   |-- job_runner.py     Job enqueueing + RQ execution loop
|   |   |   |-- orchestrator.py   Stage dispatch -> canonical pipeline
|   |   |   |-- queries.py        DB read helpers for web responses
|   |   |   |-- stages.py         Stage vocabulary + param validation
|   |   |   |-- schemas.py        Pydantic request/response schemas
|   |   |   `-- settings.py       Environment-driven configuration
|   |   |-- scripts/
|   |   |   |-- reset_case.py     Reset case to INGESTED status (preserves workspace)
|   |   |   `-- run_e2e.sh        End-to-end API test (no mocking)
|   |   |-- tests/
|   |   |-- docker-compose.yml    PostgreSQL 15 + Redis 7
|   |   |-- .env.example
|   |   `-- pyproject.toml
|   |
|   `-- frontend/                 Editorial frontend
|       |-- app/
|       |   |-- page.tsx          Home: pipeline status (SSE) + recent cases
|       |   |-- process/          Ingest + run + live console (SSE)
|       |   |-- cases/            Case listing + detail with evidence timeline
|       |   |-- results/          Metrics dashboard + D3 charts + export
|       |   |-- environment/      Runtime health checks + configuration
|       |   `-- about/            Static thesis framing
|       |-- components/
|       |   |-- chrome/           SiteHeader (fixed nav + mobile menu)
|       |   |-- case/             UnifiedDiffViewer
|       |   |-- reports/          ResultsD3Panel, ReportsPlots
|       |   |-- LiveJobConsole    SSE log viewer
|       |   `-- ActiveRunsStrip   SSE active jobs indicator
|       |-- lib/
|       |   |-- api.ts            All backend API calls (single source of truth)
|       |   |-- types.ts          TypeScript types for all API responses
|       |   |-- constants.ts      Vocabulary: stages, modes, targets, providers
|       |   |-- thesis-framework  Thesis labels, retry budgets, core principle
|       |   `-- ui.ts             Display label helpers
|       |-- tests/
|       |   |-- unit/             Vitest unit tests
|       |   `-- e2e/              Playwright end-to-end tests
|       |-- package.json
|       `-- tailwind.config.ts
|
|-- CLAUDE.md                     Project-wide AI assistant rules
`-- README.md                     This file
```

---

## Repair Baselines

The pipeline benchmarks four repair strategies to measure how much context helps:

```
Baseline            Context given to RepairAgent              Retry budget
------------------  ----------------------------------------  ------------
raw_error           Dep diff + raw compiler errors only             2
context_rich        + Localized files + source-set info + catalog   3
iterative_agentic   Same as context_rich + previous-attempt feedback 4
full_thesis         Full Case Bundle evidence + all previous attempts 5
```

All baselines share the same retry loop with in-loop validation: after each
APPLIED patch, `validate()` runs immediately. VALIDATED exits the loop;
REJECTED with progress feeds remaining errors into the next attempt.

---

## Evaluation Metrics

```
Metric               Definition
-------------------  --------------------------------------------------------
BSR                  Build Success Rate: fraction of cases where post-repair
                     validation finishes at SUCCESS_REPOSITORY_LEVEL.
CTSR                 Cross-Target Success Rate: fraction of cases where no
                     runnable target has FAILED_BUILD.
FFSR                 File Fix Success Rate: fraction of broken files repaired.
EFR                  Error Fix Rate: penalty-adjusted fraction of original
                     compile/test errors eliminated.
EFR_normalized       Same as EFR but dedup key omits line number (conservative).
Hit@k                Localization precision: overlap between localized files and
                     ground-truth files at rank k (k = 1, 3, 5).
source_set_accuracy  Fraction of candidates correctly attributed to
                     shared/platform/build source sets.
```

`NOT_RUN_ENVIRONMENT_UNAVAILABLE` targets are excluded from BSR/CTSR/FFSR.
EFR uses a penalty formula to prevent score inflation from error explosion.

---

## Case Lifecycle

```
CREATED
  |
  v (build-case)
SHADOW_BUILT
  |
  v (run-before-after)
EXECUTED ---------> NO_ERRORS_TO_FIX --> EVALUATED
  |                 (non-breaking update shortcut)
  v (localize)
LOCALIZED
  |
  v (repair)
PATCH_ATTEMPTED
  |
  v (validate)
VALIDATED
  |
  v (explain)
EXPLAINED
  |
  v (metrics)
EVALUATED

Any stage --> FAILED on unrecoverable error
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | >= 3.12 (pinned) | Use pyenv or mise |
| Node.js | >= 18 | For the frontend |
| Java | 21 (Temurin) | Java 25 not supported (Kotlin compiler limitation) |
| Docker | >= 24 | PostgreSQL 15 + Redis 7 via Compose |
| Android SDK | any | Optional — targets marked unavailable if absent |
| PostgreSQL | 15 | Via Docker Compose |
| Redis | 7 | Via Docker Compose (backend only) |

**LLM provider** (one of):
- Anthropic — set `ANTHROPIC_API_KEY`
- Google Vertex AI — set `GOOGLE_APPLICATION_CREDENTIALS` + GCP project

---

## Setup

### 1. Clone and enter the repository

```bash
git clone <repo-url>
cd Desarrollo
```

### 2. Pipeline setup

```bash
cd kmp-repair-pipeline

# Start PostgreSQL
docker compose up -d

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install pipeline in editable mode
pip install -e ".[dev]"

# Apply database migrations
alembic upgrade head

# Configure environment (copy and fill in required values)
cp .env.example .env    # if .env.example exists, otherwise copy from README

# Auto-detect Java 21, Android SDK, GCP credentials
source scripts/bootstrap_env.sh

# Verify setup
kmp-repair doctor
```

### 3. Backend setup

```bash
cd ../fullstack/backend

# Start PostgreSQL + Redis
docker compose up -d

# Install dependencies (pipeline must be installed first)
pip install -e ../../kmp-repair-pipeline
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env: set KMP_DATABASE_URL, KMP_REDIS_URL, LLM provider, etc.

# Migrations are managed by the pipeline — run from there
cd ../../kmp-repair-pipeline && alembic upgrade head
```

### 4. Frontend setup

```bash
cd fullstack/frontend

npm install

# Configure backend URL
echo 'NEXT_PUBLIC_API_BASE_URL=http://localhost:8000' > .env.local
```

---

## Running the Full Stack

```bash
# Terminal 1: Infrastructure (from fullstack/backend)
docker compose up -d

# Terminal 2: Migrations (from kmp-repair-pipeline)
alembic upgrade head

# Terminal 3: Backend API (from fullstack/backend)
source .venv/bin/activate
kmp-repair-api              # listens on http://localhost:8000

# Terminal 4: RQ Worker (from fullstack/backend)
kmp-repair-worker

# Terminal 5: Frontend (from fullstack/frontend)
npm run dev                 # listens on http://localhost:3000
```

Open http://localhost:3000.

---

## Running the Pipeline (CLI)

```bash
# Activate the pipeline environment
cd kmp-repair-pipeline
source .venv/bin/activate
source scripts/bootstrap_env.sh

# Ingest a Dependabot PR
kmp-repair ingest --pr-url https://github.com/owner/repo/pull/42

# Build the repair case
kmp-repair build-case <case_id>

# Capture before/after build evidence
kmp-repair run-before-after <case_id>

# Analyze structure and localize impact
kmp-repair analyze-case <case_id>
kmp-repair localize <case_id>

# Repair using all four baselines
kmp-repair repair <case_id> --all-baselines

# Validate and explain
kmp-repair validate <case_id>
kmp-repair explain <case_id>

# Compute metrics and export report
kmp-repair metrics <case_id>
kmp-repair report --format all
```

---

## Testing

```bash
# Pipeline unit tests (no Docker required)
cd kmp-repair-pipeline
pytest tests/unit/ -v

# Pipeline integration tests (requires Docker)
pytest tests/integration/ -v

# Backend unit + endpoint tests (no Docker required)
cd fullstack/backend
pytest tests/ -v

# Frontend unit tests
cd fullstack/frontend
npm test

# Frontend e2e tests (requires running server)
npm run test:e2e

# Full backend end-to-end (starts API + worker, runs pipeline, verifies results)
cd fullstack/backend
./scripts/run_e2e.sh
```

---

## Environment Variables

The backend `.env` drives all shared configuration.  See `fullstack/backend/.env.example`
for the complete reference.  Key variables:

```
KMP_DATABASE_URL              PostgreSQL DSN
KMP_REDIS_URL                 Redis URL for the RQ worker
KMP_ARTIFACT_BASE             Absolute path to the artifact store (data/artifacts/)
KMP_LLM_PROVIDER              anthropic | vertex
KMP_LLM_MODEL                 Model ID (e.g. claude-sonnet-4-5, gemini-2.5-flash)
JAVA_HOME                     Path to JDK 21 (auto-detected on macOS via bootstrap_env.sh)
ANDROID_HOME                  Path to Android SDK (optional)
GOOGLE_APPLICATION_CREDENTIALS  Path to GCP service account JSON (Vertex only)
ANTHROPIC_API_KEY             API key (Anthropic only)
```

---

## Key Design Decisions

**Evidence-and-decision, not conversational memory.**
All pipeline state is persisted in a typed Case Bundle backed by PostgreSQL.
Agents receive structured context objects, not a growing chat history.

**Workspace isolation.**
Each repair attempt resets the cloned repository to HEAD before applying a patch.
Cross-mode contamination is impossible.

**Honesty over optimism.**
When a target cannot run (no Android SDK, no iOS simulator), the pipeline records
`NOT_RUN_ENVIRONMENT_UNAVAILABLE` instead of counting the target as passing.
Metrics exclude unavailable targets from their denominators.

**Three fixed agents.**
LocalizationAgent re-ranks file candidates.
RepairAgent outputs a unified diff or `PATCH_IMPOSSIBLE`.
ExplanationAgent produces structured JSON + Markdown for reviewer consumption.
No fourth agent will be added.

**Anti-downgrade scanner.**
Any patch that lowers a version alias in `libs.versions.toml` is rejected before
`git apply` runs. The agent system prompt also enforces this rule explicitly.

---

## Known Limitations

- Error parsing covers Kotlin 2.x output format. Format changes in future compiler
  versions may silently degrade `required_kotlin_version` extraction.
- File content sent to RepairAgent is capped at 8 000 bytes per file.
- Only the top-k (default 5) localized files reach RepairAgent.
- iOS validation requires a macOS host with Xcode installed.
- Transitive dependency conflicts (diamond dependencies) have no structured repair strategy.
