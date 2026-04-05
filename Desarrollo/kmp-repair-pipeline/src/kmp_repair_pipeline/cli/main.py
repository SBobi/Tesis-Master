"""CLI entry point for kmp-repair-pipeline."""

from __future__ import annotations

from pathlib import Path

import click

from ..utils.log import get_logger

log = get_logger(__name__)


@click.group()
@click.version_option(version="0.1.0", prog_name="kmp-repair")
def main() -> None:
    """kmp-repair — Multi-agent dependency repair for Kotlin Multiplatform."""


# ---------------------------------------------------------------------------
# detect-changes — wraps ingest/version_catalog diff
# ---------------------------------------------------------------------------

@main.command("detect-changes")
@click.option("--before", "before_toml", required=True, type=click.Path(exists=True),
              help="Path to base libs.versions.toml")
@click.option("--after", "after_toml", required=True, type=click.Path(exists=True),
              help="Path to head libs.versions.toml")
@click.option("--format", "output_format", type=click.Choice(["json", "text"]),
              default="json", show_default=True)
def detect_changes(before_toml: str, after_toml: str, output_format: str) -> None:
    """Detect changed dependency versions between two Gradle version catalogs."""
    from ..ingest.version_catalog import detect_version_changes

    change_set = detect_version_changes(before_toml, after_toml)

    if output_format == "json":
        click.echo(change_set.model_dump_json(indent=2))
        return

    if not change_set.has_changes:
        click.echo("No changes detected.")
        return
    for c in change_set.changes:
        click.echo(f"{c.dependency_group}  {c.before} → {c.after}  (key: {c.version_key})")


# ---------------------------------------------------------------------------
# analyze-static — run KMP-aware static analysis
# ---------------------------------------------------------------------------

@main.command("analyze-static")
@click.option("--repo", required=True, type=click.Path(exists=True),
              help="Path to the KMP repository")
@click.option("--dependency", required=True, help="Dependency group (e.g. io.ktor)")
@click.option("--before-version", required=True, help="Version before the change")
@click.option("--after-version", required=True, help="Version after the change")
@click.option("--output-dir", default="output", show_default=True, help="Output directory")
def analyze_static(
    repo: str,
    dependency: str,
    before_version: str,
    after_version: str,
    output_dir: str,
) -> None:
    """Run KMP-aware static analysis on a repository."""
    from ..static_analysis.analyzer import run_static_analysis
    from ..utils.json_io import save_json

    result = run_static_analysis(repo, dependency, before_version, after_version)
    out_path = Path(output_dir) / "static_analysis.json"
    save_json(result, out_path)
    click.echo(f"Static analysis complete: {result.total_impacted}/{result.total_project_files} files impacted")
    click.echo(f"Results saved to {out_path}")


# ---------------------------------------------------------------------------
# build-shadow — create before/after shadow pair
# ---------------------------------------------------------------------------

@main.command("build-shadow")
@click.option("--repo", required=True, type=click.Path(exists=True),
              help="Path to the KMP repository")
@click.option("--dependency", required=True, help="Dependency group")
@click.option("--before-version", required=True, help="Before version")
@click.option("--after-version", required=True, help="After version")
@click.option("--output-dir", default="output", show_default=True)
@click.option("--init-script", default="", help="Path to Gradle init script")
def build_shadow(
    repo: str,
    dependency: str,
    before_version: str,
    after_version: str,
    output_dir: str,
    init_script: str,
) -> None:
    """Create reproducible before/after shadow copies of the repository."""
    from ..case_builder.shadow import build_shadow as _build
    from ..utils.json_io import save_json

    manifest = _build(
        repo_path=repo,
        dependency_group=dependency,
        before_version=before_version,
        after_version=after_version,
        output_dir=output_dir,
        init_script_path=init_script or None,
    )
    out_path = Path(output_dir) / "shadow_manifest.json"
    save_json(manifest, out_path)
    click.echo(f"Shadow manifest saved to {out_path}")
    click.echo(f"  BEFORE → {manifest.before_dir}")
    click.echo(f"  AFTER  → {manifest.after_dir}")


