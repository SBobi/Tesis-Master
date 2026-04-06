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
│  Priority injection: libs.versions.toml forced to rank-0            │
│  when KLIB_ABI_ERROR present (static graph has no build edges)      │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ▼  LocalizationResult + agent log
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 4 — Patch Synthesis (4 baselines)                            │
│  repair  [--mode | --all-baselines]                                 │
│                                                                     │
│  Mode              Context given to RepairAgent      Budget         │
│  ──────────────────────────────────────────────────────────         │
│  raw_error       │ dep diff + raw errors only        │ 2 attempts   │
│  context_rich    │ + localized files + source-set    │ 3 attempts   │
│  iterative_agentic│ context_rich + prev attempts     │ 4 attempts   │
│  full_thesis     │ full Case Bundle evidence         │ 5 attempts   │
│                                                                     │
│                    ╔══════════════════╗                             │
│                    ║   RepairAgent    ║  (unified diff output)      │
│                    ╚══════════════════╝                             │
│  Applies: patch --forward -p1  │  git apply (fallback)             │
│  Workspace reset between modes: git checkout -- . && git clean -fd  │
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
├── ExecutionEvidence     Stage 2 — before/after Gradle runs, task outcomes, error_observations
│   └── ErrorObservation  error_type, file_path, line, message, required_kotlin_version
│                         (required_kotlin_version populated from KLIB w: warning signal)
├── StructuralEvidence    Stage 3 — source-set map, impact graph, expect/actual pairs
│   └── version_catalog   dict[str,str] — parsed [versions] from gradle/libs.versions.toml
├── RepairEvidence        Stage 4 — localization candidates, patch attempts
│   └── PatchAttempt      attempt_number, repair_mode, status, diff_path, touched_files
├── ValidationEvidence    Stage 5 — per-target TargetValidation results
│   └── TargetValidation  target, status, unavailable_reason, duration_s
└── ExplanationEvidence   Stage 5 — structured JSON + Markdown explanation
    └── uncertainties     list of {kind, description} (environment/localization/patch/validation)
```

---

## Three Specialized Agents

```
LocalizationAgent ──── Reads: execution errors + structural evidence
                        Writes: re-ranked candidate list + agent_notes
                        Output: JSON {candidates: [...], agent_notes: "..."}
                        Fallback: deterministic scoring on JSON parse failure
                        Context: top-20 deterministic candidates
                        LLM params: temperature=0.0, max_tokens=4096

RepairAgent ────────── Reads: repair context (errors, localized files, prev attempts)
                        Writes: unified diff or PATCH_IMPOSSIBLE
                        Output: plain text unified diff
                        Fallback: forced best-effort retry, then PATCH_IMPOSSIBLE flag
                        Context: top-k=5 files (8000-byte limit per file)
                        LLM params: temperature=0.0, max_tokens=8192

ExplanationAgent ────── Reads: full explanation_context() from bundle
                        Writes: structured JSON + rendered Markdown
                        Output: JSON {what_was_updated, update_class_rationale,
                                     localization_summary, patch_rationale,
                                     validation_summary, target_coverage_complete,
                                     uncertainties: [{kind, description}]}
                        Fallback: deterministic JSON on parse failure
                        Context: top-5 candidates, first 30 errors
                        LLM params: temperature=0.0, max_tokens=2048
```

All agent calls are logged to `agent_logs` with prompt path, response path, token counts, model ID, and latency. All prompts and responses are written to `data/artifacts/<case_id>/prompts/` and `.../responses/`.

---

## Database Schema

```
repositories
    └── dependency_events  (pr_title, pr_ref, update_class, source)
            └── repair_cases  (status lifecycle)
                    ├── revisions           (before / after clones, git_sha, local_path)
                    ├── execution_runs      (revision_type, env_metadata JSONB)
                    │       ├── task_results   (task_name, exit_code, duration_s)
                    │       └── error_observations  (error_type, file_path, line,
                    │                               message, required_kotlin_version)
                    ├── source_entities     (fqcn, is_expect, is_actual, source_set)
                    ├── expect_actual_links (expect_fqcn → actual_fqcn pairs)
                    ├── localization_candidates  (rank, score, classification, source_set)
                    ├── agent_logs          (agent_type, call_index, prompt_path,
                    │                       response_path, tokens_in, tokens_out, latency_s)
                    ├── patch_attempts      (repair_mode, status, diff_path,
                    │                       touched_files JSONB, retry_reason)
                    │       └── validation_runs  (target, status, unavailable_reason)
                    ├── explanations        (json_path, markdown_path, sha256)
                    └── evaluation_metrics  (bsr, ctsr, ffsr, efr, hit_at_1/3/5,
                                            source_set_accuracy, per case+repair_mode)
