# kmp-repair-pipeline — CLAUDE.md

## What this is

A multi-agent pipeline to **repair breaking changes caused by dependency updates in Kotlin Multiplatform (KMP)** repositories.
Thesis: *"A Multi-Agent System to Repair Breaking Changes Caused by Dependency Updates in Kotlin Multiplatform"*

All 13 phases are implemented and tested (353/353 unit tests passing).

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

**LLM parameter summary** (hardcoded in each agent — do not change without thesis review):

| Agent | max_tokens | top_k context | Notes |
|-------|-----------|--------------|-------|
| LocalizationAgent | 4096 | top-20 candidates | JSON output only |
| RepairAgent | 8192 | top-5 files (8000-byte limit per file) | unified diff or PATCH_IMPOSSIBLE |
| ExplanationAgent | 2048 | top-5 candidates, 30 errors | JSON + Markdown |

---

## Four repair baselines (fixed vocabulary) and iteration budgets

| Mode | Context | Attempt budget |
|------|---------|:-----------:|
| `raw_error` | dep diff + raw compiler errors only | 2 |
| `context_rich` | + localized files + source-set info | 3 |
| `iterative_agentic` | context_rich + retry loop + prev attempts | 4 |
| `full_thesis` | full Case Bundle evidence + all previous attempts | 5 |

All four modes use the same retry loop — they stop as soon as a patch applies. Budgets are defined in `baselines/baseline_runner.py::_MODE_BUDGETS` and can be overridden with `max_attempts` at call time.

---

## Six evaluation metrics

| Metric | Passing condition |
|--------|------------------|
| BSR | overall ValidationStatus == SUCCESS_REPOSITORY_LEVEL |
| CTSR | no runnable target has FAILED_BUILD |
| FFSR | all runnable targets == SUCCESS_REPOSITORY_LEVEL |
| EFR | penalty-adjusted fraction of original errors eliminated (None if no originals) |
| EFR_normalized | same formula as EFR but dedup key omits line number — prevents counting line-shift as fix |
| Hit@k | any ground-truth file in top-k candidates (None if no gt) |
| source_set_accuracy | fraction of candidates with correct source_set (None if no gt) |

NOT_RUN_ENVIRONMENT_UNAVAILABLE targets are excluded from BSR/CTSR/FFSR.

**EFR uses a penalty formula** to prevent score inflation when a patch replaces N errors with M > N new errors:
```
EFR = max(0, raw_efr - max(0, |remaining| - |original|) / |original|)
```

Error deduplication key: `(error_type, file_path, line, message)` for EFR; `(error_type, file_path, message)` for EFR_normalized (no line number to avoid counting line-shift as fix).

---

## Repair case status lifecycle

```
CREATED → SHADOW_BUILT → EXECUTED → LOCALIZED → PATCH_ATTEMPTED
       → VALIDATED → EXPLAINED → EVALUATED
       EXECUTED → NO_ERRORS_TO_FIX → EVALUATED  (non-breaking update shortcut)
       (any stage → FAILED on unrecoverable error)
```

`NO_ERRORS_TO_FIX` is set by `run-before-after` when the after-state compiles with 0 errors.
The repair and validate phases are skipped entirely; `metrics` auto-scores BSR=CTSR=FFSR=1.0.

---

## Language and runtime rules

- **Python ≥ 3.10** is the primary language for all pipeline, agent, and tooling code. `.python-version` pins Python 3.12 for pyenv/mise.
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

**Migration chain** (must remain linear):
```
c4beede9862d  initial schema (16 tables)
a1b2c3d4e5f6  add pr_title to dependency_events
1c03c4a3181a  add required_kotlin_version to error_observations
d7e8f9a0b1c2  add symbol_name to error_observations + efr_normalized to evaluation_metrics
(always run `alembic upgrade head` after git pull)
```

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
- Report uncertainty in every explanation artifact (`target_coverage_complete = false` when any target was skipped).

