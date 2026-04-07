"use client";

import clsx from "clsx";

import { TimelineStage } from "@/lib/types";
import { formatSeconds } from "@/lib/format";
import { stageLabel } from "@/lib/ui";

interface TimelineProps {
  stages: TimelineStage[];
  onRunStage: (stage: string) => void;
  disableActions?: boolean;
}

function tone(status: TimelineStage["status"]) {
  if (status === "COMPLETED") return "border-success bg-success text-success";
  if (status === "FAILED") return "border-danger bg-danger text-danger";
  if (status === "RUNNING") return "border-terracotta bg-terracotta text-terracotta";
  return "border-[var(--color-border)] bg-[var(--color-border)] text-muted";
}

export function Timeline({
  stages,
  onRunStage,
  disableActions,
}: TimelineProps) {
  return (
    <section className="card-surface rounded-3xl p-6 shadow-warm">
      <h3 className="display-serif mb-5 text-3xl">Case timeline</h3>
      <ol className="relative ml-2 border-l border-[var(--color-border)] pl-6">
        {stages.map((entry) => {
          const isRunning = entry.status === "RUNNING";
          return (
            <li key={entry.stage} className="mb-5">
              <div
                className={clsx(
                  "absolute -left-[9px] mt-1 h-4 w-4 rounded-full border",
                  tone(entry.status),
                  isRunning && "timeline-pulse",
                )}
              />
              <div
                className={clsx(
                  "rounded-2xl border border-transparent px-2 py-1 transition-colors",
                )}
              >
                <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                  <h4 className="display-serif text-xl">{stageLabel(entry.stage)}</h4>
                  <span
                    className={clsx(
                      "rounded-full px-2 py-1 text-xs font-semibold",
                      entry.status === "COMPLETED" && "bg-success/15 text-success",
                      entry.status === "FAILED" && "bg-danger/10 text-danger",
                      entry.status === "RUNNING" && "bg-terracotta/12 text-terracotta",
                      entry.status === "NOT_STARTED" && "bg-black/5 text-muted",
                    )}
                  >
                    {entry.status}
                  </span>
                </div>
                <p className="text-xs text-muted">Duration: {formatSeconds(entry.duration_s)}</p>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={disableActions}
                  onClick={() => onRunStage(entry.stage)}
                  className="ring-focus rounded-full border border-[var(--color-border)] px-3 py-1 text-xs font-semibold hover:border-terracotta disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {entry.action === "retry" ? "Retry" : "Run"}
                </button>
                <span className="text-xs text-muted">Evidence: {entry.has_evidence ? "yes" : "pending"}</span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
