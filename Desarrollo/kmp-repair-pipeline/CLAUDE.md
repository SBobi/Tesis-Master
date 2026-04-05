# kmp-repair-pipeline — CLAUDE.md

## What this is

A multi-agent pipeline to **repair breaking changes caused by dependency updates in Kotlin Multiplatform (KMP)** repositories.
Thesis: *"A Multi-Agent System to Repair Breaking Changes Caused by Dependency Updates in Kotlin Multiplatform"*

All 13 phases are implemented and tested (291/291 unit tests passing).

---

## Implementation status

| Phase | Module | CLI command | Tests |
|-------|--------|-------------|-------|
| 0–3 | domain, storage, case_bundle | db-upgrade, db-seed | test_domain, test_case_bundle |
| 4 | ingest | discover, ingest | test_ingest_phase4 |
| 5 | case_builder | build-case | test_case_builder_phase5 |
| 6 | runners | run-before-after | test_runners_phase6 |
| 7 | static_analysis | analyze-case | test_structural_builder_phase7 |
| 8 | localization | localize | test_localization_phase8 |
| 9 | repair, baselines | repair | test_repair_phase9 |
| 10 | validation | validate | test_validation_phase10 |
| 11 | explanation | explain | test_explanation_phase11 |
| 12 | evaluation | metrics | test_evaluation_phase12 |
| 13 | reporting | report | test_reporting_phase13 |

---

## Pipeline stages (thesis mapping)

```
Stage 1: discover → ingest → build-case       (UpdateEvidence)
Stage 2: run-before-after                     (ExecutionEvidence)
Stage 3: analyze-case → localize              (StructuralEvidence → RepairEvidence.localization)
Stage 4: repair [--mode | --all-baselines]    (RepairEvidence.patch_attempts)
Stage 5: validate → explain                   (ValidationEvidence + ExplanationEvidence)
Eval:    metrics → report                     (EvaluationMetric rows → CSV/JSON/MD)
```

---

## Three LLM agents (fixed, do not add more)

| Agent | Module | Role |
|-------|--------|------|
| LocalizationAgent | localization/localization_agent.py | Re-ranks deterministic candidates; JSON output |
| RepairAgent | repair/repair_agent.py | Outputs unified diff or PATCH_IMPOSSIBLE |
| ExplanationAgent | explanation/explanation_agent.py | Structured JSON + Markdown explanation |

All agents: temperature=0, logged to agent_logs, fallback on parse failure, never access DB/filesystem directly.

---

## Four repair baselines (fixed vocabulary)

| Mode | Context |
|------|---------|
| `raw_error` | dep diff + raw compiler errors only |
| `context_rich` | + localized files + source-set info |
| `iterative_agentic` | context_rich + retry loop (max 3 attempts) |
| `full_thesis` | full Case Bundle evidence + previous attempts |

---

## Six evaluation metrics

| Metric | Passing condition |
|--------|------------------|
| BSR | overall ValidationStatus == SUCCESS_REPOSITORY_LEVEL |
| CTSR | no runnable target has FAILED_BUILD |
| FFSR | all runnable targets == SUCCESS_REPOSITORY_LEVEL |
| EFR | fraction of original errors eliminated (None if no originals) |
| Hit@k | any ground-truth file in top-k candidates (None if no gt) |
| source_set_accuracy | fraction of candidates with correct source_set (None if no gt) |

NOT_RUN_ENVIRONMENT_UNAVAILABLE targets are excluded from BSR/CTSR/FFSR.

---

## Repair case status lifecycle

```
CREATED → SHADOW_BUILT → EXECUTED → LOCALIZED → PATCH_ATTEMPTED
       → VALIDATED → EXPLAINED → EVALUATED
       (any stage → FAILED on unrecoverable error)
```

---

## Language and runtime rules

- **Python ≥ 3.10** is the primary language for all pipeline, agent, and tooling code.
- Introduce a small Kotlin/JVM helper **only** if a specific technical requirement makes Python impossible (e.g., calling Gradle APIs directly). Document the reason explicitly.
- Do not rewrite any core system component in Kotlin or Go.

---

## Database rules

- Use **PostgreSQL 15+ via Docker Compose** for all structured data. The database is a project deliverable.
- Run migrations with **Alembic**. Every schema change must be a numbered migration file under `migrations/`.
- Use **JSONB** only where the structure is genuinely heterogeneous (e.g., raw build logs, provider-specific LLM payloads). Prefer typed columns everywhere else.
- **Never** use a single large JSON file as the database.
- **Never** use a cloud-hosted or managed database service.
- Large binary artifacts (build logs, APKs, diffs) go to the local artifact store at `data/artifacts/<case_id>/`.

---

## Artifact store rules