```

**Technology**: PostgreSQL 15 via Docker Compose · SQLAlchemy 2.0 ORM · Alembic migrations

**Migration chain** (always run `alembic upgrade head`):
```
c4beede9862d  initial schema (16 tables)
a1b2c3d4e5f6  add pr_title to dependency_events
1c03c4a3181a  add required_kotlin_version to error_observations
```

---

## Repair Case Status Lifecycle

```
CREATED → SHADOW_BUILT → EXECUTED → LOCALIZED → PATCH_ATTEMPTED
       → VALIDATED → EXPLAINED → EVALUATED
       (any stage may transition to FAILED on unrecoverable error)
```

---

## KLIB ABI Incompatibility Detection

One of the most common KMP breaking changes is a KLIB ABI mismatch: a library's iOS KLIB was compiled with a newer Kotlin version than the project uses. The pipeline detects this at three levels:

**Error level** (`e:` lines — presence signal):
```
e: KLIB resolver: Could not find ".../ktor-client-logging-iosarm64/3.4.1/..."
```
Classified as `KLIB_ABI_ERROR`. Signals that a KLIB version bump is needed, but doesn't reveal the exact required Kotlin version.

**Warning level** (`w:` lines — exact version signal, most precise):
```
w: KLIB resolver: Skipping '.../ktor-client-logging-iosArm64Main-3.4.1.klib'
   having incompatible ABI version '2.3.0'. The library was produced by
   '2.3.0' compiler. The current Kotlin compiler can consume libraries having
   ABI version <= '2.2.0'. Please upgrade your Kotlin compiler version.
```
The parser extracts `"2.3.0"` from `"produced by '2.3.0' compiler"` and stores it in `ErrorObservation.required_kotlin_version`. This is the most actionable signal in the entire output.

**JVM metadata level** (`binary version of its metadata is X.Y.Z`):
```
Module was compiled with an incompatible version of Kotlin.
The binary version of its metadata is 2.3.0, expected version is 2.1.0.
```
Also classified as `KLIB_ABI_ERROR` with `required_kotlin_version = "2.3.0"`. Covers JVM targets (`.kotlin_module` in JAR files).

**Multi-library cascade**: When multiple libraries impose different minimum Kotlin requirements, the pipeline takes the maximum across all constraints:
```
koin 4.1.0  → required_kotlin_version = "2.1.20"
ktor 3.4.1  → required_kotlin_version = "2.3.0"
max("2.1.20", "2.3.0") = "2.3.0"  ← bumped to this in libs.versions.toml
```

The RepairAgent receives an explicit block showing the exact current value and required target:
```
## !! REQUIRED KOTLIN VERSION (extracted from compiler output) !!
  Current value in gradle/libs.versions.toml: kotlin = "2.2.0"
  Required value (max across all library constraints): kotlin = "2.3.0"
  You MUST change: -kotlin = "2.2.0"  →  +kotlin = "2.3.0"
  Direction: UPWARD only — do NOT downgrade kotlin.
  Library-level constraints (all must be satisfied):
    koin-core-iosArm64Main: requires Kotlin >= 2.1.20
    ktor-client-core-jvm: requires Kotlin >= 2.3.0  ← MAX (use this)