---

## Phase execution rules

- Implement and test **one phase at a time**.
- Each phase must be independently runnable via the CLI.
- Every phase must be **reproducible**: same inputs → same outputs.
- Phases persist state to the DB and artifact store before exiting; they do not hold critical state only in memory.

---

## Workspace isolation and idempotency

The pipeline guarantees clean state at every phase boundary:

- **`run-before-after`**: At the start, resets BOTH before and after workspaces to HEAD (`git checkout -- . && git clean -fd`) to discard any stale patches from previous runs. Use `--fresh` to also delete existing `execution_runs` rows and reset status to `SHADOW_BUILT` for a full soft-reset without re-cloning.
- **`validate`**: Resets the after-clone to HEAD after validation completes (whether VALIDATED or REJECTED). The patch is persisted in the DB and artifact store — the workspace copy is disposable.
- **`baseline_runner`**: Resets before each mode, between retries within a mode, and at the end of `run_all_baselines`. No cross-mode contamination.

## No-op detection (non-breaking updates)

When `run-before-after` finds 0 errors in the after-state, it sets case status to `NO_ERRORS_TO_FIX`. Downstream behaviour:
- `repair` CLI prints a message and returns without calling agents.
- `metrics` auto-scores all four baseline modes BSR=1.0 / CTSR=1.0 / FFSR=1.0 / EFR=N/A without requiring any repair or validate runs.

## Validate-in-loop (repair → validate cycle)

`baseline_runner.run_baseline()` now calls `validate()` immediately after each APPLIED patch:
- **VALIDATED** → done, exit loop.
- **REJECTED, same errors as original** → no progress, exit loop.
- **REJECTED, fewer errors than original** → progress detected; remaining errors are stored in `patch_attempt.retry_reason` as JSON and surfaced in `repair_context["previous_attempts"]` for the next agent call; loop continues with remaining budget.

Workspace is reset between every repair+validate cycle.

## Anti-downgrade scanner

`repairer._check_no_version_downgrade(diff_text)` scans TOML version alias lines in unified diffs.  If any alias is lowered (e.g. `"2.3.0"` → `"2.1.0"`), the diff is rejected with `FAILED_APPLY` before `git apply` is attempted.  The RepairAgent system prompt also contains an explicit rule (rule 9) prohibiting version downgrades.

## Java version requirement

**Always use Java 21 (Temurin)** for all pipeline runs. Java 25 causes a hard crash:
```
IllegalArgumentException: 25.0.1
  at org.jetbrains.kotlin.com.intellij.util.lang.JavaVersion.parse(...)
```
The Kotlin 2.x compiler cannot parse Java 25 EA/LTS version strings.

```bash
# Set Java 21 before any kmp-repair command:
export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home

# Or use the bootstrap script (auto-detects Temurin 21, ANDROID_HOME, GCP creds):
source scripts/bootstrap_env.sh

# Or load from .env (which sets JAVA_HOME automatically):
export $(grep -v '^#' .env | xargs) && kmp-repair <command>
```

The `.env` file sets `JAVA_HOME` to the Temurin 21 installation. Always load it.

---

## Android SDK / local.properties

The pipeline auto-writes `local.properties` with `sdk.dir` to **both** the before and after workspaces whenever the Android SDK is detected during `run-before-after` or `validate`. This mirrors how Gradle itself finds the SDK and prevents the "pre-existing Android build failure" caused by the before workspace missing `local.properties`.

Priority order for SDK detection:
1. `ANDROID_HOME` or `ANDROID_SDK_ROOT` env var
2. `local.properties` → `sdk.dir` at project root (read AND written by `env_detector.py`)
3. `~/Library/Android/sdk` (macOS default)
4. `~/Android/Sdk` (Linux default)

If none are found, Android targets are recorded as `NOT_RUN_ENVIRONMENT_UNAVAILABLE`.

---

## Build and development commands

