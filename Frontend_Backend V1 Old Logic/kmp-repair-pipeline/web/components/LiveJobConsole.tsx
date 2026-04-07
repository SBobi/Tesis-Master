"use client";

import { useEffect, useState } from "react";

import { jobSseUrl } from "@/lib/api";
import { Job } from "@/lib/types";

interface LiveJobConsoleProps {
  jobId: string | null;
  title?: string;
  helperText?: string;
  emptyLabel?: string;
}

export function LiveJobConsole({
  jobId,
  title = "Live job console",
  helperText,
  emptyLabel = "Waiting for logs...",
}: LiveJobConsoleProps) {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<Job | null>(null);

  useEffect(() => {
    if (!jobId) {
      setLines([]);
      setStatus(null);
      return;
    }

    setLines([]);
    const source = new EventSource(jobSseUrl(jobId));

    source.addEventListener("status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as Job;
        setStatus(payload);
      } catch {
        // ignore parse errors
      }
    });

    source.addEventListener("log", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as { line: string };
        setLines((prev) => {
          const next = [...prev, payload.line];
          return next.slice(-500);
        });
      } catch {
        // ignore parse errors
      }
    });

    source.addEventListener("done", () => {
      source.close();
    });

    return () => source.close();
  }, [jobId]);

  return (
    <section className="card-surface rounded-3xl p-5 shadow-warm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="display-serif text-2xl">{title}</h3>
        <span className="rounded-full bg-black/5 px-3 py-1 text-xs font-semibold text-muted">
          {status ? status.status : "No job"}
        </span>
      </div>
      {helperText ? <p className="mb-2 text-xs text-muted">{helperText}</p> : null}
      <pre className="h-64 overflow-auto rounded-2xl border border-[var(--color-border)] bg-[#131211] p-4 text-xs leading-relaxed text-[#f4ecde]">
        {lines.length ? lines.join("\n") : emptyLabel}
      </pre>
    </section>
  );
}