```

`libs.versions.toml` is always injected as rank-0 localization candidate when KLIB errors are present (the static import graph has no edges to build files, so deterministic scoring can never surface it).

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

After deterministic scoring:
1. `libs.versions.toml` injected at rank-0 (score 1.0) when KLIB/metadata errors present.
2. LocalizationAgent re-ranks the top-20 deterministic candidates using LLM reasoning.
3. Falls back to deterministic result on JSON parse failure.

---

## Evaluation Metrics

| Metric | Definition | Passing condition |
|--------|-----------|------------------|
| **BSR** | Build Success Rate | overall `ValidationStatus == SUCCESS_REPOSITORY_LEVEL` |
| **CTSR** | Compile-Time Success Rate | no runnable target has `FAILED_BUILD` |
| **FFSR** | Full Fix Success Rate | ALL runnable targets → `SUCCESS_REPOSITORY_LEVEL` |
| **EFR** | Error Fix Rate (penalty-adjusted) | see formula below |
| **Hit@k** | Localization recall | ground-truth file in top-k candidates (None if no ground truth) |
| **source_set_accuracy** | Localization precision | fraction of candidates with correct source_set label |

`NOT_RUN_ENVIRONMENT_UNAVAILABLE` targets (e.g. iOS on Linux, Android without SDK) are excluded from all three BSR/CTSR/FFSR calculations. They are recorded in `validation_runs` and surfaced in `uncertainties` in the explanation.

**EFR penalty formula** (prevents inflation when a patch replaces N errors with M > N new errors):
```
original_keys  = {(type, file, line, msg) for e in original_errors}
remaining_keys = {(type, file, line, msg) for e in remaining_errors}
fixed          = |original_keys - remaining_keys|
raw_efr        = fixed / |original_keys|
new_errors     = max(0, |remaining| - |original|)
penalty        = new_errors / |original|
EFR            = max(0.0, raw_efr - penalty)
```

Metrics are computed per `(case_id, repair_mode)` and upserted to `evaluation_metrics`. Returns `None` when `original_errors` is empty (EFR) or when no ground truth is provided (Hit@k, source_set_accuracy).

---

## Baseline Repair Modes

| Baseline | Context given to RepairAgent | Retry budget | Notes |
|----------|------------------------------|:---:|-------|
| `raw_error` | Dep diff + raw compiler errors only | 2 | Minimal baseline; tests if errors alone suffice |
| `context_rich` | + localized files + source-set info + version catalog | 3 | Adds file content + build evidence |
| `iterative_agentic` | Same as context_rich + previous-attempt feedback | 4 | Retry loop with rejection guidance |
| `full_thesis` | Full Case Bundle evidence + all previous attempts | 5 | Maximum context; thesis primary baseline |

All modes share the same retry loop: stop as soon as a patch applies. Between modes, the workspace is reset (`git checkout -- . && git clean -fd`). Between retry attempts within a mode, the workspace is also reset.

Run all four:
```bash
kmp-repair repair <case_id> --all-baselines
kmp-repair metrics <case_id> [--ground-truth ground_truth.json]
kmp-repair report --format all
```

---

## Artifact Store Layout

```
data/artifacts/<case_id>/
├── shadow/                     ShadowManifest JSON
├── execution/
│   ├── before/                 per-task stdout/stderr (pre-update)
│   ├── after/                  per-task stdout/stderr (post-update)
│   └── validation_<n>_<mode>/  per-task stdout/stderr (post-patch per mode)
├── patches/                    001_full_thesis.diff, 002_raw_error.diff, ...
├── prompts/                    LocalizationAgent_0000.txt, RepairAgent_0001.txt, ...
├── responses/                  LocalizationAgent_0000.txt, RepairAgent_0001.txt, ...
└── explanations/
    ├── explanation.json
    └── explanation.md
```

All artifact records in the DB include `storage_path` and `sha256` hash for provenance.

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
brew install docker
brew install --cask docker

# Start database
docker compose up -d postgres

# Install pipeline
pip install -e ".[dev]"

# Run migrations (always run after git pull)
alembic upgrade head
```

### Environment Bootstrap

The quickest way to configure all required environment variables:

```bash
# Loads JAVA_HOME (Temurin 21), ANDROID_HOME, GCP credentials, .env, and reports gaps
source scripts/bootstrap_env.sh

# Or manually load .env (also sets JAVA_HOME):
export $(grep -v '^#' .env | xargs)
```

