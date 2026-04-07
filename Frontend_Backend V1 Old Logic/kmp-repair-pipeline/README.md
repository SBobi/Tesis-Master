# kmp-repair-pipeline

**A Multi-Agent System to Repair Breaking Changes Caused by Dependency Updates in Kotlin Multiplatform**

Master's thesis implementation — Santiago Bobadilla Suarez

---

## Overview

This pipeline addresses the open problem described in the thesis: given a Dependabot pull request that updates a dependency in a Kotlin Multiplatform (KMP) repository, automatically localize the resulting build failure, synthesize a repair patch, validate it across all declared targets, and generate a reviewer-oriented explanation — without relying on free-form conversational memory as primary state.

The key insight is that KMP repositories are structurally more demanding than single-target Java repositories: shared and platform-specific source sets coexist, `expect`/`actual` contracts must remain aligned across targets, and a patch that compiles on `commonMain` may still break `androidMain` or `iosMain`.

---

## Pipeline Architecture

```
 Dependabot PR
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 1 — Update Ingestion & Typing                                │
│  discover → ingest → build-case                                     │
│  Classifies: direct_library | plugin_toolchain |                    │
│              transitive | platform_integration                      │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼  Typed Case Bundle created
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 2 — Before/After Execution & Evidence Capture                │
│  run-before-after                                                   │
│  Gradle tasks per target: shared │ android │ ios                    │
│  Unavailable targets → NOT_RUN_ENVIRONMENT_UNAVAILABLE              │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼  ExecutionEvidence persisted
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 3 — Hybrid Impact Localization                               │
│  analyze-case → localize                                            │
│                                                                     │
│  Static signals (0.6 weight)        Dynamic signals (0.4 weight)    │
│  ─ source-set membership            ─ error mention count           │
│  ─ import-level relations           ─ failed task targets           │
│  ─ expect/actual links              ─ error file attribution        │
│  ─ BFS propagation                                                  │
│                    ╔══════════════════╗                             │
│                    ║ LocalizationAgent║  (LLM re-ranking)           │
│                    ╚══════════════════╝                             │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼  LocalizationResult + agent log
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 4 — Patch Synthesis                                          │
│  repair  (4 modes)                                                  │
│                                                                     │
│  raw_error        │ dep diff + raw errors only                      │
│  context_rich     │ + localized files + source-set info             │
│  iterative_agentic│ context_rich + retry loop (max 3)              │
│  full_thesis      │ + previous attempts + full evidence             │
│                                                                     │
│                    ╔══════════════════╗                             │
│                    ║   RepairAgent    ║  (unified diff output)      │
│                    ╚══════════════════╝                             │
│  patch --forward -p1  │  git apply --ignore-whitespace (fallback)   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼  PatchAttempt persisted
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 5 — Multi-Target Validation & Explanation                    │
│  validate → explain                                                 │
│                                                                     │
│  Re-runs Gradle on patched workspace per target                     │
│  VALIDATED if all runnable targets → SUCCESS_REPOSITORY_LEVEL       │
│  REJECTED  if any runnable target  → FAILED_BUILD                   │
│                                                                     │
│                    ╔══════════════════╗                             │
│                    ║ExplanationAgent  ║  (JSON + Markdown report)   │
│                    ╚══════════════════╝                             │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Evaluation & Reporting                                             │
│  metrics → report                                                   │
│  BSR │ CTSR │ FFSR │ EFR │ Hit@k │ source_set_accuracy             │
│  CSV │ JSON │ Markdown export                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Typed Case Bundle

All pipeline state is persisted in a typed Case Bundle — never in free-form conversational memory. Agents read from and write to this bundle.

```
CaseBundle
├── UpdateEvidence        Stage 1 — version changes, update class, build-file diff, pr_title
├── ExecutionEvidence     Stage 2 — before/after Gradle runs, task outcomes, errors
│                                   ErrorObservation.required_kotlin_version (KLIB w: signal)
├── StructuralEvidence    Stage 3 — source-set map, impact graph, expect/actual pairs
│                                   version_catalog: dict[str,str] (libs.versions.toml)
├── RepairEvidence        Stage 4 — localization candidates, patch attempts
├── ValidationEvidence    Stage 5 — per-target validation results
└── ExplanationEvidence   Stage 5 — structured JSON + Markdown explanation
```

---

## Three Specialized Agents

```
LocalizationAgent ──── Reads: execution errors + structural evidence
                        Writes: re-ranked candidate list + agent_notes
                        Output: JSON {candidates: [...], agent_notes: "..."}
                        Fallback: deterministic scoring on parse failure

