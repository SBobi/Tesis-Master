"use client";

import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  cancelJob,
  getArtifactContent,
  getCase,
  runPipeline,
  runStage,
} from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";
import { CaseDetail, Job } from "@/lib/types";
import { stageLabel } from "@/lib/ui";
import { UnifiedDiffViewer } from "@/components/case/UnifiedDiffViewer";
import { LiveJobConsole } from "@/components/LiveJobConsole";
import { RunComposer } from "@/components/RunComposer";
import { Timeline } from "@/components/Timeline";

const LIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

const REPAIR_MODE_ORDER = ["full_thesis", "context_rich", "iterative_agentic", "raw_error"];

const REPAIR_MODE_LABELS: Record<string, string> = {
  full_thesis: "Full Thesis",
  context_rich: "Context Rich",
  iterative_agentic: "Iterative Agentic",
  raw_error: "Raw Error",
};

const AGENT_MODE_LABELS: Record<string, string> = {
  full_thesis: "Thesis Agent",
  context_rich: "Context Agent",
  iterative_agentic: "Iterative Agent",
  raw_error: "Baseline Agent",
};

function repairModeLabel(mode: string): string {
  return REPAIR_MODE_LABELS[mode] || mode;
}

function agentModeLabel(mode: string): string {
  return AGENT_MODE_LABELS[mode] || `${repairModeLabel(mode)} Agent`;
}

function jobTimestamp(job: Job): number {
  const value = job.finished_at || job.started_at || job.queued_at;
  return new Date(value).getTime();
}

function targetLabel(count: number): string {
  return count === 1 ? "target" : "targets";
}

