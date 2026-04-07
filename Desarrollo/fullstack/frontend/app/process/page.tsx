"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { LiveJobConsole } from "@/components/LiveJobConsole";
import { createCase, getCase, getCases, getEnvironment, runPipeline } from "@/lib/api";
import { REPAIR_MODES } from "@/lib/constants";
import { shortId } from "@/lib/format";
import { CaseDetail, CaseSummary, EnvironmentSnapshot, Job, TimelineStage } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

const LIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
const TERMINAL_JOB_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED"]);

type RunModeOption = "ALL" | (typeof REPAIR_MODES)[number];
type StepStatus = TimelineStage["status"];
type EnvironmentCheckKey =
  | "api_database"
  | "python_ok"
  | "git_available"
  | "java_available"
  | "android_sdk_available"
  | "llm_provider_available";

const PROCESS_STEPS: Array<{
  phase: string;
  title: string;
  description: string;
  chips: string[];
  stages: string[];
}> = [
  {
    phase: "Phase 01 - Input",
    title: "Discover & Ingest",
    description:
      "The pipeline scans targeted repositories and ingests a PR into a reproducible case with explicit update classification.",
    chips: ["REPOS_CRAWLED: 1,402", "SOURCE_KIND: KMM_SHARED"],
    stages: ["ingest"],
  },
  {
    phase: "Phase 02 - Construction",
    title: "Build Generation",
    description:
      "The AST foundation and before/after workspaces are generated to preserve the failure context and allow deterministic replay.",
    chips: ["STATEFUL ARTIFACTS"],
    stages: ["build-case"],
  },
  {
    phase: "Phase 03 - Execution",
    title: "Run Before/After",
    description:
      "Execution baselines capture the failure signature and explicitly mark unavailable targets as environmental constraints.",
    chips: [],
    stages: ["run-before-after"],
  },
  {
    phase: "Phase 04 - Heuristics",
    title: "Analyze Structure",
    description:
      "Structural evidence maps source sets, expect/actual links, and dependency boundaries before patch synthesis starts.",
    chips: [],
    stages: ["analyze-case"],
  },
  {
    phase: "Phase 05 - Precision",
    title: "Localize Impact",
    description:
      "Candidate files are ranked with score breakdowns and optional LLM reranking for high-confidence localization.",
    chips: [],
    stages: ["localize"],
  },
  {
    phase: "Phase 06 - Repair",
    title: "Repair Synthesis",
    description:
      "Selected mode proposes a patch strategy and records touched files, diff artifacts, and attempt metadata.",
    chips: [],
    stages: ["repair"],
  },
  {
    phase: "Phase 07 - Validation",
    title: "Validation Matrix",
    description:
      "Build/compile/test outcomes by target define whether the candidate patch reaches repository-level integrity.",
    chips: [],
    stages: ["validate"],
  },
  {
    phase: "Phase 08 - Explain",
    title: "Explanation Logic",
    description:
      "Reviewer-facing rationale records why the patch was selected and where uncertainty remains.",
    chips: [],
    stages: ["explain"],
  },
  {
    phase: "Phase 09 - Metrics",
    title: "Measure & Report",
    description:
      "BSR, CTSR, FFSR, EFR and hit@k are aggregated and exported for thesis-level comparison.",
    chips: [],
    stages: ["metrics", "report"],
  },
];

const PR_URL_PATTERN = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/;

const REQUIRED_CHECKS: Array<{ key: EnvironmentCheckKey; label: string }> = [
  { key: "api_database", label: "API/Database" },
  { key: "python_ok", label: "Python" },
  { key: "git_available", label: "Git" },
  { key: "java_available", label: "Java/JDK" },
  { key: "android_sdk_available", label: "Android SDK" },
  { key: "llm_provider_available", label: "LLM Provider" },
];

function listMissingChecks(snapshot: EnvironmentSnapshot | null): string[] {
  if (!snapshot) return REQUIRED_CHECKS.map((check) => check.label);

  return REQUIRED_CHECKS.filter((check) => !snapshot.checks[check.key]).map((check) => check.label);
}