RepairAgent ────────── Reads: repair context (errors, localized files, prev attempts)
                        Writes: unified diff or PATCH_IMPOSSIBLE
                        Output: plain text unified diff
                        Fallback: forced best-effort retry, then PATCH_IMPOSSIBLE flag

ExplanationAgent ────── Reads: full explanation_context() from bundle
                        Writes: structured JSON + rendered Markdown
                        Output: JSON {what_was_updated, patch_rationale, ...}
                        Fallback: deterministic explanation on parse failure
```

All agent calls are logged to `agent_logs` with prompt path, response path, token counts, model ID, and latency.

---

## Database Schema

```
repositories
    └── dependency_events
            └── repair_cases
                    ├── revisions           (before / after clones)
                    ├── execution_runs
                    │       ├── task_results
                    │       └── error_observations
                    ├── source_entities
                    ├── expect_actual_links
                    ├── localization_candidates
                    ├── agent_logs
                    ├── patch_attempts
                    │       └── validation_runs
                    ├── explanations
                    └── evaluation_metrics
```

**Technology**: PostgreSQL 15 via Docker Compose · SQLAlchemy 2.0 ORM · Alembic migrations

---

## Repair Case Status Lifecycle

```
CREATED → SHADOW_BUILT → EXECUTED → LOCALIZED → PATCH_ATTEMPTED
       → VALIDATED → EXPLAINED → EVALUATED
       (any stage may transition to FAILED)
```

---

## Localization Scoring

```
Static score (0.6 weight):
  base_score = DIRECT(1.0) | EXPECT_ACTUAL(0.85) | TRANSITIVE(0.5)
  decay      = -0.15 per hop beyond 1
  bonus      = +0.15 if expect/actual pair  |  +0.10 if direct import

Dynamic score (0.4 weight):
  +0.25 per error mention, capped at 1.0

final_score = 0.6 × static + 0.4 × dynamic
```

LocalizationAgent re-ranks the top-20 deterministic candidates. Falls back to deterministic result on JSON parse failure.

---

## Evaluation Metrics

| Metric | Definition |
|--------|-----------|
| **BSR** | Build Success Rate: 1.0 if overall validation → `SUCCESS_REPOSITORY_LEVEL` |
| **CTSR** | Compile-Time Success Rate: 1.0 if no target reports `FAILED_BUILD` |
| **FFSR** | Full Fix Success Rate: 1.0 if ALL runnable targets → `SUCCESS_REPOSITORY_LEVEL` |
| **EFR** | Error Fix Rate: penalty-adjusted fraction of original errors eliminated |
| **Hit@k** | 1.0 if any ground-truth changed file in top-k localization candidates |
| **source_set_accuracy** | Fraction of candidates with correct source_set label (requires ground truth) |

`NOT_RUN_ENVIRONMENT_UNAVAILABLE` targets (e.g. iOS on Linux) are excluded from BSR/CTSR/FFSR calculation.

**EFR penalty formula** (prevents inflation when a patch replaces N errors with M > N new errors):
```
raw_efr        = (|original| - |original ∩ remaining|) / |original|
new_errors     = max(0, |remaining| - |original|)
penalty        = new_errors / |original|
EFR            = max(0.0, raw_efr - penalty)
```

---

## Artifact Store Layout

```
data/artifacts/<case_id>/
├── shadow/                     ShadowManifest JSON
├── execution/
│   ├── before/                 per-task stdout/stderr (pre-update)
│   ├── after/                  per-task stdout/stderr (post-update)
│   └── validation_<n>_<mode>/  per-task stdout/stderr (post-patch)
├── patches/                    001_full_thesis.diff, 002_raw_error.diff, ...
├── prompts/                    LocalizationAgent_0000.txt, RepairAgent_0001.txt, ...
├── responses/                  LocalizationAgent_0000.txt, RepairAgent_0001.txt, ...
└── explanations/
    ├── explanation.json
    └── explanation.md
