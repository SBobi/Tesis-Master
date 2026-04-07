"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { activeJobsSseUrl, getCases } from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";
import {
  THESIS_CORE_PRINCIPLE,
  THESIS_PRIMARY_MODE,
  THESIS_REPAIR_MODE_DETAILS,
  THESIS_REPAIR_MODE_LABELS,
  THESIS_REPAIR_MODE_ORDER,
} from "@/lib/thesis-framework";
import { CaseSummary, Job } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

const RUNNING_STATUS = "RUNNING";
const LIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
const QUEUED_STATUSES = new Set(["QUEUED", "CANCEL_REQUESTED"]);

const HERO_PIPELINE_STEPS: Array<{ label: string; stages: string[] }> = [
  {
    label: "DETECTING SOURCE BREAKAGES",
    stages: ["ingest", "build-case", "run-before-after"],
  },
  {
    label: "ANALYZING KMP DEPENDENCIES",
    stages: ["analyze-case", "localize"],
  },
  {
    label: "GENERATING PATCH CANDIDATES",
    stages: ["repair", "validate", "explain", "metrics", "report"],
  },
];

function stageStepIndex(stage: string | null): number {
  if (!stage) return -1;
  return HERO_PIPELINE_STEPS.findIndex((step) => step.stages.includes(stage));
}

function asTimestamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function newestJob(jobs: Job[]): Job | null {
  if (jobs.length === 0) return null;

  return [...jobs].sort((left, right) => {
    const leftTime = asTimestamp(left.started_at) || asTimestamp(left.created_at) || asTimestamp(left.queued_at);
    const rightTime = asTimestamp(right.started_at) || asTimestamp(right.created_at) || asTimestamp(right.queued_at);
    return rightTime - leftTime;
  })[0];
}

function statusTone(status: CaseSummary["status"]): "validated" | "running" | "created" | "failed" {
  if (status === "FAILED") return "failed";
  if (status === "CREATED") return "created";
  if (status === "VALIDATED" || status === "EXPLAINED" || status === "EVALUATED" || status === "NO_ERRORS_TO_FIX") {
    return "validated";
  }
  return "running";
}

function statusDotClass(tone: ReturnType<typeof statusTone>): string {
  if (tone === "validated") return "dot dot-ok";
  if (tone === "failed") return "dot dot-bad";
  if (tone === "running") return "dot dot-warn";
  return "dot";
}

