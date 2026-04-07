"use client";

import { useEffect, useMemo, useState } from "react";

import { activeJobsSseUrl } from "@/lib/api";
import { Job } from "@/lib/types";
import { formatDate, shortId } from "@/lib/format";
import { stageLabel } from "@/lib/ui";

const CANCELED_STATUSES = new Set(["CANCEL_REQUESTED", "CANCELED", "CANCELLED"]);

function isCanceledStatus(status: string): boolean {
  return CANCELED_STATUSES.has(status);
}

export function ActiveRunsStrip() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [connected, setConnected] = useState(false);
  const [showCanceled, setShowCanceled] = useState(false);
  const [dismissedCanceledJobIds, setDismissedCanceledJobIds] = useState<string[]>([]);

  const dismissedCanceledJobIdSet = useMemo(
    () => new Set(dismissedCanceledJobIds),
    [dismissedCanceledJobIds],
  );

  useEffect(() => {
    const source = new EventSource(activeJobsSseUrl());

    source.addEventListener("active", (event) => {
      try {
        const parsed = JSON.parse((event as MessageEvent).data) as Job[];
        setJobs(parsed);
        setConnected(true);
      } catch {
        setConnected(false);
      }
    });

    source.addEventListener("heartbeat", () => {
      setConnected(true);
    });

    source.onerror = () => {
      setConnected(false);
    };

    return () => source.close();
  }, []);

  const headline = useMemo(() => {
    if (!connected) return "SSE connection is recovering";
    const nonCanceled = jobs.filter((job) => !isCanceledStatus(job.status));
    if (nonCanceled.length === 0) return "No active runs";
    if (nonCanceled.length === 1) return "1 active run";
    return `${nonCanceled.length} active runs`;
  }, [connected, jobs]);

  const visibleCanceledJobs = useMemo(
    () =>
      jobs.filter(
        (job) => isCanceledStatus(job.status) && !dismissedCanceledJobIdSet.has(job.job_id),
      ),
    [dismissedCanceledJobIdSet, jobs],
  );

  const canceledCount = visibleCanceledJobs.length;

  useEffect(() => {
    if (showCanceled && canceledCount === 0) {
      setShowCanceled(false);
    }
  }, [canceledCount, showCanceled]);

  const visibleJobs = useMemo(
    () =>
      jobs.filter((job) => {
        if (dismissedCanceledJobIdSet.has(job.job_id)) return false;
        if (!showCanceled && isCanceledStatus(job.status)) return false;
        return true;
      }),
    [dismissedCanceledJobIdSet, jobs, showCanceled],
  );

  const clearCanceled = () => {
    const canceledIds = visibleCanceledJobs.map((job) => job.job_id);
    if (canceledIds.length === 0) return;

    setDismissedCanceledJobIds((previous) => Array.from(new Set([...previous, ...canceledIds])));
    setShowCanceled(false);
  };

  return (
    <section className="card-surface rounded-3xl p-5 shadow-warm">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="display-serif text-2xl">Live system status</h3>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            connected ? "bg-success/15 text-success" : "bg-warning/20 text-warning"
          }`}
        >
          {connected ? "Connected" : "Reconnecting"}
        </span>
      </div>
      <p className="mb-4 text-sm text-muted">{headline}</p>
      {canceledCount > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>{canceledCount} canceled run{canceledCount > 1 ? "s" : ""} hidden</span>
          <button
            type="button"
            onClick={() => setShowCanceled((value) => !value)}
            className="ring-focus rounded-full border border-[var(--color-border)] bg-white/80 px-3 py-1 font-semibold text-ink"
            disabled={canceledCount === 0}
          >
            {showCanceled ? "Hide" : "Show"}
          </button>
          <button
            type="button"
            onClick={clearCanceled}
            className="ring-focus rounded-full border border-[var(--color-border)] bg-white/80 px-3 py-1 font-semibold text-ink"
            disabled={canceledCount === 0}
          >
            Remove canceled
          </button>
        </div>
      ) : null}
      <div className="space-y-2">
        {visibleJobs.slice(0, 5).map((job) => (
          <div key={job.job_id} className="card-interactive flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-[var(--color-border)] bg-white/72 px-3 py-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-terracotta/10 px-2 py-1 text-xs font-semibold text-terracotta">{job.status}</span>
              <span className="font-semibold">Case {shortId(job.case_id)}</span>
              <span className="rounded-full border border-[var(--color-border)] bg-white px-2 py-1 text-xs text-muted">
                phase: {job.current_stage ? stageLabel(job.current_stage) : "-"}
              </span>
            </div>
            <span className="text-xs text-muted">queued: {formatDate(job.queued_at)}</span>
          </div>
        ))}
        {visibleJobs.length === 0 ? (
          <div className="rounded-2xl border border-[var(--color-border)] bg-white/70 px-3 py-3 text-sm text-muted">
            No active runs to display.
          </div>
        ) : null}
      </div>
    </section>
  );
}