```

All artifact records in the DB include `storage_path` and `sha256` hash.

---

## Subject Selection Criteria (from thesis)

Repositories must:
- Be real software projects (not templates, tutorials, or playgrounds)
- Have ≥ 100 GitHub stars (relaxable to 50 if pool is small)
- Have ≥ 250 commits
- Have ≥ 3 non-bot contributors
- Not be archived
- Have at least one non-bot commit in the last 18 months
- Declare KMP shared + Android + iOS targets
- Have at least one observable dependency-update event (Dependabot PR or build-file version change)

---

## Setup

### Prerequisites

```bash
# macOS
brew install docker poppler  # poppler for PDF utilities (optional)
brew install --cask docker

# Start database
docker compose up -d postgres redis

# Install pipeline
pip install -e ".[dev]"

# Run migrations
alembic upgrade head
```

### Web Runtime (FastAPI + Next.js)

```bash
# Terminal 1 - Web API
kmp-repair-api

# Terminal 2 - Worker queue (RQ)
kmp-repair-worker

# Terminal 3 - Frontend
cd web
npm install
npm run dev
```

macOS note:

- `kmp-repair-worker` auto-configures `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`
  and prefers JDK 21 (`/usr/libexec/java_home -v 21`) when available.
- Manual `export JAVA_HOME=...` is not required for the worker.

VS Code project runtime:

- The repository includes `.vscode/settings.json` and `.vscode/launch.json`.
- Press **Run and Debug** and use `Play API + Worker (Project Runtime)` to launch
  both services with project-scoped runtime defaults:
  - Python 3.12 (`/opt/homebrew/opt/python@3.12/bin/python3.12`)
  - Java 21 (`/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`)

Pipeline continuity note:

- If `repair` produces no `APPLIED` attempt (for example `FAILED_APPLY`),
  `validate` now falls back to the latest patch attempt instead of aborting
  the pipeline. This allows `explain`, `metrics`, and `report` to run and
  preserve end-to-end evidence for the case.

Default URLs:

- API: http://localhost:8000
- Frontend: http://localhost:3000

Useful env vars:

```bash
KMP_REDIS_URL=redis://localhost:6379/0
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Environment Variables

```bash
# LLM provider selector: anthropic | vertex
KMP_LLM_PROVIDER=vertex
KMP_LLM_MODEL=gemini-fast             # alias -> gemini-2.5-flash

# Vertex (recommended)
KMP_VERTEX_PROJECT=your-gcp-project-id
KMP_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/service-account.json

# Anthropic (only if KMP_LLM_PROVIDER=anthropic)
ANTHROPIC_API_KEY=sk-ant-...

KMP_DATABASE_URL=postgresql+psycopg2://kmp_repair:kmp_repair_dev@localhost:5432/kmp_repair
# Backward-compatible fallback:
# DATABASE_URL=postgresql+psycopg2://kmp_repair:kmp_repair_dev@localhost:5432/kmp_repair
KMP_LLM_FAKE=1                        # Use FakeLLMProvider (testing only)
```

---

## CLI Reference

### Stage 1 — Ingestion

```bash
# Discover Dependabot PRs in a KMP repository
kmp-repair discover --repo owner/repo [--min-stars 100] [--max-prs 20]
# Full discovery with thesis filters
kmp-repair discover [--min-stars 100] [--min-commits 250] [--min-contributors 3]
                    [--active-months 18] [--strict-targets]

# Ingest a specific PR as a dependency-update event
kmp-repair ingest https://github.com/owner/repo/pull/42

# Build a reproducible before/after repair case
kmp-repair build-case <case_id>
```