- All artifacts are stored locally under a configurable path (default: `data/artifacts/`).
- Paths are deterministic: `data/artifacts/<case_id>/<artifact_type>/<filename>`.
- Every artifact record in the DB must have a `storage_path` and a `sha256` hash.
- Do not upload artifacts to external services.

---

## Typed Case Bundle rules

- The canonical runtime state is a **typed Case Bundle**, not free-form chat memory.
- Case Bundle sections: `UpdateEvidence`, `ExecutionEvidence`, `StructuralEvidence`, `RepairEvidence`, `ValidationEvidence`, `ExplanationEvidence`.
- Persist normalized records in PostgreSQL; rehydrate from DB on demand via `from_db_case()`.
- Agents read from and write to the Case Bundle. They do not rely on conversational history as primary state.

---

## Architecture rules

- **Deterministic orchestration** (ingestion, phase execution, retry management, DB writes) stays outside all agents.
- Exactly **three LLM-backed agents** in v1: `LocalizationAgent`, `RepairAgent`, `ExplanationAgent`. Do not add a fourth.
- All agent inputs and outputs must be logged to the DB in auditable form (prompt, response, token counts, model id, timestamp).
- Agents must not have side effects outside their designated Case Bundle section and DB tables.

---

## Validation honesty rules

- **Never** claim iOS validation succeeded if the environment could not run it.
- Persist explicit status values such as `NOT_RUN_ENVIRONMENT_UNAVAILABLE` rather than omitting the record.
- Report uncertainty in every explanation artifact.

---

## Phase execution rules

- Implement and test **one phase at a time**.
- Each phase must be independently runnable via the CLI.
- Every phase must be **reproducible**: same inputs → same outputs.
- Phases persist state to the DB and artifact store before exiting; they do not hold critical state only in memory.

---

## Build and development commands

```bash
# Setup
docker compose up -d postgres      # start local DB
python -m pip install -e ".[dev]"  # install in editable mode
alembic upgrade head                # run migrations

# CLI entry point
kmp-repair --help

# Full pipeline (one case)
kmp-repair discover --repo owner/repo
kmp-repair ingest --repo owner/repo --pr-number 42
kmp-repair build-case <event_id>
kmp-repair run-before-after <case_id>
kmp-repair analyze-case <case_id>
kmp-repair localize <case_id>
kmp-repair repair <case_id> --all-baselines
kmp-repair validate <case_id>
kmp-repair explain <case_id>
kmp-repair metrics <case_id> [--ground-truth ground_truth.json]
kmp-repair report --format all

# Utilities
kmp-repair doctor          # check DB, artifacts, environment

# Tests
pytest tests/unit/          # 291 tests, no DB required
pytest tests/integration/   # needs Docker
```

---

## Module layout

```
src/kmp_repair_pipeline/
  cli/             — Click entry points (main.py)
  domain/          — Pure domain types (no I/O)
  case_bundle/     — Typed Case Bundle model and serialization
  storage/         — DB layer (SQLAlchemy 2.0 models, repositories, Alembic)
  ingest/          — Stage 1: update ingestion and typing
  case_builder/    — Stage 1/2: reproducible repair case construction
  runners/         — Stage 2: Gradle/build execution, env detection, error parsing
  static_analysis/ — Stage 3: KMP-aware structural analysis (tree-sitter + BFS)
  localization/    — Stage 3: hybrid impact localization + LocalizationAgent
  repair/          — Stage 4: patch synthesis + RepairAgent + patch_applier
  baselines/       — Stage 4: baseline_runner for all 4 modes
  validation/      — Stage 5: multi-target validation
  explanation/     — Stage 5: ExplanationAgent, structured + Markdown output
  evaluation/      — Metrics: BSR, CTSR, FFSR, EFR, Hit@k, attribution accuracy
  reporting/       — CSV / JSON / Markdown report export
  utils/           — llm_provider, logging, hashing, JSON I/O
migrations/        — Alembic migration files
tests/
  unit/            — 291 passing tests (no network, no Docker)
  integration/     — DB schema + bundle rehydration (requires Docker)
data/
  artifacts/       — local artifact store (gitignored except .gitkeep)
docker-compose.yml
pyproject.toml
alembic.ini
```

---

## What NOT to do

- Do not add a fourth LLM agent.
- Do not add features beyond what the current phase requires.
- Do not introduce cloud dependencies (S3, GCS, Firestore, hosted Postgres, etc.).
- Do not commit `.env` files or secrets.
- Do not skip migrations for schema changes.
- Do not run destructive DB commands (`DROP TABLE`, `DELETE FROM` without a WHERE) from application code.
- Do not use `git push --force` on `main`.
- Do not silently skip unavailable targets — always record NOT_RUN_ENVIRONMENT_UNAVAILABLE.
