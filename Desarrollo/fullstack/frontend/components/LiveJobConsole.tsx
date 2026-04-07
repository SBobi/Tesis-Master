"use client";

import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";

import { jobSseUrl } from "@/lib/api";
import { Job } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

interface LiveJobConsoleProps {
  jobId: string | null;
  title?: string;
  helperText?: string;
  emptyLabel?: string;
  className?: string;
  logClassName?: string;
  tone?: "dark" | "light";
  onStatus?: (job: Job | null) => void;
  onStageDetected?: (stage: string) => void;
}

type ParsedLogTone = "neutral" | "run" | "success" | "error";

interface ParsedLogLine {
  raw: string;
  timeLabel: string;
  stage: string | null;
  stageLabelText: string | null;
  message: string;
  tone: ParsedLogTone;
}

const LOG_LINE_PATTERN = /^(\d{4}-\d{2}-\d{2}T[^\s]+)\s+\[([^\]]+)\]\s+(.*)$/;
const PIPELINE_STAGE_NAMES = new Set([
  "ingest",
  "build-case",
  "run-before-after",
  "analyze-case",
  "localize",
  "repair",
  "validate",
  "explain",
  "metrics",
  "report",
]);

function compactTime(isoLike: string): string {
  const date = new Date(isoLike);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString("es-ES", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function classifyTone(message: string): ParsedLogTone {
  const normalized = message.toLowerCase();
  if (normalized.includes("error") || normalized.includes("fall")) return "error";
  if (normalized.includes("completad") || normalized.includes("done") || normalized.includes("valid")) return "success";
  if (normalized.includes("inicio") || normalized.includes("start") || normalized.includes("run ")) return "run";
  return "neutral";
}

function parseLogLine(raw: string): ParsedLogLine {
  const trimmed = raw.trim();
  const match = trimmed.match(LOG_LINE_PATTERN);

  if (!match) {
    return {
      raw,
      timeLabel: "--:--:--",
      stage: null,
      stageLabelText: null,
      message: trimmed,
      tone: classifyTone(trimmed),
    };
  }

  const [, timestamp, stageTokenRaw, messageRaw] = match;
  const stageToken = stageTokenRaw.trim();
  const message = messageRaw.trim();

  return {
    raw,
    timeLabel: compactTime(timestamp),
    stage: stageToken,
    stageLabelText: stageToken === "job" ? "Job" : stageLabel(stageToken),
    message,
    tone: classifyTone(message),
  };
}

function lineToneClass(tone: ParsedLogTone): string {
  if (tone === "error") return "text-[var(--bad)]";
  if (tone === "success") return "text-[var(--ok)]";
  if (tone === "run") return "text-[var(--ink)]";
  return "text-[var(--muted)]";
}

export function LiveJobConsole({
  jobId,
  title = "Live job console",
  helperText,
  emptyLabel = "Waiting for logs...",
  className,
  logClassName,
  tone = "dark",
  onStatus,
  onStageDetected,
}: LiveJobConsoleProps) {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<Job | null>(null);

  const parsedLines = useMemo(() => lines.map((line) => parseLogLine(line)), [lines]);

  useEffect(() => {
    if (!jobId) {
      setLines([]);
      setStatus(null);
      onStatus?.(null);
      return;
    }

    setLines([]);
    const source = new EventSource(jobSseUrl(jobId));

    source.addEventListener("status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as Job;
        setStatus(payload);
        onStatus?.(payload);

        if (payload.current_stage && PIPELINE_STAGE_NAMES.has(payload.current_stage)) {
          onStageDetected?.(payload.current_stage);
        }
      } catch {
        // ignore parse errors
      }
    });

    source.addEventListener("log", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as { line: string };
        const line = payload.line;
        if (!line || line.includes("[ALIVE]")) return;

        const parsed = parseLogLine(line);
        if (
          parsed.stage
          && PIPELINE_STAGE_NAMES.has(parsed.stage)
          && /inicio|start/i.test(parsed.message)
        ) {
          onStageDetected?.(parsed.stage);
        }

        setLines((prev) => {
          const next = [...prev, line];
          return next.slice(-500);
        });
      } catch {
        // ignore parse errors
      }
    });

    source.addEventListener("done", () => {
      source.close();
    });

    return () => {
      source.close();
    };
  }, [jobId, onStageDetected, onStatus]);

  return (
    <section className={clsx("card-surface rounded-3xl p-5 shadow-warm flex h-full min-h-0 flex-col", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="display-serif text-2xl">{title}</h3>
        <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-semibold text-muted">
          {status ? status.status : "No job"}
        </span>
      </div>
      {helperText ? <p className="mb-2 text-xs text-muted">{helperText}</p> : null}

      <div
        className={clsx(
          "min-h-0 flex-1 overflow-hidden rounded-2xl border",
          tone === "light"
            ? "border-[var(--line)] bg-[var(--surface)] text-[var(--ink)]"
            : "border-[var(--color-border)] bg-[#131211] text-[#f4ecde]",
          logClassName,
        )}
      >
        {parsedLines.length ? (
          <ul className="h-full overflow-auto px-3 py-3">
            {parsedLines.map((entry, index) => (
              <li key={`${entry.raw}-${index}`} className="grid grid-cols-[auto_auto_minmax(0,1fr)] items-start gap-2 py-1">
                <span className="technical-font pt-0.5 text-[0.53rem] tracking-[0.07em] text-[var(--muted)] tabular-nums">
                  {entry.timeLabel}
                </span>
                {entry.stageLabelText ? (
                  <span className="technical-font rounded-full border border-[var(--line-quiet)] bg-[var(--surface-low)] px-2 py-[2px] text-[0.5rem] tracking-[0.08em] text-[var(--muted)]">
                    {entry.stageLabelText}
                  </span>
                ) : (
                  <span className="w-2" />
                )}
                <span className={clsx("text-xs leading-relaxed", lineToneClass(entry.tone))}>{entry.message}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex h-full items-center justify-center px-4 text-xs text-[var(--muted)]">
            {emptyLabel}
          </div>
        )}
      </div>
    </section>
  );
}