```bash
# Setup
docker compose up -d postgres      # start local DB
python -m pip install -e ".[dev]"  # install in editable mode
alembic upgrade head                # run migrations

# Environment bootstrap (auto-detects JAVA_HOME, ANDROID_HOME, GCP credentials)
source scripts/bootstrap_env.sh

# CLI entry point
kmp-repair --help

# Full pipeline (one case)
kmp-repair discover --repo owner/repo
kmp-repair ingest --repo owner/repo --pr-number 42
kmp-repair build-case <event_id>
kmp-repair run-before-after <case_id>              # standard run
kmp-repair run-before-after <case_id> --fresh      # soft-reset: delete existing execution_runs first
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
pytest tests/unit/          # 353 tests, no DB required
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
scripts/           — bootstrap_env.sh (env auto-detection), run_e2e.sh (full e2e pipeline runner)
tests/
  unit/            — 310 passing tests (no network, no Docker)
  integration/     — DB schema + bundle rehydration (requires Docker)
data/
  artifacts/       — local artifact store (gitignored except .gitkeep)
docker-compose.yml
pyproject.toml
alembic.ini
.python-version    — Python 3.12 (pyenv/mise pin)
```

---

## KLIB ABI error handling (system-level, not manual)

The pipeline autonomously detects and guides repair of Kotlin/Native KLIB ABI incompatibilities.
Do NOT manually intervene when a KLIB error appears — let the system handle it end-to-end.

### Error classification (error_parser.py — 11 patterns)

Patterns are applied in order (more specific first):

| Pattern | Signal | Action |
|---------|--------|--------|
| `e: file.kt:(line,col): msg` | Kotlin compile error with location | COMPILE_ERROR |
| `e: file.kt:line:col: msg` | Kotlin compile error (simple format) | COMPILE_ERROR |
| Could not resolve / Could not find | Gradle dependency failure | DEPENDENCY_RESOLUTION_ERROR |
| `*.xml:line: error:` | Android AAPT2 resource error | RESOURCE_ERROR |
| `e: KLIB resolver: ...` | KLIB ABI error (presence signal) | KLIB_ABI_ERROR |
| `w: KLIB resolver: Skipping '...' ... produced by 'X.Y.Z'` | KLIB w: warning with **exact required version** | KLIB_ABI_ERROR + `required_kotlin_version` |
| `w: KLIB resolver: ...` (simple) | KLIB w: warning without version | KLIB_ABI_ERROR |
| `binary version of its metadata is X.Y.Z` | JVM metadata incompatibility | KLIB_ABI_ERROR + `required_kotlin_version` |
| `actual metadata version is X.Y.Z` | In-source class metadata mismatch | KLIB_ABI_ERROR + `required_kotlin_version` |
| `Conflict with dependency '...'` | Transitive / diamond dependency conflict | DEPENDENCY_CONFLICT_ERROR |
| `Could not apply plugin [id: '...']` | Gradle plugin API failure | BUILD_SCRIPT_ERROR |
| `Unresolved reference: Foo` | API removal / rename (extracts `symbol_name`) | API_BREAK_ERROR + `symbol_name` |
| `Type mismatch: inferred type is Foo` | Type API change | API_BREAK_ERROR |
| `e: error: ...` / `e: msg` | Generic Kotlin error (fallback) | COMPILE_ERROR |
| `* What went wrong: ...` | Gradle init / JVM crash | GRADLE_INIT_ERROR |

The `w:` line is the most precise signal: it tells the system the exact Kotlin version to bump TO.
Always add new KLIB-specific patterns **before** generic patterns.

### Version catalog (structural_builder.py)
- `gradle/libs.versions.toml` is parsed at `analyze-case` time.
- Result: `StructuralEvidence.version_catalog: dict[str, str]` (the `[versions]` section).
- Logged at analyze-case: `"Case X: version catalog has N entries (kotlin=2.2.0)"`.