# ---------------------------------------------------------------------------
# evaluate — score results against ground truth
# ---------------------------------------------------------------------------

@main.command("evaluate")
@click.option("--results", required=True, type=click.Path(exists=True),
              help="Path to consolidated.json (legacy) or static_analysis.json")
@click.option("--ground-truth", required=True, type=click.Path(exists=True),
              help="Path to ground_truth.yml")
@click.option("--output-dir", default="output/evaluation", show_default=True)
def evaluate(results: str, ground_truth: str, output_dir: str) -> None:
    """Evaluate pipeline results against ground truth YAML."""
    from ..domain.consolidation import ConsolidatedResult
    from ..evaluation.report import generate_report
    from ..evaluation.scorer import score
    from ..utils.json_io import load_json

    consolidated = load_json(ConsolidatedResult, results)
    result = score(consolidated, ground_truth)
    generate_report(result, output_dir)
    log.info(
        f"F1={result.f1:.4f}  Precision={result.precision:.4f}  Recall={result.recall:.4f}"
    )


# ---------------------------------------------------------------------------
# metrics — Phase 12 thesis metrics per case
# ---------------------------------------------------------------------------


@main.command("metrics")
@click.argument("case_id")
@click.option("--ground-truth", default=None, type=click.Path(exists=True),
              help="JSON file with {changed_files: [...], source_sets: {...}} for Hit@k / accuracy.")
