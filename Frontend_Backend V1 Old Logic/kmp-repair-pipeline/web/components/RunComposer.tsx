"use client";

import { FormEvent, useMemo, useState } from "react";

import { RUNNABLE_STAGES } from "@/lib/constants";
import { Job } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

interface RunComposerProps {
  activeJob: Job | null;
  onRunStage: (stage: string, params: Record<string, unknown>) => Promise<void>;
  onRunPipeline: (startFromStage: string, paramsByStage: Record<string, Record<string, unknown>>) => Promise<void>;
  onCancel: (jobId: string) => Promise<void>;
}

function safeJsonObject(text: string): Record<string, unknown> {
  if (!text.trim()) return {};
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON must be an object");
  }
  return parsed as Record<string, unknown>;
}

export function RunComposer({ activeJob, onRunStage, onRunPipeline, onCancel }: RunComposerProps) {
  const [stage, setStage] = useState<string>(RUNNABLE_STAGES[0]);
  const [stageParams, setStageParams] = useState<string>("{}");
  const [pipelineStart, setPipelineStart] = useState<string>(RUNNABLE_STAGES[0]);
  const [pipelineParams, setPipelineParams] = useState<string>("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const stageCommand = useMemo(() => {
    return `kmp-repair ${stage} <case_id>`;
  }, [stage]);

  async function handleStageRun(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const params = safeJsonObject(stageParams);
      await onRunStage(stage, params);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run stage");
    } finally {
      setBusy(false);
    }
  }

  async function handlePipelineRun(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const paramsByStage = safeJsonObject(pipelineParams) as Record<string, Record<string, unknown>>;
      await onRunPipeline(pipelineStart, paramsByStage);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run pipeline");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card-surface rounded-3xl p-4 shadow-warm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs uppercase tracking-[0.2em] text-muted">Execution controls</p>
        <div className="flex flex-wrap items-center gap-2">
          <form onSubmit={handleStageRun} className="flex flex-wrap items-center gap-2">
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
            >
              {RUNNABLE_STAGES.map((value) => (
                <option key={value} value={value}>
                  {stageLabel(value)}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={busy}
              className="ring-focus rounded-full bg-terracotta px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
            >
              Run stage
            </button>
          </form>

          <form onSubmit={handlePipelineRun} className="flex flex-wrap items-center gap-2">
            <select
              value={pipelineStart}
              onChange={(e) => setPipelineStart(e.target.value)}
              className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
            >
              {RUNNABLE_STAGES.map((value) => (
                <option key={value} value={value}>
                  From {stageLabel(value)}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={busy}
              className="ring-focus rounded-full border border-terracotta px-4 py-2 text-xs font-semibold text-terracotta disabled:opacity-60"
            >
              Run pipeline
            </button>
          </form>

          {activeJob ? (
            <button
              type="button"
              onClick={() => onCancel(activeJob.job_id)}
              disabled={busy}
              className="ring-focus rounded-full bg-danger px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
            >
              Cancel job
            </button>
          ) : null}

          <button
            type="button"
            onClick={() => setShowAdvanced((value) => !value)}
            className="ring-focus rounded-full border border-[var(--color-border)] bg-white px-4 py-2 text-xs font-semibold"
          >
            {showAdvanced ? "Hide advanced" : "Show advanced"}
          </button>
        </div>
      </div>

      {activeJob ? (
        <p className="mt-2 text-xs text-muted">
          Job {activeJob.job_id.slice(0, 8)} · {activeJob.status} · phase {activeJob.current_stage ? stageLabel(activeJob.current_stage) : "-"}
        </p>
      ) : (
        <p className="mt-2 text-xs text-muted">No active job for this case.</p>
      )}

      {showAdvanced ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.2em] text-muted">Stage params</p>
            <textarea
              value={stageParams}
              onChange={(e) => setStageParams(e.target.value)}
              className="ring-focus h-20 w-full rounded-xl border border-[var(--color-border)] bg-white p-2 font-mono text-xs"
              aria-label="JSON parameters for stage"
            />
            <p className="text-[11px] text-muted">cmd: {stageCommand}</p>
          </div>
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.2em] text-muted">Pipeline params</p>
            <textarea
              value={pipelineParams}
              onChange={(e) => setPipelineParams(e.target.value)}
              className="ring-focus h-20 w-full rounded-xl border border-[var(--color-border)] bg-white p-2 font-mono text-xs"
              aria-label="JSON params_by_stage"
            />
            <p className="text-[11px] text-muted">params_by_stage JSON.</p>
          </div>
        </div>
      ) : null}

      {error ? <p className="mt-3 text-xs text-danger">{error}</p> : null}
    </section>
  );
}