function selectLiveJob(jobs: Job[]): Job | null {
  return jobs.find((job) => LIVE_JOB_STATUSES.has(job.status)) || null;
}

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>();
  const caseId = String(params.caseId);

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [repairModeFilter, setRepairModeFilter] = useState<string>("all");
  const [selectedRepairAttemptIdForDiff, setSelectedRepairAttemptIdForDiff] = useState<string | null>(null);
  const [repairDiffContent, setRepairDiffContent] = useState<string | null>(null);
  const [repairDiffLoading, setRepairDiffLoading] = useState(false);

  async function fetchCase(showLoader: boolean) {
    if (showLoader) setLoading(true);
    if (showLoader) setError(null);

    try {
      const payload = await getCase(caseId);
      setDetail(payload);
      setActiveJob(selectLiveJob(payload.jobs));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load case");
    } finally {
      if (showLoader) setLoading(false);
    }
  }

  async function refresh() {
    await fetchCase(true);
  }

  useEffect(() => {
    refresh();
  }, [caseId]);

  useEffect(() => {
    if (!activeJob || ["SUCCEEDED", "FAILED", "CANCELED"].includes(activeJob.status)) return;

    const interval = setInterval(async () => {
      await fetchCase(false);
    }, 3000);

    return () => clearInterval(interval);
  }, [caseId, activeJob]);

  const latestCaseJob = useMemo(() => {
    if (!detail) return null;
    const ordered = [...detail.jobs].sort((left, right) => jobTimestamp(right) - jobTimestamp(left));
    return ordered.find((job) => Boolean(job.log_path)) || ordered[0] || null;
  }, [detail]);

  const consoleJobId = activeJob?.job_id || latestCaseJob?.job_id || null;

  const consoleHelperText = useMemo(() => {
    if (activeJob?.current_stage) {
      return `Showing active job at stage ${stageLabel(activeJob.current_stage)}.`;
    }

    const latestStage = latestCaseJob?.current_stage || latestCaseJob?.stage;
    if (latestStage) {
      return `Showing latest executed job at stage ${stageLabel(latestStage)}.`;
    }

    if (latestCaseJob) {
      return "Showing latest executed job for this case.";
    }

    return "The console will stream logs once jobs run for this case.";
  }, [activeJob?.current_stage, latestCaseJob]);

  const consoleEmptyLabel = useMemo(() => {
    if (activeJob) return "Waiting for active job logs...";
    if (latestCaseJob && !latestCaseJob.log_path) {
      return "Latest job has no persisted log path in backend.";
    }
    return "Waiting for logs...";
  }, [activeJob, latestCaseJob]);

  const rawUpdateDiff = detail?.evidence.update_evidence.raw_diff || detail?.case.event.raw_diff || null;

  const availableRepairModes = useMemo(() => {
    if (!detail) return [];
    const discovered = Array.from(
      new Set(detail.evidence.patch_attempts.map((attempt) => attempt.repair_mode)),
    );
    const prioritized = REPAIR_MODE_ORDER.filter((mode) => discovered.includes(mode));
    const extras = discovered.filter((mode) => !REPAIR_MODE_ORDER.includes(mode));
    return [...prioritized, ...extras];
  }, [detail]);

  const patchAttemptModeById = useMemo(() => {
    const map = new Map<string, string>();
    if (!detail) return map;
    for (const attempt of detail.evidence.patch_attempts) {
      map.set(attempt.id, attempt.repair_mode);
    }
    return map;
  }, [detail]);

  const filteredPatchAttempts = useMemo(() => {
    if (!detail) return [];
    const ordered = [...detail.evidence.patch_attempts].sort(
      (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
    );
    if (repairModeFilter === "all") return ordered;
    return ordered.filter((attempt) => attempt.repair_mode === repairModeFilter);
  }, [detail, repairModeFilter]);

  const filteredValidationByTarget = useMemo(() => {
    if (!detail) return [];
    if (repairModeFilter === "all") return detail.evidence.validation_by_target;
    return detail.evidence.validation_by_target.filter(
      (item) => patchAttemptModeById.get(item.patch_attempt_id) === repairModeFilter,
    );
  }, [detail, patchAttemptModeById, repairModeFilter]);

  const selectedPatchAttemptForDiff = useMemo(() => {
    if (filteredPatchAttempts.length === 0) return null;
    if (!selectedRepairAttemptIdForDiff) return filteredPatchAttempts[0];

    return (
      filteredPatchAttempts.find((attempt) => attempt.id === selectedRepairAttemptIdForDiff) ||
      filteredPatchAttempts[0]
    );
  }, [filteredPatchAttempts, selectedRepairAttemptIdForDiff]);

  useEffect(() => {
    if (!selectedRepairAttemptIdForDiff) return;
    if (!filteredPatchAttempts.some((attempt) => attempt.id === selectedRepairAttemptIdForDiff)) {
      setSelectedRepairAttemptIdForDiff(null);
    }
  }, [filteredPatchAttempts, selectedRepairAttemptIdForDiff]);

  useEffect(() => {
    let cancelled = false;

    async function loadRepairDiff() {
      const attempt = selectedPatchAttemptForDiff;
      if (!attempt) {
        setRepairDiffContent(null);
        setRepairDiffLoading(false);
        return;
      }

      if (!attempt.diff_path) {
        setRepairDiffContent(attempt.diff_preview || null);
        setRepairDiffLoading(false);
        return;
      }

      setRepairDiffLoading(true);
      try {
        const content = await getArtifactContent(caseId, attempt.diff_path);
        if (!cancelled) {
          setRepairDiffContent(content);
        }
      } catch {
        if (!cancelled) {
          setRepairDiffContent(attempt.diff_preview || null);
        }
      } finally {
        if (!cancelled) {
          setRepairDiffLoading(false);
        }
      }
    }

    loadRepairDiff();
    return () => {
      cancelled = true;
    };
  }, [caseId, selectedPatchAttemptForDiff]);

  async function handleRunStage(stage: string, params: Record<string, unknown>) {
    if (stage === "ingest") return;
    const job = await runStage(caseId, stage, params);
    setActiveJob(job);
    await refresh();
  }

  async function handleRunPipeline(
    startFromStage: string,
    paramsByStage: Record<string, Record<string, unknown>>,
  ) {
    const job = await runPipeline(caseId, startFromStage, paramsByStage);
    setActiveJob(job);
    await refresh();
  }

  async function handleCancel(jobId: string) {
    const canceled = await cancelJob(jobId);
    setActiveJob(canceled);
    await refresh();
  }

  async function handleTimelineStageRun(stage: string) {
    if (stage === "ingest") return;
    await handleRunStage(stage, {});
  }

  const validationCounters = useMemo(() => {
    let failed = 0;
    let passed = 0;
    let notRun = 0;

    for (const row of filteredValidationByTarget) {
      if (row.status === "FAILED_BUILD" || row.status === "FAILED_TESTS") {
        failed += 1;
      } else if (row.status === "SUCCESS_REPOSITORY_LEVEL" || row.status === "PARTIAL_SUCCESS") {
        passed += 1;
      } else if (row.status === "NOT_RUN_ENVIRONMENT_UNAVAILABLE" || row.status === "NOT_RUN_YET") {
        notRun += 1;
      }
    }

    return {
      failed,
      passed,
      notRun,
      total: filteredValidationByTarget.length,
    };
  }, [filteredValidationByTarget]);

  const reviewOutcome = useMemo(() => {
    if (validationCounters.failed > 0) {
      return {
        label: "Not ready",
        toneClass: "bg-danger/15 text-danger",
        detail: `${validationCounters.failed} validation ${targetLabel(validationCounters.failed)} failed. The patch needs updates before merge.`,
      };
    }

    if (validationCounters.passed > 0 && validationCounters.failed === 0) {
      return {
        label: "Ready to merge",
        toneClass: "bg-success/20 text-success",
        detail: `${validationCounters.passed} validation ${targetLabel(validationCounters.passed)} passed with no active failures.`,
      };
    }

    if (validationCounters.notRun > 0) {
      return {
        label: "Review incomplete",
        toneClass: "bg-warning/20 text-warning",
        detail: `${validationCounters.notRun} ${targetLabel(validationCounters.notRun)} could not run in this environment.`,
      };
    }

    return {
      label: "No final verdict",
      toneClass: "bg-black/5 text-muted",
      detail: "There is not enough evidence yet to confirm this update result.",
    };
  }, [validationCounters.failed, validationCounters.notRun, validationCounters.passed]);

  const failedTargets = useMemo(
    () =>
      filteredValidationByTarget.filter(
        (row) => row.status === "FAILED_BUILD" || row.status === "FAILED_TESTS",
      ),
    [filteredValidationByTarget],
  );

  const passedTargets = useMemo(
    () =>
      filteredValidationByTarget.filter(
        (row) => row.status === "SUCCESS_REPOSITORY_LEVEL" || row.status === "PARTIAL_SUCCESS",
      ),
    [filteredValidationByTarget],
  );

  const pendingTargets = useMemo(
    () =>
      filteredValidationByTarget.filter(
        (row) => row.status === "NOT_RUN_ENVIRONMENT_UNAVAILABLE" || row.status === "NOT_RUN_YET",
      ),
    [filteredValidationByTarget],
  );

  const hasRepairDiff = Boolean(repairDiffContent && repairDiffContent.trim());
  const reviewDiff = hasRepairDiff ? repairDiffContent : rawUpdateDiff;
  const hasMultipleAgentModes = availableRepairModes.length > 1;

  function handleSwitchAgentMode() {
    if (availableRepairModes.length === 0) return;
    const currentMode = selectedPatchAttemptForDiff?.repair_mode || availableRepairModes[0];
    const currentIndex = availableRepairModes.indexOf(currentMode);
    const nextMode = availableRepairModes[(currentIndex + 1) % availableRepairModes.length];
    setRepairModeFilter(nextMode);
    setSelectedRepairAttemptIdForDiff(null);
  }

  if (loading) return <p className="text-sm text-muted">Loading case...</p>;
  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!detail) return <p className="text-sm text-muted">Case not found.</p>;

  return (
    <div className="space-y-8">
      <header className="grid gap-4 rounded-3xl border border-[var(--color-border)] bg-white/50 p-6 lg:grid-cols-[1fr_auto] lg:items-end">
        <div>
          <p className="mb-2 text-xs uppercase tracking-[0.22em] text-terracotta">Case identity</p>
          <h1 className="display-serif text-4xl">Case {shortId(detail.case.case_id)} · {detail.case.status}</h1>
          <p className="mt-2 text-sm text-muted">{detail.case.repository.owner}/{detail.case.repository.name} · {detail.case.event.pr_ref}</p>
          <p className="mt-1 text-sm text-ink">{detail.case.event.pr_title || "No PR title"}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
            <span className="rounded-full border border-[var(--color-border)] px-2 py-1">update_class: {detail.case.event.update_class}</span>
            <span className="rounded-full border border-[var(--color-border)] px-2 py-1">created: {formatDate(detail.case.created_at)}</span>
            <span className="rounded-full border border-[var(--color-border)] px-2 py-1">updated: {formatDate(detail.case.updated_at)}</span>
          </div>
        </div>
        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm">
          <p className="font-semibold">Active job</p>
          <p className="text-muted">{activeJob ? activeJob.status : "None"}</p>
          <p className="text-xs text-muted">
            phase: {activeJob?.current_stage ? stageLabel(activeJob.current_stage) : "-"}
          </p>
        </div>
      </header>

      <RunComposer
        activeJob={activeJob}
        onRunStage={handleRunStage}
        onRunPipeline={handleRunPipeline}
        onCancel={handleCancel}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(360px,390px)_minmax(0,1fr)]">
        <Timeline
          stages={detail.timeline}
          onRunStage={handleTimelineStageRun}
          disableActions={Boolean(activeJob)}
        />
        <div className="min-w-0 space-y-6">
          <LiveJobConsole
            jobId={consoleJobId}
            title="Execution logs"
            helperText={consoleHelperText}
            emptyLabel={consoleEmptyLabel}
          />

          <section className="card-surface rounded-3xl p-5 shadow-warm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="display-serif text-2xl">Review summary</h3>
              <span className={`rounded-full px-3 py-1 text-xs font-semibold ${reviewOutcome.toneClass}`}>
                {reviewOutcome.label}
              </span>
            </div>

            <p className="mb-4 text-sm text-muted">{reviewOutcome.detail}</p>

            <div className="space-y-2">
              {detail.evidence.update_evidence.changes.map((change) => (
                <div
                  key={`${change.dependency_group}-${change.version_key}`}
                  className="rounded-xl border border-[var(--color-border)] bg-white/70 px-3 py-2"
                >
                  <p className="text-sm font-semibold text-ink">{change.dependency_group}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full border border-danger/40 bg-danger/10 px-2 py-1 text-danger">
                      - {change.before}
                    </span>
                    <span className="text-muted">→</span>
                    <span className="rounded-full border border-success/40 bg-success/10 px-2 py-1 text-success">
                      + {change.after}
                    </span>
                  </div>
                </div>
              ))}
              {detail.evidence.update_evidence.changes.length === 0 ? (
                <p className="rounded-xl border border-[var(--color-border)] bg-white/80 px-3 py-2 text-sm text-muted">
                  No dependency version changes were detected in this diff.
                </p>
              ) : null}
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--color-border)] bg-white/80 px-3 py-2 text-xs text-muted">
              {hasRepairDiff && selectedPatchAttemptForDiff ? (
                <p>
                  Showing patch proposed by: <span className="font-semibold text-ink">{agentModeLabel(selectedPatchAttemptForDiff.repair_mode)}</span>
                  {" "}· attempt {selectedPatchAttemptForDiff.attempt_number} · status {selectedPatchAttemptForDiff.status}
                </p>
              ) : (
                <p>Showing base dependency update diff (no patch applied).</p>
              )}

              <button
                type="button"
                onClick={handleSwitchAgentMode}
                disabled={!hasMultipleAgentModes}
                title={
                  hasMultipleAgentModes
                    ? "Switch to the next agent proposal"
                    : "Only one agent proposal is available"
                }
                className={`ring-focus rounded-full px-4 py-1.5 text-xs font-semibold transition-transform duration-200 ${
                  hasMultipleAgentModes
                    ? "bg-gradient-to-r from-terracotta to-[#d35a43] text-white shadow-[0_8px_20px_rgba(211,90,67,0.28)] hover:-translate-y-0.5 hover:brightness-105"
                    : "cursor-not-allowed border border-[var(--color-border)] bg-white text-muted"
                }`}
              >
                Change agent
              </button>
            </div>

            {repairDiffLoading ? (
              <p className="mt-2 text-sm text-muted">Loading repair diff...</p>
            ) : null}

            <div className="mt-4">
              <p className="mb-2 text-sm font-semibold text-ink">Code changes</p>
              <UnifiedDiffViewer rawDiff={reviewDiff} />
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <div className="rounded-xl border border-danger/40 bg-danger/5 px-3 py-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-danger">Failing targets</p>
                  <span className="rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 text-xs font-semibold text-danger">
                    {failedTargets.length}
                  </span>
                </div>
                {failedTargets.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {failedTargets.map((row) => (
                      <span key={`failed-${row.id}`} className="rounded-full border border-danger/35 bg-white px-2 py-0.5 text-xs text-danger">
                        {row.target}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted">No failures detected.</p>
                )}
              </div>

              <div className="rounded-xl border border-success/40 bg-success/10 px-3 py-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-success">Passing targets</p>
                  <span className="rounded-full border border-success/40 bg-success/15 px-2 py-0.5 text-xs font-semibold text-success">
                    {passedTargets.length}
                  </span>
                </div>
                {passedTargets.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {passedTargets.map((row) => (
                      <span key={`passed-${row.id}`} className="rounded-full border border-success/35 bg-white px-2 py-0.5 text-xs text-success">
                        {row.target}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted">No successful targets yet.</p>
                )}
              </div>
            </div>

            {pendingTargets.length > 0 ? (
              <div className="mt-3 rounded-xl border border-warning/45 bg-warning/10 px-3 py-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-[0.08em] text-warning">Pending or not-run targets</p>
                <div className="flex flex-wrap gap-1.5">
                  {pendingTargets.map((row) => (
                    <span key={`pending-${row.id}`} className="rounded-full border border-warning/35 bg-white px-2 py-0.5 text-xs text-warning">
                      {row.target}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      </div>


    </div>
  );
}