> **Critical**: The Kotlin 2.x compiler crashes on Java 25 with `IllegalArgumentException: 25.0.1`.
> Java 21 (Temurin) is required. See [Java version requirement](#java-version-requirement).

### Environment Variables

```bash
# ── Java (critical) ──────────────────────────────────────────────────
JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home

# ── LLM provider ─────────────────────────────────────────────────────
KMP_LLM_PROVIDER=vertex               # vertex | anthropic
KMP_LLM_MODEL=gemini-fast             # alias → gemini-2.5-flash

# ── Vertex AI (Gemini) ───────────────────────────────────────────────
KMP_VERTEX_PROJECT=your-gcp-project-id
KMP_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/service-account.json

# ── Anthropic (only if KMP_LLM_PROVIDER=anthropic) ───────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── Database ─────────────────────────────────────────────────────────
KMP_DATABASE_URL=postgresql+psycopg2://kmp_repair:kmp_repair_dev@localhost:5432/kmp_repair

# ── Android SDK (optional — enables Android target builds) ────────────
ANDROID_HOME=/path/to/Android/sdk    # or let bootstrap_env.sh auto-detect

# ── Testing ──────────────────────────────────────────────────────────
KMP_LLM_FAKE=1                        # Use FakeLLMProvider (no real LLM calls)
```

---

## Java Version Requirement

**Always use Java 21 (Temurin).** Running with Java 25 causes a hard crash inside the Kotlin 2.x compiler:

```
IllegalArgumentException: 25.0.1
  at org.jetbrains.kotlin.com.intellij.util.lang.JavaVersion.parse(...)
```

The Kotlin 2.x compiler cannot parse Java EA/LTS multi-component version strings.

```bash
# macOS install
brew install --cask temurin21

# Set before any kmp-repair command:
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home

# Or let bootstrap_env.sh handle it automatically:
source scripts/bootstrap_env.sh
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
# Targets detected from environment; unavailable → NOT_RUN_ENVIRONMENT_UNAVAILABLE
kmp-repair run-before-after <case_id> [--target shared --target android --target ios] [--timeout 600]
```

### Stage 3 — Structural Analysis + Localization

```bash
# KMP-aware static analysis: source sets, impact graph, expect/actual pairs
# Parses gradle/libs.versions.toml → StructuralEvidence.version_catalog
kmp-repair analyze-case <case_id>

# Hybrid localization: deterministic scoring + optional LocalizationAgent
# libs.versions.toml auto-injected at rank-0 when KLIB errors present
kmp-repair localize <case_id> [--no-agent] [--top-k 10] [--provider vertex] [--model gemini-fast]
```

### Stage 4 — Patch Synthesis

```bash
# Single repair mode (5-attempt budget for full_thesis)
kmp-repair repair <case_id> --mode full_thesis [--top-k 5] [--provider vertex] [--model gemini-fast]
                  [--patch-strategy single_diff|chain_by_file] [--no-force-patch-attempt]

# Run all 4 baseline modes (each with its own attempt budget and workspace reset)
kmp-repair repair <case_id> --all-baselines

# Available modes:    raw_error | context_rich | iterative_agentic | full_thesis
# Attempt budgets:    2         | 3            | 4                  | 5
# Available strategies:
#   single_diff   → apply the full unified diff in one shot
#   chain_by_file → split by file and apply sequentially (stops on first failure)
# Lightweight diff precheck runs before patch apply (rejects syntax-malformed diffs).
# Default: force_patch_attempt=True — retries once when model returns PATCH_IMPOSSIBLE.
# Use --no-force-patch-attempt to disable.
```

### Stage 5 — Validation & Explanation

```bash
# Multi-target validation on the patched workspace
# Detects targets from environment; records NOT_RUN_ENVIRONMENT_UNAVAILABLE for missing platforms
kmp-repair validate <case_id> [--attempt-id UUID] [--targets shared,android]

# Generate structured explanation (JSON + Markdown)
kmp-repair explain <case_id> [--provider vertex] [--model gemini-fast]
```

### Evaluation & Reporting

```bash
# Compute BSR, CTSR, FFSR, EFR, Hit@k for a case
kmp-repair metrics <case_id> [--ground-truth ground_truth.json]

# Export evaluation report (CSV, JSON, or Markdown)
kmp-repair report [--output-dir data/reports] [--format all|csv|json|markdown]
              [--modes full_thesis,raw_error] [--cases case-id-1,case-id-2]
```

### Utilities

```bash
kmp-repair doctor          # check Python, Java, Gradle, Android SDK, Xcode, DB, LLM
kmp-repair db-status       # show Alembic migration status
kmp-repair db-upgrade      # run pending migrations (alias for alembic upgrade head)
kmp-repair db-seed         # insert sample repository record (idempotent)
kmp-repair detect-changes  # compare two TOML files for version changes (diff)
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
kmp-repair-pipeline/
├── .python-version              Python 3.12 (pyenv/mise pin)
├── scripts/
│   └── bootstrap_env.sh         Env auto-detection: JAVA_HOME, ANDROID_HOME, GCP creds
├── src/kmp_repair_pipeline/
│   ├── cli/
│   │   └── main.py              Click entry point — 20+ commands
│   ├── domain/                  Pure domain types (no I/O)
│   │   ├── events.py            UpdateClass, DependencyUpdateEvent, VersionChange
│   │   ├── analysis.py          ImpactGraph, FileImpact, ExpectActualPair, ImpactRelation
│   │   └── validation.py        ValidationStatus vocabulary
│   ├── case_bundle/
│   │   ├── bundle.py            CaseBundle + 5 context() methods + _max_kotlin_version()
│   │   ├── evidence.py          6 typed evidence sections (Pydantic v2)
│   │   └── serialization.py     from_db_case() / to_db()
│   ├── storage/
│   │   ├── models.py            SQLAlchemy 2.0 ORM (16 tables)
│   │   ├── repositories.py      Typed CRUD repos (no raw SQL in business logic)
│   │   ├── artifact_store.py    Deterministic local artifact paths + SHA256
│   │   └── db.py                Engine + session factory
│   ├── ingest/
│   │   ├── repo_discoverer.py   GitHub search + Dependabot PR discovery
│   │   ├── event_builder.py     DependencyEvent + VersionChange extraction
│   │   └── github_client.py     Thin GitHub API wrapper
│   ├── case_builder/
│   │   └── case_factory.py      Before/after clone + ShadowManifest
│   ├── runners/
│   │   ├── env_detector.py      Java/Gradle/Android SDK/Xcode detection
│   │   │                        Reads sdk.dir from local.properties (Gradle fallback)
│   │   │                        Writes local.properties with sdk.dir when SDK found
│   │   ├── gradle_runner.py     run_tasks() → GradleRunResult per task
│   │   ├── error_parser.py      11 regex patterns → ErrorObservation list
│   │   │                        Includes: KLIB e: errors, KLIB w: warnings (exact version),
│   │   │                                  JVM metadata incompatibility (binary version X.Y.Z)
│   │   └── execution_runner.py  run_before_after() orchestrator
│   ├── static_analysis/
│   │   ├── kotlin_parser.py     tree-sitter-kotlin AST + regex fallback
│   │   ├── structural_builder.py analyze_case() orchestrator (parses version catalog)
│   │   └── analyzer.py          Impact analysis per dependency group
│   ├── localization/
│   │   ├── scoring.py           Deterministic hybrid scorer (static 0.6 + dynamic 0.4)
│   │   ├── localization_agent.py LocalizationAgent (LLM #1) — re-ranks top-20 candidates
│   │   └── localizer.py         localize() orchestrator + KLIB priority injection
│   ├── repair/
│   │   ├── repair_agent.py      RepairAgent (LLM #2) — 4 prompt modes
│   │   │                        Anti-hallucination: shows exact current + required Kotlin
│   │   │                        Cascade map: library → required Kotlin version
│   │   ├── patch_applier.py     patch -p1 + git apply fallback
│   │   └── repairer.py          repair() orchestrator
│   ├── baselines/
│   │   └── baseline_runner.py   Per-mode budgets: 2/3/4/5 attempts
│   │                            Workspace reset between modes and between retries
│   ├── validation/
│   │   └── validator.py         validate() — per-target Gradle + NOT_RUN_ENVIRONMENT_UNAVAILABLE
│   ├── explanation/
│   │   ├── explanation_agent.py ExplanationAgent (LLM #3) — JSON + Markdown
│   │   └── explainer.py         explain() orchestrator
│   ├── evaluation/
│   │   ├── metrics.py           compute_bsr/ctsr/ffsr/efr/hit_at_k (pure functions)
│   │   └── evaluator.py         evaluate() orchestrator
│   └── reporting/
│       ├── report_builder.py    build_report() → list[ReportRow]
│       ├── formatters.py        to_csv / to_json / to_markdown / aggregate_by_mode
│       └── reporter.py          generate_report() orchestrator
├── migrations/
│   └── versions/                Alembic migration files (c4beede → a1b2c3d → 1c03c4a → d7e8f9a)
├── tests/
│   ├── unit/                    353 passing tests (no network, no Docker)
│   └── integration/             DB schema + bundle rehydration (requires Docker)
├── data/
│   └── artifacts/               Local artifact store (gitignored except .gitkeep)
├── docker-compose.yml
├── pyproject.toml
└── alembic.ini
```

---

## LLM Provider

```python
from kmp_repair_pipeline.utils.llm_provider import (
    ClaudeProvider,      # Anthropic SDK — default model: claude-sonnet-4-6
    VertexProvider,      # Vertex Gemini — default alias: gemini-fast → gemini-2.5-flash
    FakeLLMProvider,     # Pre-programmed responses, records calls (for tests)
    NoOpProvider,        # Raises AssertionError if called (test guard)
    get_default_provider # Returns Fake when KMP_LLM_FAKE=1, else selected provider
)

# Override provider/model at runtime
provider = get_default_provider(provider_name="vertex", model_id="gemini-fast")
```

All providers implement `complete(prompt, system, max_tokens, temperature) → LLMResponse`. All agents enforce `temperature=0.0` in code (not config) for reproducibility.

---

## Running Tests

```bash
# Unit tests (no DB, no network required)
pytest tests/unit/ -v

# Integration tests (requires Docker Compose postgres)
docker compose up -d postgres
pytest tests/integration/ -v

# Coverage
pytest tests/unit/ --cov=kmp_repair_pipeline --cov-report=term-missing
```

**Test counts**: 353 unit tests across 16 test files, 0 external dependencies required.

---

## Known Technical Limitations

These are documented gaps — the pipeline degrades gracefully rather than crashing on them.

### Error Parsing

| Gap | Risk | Mitigation |
|-----|------|-----------|
| New Kotlin compiler error formats | Silent miss — errors not counted, EFR appears better | Expand regex patterns in `error_parser.py` before generic catch-alls |
| AAPT2 format changes (Android build tools) | Android resource errors silently skipped | AAPT error pattern tested on build-tools ≤ 36 |
| `tree-sitter-kotlin` grammar update | AST structure change breaks Kotlin parser | Fallback to regex parser; pinned `>=0.1,<0.24` |

### Repair Context

| Gap | Risk | Mitigation |
|-----|------|-----------|
| File content truncation at 8 000 bytes | Large files lose context; agent patches wrong lines | Truncation now annotated with `[truncated: showing N of M bytes]` in prompt and logged at INFO level |
| Only top-k=5 files sent to RepairAgent | Breaking change in rank 6+ file cannot be repaired | Increase `--top-k` flag; use `full_thesis` mode. expect/actual counterparts always added regardless of rank |
| Version catalog alias/artifact changes | `ktor-xml` renamed to `ktor-client-content-negotiation-xmlutil` — agent previously had no structured signal | `diff_catalogs()` in `ingest/catalog_diff.py` computes `CatalogDiff`; surfaced in `repair_context` as `catalog_alias_diff` and `artifact_renames` |

### Patch Application

| Gap | Risk | Mitigation |
|-----|------|-----------|
| `chain_by_file` partial apply | First file patched, second fails → workspace corrupted | **Fixed**: on failure, all applied blocks are reverted in reverse order via `revert_patch` |
| Lightweight diff precheck | Malformed context lines pass precheck but fail `patch` command | Precheck catches syntax issues; `patch` provides final validation |
| Concurrent repair runs | Two concurrent invocations on same case would corrupt workspace | **Fixed**: `WorkspaceLock` (`fcntl.flock`) acquired at start of every `repair()` call; 30 s timeout |

### Validation

| Gap | Risk | Mitigation |
|-----|------|-----------|
| Per-task timeout (600 s) | Total validation time = sum across tasks (unbounded) | Set `--timeout` flag; large projects may take 20+ minutes |
| Unpatched workspace if Phase 9 skipped | Validate runs on original code → false SUCCESS | **Improved**: patch presence check (`_verify_patch_present`) warns; workspace is always reset to HEAD after validate exits |
| iOS only on macOS | iOS KLIB fix never fully validated on Linux CI | `NOT_RUN_ENVIRONMENT_UNAVAILABLE` recorded; `target_coverage_complete = false` in explanation |

### Breaking Change Types Not Fully Covered

The pipeline focuses on the Kotlin version / KLIB ABI class of breaking change. These types are detected but the repair agent may have lower success rates:

| Breaking change type | Detection | Repair coverage |
|---------------------|-----------|----------------|
| KLIB ABI mismatch (iOS KLIBs) | ✅ Full — `w:` warning extracts exact version | ✅ High — version bump prompt is precise |
| JVM metadata incompatibility | ✅ Full — binary version extracted from error | ✅ High — same version bump strategy |
| Dependency artifact rename (`ktor-xml` → `ktor-client-content-negotiation-xmlutil`) | ✅ Detected as API_BREAK_ERROR (symbol_name) + artifact_renames in catalog diff | ⚠️ Medium — agent has structured signal but source-level fixes still needed |
| API removal / deprecation (method renamed, class moved) | ✅ Detected as API_BREAK_ERROR with `symbol_name` extracted | ⚠️ Medium — depends on file content in repair context |
| Gradle plugin API change (AGP 8→9) | ✅ Detected as BUILD_SCRIPT_ERROR | ⚠️ Low — build script changes not in repair context by default |
| expect/actual signature mismatch after API update | ✅ Detected via structural analysis | ✅ Improved — counterpart files appended to repair context regardless of rank |
| Version catalog alias rename | ✅ Detected by `diff_catalogs()` in `ingest/catalog_diff.py` | ⚠️ Medium — structured signal in context; agent must apply |
| Transitive conflict (diamond dependency) | ✅ Detected as DEPENDENCY_CONFLICT_ERROR | ⚠️ Low — resolution strategy not in repair prompts |

---

## Key Design Constraints

- **Deterministic orchestration** — no LLM in the control flow (ingestion, phase execution, retry management, DB writes)
- **Exactly 3 LLM agents** — `LocalizationAgent`, `RepairAgent`, `ExplanationAgent`; no fourth agent
- **Honest validation** — `NOT_RUN_ENVIRONMENT_UNAVAILABLE` rather than silently skipping unavailable targets
- **Auditable** — every agent call logged with prompt, response, token counts, model ID, latency
- **Reproducible** — same inputs → same outputs; `temperature=0.0` enforced in code; all state persisted to DB before exit
- **No cloud** — PostgreSQL via Docker Compose, artifacts local under `data/artifacts/`
- **Workspace isolation** — `git checkout -- . && git clean -fd` before every baseline mode, between retry attempts, and after every validate call; `WorkspaceLock` (fcntl.flock) prevents concurrent access
- **Idempotent re-runs** — `run-before-after` resets both before and after workspaces at the start; `--fresh` also deletes existing execution_runs rows (soft reset without re-cloning)
- **Atomic chain_by_file patching** — on failure, all applied file blocks are reverted before returning; workspace left clean
- **Patch presence verification** — validation warns (non-blocking) when expected touched files are absent from `git diff`
- **No-op detection** — `run-before-after` sets `NO_ERRORS_TO_FIX` when after-state has 0 errors; `metrics` auto-scores BSR=CTSR=FFSR=1.0
- **Validate-in-loop** — `baseline_runner` calls `validate` after each APPLIED patch; REJECTED+progress → repatch with remaining errors in context
- **Anti-downgrade scanner** — `_check_no_version_downgrade` rejects diffs that lower a TOML version alias before `git apply`
- **JAVA_HOME forwarding** — `env_extra={"JAVA_HOME": env.java_home}` passed to all `run_tasks()` calls; prevents Java 25 crash in Kotlin 2.x compiler
- **Build-file evidence first** — `libs.versions.toml` is the first entry in `build_file_contents` sent to RepairAgent; version bump opportunities are immediately visible
- **Anti-hallucination** — repair prompts show the exact current value AND required target value from the version catalog; agents are instructed to use verbatim context lines only

---

## References

Key papers informing this work:
- [BUMP] Reyes et al., "BUMP: A Benchmark of Reproducible Breaking Dependency Updates", SANER 2024
- [Byam] Reyes et al., "Byam: Fixing Breaking Dependency Updates with LLMs", arXiv 2025
- [Fruntke] Fruntke & Krinke, "Automatically Fixing Dependency Breaking Changes", FSE 2025
- [Jayasuriya] Jayasuriya et al., "Understanding Breaking Changes in the Wild", ISSTA 2023
- [Hejderup] Hejderup & Gousios, "Can we trust tests to automate dependency updates?", JSS 2022
- [Breaking-Good] Reyes et al., "Breaking-Good: Explaining Breaking Dependency Updates", SCAM 2024