### Stage 2 — Execution

```bash
# Run Gradle before and after the update, capture errors
kmp-repair run-before-after <case_id> [--target shared --target android --target ios] [--timeout 600]
```

### Stage 3 — Structural Analysis + Localization

```bash
# KMP-aware static analysis: source sets, impact graph, expect/actual
kmp-repair analyze-case <case_id>

# Hybrid localization: deterministic scoring + optional LocalizationAgent
kmp-repair localize <case_id> [--no-agent] [--top-k 10] [--provider vertex] [--model gemini-fast]
```

### Stage 4 — Patch Synthesis

```bash
# Single repair mode
kmp-repair repair <case_id> --mode full_thesis [--top-k 5] [--provider vertex] [--model gemini-fast]
                  [--patch-strategy single_diff|chain_by_file] [--no-force-patch-attempt]

# Run all 4 baseline modes
kmp-repair repair <case_id> --all-baselines

# Available modes: raw_error | context_rich | iterative_agentic | full_thesis
# Available patch strategies:
#   single_diff   -> apply the full unified diff in one shot
#   chain_by_file -> split by file and apply sequentially (stops on first failure)
# Stage 9 now rejects malformed unified diffs before apply_patch/apply_chain.
# Default: retries once with forced best-effort diff when the model returns PATCH_IMPOSSIBLE.
# Use --no-force-patch-attempt to disable that retry.
```

### Stage 5 — Validation & Explanation

```bash
# Multi-target validation on the patched workspace
kmp-repair validate <case_id> [--attempt-id UUID] [--targets shared,android]

# Generate structured explanation (JSON + Markdown)
kmp-repair explain <case_id> [--provider vertex] [--model gemini-fast]
```

### Evaluation & Reporting

```bash
# Compute BSR, CTSR, FFSR, EFR, Hit@k for a case
kmp-repair metrics <case_id> [--ground-truth ground_truth.json]

# Export evaluation report
kmp-repair report [--output-dir data/reports] [--format all|csv|json|markdown]
              [--modes full_thesis,raw_error] [--cases case-id-1,case-id-2]
# Markdown report includes an "Attempt Strategy Comparison" table by attempt.

# Legacy: score against ground-truth YAML (prototype)
kmp-repair evaluate --results results.json --ground-truth gt.yml --output-dir output/
```

### Utilities

```bash
kmp-repair doctor          # check environment (Java, Gradle, Android SDK, Xcode)
kmp-repair db-status       # show Alembic migration status
kmp-repair db-upgrade      # run pending migrations
kmp-repair db-seed         # insert sample repository record
kmp-repair detect-changes  # compare two TOML files for version changes
```

---

## Ground Truth JSON Format (for Hit@k and source_set_accuracy)

```json
{
  "changed_files": [
    "src/commonMain/kotlin/com/example/App.kt",
    "src/androidMain/kotlin/com/example/Platform.kt"
  ],
  "source_sets": {
    "src/commonMain/kotlin/com/example/App.kt": "common",
    "src/androidMain/kotlin/com/example/Platform.kt": "android"
  }
}
```

---

## Module Layout