### Cascade version consolidation (bundle.py)
- `repair_context["required_kotlin_version"]` — the **MAX** across all `w:` warnings and JVM metadata errors.
- `repair_context["kotlin_cascade_constraints"]` — `dict[library_name → required_kotlin_version]` — shows each library's individual constraint so the agent can reason about the full picture.
- `_max_kotlin_version()` in `bundle.py` computes the max using tuple comparison so `"2.1.20" > "2.1.9"`.

Example cascade:
```
koin 4.1.0  → required_kotlin_version = "2.1.20"
ktor 3.4.1  → required_kotlin_version = "2.3.0"
max = "2.3.0"  ← the value written into the diff context
```

### Catalog diff (ingest/catalog_diff.py)
- `diff_catalogs(before, after)` computes a `CatalogDiff` from two `VersionCatalog` objects (or TOML strings / paths).
- `CatalogDiff` surfaces: **alias renames** (same artifact module, different alias), **artifact renames** (same alias, different module), **added aliases**, **removed aliases**.
- The diff is stored in `UpdateEvidence.catalog_alias_diff` (dict) and `UpdateEvidence.artifact_renames` (list of dicts).
- Both fields are propagated into `repair_context["catalog_alias_diff"]` and `repair_context["artifact_renames"]` so the RepairAgent can identify alias/module changes that otherwise appear as opaque "Unresolved reference" errors.

### Repair context (bundle.py + repairer.py)
- `repair_context["required_kotlin_version"]` — the exact version from `w:` warning (or None).
- `repair_context["version_catalog"]` — current versions from `libs.versions.toml`.
- `repair_context["build_file_contents"]` — `libs.versions.toml` is always listed FIRST.
- `repair_context["catalog_alias_diff"]` — structured catalog diff: alias renames and artifact renames.
- `repair_context["artifact_renames"]` — list of `{alias, before_module, after_module}` for renamed artifacts.
- **expect/actual coupling**: if a localized file participates in an expect/actual pair (from `StructuralEvidence.expect_actual_pairs`), its counterpart(s) are appended to `localized_files` even if they fall outside top-k. This prevents half-patched expect/actual mismatches.
- **Visible truncation**: files larger than 8000 bytes are truncated and marked with `[truncated: showing N of M bytes]`. The size is also logged at INFO level.
- RepairAgent receives a `## !! REQUIRED KOTLIN VERSION !!` block when the version is known:
  ```
  Current value in gradle/libs.versions.toml: kotlin = "2.2.0"
  Required value (max across all library constraints): kotlin = "2.3.0"
  You MUST change: -kotlin = "2.2.0"  →  +kotlin = "2.3.0"
  ```
  This anti-hallucination approach prevents the agent from inventing wrong old values in diff context lines.

### Priority injection (localizer.py)
- `libs.versions.toml` is injected as **rank-0** localization candidate (score=1.0) when any `KLIB_ABI_ERROR` is present.
- The static import graph has no edges to build files — injection is the only way to surface it.
- Skipped if `libs.versions.toml` already appears in the scored list.

### Workspace isolation (baseline_runner.py)
- `git checkout -- . && git clean -fd` is run before every baseline mode.
- Also run between iterative retries within a mode.
- Ensures each baseline sees the original unpatched workspace — prevents cross-mode contamination.

### pr_title (models.py + domain/events.py)
- `DependencyEvent.pr_title` (nullable Text column, migration `a1b2c3d4e5f6`).
- Surfaced in all repair prompts as `PR: Bump ktor from 3.1.3 to 3.4.1`.
- Gives RepairAgent human-readable context about what changed.

---

## Known technical gaps (audit findings)

These are documented limitations. Do NOT silently work around them — document any change here.