export default function HomePage() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [activeJobs, setActiveJobs] = useState<Job[]>([]);
  const [activeStreamConnected, setActiveStreamConnected] = useState(false);

  useEffect(() => {
    let mounted = true;
    getCases()
      .then((items) => {
        if (mounted) setCases(items);
      })
      .catch(() => {
        if (mounted) setCases([]);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const source = new EventSource(activeJobsSseUrl());

    source.addEventListener("active", (event) => {
      try {
        const parsed = JSON.parse((event as MessageEvent).data) as Job[];
        setActiveJobs(parsed);
        setActiveStreamConnected(true);
      } catch {
        setActiveStreamConnected(false);
      }
    });

    source.addEventListener("heartbeat", () => {
      setActiveStreamConnected(true);
    });

    source.onerror = () => {
      setActiveStreamConnected(false);
    };

    return () => source.close();
  }, []);

  const featuredCase = useMemo(() => {
    return cases.find((item) => item.active_job) || cases[0] || null;
  }, [cases]);

  const featuredLiveJob = useMemo(() => {
    const candidate = featuredCase?.active_job;
    if (!candidate) return null;
    return LIVE_JOB_STATUSES.has(candidate.status) ? candidate : null;
  }, [featuredCase]);

  const latestRunningJob = useMemo(() => {
    const fromStream = newestJob(activeJobs.filter((job) => job.status === RUNNING_STATUS));
    if (fromStream) return fromStream;
    if (featuredLiveJob?.status === RUNNING_STATUS) return featuredLiveJob;
    return null;
  }, [activeJobs, featuredLiveJob]);

  const latestQueuedJob = useMemo(() => {
    const fromStream = newestJob(activeJobs.filter((job) => QUEUED_STATUSES.has(job.status)));
    if (fromStream) return fromStream;
    if (featuredLiveJob && QUEUED_STATUSES.has(featuredLiveJob.status)) return featuredLiveJob;
    return null;
  }, [activeJobs, featuredLiveJob]);

  const heroJob = useMemo(() => latestRunningJob || latestQueuedJob, [latestQueuedJob, latestRunningJob]);

  const heroCaseId = heroJob?.case_id || featuredCase?.case_id || null;

  const activeHeroStepIndex = useMemo(
    () => stageStepIndex(latestRunningJob?.current_stage || null),
    [latestRunningJob?.current_stage],
  );

  const heroStageText = useMemo(() => {
    if (latestRunningJob?.current_stage) return stageLabel(latestRunningJob.current_stage);
    if (latestQueuedJob) return "Queued";
    return "Idle";
  }, [latestQueuedJob, latestRunningJob]);

  const heroStatusText = useMemo(() => {
    if (!activeStreamConnected) return "SYNCING";
    if (latestRunningJob) return "RUNNING";
    if (latestQueuedJob) return latestQueuedJob.status;
    return "IDLE";
  }, [activeStreamConnected, latestQueuedJob, latestRunningJob]);

  const orderedCases = useMemo(() => {
    return [...cases].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
  }, [cases]);

  const selectedCases = useMemo(() => orderedCases.slice(0, 3), [orderedCases]);

  return (
    <div className="fade-in">
      <header className="hero-abstract hero-gridline relative min-h-[calc(100vh-4.5rem)] overflow-hidden">
        <div className="hero-animated-line" aria-hidden />
        <div className="page-shell relative grid min-h-[calc(100vh-4.5rem)] items-end gap-12 py-20 lg:grid-cols-12">
          <div className="lg:col-span-8">
            <h1 className="editorial-title max-w-5xl text-[clamp(3rem,8vw,5.5rem)] font-black text-white">
              From breaking update
              <br />
              to validated repair.
            </h1>
            <p className="mt-10 max-w-2xl text-lg leading-relaxed text-stone-300">
              A visual frontend for exploring dependency repair, execution modes, and multi-target validation in Kotlin Multiplatform repositories.
            </p>

            <div className="mt-14 flex flex-wrap gap-4">
              <Link href="/process" className="focus-ring button-primary">
                Explore the pipeline
              </Link>
              <Link href="/cases" className="focus-ring button-ghost border-white/40 bg-white/10 text-white">
                View repair cases
              </Link>
            </div>
          </div>

          <div className="hidden lg:col-span-4 lg:block">
            <div className="border-l-2 border-white/20 bg-white/8 p-8">
              <div className="mb-4 flex items-center justify-between border-b border-white/20 pb-3">
                <span className="technical-font text-[0.58rem] text-white/70">Active Pipeline</span>
                <span className="technical-font text-[0.58rem] text-white">
                  {heroCaseId ? `${shortId(heroCaseId).toUpperCase()}_REPAIR` : "NO_CASE"}
                </span>
              </div>

              <div className="technical-font space-y-2 text-[0.58rem]">
                {HERO_PIPELINE_STEPS.map((step, index) => {
                  const isRunning = !!latestRunningJob;
                  const isActive = isRunning && activeHeroStepIndex === index;
                  const isCompleted = isRunning && activeHeroStepIndex > index;
                  const isQueuedLead = !isRunning && !!latestQueuedJob && index === 0;

                  const lineClass = isActive
                    ? "text-white"
                    : isCompleted
                      ? "text-[#dddddd]"
                      : isQueuedLead
                        ? "text-[#cecece]"
                        : "text-[#b8b8b8]";

                  const dotClass = isActive
                    ? "hero-step-dot-live h-1.5 w-1.5 rounded-full bg-white"
                    : isCompleted
                      ? "h-1.5 w-1.5 rounded-full bg-[#d8d8d8]"
                      : isQueuedLead
                        ? "h-1.5 w-1.5 rounded-full bg-[#cbcbcb]"
                        : "h-1.5 w-1.5 rounded-full bg-[#8f8f8f]";

                  return (
                    <div key={step.label} className="flex items-center gap-2">
                      <span aria-hidden className={dotClass} />
                      <p className={lineClass}>{step.label}...</p>
                    </div>
                  );
                })}
              </div>

              <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/20 pt-3">
                <p className="technical-font text-[0.52rem] text-[#d0d0d0]">Current step: {heroStageText}</p>
                <span className="technical-font rounded-full border border-white/20 px-2 py-1 text-[0.5rem] text-white">
                  {heroStatusText}
                </span>
              </div>
            </div>
          </div>

        </div>
      </header>

      <section className="border-y border-[var(--line-quiet)] bg-white py-36">
        <div className="page-shell grid gap-16 lg:grid-cols-[1fr_1.4fr] lg:gap-28">
          <div>
            <p className="eyebrow mb-8">01 / Challenge</p>
            <h2 className="editorial-title text-5xl font-bold text-[var(--ink)]">The KMP Fragmentation Paradox.</h2>
          </div>

          <div className="space-y-10 text-lg leading-loose text-[var(--muted)]">
            <p>
              Kotlin Multiplatform combines shared and platform-specific source sets with expect/actual contracts. A single dependency bump can break compatibility differently across shared, android, and ios targets.
            </p>
            <p>
              This thesis implements kmp-repair as an evidence-and-decision pipeline: ingest and type the update, capture before/after execution evidence, localize impact, synthesize a patch, and validate plus explain outcomes without relying on free-form memory.
            </p>

            <div className="flex gap-16 border-t border-[var(--line-quiet)] pt-10">
              <div>
                <p className="display-font text-4xl font-black text-[var(--ink)]">5</p>
                <p className="technical-font mt-2 text-[0.58rem] text-[var(--muted)]">Pipeline Stages</p>
              </div>
              <div>
                <p className="display-font text-4xl font-black text-[var(--ink)]">3</p>
                <p className="technical-font mt-2 text-[0.58rem] text-[var(--muted)]">Specialized Agents</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[var(--bg)] py-32">
        <div className="page-shell">
          <div className="mb-16 flex flex-wrap items-end justify-between gap-8">
            <div>
              <p className="eyebrow mb-8">02 / Selected Cases</p>
              <h2 className="editorial-title text-[clamp(2.4rem,6vw,4rem)] font-bold text-[var(--ink)]">Recent Validations.</h2>
            </div>

            <Link href="/cases" className="technical-font focus-ring border-b-2 border-[var(--ink)] pb-2 text-[0.58rem] text-[var(--ink)]">
              View Archive
            </Link>
          </div>

          <div className="space-y-1">
            {selectedCases.length > 0 ? (
              selectedCases.map((item) => {
                const tone = statusTone(item.status);
                const caseTitle = item.event.pr_title || `${item.repository.name || "repository"} repair`;

                return (
                  <Link
                    key={item.case_id}
                    href={`/cases/${item.case_id}`}
                    className="group relative grid grid-cols-12 items-center gap-4 rounded-xl border-b border-[var(--line-quiet)] px-4 py-7 transition hover:bg-white hover:shadow-[0_16px_48px_rgba(0,0,0,0.06)]"
                  >
                    <div className="col-span-12 text-sm text-[var(--muted)] md:col-span-1">0x{shortId(item.case_id, 3).toUpperCase()}</div>

                    <div className="col-span-12 md:col-span-6">
                      <h3 className="display-font text-[1.9rem] font-bold text-[var(--ink)] transition group-hover:text-[var(--brand-dim)]">
                        {caseTitle}
                      </h3>
                      <p className="technical-font mt-1 text-[0.54rem] text-[var(--muted)]">
                        Repo: {item.repository.owner}/{item.repository.name}
                      </p>
                      <p className="technical-font mt-2 text-[0.54rem] text-[var(--muted)]">Updated: {formatDate(item.updated_at)}</p>
                      {item.active_job?.current_stage ? (
                        <p className="technical-font mt-1 text-[0.54rem] text-[var(--muted)]">
                          Active stage: {stageLabel(item.active_job.current_stage)}
                        </p>
                      ) : null}
                    </div>

                    <div className="col-span-6 flex flex-wrap gap-2 md:col-span-2">
                      <span className="pill">{item.event.update_class}</span>
                    </div>

                    <div className="col-span-6 flex items-center gap-2 md:col-span-2">
                      <span className={statusDotClass(tone)} />
                      <span className="technical-font text-[0.58rem] text-[var(--muted)]">{item.status}</span>
                    </div>

                    <div className="col-span-1 hidden justify-end text-lg text-[var(--muted)] md:flex">→</div>
                  </Link>
                );
              })
            ) : (
              <div className="border-y border-[var(--line-quiet)] px-4 py-12 text-[var(--muted)]">
                No cases available yet. Create one from the Cases page.
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="bg-[var(--surface-high)] py-24">
        <div className="page-shell">
          <div className="mx-auto mb-10 max-w-3xl text-center">
            <p className="eyebrow mb-5">03 / Thesis Repair Framework</p>
            <h2 className="editorial-title text-[clamp(2rem,4.8vw,3.5rem)] font-black text-[var(--ink)]">Evidence-and-decision pipeline.</h2>
            <p className="mx-auto mt-4 max-w-2xl text-[0.98rem] leading-relaxed text-[var(--muted)]">{THESIS_CORE_PRINCIPLE}</p>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {THESIS_REPAIR_MODE_ORDER.map((mode, index) => {
              const details = THESIS_REPAIR_MODE_DETAILS[mode];
              const thesisPrimary = mode === THESIS_PRIMARY_MODE;

              return (
                <article key={mode} className={thesisPrimary ? "surface-card-dark relative overflow-hidden p-6" : "surface-card p-6"}>
                  <p className={thesisPrimary ? "technical-font text-[0.54rem] text-white/70" : "technical-font text-[0.54rem] text-[var(--muted)]"}>
                    MODE_{String(index + 1).padStart(2, "0")} / {THESIS_REPAIR_MODE_LABELS[mode]}{thesisPrimary ? " / THESIS PRIMARY BASELINE" : ""}
                  </p>
                  <h3 className={thesisPrimary ? "display-font mt-4 text-[2.05rem] font-semibold text-white" : "display-font mt-4 text-[2.05rem] font-semibold text-[var(--ink)]"}>
                    {mode}
                  </h3>

                  <p className={thesisPrimary
                    ? "technical-font mt-4 text-[0.5rem] uppercase tracking-[0.12em] text-white/62"
                    : "technical-font mt-4 text-[0.5rem] uppercase tracking-[0.12em] text-[var(--muted)]"}
                  >
                    Context window
                  </p>
                  <p className={thesisPrimary ? "mt-1 text-[0.98rem] leading-relaxed text-white/82" : "mt-1 text-[0.98rem] leading-relaxed text-[var(--muted)]"}>
                    {details.contextGivenToRepairAgent}
                  </p>

                  <div className={thesisPrimary ? "mt-4 border-t border-white/15 pt-3" : "mt-4 border-t border-[var(--line-quiet)] pt-3"}>
                    <p className={thesisPrimary ? "technical-font text-[0.54rem] text-white/68" : "technical-font text-[0.54rem] text-[var(--muted)]"}>
                      Retry budget: {details.retryBudget}
                    </p>
                    <p className={thesisPrimary ? "mt-2 text-[0.95rem] leading-relaxed text-white/82" : "mt-2 text-[0.95rem] leading-relaxed text-[var(--muted)]"}>
                      {details.notes}
                    </p>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
