# Architecture Memo — kmp-repair-pipeline

**Thesis**: A Multi-Agent System to Repair Breaking Changes Caused by Dependency Updates in Kotlin Multiplatform
**Date**: 2026-04-04
**Status**: PHASE 0 — Grounding

---

## 1. Core design principle

The system is an **evidence-and-decision pipeline**, not a generic coding agent.
Every stage either produces typed evidence or makes a decision backed by that evidence.
No stage relies on free-form LLM memory as primary state.

---

## 2. Five pipeline stages (from thesis)

| Stage | Name | Key output |
|-------|------|-----------|
| 1 | Update ingestion and typing | `DependencyUpdateEvent`, update class, raw PR/diff evidence |
| 2 | Before/after execution and evidence capture | `ExecutionEvidence` (stdout, stderr, exit codes, parsed errors) |
| 3 | Hybrid impact localization | `LocalizationResult` (ranked candidates, score breakdowns, source-set attribution) |
| 4 | Patch synthesis | `PatchAttempt` (diff, touched entities, prompt/response log) |
| 5 | Multi-target validation and explanation | `ValidationRun`, `ExplanationArtifact` |

---

## 3. Three agents (v1)

| Agent | Stage | Input | Output |
|-------|-------|-------|--------|
| `LocalizationAgent` | 3 | `ExecutionEvidence` + `StructuralEvidence` | `LocalizationResult` |
| `RepairAgent` | 4 | `LocalizationResult` + restricted context window | `PatchAttempt` |
| `ExplanationAgent` | 5 | Full `CaseBundle` | `ExplanationArtifact` (JSON + Markdown) |

All agents are backed by an LLM (Claude by default). All prompts and responses are logged.
Agents do not call each other. The orchestrator calls them in sequence.

---

## 4. Typed Case Bundle

The Case Bundle is the single source of truth for one repair case.
It is stored across multiple normalized PostgreSQL tables (not one JSONB blob).

```
CaseBundle
  ├── UpdateEvidence        — version delta, PR ref, build-file diff, SBOM (auxiliary)
  ├── ExecutionEvidence     — before/after runs, parsed ErrorObservations, env metadata
  ├── StructuralEvidence    — source-set map, symbol table, expect/actual pairs, imports
  ├── RepairEvidence        — LocalizationResult, PatchAttempts, prompt/response log
  ├── ValidationEvidence    — ValidationRuns per target, aggregate outcome
  └── ExplanationEvidence   — ExplanationArtifact (JSON + Markdown path)
```

---

## 5. Update classification (Stage 1)

Following the taxonomy from the thesis (informed by [Jayasuriya et al., He et al., Gromov & Chernyshev]):

| Class | Description |
|-------|-------------|
| `DIRECT_LIBRARY` | Direct library or API version bump |
| `PLUGIN_TOOLCHAIN` | Gradle plugin or Kotlin toolchain version change |
| `TRANSITIVE` | Transitive dependency version change |
| `PLATFORM_INTEGRATION` | CocoaPods, SPM, or other platform-integration change |
| `UNKNOWN` | Could not be classified |

---

## 6. Validation status vocabulary

All validation outcomes must use these explicit status values:

| Status | Meaning |
|--------|---------|
| `SUCCESS_REPOSITORY_LEVEL` | All targets pass |
| `PARTIAL_SUCCESS` | Some targets pass, some fail or unavailable |
| `FAILED_BUILD` | Build fails on at least one required target |
| `FAILED_TESTS` | Build passes but tests fail |
| `NOT_RUN_ENVIRONMENT_UNAVAILABLE` | Target could not be executed in current env |
| `INCONCLUSIVE` | Execution produced ambiguous results |

---

## 7. Baselines (for evaluation)

| Baseline | Description |
|----------|-------------|
| `raw_error` | Model receives only dependency diff + raw compiler/test error |
| `context_rich_single_shot` | Richer prompt (failing lines, build info, dep diff) but no staged localization |
| `iterative_agentic` | May iterate/retry but without the full evidence model |
| `full_thesis` | Complete staged pipeline with all three agents |

---

## 8. Key infrastructure decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Primary language | Python | Prototype already in Python; ecosystem fit |
| Structured storage | PostgreSQL via Docker Compose | Durable, queryable, local, project deliverable |
| Migrations | Alembic | Standard for SQLAlchemy projects |
| Schema definition | SQLAlchemy ORM | Typed, readable, migration-friendly |
| Domain models | Pydantic v2 | Already used in prototype; validation + serialization |
| LLM abstraction | Provider interface + Claude implementation + fake for tests | Testability and provider flexibility |
| Artifact storage | Local filesystem under `data/artifacts/` | Simple, auditable, no cloud dependency |
| Kotlin parsing | tree-sitter-kotlin + regex fallback | Inherited from prototype; proven robust |
| CLI | Click | Inherited from prototype |

---

## 9. Evaluation metrics

| Metric | Abbrev | Definition |
|--------|--------|-----------|
| Build Success Rate | BSR | Fraction of cases where post-repair validation workflow succeeds |
| Cross-Target Success Rate | CTSR | Fraction where ALL declared target validations succeed |
| File Fix Success Rate | FFSR | Fraction of broken files correctly repaired |
| Error Fix Rate | EFR | Fraction of individual compile/test errors resolved |
| Hit@k | Hit@k | Localized files overlap with accepted-fix files at rank k |
| Source-set attribution accuracy | — | Correct shared/platform/build attribution |

---

## 10. What is deliberately out of scope (v1)

- Cloud CI execution (all runs are local or in local Docker)
- Automated PR submission back to repositories
- More than three agent roles
- Multi-hop transitive repair across more than one dependency level
- iOS/macOS execution on Linux-only environments (status: `NOT_RUN_ENVIRONMENT_UNAVAILABLE`)
