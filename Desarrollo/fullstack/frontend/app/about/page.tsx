export default function AboutPage() {
  return (
    <div className="page-shell py-16">
      <header className="mb-20 grid gap-8 lg:grid-cols-12 lg:items-end">
        <div className="lg:col-span-8">
          <p className="eyebrow mb-4">Thesis / Research Framing</p>
          <h1 className="editorial-title text-[clamp(2.8rem,7vw,5.6rem)] font-black text-[var(--ink)]">
            About the Thesis
          </h1>
        </div>
        <div className="lg:col-span-4">
          <p className="leading-relaxed text-[var(--muted)]">
            Thesis frames dependency-update breakages in Kotlin Multiplatform as an evidence-and-decision problem: localize impact, synthesize a patch, validate runnable targets, and explain outcomes for reviewers.
          </p>
        </div>
      </header>

      <section className="grid gap-12 lg:grid-cols-12">
        <article className="surface-card p-8 lg:col-span-6">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Problem Framing</h2>
          <p className="mt-5 leading-relaxed text-[var(--muted)]">
            Kotlin Multiplatform repositories amplify update risk because shared and platform-specific source sets evolve together. `expect`/`actual` contracts must stay aligned,
            and a patch that compiles in `commonMain` can still fail in `androidMain` or `iosMain`.
          </p>
        </article>

        <article className="surface-card p-8 lg:col-span-6">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Core Contribution</h2>
          <p className="mt-5 leading-relaxed text-[var(--muted)]">
            Thesis formalizes a typed Case Bundle and a five-stage pipeline: ingestion, before/after execution, hybrid localization, patch synthesis, and multi-target
            validation with explanation. Decisions are persisted as evidence, not conversational memory.
          </p>
        </article>

        <article className="surface-card p-8 lg:col-span-7">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Methodology Summary</h2>
          <ul className="mt-5 space-y-3 text-[var(--muted)]">
            <li>Five stages produce typed outputs: update evidence, execution evidence, localization result, patch attempt, and validation plus explanation artifacts.</li>
            <li>Three specialized agents run in sequence: LocalizationAgent, RepairAgent, and ExplanationAgent.</li>
            <li>Repair is benchmarked across four baselines: raw_error, context_rich, iterative_agentic, and full_thesis.</li>
            <li>Metrics include BSR, CTSR, FFSR, EFR, and localization hit@k for case-level comparability.</li>
          </ul>
        </article>

        <article className="surface-card p-8 lg:col-span-5">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Limitations & Scope</h2>
          <p className="mt-5 leading-relaxed text-[var(--muted)]">
            Validation is environment-bounded: when a target cannot execute, the system records `NOT_RUN_ENVIRONMENT_UNAVAILABLE` instead of masking infrastructure limits.
          </p>
          <p className="mt-4 leading-relaxed text-[var(--muted)]">
            Thesis scope deliberately excludes cloud CI orchestration, automated PR submission, and multi-hop transitive repair beyond one dependency level.
          </p>
        </article>
      </section>
    </div>
  );
}