```
src/kmp_repair_pipeline/
  cli/               Click entry points (main.py)
  domain/            Pure domain types — no I/O
    events.py        UpdateClass, DependencyUpdateEvent, VersionChange
    analysis.py      ImpactGraph, FileImpact, ExpectActualPair, ImpactRelation
    validation.py    ValidationStatus vocabulary
  case_bundle/
    bundle.py        CaseBundle + 5 context() methods
    evidence.py      6 typed evidence sections (Pydantic v2)
    serialization.py from_db_case() / to_db()
  storage/
    models.py        SQLAlchemy 2.0 ORM models (18 tables)
    repositories.py  Typed repository classes (no raw SQL in business logic)
    artifact_store.py Deterministic local artifact paths
    db.py            Engine + session factory
  webapi/
    app.py           FastAPI endpoints (cases, jobs, SSE, reports)
    job_runner.py    RQ enqueue/dequeue orchestration
    orchestrator.py  Stage-by-stage execution wrapper with audit transitions
    stages.py        Allowlist stricta de parametros + comando equivalente
    queries.py       Case detail/feed projection for UI
    worker.py        RQ worker entrypoint
  ingest/
    repo_discoverer.py  GitHub search + Dependabot PR discovery
    event_builder.py    DependencyEvent + VersionChange extraction
    github_client.py    Thin GitHub API wrapper
  case_builder/
    case_factory.py     Before/after clone + ShadowManifest
  runners/
    env_detector.py     Java / Gradle / Android SDK / Xcode detection
    gradle_runner.py    run_tasks() → GradleRunResult per task
    error_parser.py     9 regex patterns → ErrorObservation list (incl. KLIB w: warnings)
    execution_runner.py run_before_after() orchestrator
  static_analysis/
    kotlin_parser.py    tree-sitter-kotlin AST + regex fallback
    impact_analyzer.py  BFS propagation through import graph
    structural_builder.py analyze_case() orchestrator
  localization/
    scoring.py          Deterministic hybrid scorer
    localization_agent.py LocalizationAgent (LLM #1)
    localizer.py        localize() orchestrator
  repair/
    repair_agent.py     RepairAgent (LLM #2), 4 prompt modes
    patch_applier.py    patch -p1 + git apply fallback
    repairer.py         repair() orchestrator
  baselines/
    baseline_runner.py  run_baseline() for all 4 modes
  validation/
    validator.py        validate() orchestrator
  explanation/
    explanation_agent.py ExplanationAgent (LLM #3), render_markdown()
    explainer.py        explain() orchestrator
  evaluation/
    metrics.py          compute_bsr/ctsr/ffsr/efr/hit_at_k (pure functions)
    evaluator.py        evaluate() orchestrator
    scorer.py           Legacy precision/recall scorer
    report.py           Legacy Markdown/JSON report
  reporting/
    report_builder.py   build_report() → list[ReportRow]
    formatters.py       to_csv / to_json / to_markdown / aggregate_by_mode
    reporter.py         generate_report() orchestrator
  utils/
    llm_provider.py     ClaudeProvider / VertexProvider / FakeLLMProvider / NoOpProvider
    log.py              Structured logger
    json_io.py          load_json / save_json / sha256_of_file

migrations/            Alembic migration files
tests/
  unit/                291 passing tests (no network, no Docker)
  integration/         DB schema + bundle rehydration (requires Docker)
data/
  artifacts/           Local artifact store (gitignored)
docker-compose.yml
pyproject.toml
alembic.ini
web/                  Next.js App Router frontend (TypeScript + Tailwind)
```

---

## LLM Provider

```python
from kmp_repair_pipeline.utils.llm_provider import (
  ClaudeProvider,      # Anthropic SDK
  VertexProvider,      # Vertex Gemini via google-genai
    FakeLLMProvider,     # Pre-programmed responses, records calls (tests)
    NoOpProvider,        # Raises AssertionError if called (test guard)
  get_default_provider # Returns Fake when KMP_LLM_FAKE=1, else selected provider
)

# Override provider/model at runtime
provider = get_default_provider(provider_name="vertex", model_id="gemini-fast")
```

All providers implement `complete(prompt, system, max_tokens, temperature) → LLMResponse`.

---

## Running Tests

```bash
# Unit tests (no DB, no network required)
pytest tests/unit/ -v

# Integration tests (requires Docker Compose postgres)
docker compose up -d postgres
pytest tests/integration/ -v

# All tests
pytest tests/

# Frontend tests
cd web
npm test
npm run test:e2e
```

**Test counts**: 291 unit tests across 14 test files.

---

## Thesis Baselines Comparison

