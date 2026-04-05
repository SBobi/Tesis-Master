# Refactor Map — Old Prototype → Thesis Architecture

**From**: `kmp-impact-analyzer` (v1 prototype, GitHub: estebancastelblanco/kmp-production-sample-impact-demo)
**To**: `kmp-repair-pipeline` (thesis implementation)

---

## Disposition summary

| Old module | Size/role | Disposition | Target module |
|-----------|-----------|-------------|--------------|
| `cli.py` | Click entry point (analyze, run-scenario, detect-version-changes, evaluate) | **Refactor** — keep Click, add new commands, restructure into `cli/` package | `cli/` |
| `config.py` | `AnalysisConfig` dataclass | **Refactor** — extend to `PipelineConfig`, add DB settings, LLM settings, artifact dir | `cli/config.py` |
| `contracts.py` | Pydantic domain models for 5-phase pipeline | **Refactor** — split into `domain/` types and `case_bundle/` models; promote to DB-persisted records | `domain/`, `case_bundle/` |
| `pipeline.py` | Sequential orchestrator | **Refactor** — break into per-phase CLI commands + `Orchestrator` class; add Case Bundle writes | `cli/`, `case_bundle/` |
| `github_version_change.py` | TOML diff for version catalog | **Reuse** — move to `ingest/` with minor cleanup | `ingest/version_catalog.py` |
| `phase1_shadow/shadow.py` | Project copy + version injection | **Reuse** — move to `case_builder/`; add reproducibility manifest | `case_builder/shadow.py` |
| `phase1_shadow/toml_parser.py` | TOML version catalog parser | **Reuse** — move to `ingest/`; minor cleanup | `ingest/toml_parser.py` |
| `phase2_static/kotlin_parser.py` | tree-sitter + regex parser | **Reuse** — move to `static_analysis/`; improve source-set inference | `static_analysis/kotlin_parser.py` |
| `phase2_static/symbol_table.py` | FQCN → file mapping | **Reuse** — move to `static_analysis/` | `static_analysis/symbol_table.py` |
| `phase2_static/dependency_graph.py` | BFS propagation graph | **Reuse** — move to `static_analysis/`; persist to DB | `static_analysis/dependency_graph.py` |
| `phase2_static/expect_actual.py` | expect/actual resolution | **Reuse** — move to `static_analysis/`; link to StructuralEvidence | `static_analysis/expect_actual.py` |
| `phase2_static/analyzer.py` | Static analysis orchestrator | **Refactor** — move to `static_analysis/`; persist StructuralEvidence to DB | `static_analysis/analyzer.py` |
| `phase2_static/source_metrics.py` | LOC / cyclomatic complexity | **Reuse** — move to `static_analysis/` | `static_analysis/source_metrics.py` |
| `phase3_dynamic/droidbot_runner.py` | DroidBot APK testing | **Defer** — move to `runners/droidbot.py`; currently low priority for thesis v1 | `runners/droidbot.py` |
| `phase3_dynamic/utg_parser.py` | UI transition graph parsing | **Defer** — move to `runners/` | `runners/utg_parser.py` |
| `phase3_dynamic/utg_diff.py` | Screen regression detection | **Defer** — move to `runners/` | `runners/utg_diff.py` |
| `phase4_consolidate/consolidator.py` | Merge static + dynamic evidence | **Refactor** — becomes part of the LocalizationAgent input construction | `localization/evidence_builder.py` |
| `phase4_consolidate/code_screen_mapper.py` | File-to-UI mapping | **Defer** — low priority for v1 without DroidBot | `localization/code_screen_mapper.py` |
| `phase5_visualize/codecharta_builder.py` | CodeCharta JSON export | **Preserve** — move to `reporting/codecharta.py`; useful for visualization | `reporting/codecharta.py` |
| `phase5_visualize/tree_builder.py` | File tree construction | **Preserve** — move to `reporting/` | `reporting/tree_builder.py` |
| `reporting/report_site.py` | HTML + SVG + Markdown report | **Refactor** — keep HTML/Markdown, add structured JSON export, align with ExplanationArtifact | `reporting/report_site.py` |
| `evaluation/scorer.py` | Precision/recall/F1 calculation | **Refactor** — extend with BSR, CTSR, FFSR, EFR, Hit@k metrics | `evaluation/scorer.py` |
| `evaluation/report.py` | Evaluation report | **Refactor** — extend with per-baseline comparison | `evaluation/report.py` |
| `utils/git_utils.py` | Clone + git detection | **Reuse** — move to `utils/` | `utils/git_utils.py` |
| `utils/json_io.py` | JSON serialization | **Reuse** — move to `utils/` | `utils/json_io.py` |
| `utils/log.py` | Logging configuration | **Reuse** — move to `utils/` | `utils/log.py` |
| `scenarios/` | YAML test scenarios | **Preserve** — move to `tests/fixtures/scenarios/` | `tests/fixtures/scenarios/` |
| `gradle-init/` | Gradle init scripts | **Preserve** — keep in place | `gradle-init/` |
| `Dockerfile` | Python + Java container | **Refactor** — update to include PostgreSQL client, keep Java for Gradle | `Dockerfile` |
| `docker-compose.yml` | (missing from prototype) | **Add** — new file, PostgreSQL + pipeline services | `docker-compose.yml` |
| GitHub Actions workflow | impact-analysis.yml | **Preserve as reference** — new workflow will differ significantly | docs reference only |