### Error parsing brittle zones
- **Pattern scope**: Error patterns cover Kotlin 2.x compiler output format. If Kotlin 3.x changes the `w:` warning text or metadata error format, `required_kotlin_version` will silently become `None` and the repair prompt will lose the required-version block.
- **AAPT2 variations**: Android resource error pattern assumes format `file.xml:line: error: msg`. Tested on AGP 8.x. Later versions may differ.
- **Tree-sitter grammar**: `tree-sitter-kotlin` pinned at `>=0.21,<0.24`. Grammar API changes can break the AST parser. Fallback to regex is automatic but loses accuracy.

### Repair context limits
- **File truncation**: File content sent to RepairAgent is capped at 8 000 bytes per file. Files larger than this are truncated with `"... [truncated]"`. The agent receives incomplete context for large files.
- **Top-k ceiling**: Only the top-k (default 5) localized files are sent to RepairAgent. A breaking change in a file ranked 6th or lower cannot be repaired in one pass.
- **Breaking change types with lower repair coverage**:
  - Dependency artifact rename (e.g. `ktor-xml` → `ktor-client-content-negotiation-xmlutil`): detected as COMPILE_ERROR, but agent must infer the mapping from the error message. No structured artifact-rename signal exists.
  - API removal / method rename: classified as COMPILE_ERROR (Unresolved reference). Agent can repair if file content is in context.
  - Gradle plugin API change: may appear as GRADLE_INIT_ERROR. Build scripts are not in repair context by default.
  - Version catalog alias rename: no diff between before/after catalog is computed. The alias rename is invisible to the agent.
  - Transitive dependency conflict (diamond): DEPENDENCY_RESOLUTION_ERROR is classified but no resolution strategy is in any repair prompt.

### Patch application
- **`chain_by_file` partial apply**: Fixed — if the second file fails, all previously applied blocks are reverted in reverse order via `revert_patch`. The workspace is left clean.
- **Workspace lock**: `WorkspaceLock` (`utils/workspace_lock.py`) wraps every `repair()` call with an exclusive `fcntl.flock` to prevent concurrent CLI invocations corrupting the same workspace. Default timeout 30 s.
- **Anti-downgrade scanner**: `_check_no_version_downgrade` in `repairer.py` rejects diffs that lower a TOML version alias. Patched to run before `git apply`.

### Validation
- **Timeout is per-task, not per-case**: Total validation time = sum across all tasks across all targets. For large KMP projects this can exceed 30 minutes.
- **Workspace assumption**: `validate` now runs a **patch presence check** (`_verify_patch_present`) before each target. If none of the expected touched files appear in `git diff --name-only`, a WARNING is logged. Validation continues (non-blocking) but the log surfaces the misconfiguration.
- **Workspace reset after validate**: `validate()` always resets the after-clone to HEAD on exit. The patch survives in DB + artifact store.

### Evaluation
- **EFR deduplication key** includes the line number. If a patch moves an error to a different line but keeps the same message, standard EFR counts it as fixed. Use `efr_normalized` (dedup key without line) for a conservative lower-bound estimate.
- **Hit@k and source_set_accuracy return None** when no ground truth is provided. All aggregation code must handle None.
- **Evaluator call order** (evaluator.py): `_load_remaining_errors` is called BEFORE `_load_validation_for_patch`. This ordering is tested and must not be reversed. Reversing it breaks `test_validation_is_scoped_per_baseline_attempt`.

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
- Do not manually fix KLIB errors by editing files — the system must detect and repair them autonomously.
- Do not change agent temperature from 0.0 — reproducibility is a thesis requirement.
- Do not reverse the call order in `evaluator.py` (remaining errors before validation) — tests will break.
- Do not bump `tree-sitter-kotlin` past `<0.24` without verifying the Kotlin parser still works.
- Do not use `efr` and `efr_normalized` interchangeably — EFR includes line number in dedup key; EFR_normalized does not. Both are computed and stored; choose based on analysis goal.
- Do not remove the `WorkspaceLock` wrapper in `repair()` — it prevents workspace corruption from concurrent repair/validate invocations on the same case.