| Baseline | Context given to RepairAgent | Retry? |
|----------|------------------------------|--------|
| `raw_error` | Dep diff + raw compiler errors | No |
| `context_rich` | + localized files + source-set map | No |
| `iterative_agentic` | Same as context_rich + previous attempts | Yes (≤3) |
| `full_thesis` | Full Case Bundle evidence | No |

Run all four for a case:
```bash
kmp-repair repair <case_id> --all-baselines
kmp-repair metrics <case_id> --ground-truth ground_truth.json
kmp-repair report --format all
```

---

## KLIB ABI Incompatibility Detection

One of the most common KMP breaking changes is a KLIB ABI mismatch: a library's iOS KLIB was compiled with a newer Kotlin version than the project uses.

The pipeline detects this at two levels:

**Error level** (`e:` lines in Gradle output):
```
e: KLIB resolver: Could not find ".../ktor-client-logging-iosarm64/3.4.1/..."
```
Classified as `KLIB_ABI_ERROR` with hint to check `kotlin` in `gradle/libs.versions.toml`.

**Warning level** (`w:` lines — most precise signal):
```
w: KLIB resolver: Skipping '.../ktor-client-logging-iosArm64Main-3.4.1.klib'
   having incompatible ABI version '2.3.0'. The library was produced by
   '2.3.0' compiler. The current Kotlin compiler can consume libraries having
   ABI version <= '2.2.0'. Please upgrade your Kotlin compiler version.
```
The parser extracts `"2.3.0"` from `"produced by '2.3.0' compiler"` and stores it in `ErrorObservation.required_kotlin_version`. This field is propagated to `repair_context["required_kotlin_version"]` so the RepairAgent receives an unambiguous instruction:

```
## !! REQUIRED KOTLIN VERSION (extracted from compiler output) !!
  The failing KLIB was produced by Kotlin '2.3.0' compiler.
  You MUST bump the `kotlin` alias in gradle/libs.versions.toml
  UP to '2.3.0' (do NOT downgrade).
  Correct patch target: kotlin = "2.3.0"
```

**Version catalog**: `gradle/libs.versions.toml` is parsed into `StructuralEvidence.version_catalog` at analyze-case time. The current values (e.g. `kotlin = "2.2.0" ★`) are shown in every repair prompt alongside the required target version.

---

## Key Design Constraints

- **Deterministic orchestration** — no LLM in the control flow (ingestion, phase execution, retry management, DB writes)
- **Exactly 3 LLM agents** — LocalizationAgent, RepairAgent, ExplanationAgent
- **Honest validation** — `NOT_RUN_ENVIRONMENT_UNAVAILABLE` rather than silently skipping unavailable targets
- **Auditable** — every agent call logged with prompt, response, token counts, model ID, latency
- **Reproducible** — same inputs → same outputs; all state persisted to DB before exit
- **No cloud** — PostgreSQL via Docker Compose, artifacts local under `data/artifacts/`
- **Workspace isolation** — `git checkout -- . && git clean -fd` before every baseline mode and between iterative retries; ensures each baseline sees the original unpatched workspace
- **Build-file evidence first** — `libs.versions.toml` is always the first entry in `build_file_contents` sent to RepairAgent so version bump opportunities are immediately visible

---

## References

Key papers informing this work:
- [BUMP] Reyes et al., "BUMP: A Benchmark of Reproducible Breaking Dependency Updates", SANER 2024
- [Byam] Reyes et al., "Byam: Fixing Breaking Dependency Updates with LLMs", arXiv 2025
- [Fruntke] Fruntke & Krinke, "Automatically Fixing Dependency Breaking Changes", FSE 2025
- [Jayasuriya] Jayasuriya et al., "Understanding Breaking Changes in the Wild", ISSTA 2023
- [Hejderup] Hejderup & Gousios, "Can we trust tests to automate dependency updates?", JSS 2022
- [Breaking-Good] Reyes et al., "Breaking-Good: Explaining Breaking Dependency Updates", SCAM 2024