function summarizeStepStatus(stages: string[], map: Map<string, StepStatus>): StepStatus {
  const statuses = stages.map((stage) => map.get(stage) ?? "NOT_STARTED");

  if (statuses.includes("RUNNING")) return "RUNNING";
  if (statuses.includes("FAILED")) return "FAILED";
  if (statuses.every((status) => status === "COMPLETED")) return "COMPLETED";
  return "NOT_STARTED";
}

function selectConsoleJob(detail: CaseDetail | null): Job | null {
  if (!detail || detail.jobs.length === 0) return null;

  const running = detail.jobs.find((job) => job.status === "RUNNING");
  if (running) return running;

  const queued = [...detail.jobs]
    .filter((job) => job.status === "QUEUED" || job.status === "CANCEL_REQUESTED")
    .sort((left, right) => Date.parse(left.queued_at) - Date.parse(right.queued_at))[0];

  if (queued) return queued;
  return detail.jobs[0];
}

function caseOptionLabel(item: CaseSummary): string {
  if (item.event.pr_title && item.event.pr_title.trim().length > 0) return item.event.pr_title;

  let parsedOwner: string | undefined;
  let parsedRepo: string | undefined;
  try {
    const parsed = new URL(item.repository.url);
    const parts = parsed.pathname.split("/").filter(Boolean);
    parsedOwner = parts[0];
    parsedRepo = parts[1];
  } catch {
    parsedOwner = undefined;
    parsedRepo = undefined;
  }

  const owner = item.repository.owner || parsedOwner || "unknown-owner";
  const repo = item.repository.name || parsedRepo || "unknown-repo";
  const prRefRaw = item.event.pr_ref?.trim() || shortId(item.case_id);
  const prRefMatch = prRefRaw.match(/^pull\/(\d+)$/i);
  const prRef = prRefMatch ? `PR #${prRefMatch[1]}` : prRefRaw;
  return `${owner}/${repo} - ${prRef}`;
}

function extractRepairMode(job: Job | null): string | null {
  if (!job) return null;

  const fromStageParams = (source: Record<string, unknown> | null | undefined): string | null => {
    if (!source) return null;
    const repair = source.repair;
    if (!repair || typeof repair !== "object" || Array.isArray(repair)) return null;

    const mode = (repair as Record<string, unknown>).mode;
    if (typeof mode === "string" && mode.trim().length > 0) return mode;
    return null;
  };

  const fromCommand = (): string | null => {
    if (!job.command_preview) return null;
    const match = job.command_preview.match(/--mode\s+([A-Za-z0-9_-]+)/);
    return match?.[1] || null;
  };

  return fromStageParams(job.effective_params)
    || fromStageParams(job.params)
    || fromCommand();
}