---

## New modules with no prototype equivalent

| New module | Stage | Purpose |
|-----------|-------|---------|
| `storage/models.py` | All | SQLAlchemy ORM table definitions |
| `storage/repositories.py` | All | DB access layer (no raw SQL in business logic) |
| `migrations/` | All | Alembic migration files |
| `case_bundle/bundle.py` | All | Typed Case Bundle: aggregate + per-section models |
| `case_bundle/serialization.py` | All | Rehydrate from DB |
| `ingest/event_detector.py` | Stage 1 | PR/diff → `DependencyUpdateEvent` |
| `ingest/event_classifier.py` | Stage 1 | Classify into DIRECT_LIBRARY / PLUGIN_TOOLCHAIN / TRANSITIVE / PLATFORM_INTEGRATION |
| `runners/gradle_runner.py` | Stage 2 | Execute Gradle tasks, capture stdout/stderr/exit codes |
| `runners/env_probe.py` | Stage 2 | Detect available targets (Android SDK, Xcode, emulator) |
| `runners/error_parser.py` | Stage 2 | Parse build/test output → `ErrorObservation` records |
| `localization/localization_agent.py` | Stage 3 | `LocalizationAgent` — LLM-backed, reads StructuralEvidence + ExecutionEvidence |
| `localization/candidate_ranker.py` | Stage 3 | Deterministic ranking + score breakdown |
| `repair/llm_provider.py` | Stage 4 | LLM provider interface + Claude impl + Fake impl |
| `repair/repair_agent.py` | Stage 4 | `RepairAgent` — generates patches, logs prompts |
| `repair/patch_applicator.py` | Stage 4 | Apply unified diff to working copy |
| `validation/validator.py` | Stage 5 | Re-run Gradle tasks on patched revision |
| `validation/outcome_aggregator.py` | Stage 5 | Aggregate per-target outcomes → repository-level status |
| `explanation/explanation_agent.py` | Stage 5 | `ExplanationAgent` — produces JSON + Markdown explanation |
| `baselines/raw_error.py` | Eval | Baseline: raw error only |
| `baselines/context_rich.py` | Eval | Baseline: context-rich single-shot (Byam-inspired) |
| `baselines/iterative_agentic.py` | Eval | Baseline: iterative agentic (Fruntke-inspired) |
| `evaluation/metrics.py` | Eval | BSR, CTSR, FFSR, EFR, Hit@k, attribution accuracy |
| `evaluation/baseline_comparison.py` | Eval | Aggregate per-baseline, per-case results |

---

## Priority ordering for phases

1. **PHASE 1** — Skeleton: create directory structure, move reusable files, fix imports.
2. **PHASE 2** — DB + artifact store: Docker Compose, SQLAlchemy models, Alembic.
3. **PHASE 3** — Typed Case Bundle: Pydantic models for all sections.
4. **PHASE 4** — Ingest: event detection and classification.
5. **PHASE 5** — Case builder: before/after reproducibility.
6. **PHASE 6** — Before/after execution: Gradle runner, error parser, env probe.
7. **PHASE 7** — Structural analysis: port + extend existing KMP parser.
8. **PHASE 8** — Localization: candidate ranker + LocalizationAgent.
9. **PHASE 9** — Repair: LLM provider, RepairAgent, patch applicator, baselines.
10. **PHASE 10** — Validation: re-run + aggregate multi-target outcomes.
11. **PHASE 11** — Explanation: ExplanationAgent, JSON + Markdown artifacts.
12. **PHASE 12** — Evaluation: metrics, baseline comparison, export.
13. **PHASE 13** — Hardening: smoke tests, runbooks, doctor command.

---

## Reuse risk notes

- `kotlin_parser.py`: tree-sitter grammar version is pinned to 0.21–0.24; verify compatibility when upgrading.
- `droidbot_runner.py`: DroidBot requires Android emulator + ADB; deprioritized for v1 but preserved.
- `report_site.py`: Spanish-language strings inside; keep for now, refactor to template later.
- `phase1_shadow/shadow.py`: copies the entire project tree; needs a size guard for large repos.