def metrics_cmd(case_id: str, ground_truth: str | None) -> None:
    """[Phase 12] Compute and persist thesis metrics (BSR, CTSR, FFSR, EFR, Hit@k) for a case."""
    import json as _json
    from ..evaluation.evaluator import evaluate as run_evaluate

    gt: dict | None = None
    if ground_truth:
        with open(ground_truth) as f:
            gt = _json.load(f)

    session = _get_session()
    try:
        result = run_evaluate(case_id=case_id, session=session, ground_truth=gt)
        session.commit()
        click.echo(f"Evaluated {len(result.metrics)} repair mode(s) for case {case_id[:8]}:")
        for m in result.metrics:
            efr_s = f"{m.efr:.3f}" if m.efr is not None else "N/A"
            h1_s = f"{m.hit_at_1:.1f}" if m.hit_at_1 is not None else "N/A"
            click.echo(
                f"  [{m.repair_mode}]  BSR={m.bsr:.1f}  CTSR={m.ctsr:.1f}  "
                f"FFSR={m.ffsr:.1f}  EFR={efr_s}  Hit@1={h1_s}"
            )
    except Exception as exc:
        session.rollback()
        click.echo(f"ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        session.close()


# ---------------------------------------------------------------------------
# doctor — check environment and configuration
# ---------------------------------------------------------------------------
# report — Phase 13 CSV / JSON / Markdown export
# ---------------------------------------------------------------------------


@main.command("report")
@click.option("--output-dir", default="data/reports", show_default=True,
              help="Directory to write report files.")
@click.option("--format", "fmt", default="all",
              type=click.Choice(["csv", "json", "markdown", "all"], case_sensitive=False),
              show_default=True, help="Output format(s) to generate.")
@click.option("--modes", default=None,
              help="Comma-separated repair modes to include (default: all).")
@click.option("--cases", default=None,
              help="Comma-separated case UUIDs to include (default: all).")
def report_cmd(output_dir: str, fmt: str, modes: str | None, cases: str | None) -> None:
    """[Phase 13] Export evaluation metrics as CSV / JSON / Markdown."""
    from ..reporting.reporter import generate_report

    mode_list = [m.strip() for m in modes.split(",")] if modes else None
    case_list = [c.strip() for c in cases.split(",")] if cases else None

    session = _get_session()
    try:
        result = generate_report(
            session=session,
            output_dir=Path(output_dir),
            formats=(fmt,),
            repair_modes=mode_list,
            case_ids=case_list,
        )
        session.commit()
        click.echo(f"Report: {result.row_count} row(s) → {result.output_dir}")
        for f in result.files:
            click.echo(f"  {f}")
        if result.aggregates:
            click.echo("Per-mode averages:")
            for mode, vals in result.aggregates.items():
                click.echo(
                    f"  [{mode}]  BSR={vals.get('bsr') or 'N/A'}  "
                    f"CTSR={vals.get('ctsr') or 'N/A'}  "
                    f"FFSR={vals.get('ffsr') or 'N/A'}"
                )
    except Exception as exc:
        session.rollback()
        click.echo(f"ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        session.close()


# ---------------------------------------------------------------------------

@main.command("doctor")
def doctor() -> None:
    """Check environment, dependencies, and configuration."""
    import shutil
    import sys

    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        status = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
        msg = f"  {status}  {label}"
        if detail:
            msg += f" — {detail}"
        click.echo(msg)
        if not passed:
            ok = False

    click.echo("kmp-repair doctor\n")

    check("Python ≥ 3.10", sys.version_info >= (3, 10), f"Found {sys.version_info.major}.{sys.version_info.minor}")
    check("git available", shutil.which("git") is not None)
    check("java available", shutil.which("java") is not None, "needed for Gradle")

    try:
        import tree_sitter_kotlin  # noqa: F401
        check("tree-sitter-kotlin", True)
    except ImportError:
        check("tree-sitter-kotlin", False, "regex fallback will be used")

    try:
        import anthropic  # noqa: F401
        check("anthropic SDK", True)
    except ImportError:
        check("anthropic SDK", False, "needed for Phase 9 (repair agents)")

    try:
        import sqlalchemy  # noqa: F401
        check("sqlalchemy", True)
    except ImportError:
        check("sqlalchemy", False, "needed for Phase 2 (database)")

    try:
        import alembic  # noqa: F401
        check("alembic", True)
    except ImportError:
        check("alembic", False, "needed for Phase 2 (migrations)")

    # Database connectivity
    try:
        from ..storage.db import check_connection
        db_ok, db_detail = check_connection()
        check("PostgreSQL connection", db_ok, db_detail[:80] if db_detail else "")
    except Exception as exc:
        check("PostgreSQL connection", False, str(exc)[:80])

    click.echo("\n" + ("All checks passed." if ok else "Some checks failed — see above."))


# ---------------------------------------------------------------------------
# db commands
# ---------------------------------------------------------------------------


@main.command("db-status")
def db_status() -> None:
    """Show database migration status."""
    import subprocess
    result = subprocess.run(["alembic", "current"], capture_output=True, text=True)
    click.echo(result.stdout or result.stderr)


@main.command("db-upgrade")
def db_upgrade() -> None:
    """Apply pending database migrations (alembic upgrade head)."""
    import subprocess
    result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
    click.echo(result.stdout)
    if result.returncode != 0:
        raise click.ClickException(result.stderr)


@main.command("db-seed")
def db_seed() -> None:
    """Seed the database with a minimal set of known-good test data.

    Inserts one sample repository record so the schema can be validated end-to-end.
    Safe to run multiple times (uses get_or_create).
    """
    from ..storage.db import get_session
    from ..storage.repositories import RepositoryRepo

    with get_session() as session:
        repo = RepositoryRepo(session).get_or_create(
            url="https://github.com/estebancastelblanco/kmp-production-sample-impact-demo"
        )
        click.echo(f"Seed repository: id={repo.id}  url={repo.url}")
    click.echo("Seed complete.")


# ---------------------------------------------------------------------------
# Placeholder commands (implemented in later phases)
# ---------------------------------------------------------------------------

def _not_implemented(name: str) -> None:
    raise click.ClickException(f"'{name}' is not implemented yet. See the phase plan.")


@main.command("discover")
@click.option("--min-stars", default=5, show_default=True,
              help="Minimum star count for a repository to be included.")
@click.option("--max-repos", default=50, show_default=True,
              help="Maximum number of repositories to return.")
@click.option("--max-prs", default=10, show_default=True,
              help="Maximum number of Dependabot PRs to inspect per repo.")
@click.option("--repo", "single_repo", default=None,
              help="Limit discovery to a single repo (owner/repo).")
@click.option("--format", "output_format", type=click.Choice(["text", "json"]),
              default="text", show_default=True)
def discover(
    min_stars: int,
    max_repos: int,
    max_prs: int,
    single_repo: str | None,
    output_format: str,
) -> None:
    """[Phase 4] Discover KMP repositories with open Dependabot PRs.

    \b
    Examples:
      kmp-repair discover
      kmp-repair discover --min-stars 20 --max-repos 10
      kmp-repair discover --repo estebancastelblanco/kmp-production-sample-impact-demo
    """
    import json as _json
    from ..ingest.repo_discoverer import discover as _discover, discover_prs_for_repo

    if single_repo:
        owner, _, repo_name = single_repo.partition("/")
        if not repo_name:
            raise click.BadParameter("--repo must be in owner/repo format")
        prs = discover_prs_for_repo(owner, repo_name, max_prs)
        if output_format == "json":
            click.echo(_json.dumps({"repo": single_repo, "prs": prs}, indent=2))
        else:
            if prs:
                click.echo(f"{single_repo}: {len(prs)} Dependabot PR(s)")
                for n in prs:
                    click.echo(f"  https://github.com/{single_repo}/pull/{n}")
            else:
                click.echo(f"{single_repo}: no open Dependabot PRs found")
        return

    results = _discover(min_stars=min_stars, max_repos=max_repos, max_prs_per_repo=max_prs)

    if output_format == "json":
        click.echo(
            _json.dumps(
                [
                    {
                        "repo": r.full_name,
                        "stars": r.stars,
                        "prs": r.open_dependabot_prs,
                        "pr_urls": r.pr_urls,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        click.echo("No repositories found matching the criteria.")
        return

    click.echo(f"Found {len(results)} repositor{'y' if len(results) == 1 else 'ies'}:\n")
    for r in results:
        click.echo(f"  {r.full_name} ({r.stars} stars) — {len(r.open_dependabot_prs)} PR(s)")
        for url in r.pr_urls:
            click.echo(f"    {url}")


@main.command("ingest")
@click.argument("pr_url")
@click.option("--artifact-dir", default=None, help="Override artifact storage path.")
@click.option("--source", default="dependabot", show_default=True,
              help="Detection source label stored in the DB.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Fetch and classify but do not write to the database.")
def ingest(
    pr_url: str,
    artifact_dir: str | None,
    source: str,
    dry_run: bool,
) -> None:
    """[Phase 4] Ingest a single Dependabot PR and persist it to the database.

    \b
    Example:
      kmp-repair ingest https://github.com/owner/repo/pull/42
    """
    from ..ingest.pr_fetcher import fetch_pr_from_url
    from ..ingest.event_classifier import classify_all, dominant_class
    from ..ingest.version_catalog import detect_version_changes

    if dry_run:
        click.echo(f"[dry-run] Fetching PR: {pr_url}")
        pr = fetch_pr_from_url(pr_url)
        click.echo(f"  title : {pr.title}")
        click.echo(f"  state : {pr.state}")
        click.echo(f"  head  : {pr.head_sha[:12]}")
        click.echo(f"  base  : {pr.base_sha[:12]}")
        click.echo(f"  files changed in catalog: {pr.catalog_files_changed or 'none'}")

        for path in pr.catalog_files_changed:
            before = pr.before_contents.get(path, "")
            after = pr.after_contents.get(path, "")
            cs = detect_version_changes(before, after)
            if cs.has_changes:
                classes = classify_all(cs.changes)
                click.echo(f"\n  Version changes in {path}:")
                for vc in cs.changes:
                    cls = classes[vc.dependency_group]
                    click.echo(f"    {vc.dependency_group}: {vc.before} → {vc.after}  [{cls.value}]")
                click.echo(f"\n  Dominant update class: {dominant_class(list(classes.values())).value}")
            else:
                click.echo(f"  No version changes detected in {path}")
        return

    from ..ingest.event_builder import ingest_pr_url
    from ..storage.db import get_session

    with get_session() as session:
        result = ingest_pr_url(
            pr_url=pr_url,
            session=session,
            artifact_dir=artifact_dir,
            detection_source=source,
        )

    if result.skipped:
        click.echo(f"Skipped: {result.skip_reason}")
        return

    click.echo(f"Ingested PR: {pr_url}")
    click.echo(f"  case_id       : {result.case_id}")
    click.echo(f"  event_id      : {result.event_id}")
    click.echo(f"  update_class  : {result.update_class.value}")
    click.echo(f"  changes       : {len(result.version_changes)}")
    for vc in result.version_changes:
        click.echo(f"    {vc.dependency_group}: {vc.before} → {vc.after}")


@main.command("build-case")
@click.argument("case_id")
@click.option("--artifact-base", default="data/artifacts", show_default=True,
              help="Root directory for artifact storage.")
@click.option("--work-dir", default=None,
              help="Override directory for git clones (default: <artifact-base>/<case_id>/workspace).")
@click.option("--overwrite", is_flag=True, default=False,
              help="Delete and re-clone even if the workspace already exists.")
def build_case_cmd(
    case_id: str,
    artifact_base: str,
    work_dir: str | None,
    overwrite: bool,
) -> None:
    """[Phase 5] Clone before/after revisions and set up the repair case workspace.

    \b
    Accepts the case_id UUID printed by `kmp-repair ingest`.

    Examples:
      kmp-repair build-case <case_id>
      kmp-repair build-case <case_id> --artifact-base /tmp/artifacts
      kmp-repair build-case <case_id> --overwrite
    """
    from pathlib import Path
    from ..case_builder.case_factory import build_case
    from ..storage.db import get_session

    with get_session() as session:
        result = build_case(
            case_id=case_id,
            session=session,
            artifact_base=Path(artifact_base),
            work_base=Path(work_dir) if work_dir else None,
            overwrite_clone=overwrite,
        )

    action = "reused" if result.already_built else "cloned"
    click.echo(f"Case {case_id[:8]} built ({action}):")
    click.echo(f"  status       : {result.bundle.meta.status}")
    click.echo(f"  before       : {result.before_path}")
    click.echo(f"  after        : {result.after_path}")
    click.echo(f"  artifact_dir : {result.artifact_dir}")


@main.command("run-before-after")
@click.argument("case_id")
@click.option("--artifact-base", default="data/artifacts", show_default=True,
              help="Root directory for artifact storage.")
@click.option("--target", "targets", multiple=True,
              help="KMP targets to run (shared/android/ios). Repeatable. Default: auto-detect.")
@click.option("--timeout", default=600, show_default=True,
              help="Per-task timeout in seconds.")
def run_before_after_cmd(
    case_id: str,
    artifact_base: str,
    targets: tuple[str, ...],
    timeout: int,
) -> None:
    """[Phase 6] Execute before/after Gradle builds and capture execution evidence.

    \b
    Examples:
      kmp-repair run-before-after <case_id>
      kmp-repair run-before-after <case_id> --target shared --target android
      kmp-repair run-before-after <case_id> --timeout 300
    """
    from pathlib import Path
    from ..runners.execution_runner import run_before_after
    from ..storage.db import get_session

    with get_session() as session:
        result = run_before_after(
            case_id=case_id,
            session=session,
            artifact_base=Path(artifact_base),
            targets=list(targets) if targets else None,
            timeout_s=timeout,
        )

    click.echo(f"Case {case_id[:8]} execution complete:")
    click.echo(f"  status         : {result.bundle.meta.status}")
    click.echo(f"  revisions run  : {', '.join(result.ran_revisions)}")
    click.echo(f"  runnable targets: {result.env_profile.runnable_targets}")
    if result.env_profile.unavailable_targets:
        for t, reason in result.env_profile.unavailable_targets.items():
            click.echo(f"  [unavailable] {t}: {reason}")
    click.echo(f"  errors (after) : {result.total_errors}")


@main.command("analyze-case")
@click.argument("case_id")
def analyze_case_cmd(case_id: str) -> None:
    """[Phase 7] Run KMP structural analysis and build StructuralEvidence.

    \b
    Must be run after `build-case`. Reads the after-clone, parses Kotlin sources,
    computes the impact graph and expect/actual pairs, and persists source_entities
    to the database.

    Example:
      kmp-repair analyze-case <case_id>
    """
    from ..static_analysis.structural_builder import analyze_case
    from ..storage.db import get_session

    with get_session() as session:
        result = analyze_case(case_id=case_id, session=session)

    b = result.bundle
    click.echo(f"Case {case_id[:8]} structural analysis complete:")
    click.echo(f"  status          : {b.meta.status}")
    click.echo(f"  kotlin files    : {result.total_kotlin_files}")
    click.echo(f"  impacted files  : {result.total_impacted_files}")
    if b.structural:
        click.echo(f"  direct imports  : {len(b.structural.direct_import_files)}")
        click.echo(f"  expect/actual   : {len(b.structural.expect_actual_pairs)} pairs")
        click.echo(f"  build files     : {b.structural.relevant_build_files}")
    for g in result.impact_graphs:
        click.echo(f"  [{g.dependency_group}] seeds={len(g.seed_files)} impacted={g.total_impacted}")


@main.command("localize")
@click.argument("case_id")
@click.option("--no-agent", is_flag=True, default=False,
              help="Skip the LocalizationAgent — use deterministic scoring only.")
@click.option("--top-k", default=10, show_default=True,
              help="Maximum candidates to persist and display.")
@click.option("--model", default=None,
              help="Override LLM model ID (default: claude-sonnet-4-6).")
def localize_cmd(case_id: str, no_agent: bool, top_k: int, model: str | None) -> None:
    """[Phase 8] Run hybrid impact localization on a repair case.

    \b
    Combines deterministic scoring (static impact graph + dynamic error
    observations) with an optional LocalizationAgent LLM call.

    Examples:
      kmp-repair localize <case_id>
      kmp-repair localize <case_id> --no-agent
      kmp-repair localize <case_id> --top-k 5 --model claude-opus-4-6
    """
    from pathlib import Path
    from ..localization.localizer import localize
    from ..storage.db import get_session
    from ..utils.llm_provider import ClaudeProvider

    provider = ClaudeProvider(model_id=model) if model else None

    with get_session() as session:
        result = localize(
            case_id=case_id,
            session=session,
            use_agent=not no_agent,
            provider=provider,
            top_k=top_k,
        )

    b = result.bundle
    click.echo(f"Case {case_id[:8]} localization complete:")
    click.echo(f"  status          : {b.meta.status}")
    click.echo(f"  candidates      : {result.total_candidates}")
    click.echo(f"  agent used      : {result.used_agent}")
    if result.agent_notes:
        click.echo(f"  agent notes     : {result.agent_notes}")
    click.echo("")
    for cand in b.localized_files(top_k=top_k):
        click.echo(f"  {cand}")


@main.command("repair")
@click.argument("case_id")
@click.option("--mode",
              type=click.Choice(["full_thesis", "raw_error", "context_rich", "iterative_agentic"]),
              default="full_thesis", show_default=True,
              help="Repair strategy / baseline mode.")
@click.option("--artifact-base", default="data/artifacts", show_default=True)
@click.option("--top-k", default=5, show_default=True,
              help="Number of localized files to include in the repair context.")
@click.option("--model", default=None,
              help="Override LLM model ID.")
@click.option("--all-baselines", is_flag=True, default=False,
              help="Run all four baseline modes sequentially.")
def repair_cmd(
    case_id: str,
    mode: str,
    artifact_base: str,
    top_k: int,
    model: str | None,
    all_baselines: bool,
) -> None:
    """[Phase 9] Synthesize a repair patch for a localized case.

    \b
    Examples:
      kmp-repair repair <case_id>
      kmp-repair repair <case_id> --mode raw_error
      kmp-repair repair <case_id> --all-baselines
    """
    from pathlib import Path
    from ..utils.llm_provider import ClaudeProvider

    provider = ClaudeProvider(model_id=model) if model else None

    if all_baselines:
        from ..baselines.baseline_runner import run_all_baselines
        from ..storage.db import get_session

        with get_session() as session:
            results = run_all_baselines(
                case_id=case_id,
                session=session,
                artifact_base=Path(artifact_base),
                provider=provider,
                top_k=top_k,
            )
        click.echo(f"Case {case_id[:8]} — all baselines:")
        for bmode, result in results.items():
            click.echo(f"  {bmode}: {result.final_status} ({len(result.results)} attempt(s))")
        return

    from ..repair.repairer import repair
    from ..storage.db import get_session

    with get_session() as session:
        result = repair(
            case_id=case_id,
            session=session,
            artifact_base=Path(artifact_base),
            repair_mode=mode,
            provider=provider,
            top_k=top_k,
        )

    click.echo(f"Case {case_id[:8]} repair (mode={mode}):")
    click.echo(f"  status         : {result.bundle.meta.status}")
    click.echo(f"  attempt        : {result.attempt_number}")
    click.echo(f"  patch_status   : {result.patch_status}")
    click.echo(f"  touched_files  : {len(result.touched_files)}")
    if result.diff_path:
        click.echo(f"  diff_path      : {result.diff_path}")
    for f in result.touched_files:
        click.echo(f"    {f}")


@main.command("validate")
@click.argument("case_id")
@click.option("--attempt-id", default=None, help="Specific PatchAttempt UUID to validate (default: latest APPLIED).")
@click.option("--targets", default=None, help="Comma-separated targets to run (default: auto-detect).")
@click.option("--artifact-base", default="data/artifacts", show_default=True, help="Artifact store root.")
@click.option("--timeout", default=600, show_default=True, help="Per-Gradle-task timeout in seconds.")
def validate_cmd(case_id: str, attempt_id: str | None, targets: str | None, artifact_base: str, timeout: int) -> None:
    """[Phase 10] Validate a patch across all declared KMP targets."""
    from ..validation.validator import validate as run_validate

    target_list = [t.strip() for t in targets.split(",")] if targets else None

    session = _get_session()
    try:
        result = run_validate(
            case_id=case_id,
            session=session,
            artifact_base=Path(artifact_base),
            patch_attempt_id=attempt_id,
            targets=target_list,
            timeout_s=timeout,
        )
        session.commit()
        click.echo(f"Patch attempt #{result.patch_attempt_number} ({result.repair_mode}): {result.patch_status}")
        click.echo(f"Overall status : {result.overall_status}")
        for tv in result.target_results:
            click.echo(f"  [{tv.target}] {tv.status.value}")
    except Exception as exc:
        session.rollback()
        click.echo(f"ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        session.close()


@main.command("explain")
@click.argument("case_id")
@click.option("--artifact-base", default="data/artifacts", show_default=True, help="Artifact store root.")
@click.option("--model", default=None, help="Override LLM model ID.")
def explain_cmd(case_id: str, artifact_base: str, model: str | None) -> None:
    """[Phase 11] Generate a reviewer-oriented explanation for a repair case."""
    from ..explanation.explainer import explain as run_explain
    from ..utils.llm_provider import ClaudeProvider, get_default_provider

    provider = ClaudeProvider(model_id=model) if model else get_default_provider()

    session = _get_session()
    try:
        result = run_explain(
            case_id=case_id,
            session=session,
            artifact_base=Path(artifact_base),
            provider=provider,
        )
        session.commit()
        click.echo(f"Explanation generated (model={result.model_id})")
        click.echo(f"  JSON    : {result.json_path}")
        click.echo(f"  Markdown: {result.markdown_path}")
        click.echo(f"  Tokens  : in={result.tokens_in} out={result.tokens_out}")
    except Exception as exc:
        session.rollback()
        click.echo(f"ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        session.close()