export default function ProcessPage() {
  const [prUrl, setPrUrl] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);

  const [pastCases, setPastCases] = useState<CaseSummary[]>([]);
  const [casesLoading, setCasesLoading] = useState(false);
  const [selectedPastCaseId, setSelectedPastCaseId] = useState("");

  const [selectedRunMode, setSelectedRunMode] = useState<RunModeOption>("ALL");
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runInfo, setRunInfo] = useState<string | null>(null);

  const [environment, setEnvironment] = useState<EnvironmentSnapshot | null>(null);
  const [environmentLoading, setEnvironmentLoading] = useState(true);
  const [environmentError, setEnvironmentError] = useState<string | null>(null);

  const [selectedCaseDetail, setSelectedCaseDetail] = useState<CaseDetail | null>(null);
  const [caseDetailLoading, setCaseDetailLoading] = useState(false);
  const [caseDetailError, setCaseDetailError] = useState<string | null>(null);
  const [liveStatus, setLiveStatus] = useState<Job | null>(null);
  const [liveStageHint, setLiveStageHint] = useState<string | null>(null);

  const refreshEnvironment = useCallback(async (showLoader = false) => {
    if (showLoader) setEnvironmentLoading(true);

    try {
      const snapshot = await getEnvironment();
      setEnvironment(snapshot);
      setEnvironmentError(null);
      return snapshot;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No se pudo consultar environment";
      setEnvironmentError(message);
      return null;
    } finally {
      if (showLoader) setEnvironmentLoading(false);
    }
  }, []);

  const loadSelectedCase = useCallback(async (caseId: string, showLoader = true) => {
    if (!caseId) {
      setSelectedCaseDetail(null);
      setCaseDetailError(null);
      return;
    }

    if (showLoader) setCaseDetailLoading(true);

    try {
      const detail = await getCase(caseId);
      setSelectedCaseDetail(detail);
      setCaseDetailError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "No se pudo cargar el caso";
      setSelectedCaseDetail(null);
      setCaseDetailError(message);
    } finally {
      if (showLoader) setCaseDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;

    async function loadPastCases() {
      setCasesLoading(true);
      try {
        const items = await getCases();
        if (!mounted) return;

        const ordered = [...items].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
        setPastCases(ordered);
        setSelectedPastCaseId((current) => {
          if (current) return current;
          return "";
        });
      } catch {
        if (!mounted) return;
        setPastCases([]);
      } finally {
        if (mounted) setCasesLoading(false);
      }
    }

    loadPastCases();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    void refreshEnvironment(true);

    const timer = setInterval(() => {
      void refreshEnvironment(false);
    }, 15000);

    return () => clearInterval(timer);
  }, [refreshEnvironment]);

  useEffect(() => {
    void loadSelectedCase(selectedPastCaseId, true);
  }, [loadSelectedCase, selectedPastCaseId]);

  useEffect(() => {
    setLiveStatus(null);
    setLiveStageHint(null);
  }, [selectedPastCaseId]);

  const consoleJob = useMemo(() => selectConsoleJob(selectedCaseDetail), [selectedCaseDetail]);

  const effectiveConsoleJob = useMemo(() => {
    if (liveStatus && consoleJob && liveStatus.job_id === consoleJob.job_id) return liveStatus;
    return consoleJob;
  }, [consoleJob, liveStatus]);

  useEffect(() => {
    setLiveStageHint(null);
  }, [effectiveConsoleJob?.job_id]);

  useEffect(() => {
    if (!effectiveConsoleJob) return;
    if (TERMINAL_JOB_STATUSES.has(effectiveConsoleJob.status)) {
      setLiveStageHint(null);
    }
  }, [effectiveConsoleJob]);

  useEffect(() => {
    if (!selectedPastCaseId || !consoleJob || !LIVE_JOB_STATUSES.has(consoleJob.status)) return;

    const timer = setInterval(() => {
      void loadSelectedCase(selectedPastCaseId, false);
    }, 2200);

    return () => clearInterval(timer);
  }, [consoleJob, loadSelectedCase, selectedPastCaseId]);

  const missingChecks = useMemo(() => listMissingChecks(environment), [environment]);
  const environmentReady = !!environment && missingChecks.length === 0;

  const environmentBanner = useMemo(() => {
    if (environmentReady) return "Environment OK. Ingest y Run habilitados.";
    if (environmentLoading) return "Validando environment...";
    if (environmentError) return `Environment no disponible: ${environmentError}`;
    if (missingChecks.length > 0) return `Run e ingest bloqueados. Checks pendientes: ${missingChecks.join(", ")}.`;
    return "Run e ingest bloqueados hasta que todos los checks esten en OK.";
  }, [environmentError, environmentLoading, environmentReady, missingChecks]);

  const timelineMap = useMemo(() => {
    const map = new Map<string, StepStatus>();
    selectedCaseDetail?.timeline.forEach((entry) => {
      map.set(entry.stage, entry.status);
    });

    const isActivelyRunning = effectiveConsoleJob?.status === "RUNNING";
    const stageFromLive = isActivelyRunning ? liveStageHint || effectiveConsoleJob?.current_stage : null;
    if (isActivelyRunning && stageFromLive) {
      map.set(stageFromLive, "RUNNING");
    }

    return map;
  }, [effectiveConsoleJob?.current_stage, liveStageHint, selectedCaseDetail]);

  const stepStatuses = useMemo(
    () => PROCESS_STEPS.map((step) => summarizeStepStatus(step.stages, timelineMap)),
    [timelineMap],
  );

  const activeStepIndex = useMemo(() => {
    if (!selectedCaseDetail) return -1;

    return stepStatuses.findIndex((status) => status === "RUNNING");
  }, [selectedCaseDetail, stepStatuses]);

  const activeStage = useMemo(() => {
    if (effectiveConsoleJob?.status === "RUNNING") {
      if (liveStageHint) return liveStageHint;
      if (effectiveConsoleJob.current_stage) return effectiveConsoleJob.current_stage;
    }

    return selectedCaseDetail?.timeline.find((entry) => entry.status === "RUNNING")?.stage || null;
  }, [effectiveConsoleJob, liveStageHint, selectedCaseDetail]);

  const activeMode = useMemo(() => extractRepairMode(effectiveConsoleJob), [effectiveConsoleJob]);

  const consoleHelper = useMemo(() => {
    if (!selectedPastCaseId) return "Select a case to observe the pipeline in real time.";
    if (!effectiveConsoleJob) return "No active job yet. Start a fresh run to stream live logs.";

    const stageText = activeStage ? ` - ${stageLabel(activeStage)}` : "";
    const modeText = activeMode ? ` - Mode ${activeMode}` : "";
    return `Case ${shortId(selectedPastCaseId)} - ${effectiveConsoleJob.status}${stageText}${modeText}`;
  }, [activeMode, activeStage, effectiveConsoleJob, selectedPastCaseId]);

  const ensureEnvironmentReady = useCallback(async (): Promise<{ ok: boolean; message: string }> => {
    const snapshot = await refreshEnvironment(false);

    if (!snapshot) {
      return {
        ok: false,
        message: "No se pudo validar el estado de Environment.",
      };
    }

    const missing = listMissingChecks(snapshot);
    if (missing.length > 0) {
      return {
        ok: false,
        message: `Faltan checks en OK: ${missing.join(", ")}.`,
      };
    }

    return { ok: true, message: "" };
  }, [refreshEnvironment]);

  async function onIngest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = prUrl.trim();
    if (!value) return;

    const gate = await ensureEnvironmentReady();
    if (!gate.ok) {
      setIngestError(`Ingest bloqueado. ${gate.message}`);
      return;
    }

    if (!PR_URL_PATTERN.test(value)) {
      setIngestError("PR URL must match: https://github.com/owner/repo/pull/123");
      return;
    }

    setIngesting(true);
    setIngestError(null);
    try {
      const created = await createCase(value);

      const createdCaseSummary: CaseSummary = {
        case_id: created.case.case_id,
        status: created.case.status,
        created_at: created.case.created_at,
        updated_at: created.case.updated_at,
        repository: {
          url: created.case.repository.url,
          owner: created.case.repository.owner,
          name: created.case.repository.name,
        },
        event: {
          pr_ref: created.case.event.pr_ref,
          pr_title: created.case.event.pr_title,
          update_class: created.case.event.update_class,
        },
        latest_repair_mode: null,
        latest_patch_status: null,
        active_job: null,
      };

      setPastCases((current) => {
        const next = current.filter((item) => item.case_id !== created.case.case_id);
        return [createdCaseSummary, ...next];
      });
      setSelectedPastCaseId(created.case.case_id);
      setSelectedCaseDetail(created);
      setCaseDetailError(null);
      setPrUrl("");
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : "Could not ingest PR URL");
    } finally {
      setIngesting(false);
    }
  }

  async function onRunSelectedCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedPastCaseId) {
      setRunError("Select a previous case first.");
      return;
    }

    const gate = await ensureEnvironmentReady();
    if (!gate.ok) {
      setRunError(`Run bloqueado. ${gate.message}`);
      return;
    }

    setRunBusy(true);
    setRunError(null);
    setRunInfo(null);

    try {
      const modes: string[] = selectedRunMode === "ALL" ? [...REPAIR_MODES] : [selectedRunMode];
      const queuedJobs: string[] = [];

      for (const mode of modes) {
        const paramsByStage: Record<string, Record<string, unknown>> = {
          "build-case": {
            overwrite: true,
          },
          "run-before-after": {
            targets: ["shared", "android", "ios"],
            timeout_s: 600,
          },
          "analyze-case": {},
          localize: {
            top_k: 10,
          },
          repair: {
            mode,
          },
          validate: {
            targets: ["shared", "android", "ios"],
            timeout_s: 600,
          },
          explain: {},
          metrics: {},
          report: {
            format: "all",
            modes: [mode],
            cases: [selectedPastCaseId],
          },
        };

        const job = await runPipeline(selectedPastCaseId, "build-case", paramsByStage);
        queuedJobs.push(job.job_id);
      }

      await loadSelectedCase(selectedPastCaseId, false);

      setRunInfo(
        selectedRunMode === "ALL"
          ? `Queued ${queuedJobs.length} fresh runs from build-case.`
          : `Queued fresh run for mode ${selectedRunMode}.`,
      );
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Could not queue run for selected case.");
    } finally {
      setRunBusy(false);
    }
  }

  return (
    <div className="page-shell py-16">
      <div className="grid gap-10 lg:grid-cols-12">
        <section className="pb-24 lg:col-span-7 xl:col-span-6">
          <header className="mb-10">
            <p className="eyebrow mb-5">Sequence Methodology</p>
            <h1 className="editorial-title max-w-3xl text-[clamp(2.8rem,6.8vw,5.8rem)] font-black text-[var(--ink)]">
              The Process <span className="text-stone-300">&amp;</span> Decision.
            </h1>
          </header>

          <Link
            href="/environment"
            className={environmentReady
              ? "mb-6 inline-flex w-fit items-center gap-2 rounded-full border border-[var(--ok)]/35 bg-[var(--ok)]/8 px-3 py-1.5 technical-font text-[0.56rem] uppercase tracking-[0.12em] text-[var(--ink)] transition hover:opacity-80"
              : "mb-6 inline-flex w-fit items-center gap-2 rounded-full border border-[var(--bad)]/35 bg-[var(--bad)]/8 px-3 py-1.5 technical-font text-[0.56rem] uppercase tracking-[0.12em] text-[var(--ink)] transition hover:opacity-80"}
            title={environmentBanner}
          >
            <span
              aria-hidden
              className={environmentReady ? "h-2 w-2 rounded-full bg-[var(--ok)]" : "h-2 w-2 rounded-full bg-[var(--bad)]"}
            />
            {environmentReady ? "Environment bien" : "Env mal"}
          </Link>

          <form onSubmit={onIngest} className="mb-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-center">
            <input
              value={prUrl}
              onChange={(event) => setPrUrl(event.target.value)}
              placeholder="https://github.com/owner/repo/pull/42"
              className="focus-ring w-full rounded-lg border border-[var(--line)] bg-white px-4 py-3 text-sm"
            />

            <button
              type="submit"
              disabled={ingesting || !environmentReady}
              className="button-primary px-6 py-3 disabled:cursor-not-allowed disabled:opacity-60"
              title={!environmentReady ? "Environment debe estar 100% OK para ingest" : undefined}
            >
              {ingesting ? "Ingesting..." : "Ingest PR URL"}
            </button>
          </form>

          {ingestError ? <p className="mb-4 text-sm text-[var(--bad)]">{ingestError}</p> : null}

          <form onSubmit={onRunSelectedCase} className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center">
            <select
              value={selectedPastCaseId}
              onChange={(event) => setSelectedPastCaseId(event.target.value)}
              disabled={casesLoading || pastCases.length === 0 || runBusy}
              className="focus-ring w-full rounded-lg border border-[var(--line)] bg-white px-4 py-3 text-sm text-[var(--ink)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {pastCases.length === 0 ? (
                <option value="">No previous cases available</option>
              ) : (
                <>
                  <option value="">Select a case...</option>
                  {pastCases.map((item) => (
                    <option key={item.case_id} value={item.case_id}>
                      {caseOptionLabel(item)}
                    </option>
                  ))}
                </>
              )}
            </select>

            <select
              value={selectedRunMode}
              onChange={(event) => setSelectedRunMode(event.target.value as RunModeOption)}
              disabled={runBusy}
              className={selectedRunMode === "ALL"
                ? "focus-ring technical-font w-auto min-w-[6.25rem] rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-3 py-3 text-sm font-bold text-[var(--ink)]"
                : "focus-ring technical-font w-auto min-w-[6.25rem] rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-3 py-3 text-sm text-[var(--muted)]"}
            >
              <option value="ALL">All</option>
              {REPAIR_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>

            <button
              type="submit"
              disabled={!selectedPastCaseId || runBusy || !environmentReady}
              className="button-primary px-6 py-3 disabled:cursor-not-allowed disabled:opacity-60"
              title={!environmentReady ? "Environment debe estar 100% OK para run" : undefined}
            >
              {runBusy ? "Running..." : "Run"}
            </button>
          </form>

          <p className="technical-font mb-8 text-[0.55rem] text-[var(--muted)]">
            Run always starts fresh from build-case (overwrite enabled).
          </p>

          {runError ? <p className="mb-4 text-sm text-[var(--bad)]">{runError}</p> : null}
          {runInfo ? <p className="mb-4 text-sm text-[var(--muted)]">{runInfo}</p> : null}
          {caseDetailError ? <p className="mb-4 text-sm text-[var(--bad)]">{caseDetailError}</p> : null}
          {caseDetailLoading ? <p className="mb-4 text-sm text-[var(--muted)]">Loading case timeline...</p> : null}

          <div className="mb-8 rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4 py-3">
            <p className="technical-font text-[0.55rem] text-[var(--muted)]">Current Phase</p>
            <p className="mt-1 text-sm text-[var(--ink)]">
              {activeStage ? stageLabel(activeStage) : "Idle (no active pipeline stage)"}
            </p>
          </div>

          <div className="relative flex flex-col gap-20 pl-12">
            <div className="absolute bottom-8 left-[3px] top-4 w-px bg-[var(--line)]" />
            {PROCESS_STEPS.map((step, index) => {
              const status = stepStatuses[index] || "NOT_STARTED";
              const isActive = index === activeStepIndex;
                const isHighlighted = isActive && activeStepIndex >= 0;

                const markerClass = isHighlighted
                  ? status === "FAILED"
                    ? "absolute -left-12 top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--bad)] ring-4 ring-[var(--bg)]"
                    : status === "COMPLETED"
                      ? "absolute -left-12 top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--ok)] ring-4 ring-[var(--bg)]"
                        : "timeline-dot-live absolute -left-12 top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--ink)] ring-8 ring-black/5"
                  : "absolute -left-12 top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--line)] ring-4 ring-[var(--bg)]";

                const phaseClass = isHighlighted
                  ? status === "COMPLETED"
                    ? "ml-2 status-success"
                    : status === "FAILED"
                      ? "ml-2 status-error"
                      : "ml-2 text-[var(--ink)]"
                  : "ml-2 text-[var(--muted)]";

              return (
                  <article key={step.title} className={isHighlighted ? "" : "opacity-55"}>
                  <div className="relative">
                    <span className={markerClass} />
                      {isHighlighted && status === "RUNNING" ? <span className="absolute -left-[45px] top-8 h-24 w-[2px] bg-[var(--ink)]" /> : null}
                    <p className="technical-font text-[0.58rem] text-[var(--muted)]">
                      {step.phase}
                      <span className={phaseClass}>- {status}</span>
                    </p>
                  </div>

                    <h2 className={isHighlighted
                      ? "display-font mt-2 text-4xl font-bold text-[var(--ink)]"
                      : "display-font mt-2 text-4xl font-bold text-[var(--muted)]"}
                    >
                      {step.title}
                    </h2>
                    <p className={isHighlighted
                      ? "mt-4 max-w-xl leading-relaxed text-[var(--ink)]"
                      : "mt-4 max-w-xl leading-relaxed text-[var(--muted)]"}
                    >
                    {step.description}
                  </p>

                </article>
              );
            })}
          </div>
        </section>

        <aside className="lg:sticky lg:top-24 lg:col-span-5 lg:h-[calc(100vh-6rem)] xl:col-span-6">
          <LiveJobConsole
            jobId={effectiveConsoleJob?.job_id || null}
            title="Live Console"
            helperText={consoleHelper}
            emptyLabel="Waiting for real pipeline logs..."
            tone="light"
            className="surface-card h-full min-h-0 p-6"
            logClassName="min-h-0 flex-1"
            onStatus={setLiveStatus}
            onStageDetected={setLiveStageHint}
          />
        </aside>
      </div>
    </div>
  );
}
