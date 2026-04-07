"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { createCase, getCases } from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";
import { CaseSummary } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

type CaseSemantic = "pending" | "running" | "review" | "failed";

const LIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

function semanticFromCaseStatus(status: CaseSummary["status"]): CaseSemantic {
  if (status === "FAILED") return "failed";
  if (status === "CREATED") return "pending";
  if (status === "VALIDATED" || status === "EXPLAINED" || status === "EVALUATED") return "review";
  return "running";
}

function semanticLabel(semantic: CaseSemantic): string {
  if (semantic === "pending") return "Pending";
  if (semantic === "running") return "In progress";
  if (semantic === "review") return "Ready for review";
  return "Failed";
}

function semanticHint(semantic: CaseSemantic): string {
  if (semantic === "pending") return "Pipeline has not started yet.";
  if (semantic === "running") return "Pipeline is running or waiting for the next stage.";
  if (semantic === "review") return "Validation finished and evidence is ready to review.";
  return "At least one stage failed and requires diagnosis.";
}

function semanticTone(semantic: CaseSemantic): string {
  if (semantic === "pending") return "border-[#9aaabd] bg-[#eef3f8] text-[#334d66]";
  if (semantic === "running") return "border-[#2f7fb5]/50 bg-[#e6f1fb] text-[#1f5f8a]";
  if (semantic === "review") return "border-[#2f9f80]/50 bg-[#e7f6f1] text-[#1f7b62]";
  return "border-[#d06a6a]/50 bg-[#fbeeee] text-[#9f3f3f]";
}

function patchTone(status: string | null): string {
  if (!status) return "border-[var(--color-border)] text-muted";
  if (status === "APPLIED") return "border-[#2f9f80]/45 text-[#1f7b62]";
  if (status === "REJECTED") return "border-[#d9962c]/50 text-[#8c5f18]";
  if (status === "FAILED_APPLY") return "border-[#d06a6a]/50 text-[#9f3f3f]";
  return "border-[var(--color-border)] text-muted";
}

function caseActionLabel(item: CaseSummary): string {
  if (item.active_job && LIVE_JOB_STATUSES.has(item.active_job.status)) {
    return "Monitor run";
  }

  const semantic = semanticFromCaseStatus(item.status);
  if (semantic === "review") return "Review evidence";
  if (semantic === "failed") return "Diagnose failure";
  if (semantic === "running") return "Continue pipeline";
  return "Open case";
}

function nextStepFromCase(item: CaseSummary): string {
  if (item.active_job && item.active_job.current_stage) {
    return `Active stage: ${stageLabel(item.active_job.current_stage)}`;
  }

  switch (item.status) {
    case "CREATED":
      return "Next: Build Case";
    case "SHADOW_BUILT":
      return "Next: Run Before/After";
    case "EXECUTED":
      return "Next: Analyze Case";
    case "LOCALIZED":
      return "Next: Repair";
    case "PATCH_ATTEMPTED":
      return "Next: Validate";
    case "VALIDATED":
      return "Next: Explain";
    case "EXPLAINED":
      return "Next: Metrics";
    case "EVALUATED":
      return "Completed: ready for comparison";
    case "FAILED":
      return "Review failure logs and evidence";
    default:
      return "Unmapped status";
  }
}

export default function CasesPage() {
  const router = useRouter();
  const [items, setItems] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"feed" | "table">("feed");

  const [status, setStatus] = useState("");
  const [updateClass, setUpdateClass] = useState("");
  const [repo, setRepo] = useState("");
  const [repairMode, setRepairMode] = useState("");

  const [prUrl, setPrUrl] = useState("");
  const [creating, setCreating] = useState(false);

  async function loadCases() {
    setLoading(true);
    setError(null);
    try {
      const data = await getCases({
        status: status || undefined,
        update_class: updateClass || undefined,
        repo: repo || undefined,
        repair_mode: repairMode || undefined,
      });
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load cases");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCases();
  }, []);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!prUrl.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await createCase(prUrl.trim());
      router.push(`/cases/${created.case.case_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create case");
    } finally {
      setCreating(false);
    }
  }

  const empty = useMemo(() => !loading && items.length === 0, [loading, items.length]);

  const summary = useMemo(() => {
    let pending = 0;
    let running = 0;
    let review = 0;
    let failed = 0;
    let activeJobs = 0;

    for (const item of items) {
      const semantic = semanticFromCaseStatus(item.status);
      if (semantic === "pending") pending += 1;
      if (semantic === "running") running += 1;
      if (semantic === "review") review += 1;
      if (semantic === "failed") failed += 1;
      if (item.active_job && LIVE_JOB_STATUSES.has(item.active_job.status)) activeJobs += 1;
    }

    return {
      total: items.length,
      pending,
      running,
      review,
      failed,
      activeJobs,
    };
  }, [items]);

  return (
    <div className="space-y-8 pb-28">
      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <div>
          <p className="mb-2 text-xs uppercase tracking-[0.25em] text-terracotta">Cases</p>
          <h1 className="display-serif text-5xl">Case workspace</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Each card shows current state, next recommended action, and technical context.
          </p>
        </div>
        <form onSubmit={onCreate} className="card-surface rounded-3xl p-5 shadow-warm">
          <h2 className="display-serif mb-3 text-2xl">Create case</h2>
          <label htmlFor="pr-url" className="text-xs text-muted">
            Dependabot PR URL
          </label>
          <input
            id="pr-url"
            value={prUrl}
            onChange={(event) => setPrUrl(event.target.value)}
            className="ring-focus mt-1 w-full rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
            placeholder="https://github.com/owner/repo/pull/42"
          />
          <button
            type="submit"
            disabled={creating}
            className="ring-focus mt-3 rounded-full bg-terracotta px-4 py-2 text-xs font-semibold text-white disabled:opacity-60"
          >
            {creating ? "Creating..." : "Create case"}
          </button>
        </form>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <article className="rounded-2xl border border-[var(--color-border)] bg-white/74 px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-muted">Total</p>
          <p className="display-serif mt-1 text-3xl">{summary.total}</p>
        </article>
        <article className="rounded-2xl border border-[#9aaabd]/55 bg-[#eef3f8] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#334d66]">Pending</p>
          <p className="display-serif mt-1 text-3xl text-[#334d66]">{summary.pending}</p>
        </article>
        <article className="rounded-2xl border border-[#2f7fb5]/45 bg-[#e6f1fb] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#1f5f8a]">In progress</p>
          <p className="display-serif mt-1 text-3xl text-[#1f5f8a]">{summary.running}</p>
        </article>
        <article className="rounded-2xl border border-[#2f9f80]/45 bg-[#e7f6f1] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#1f7b62]">Ready for review</p>
          <p className="display-serif mt-1 text-3xl text-[#1f7b62]">{summary.review}</p>
        </article>
        <article className="rounded-2xl border border-[#d06a6a]/45 bg-[#fbeeee] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.12em] text-[#9f3f3f]">Failed</p>
          <p className="display-serif mt-1 text-3xl text-[#9f3f3f]">{summary.failed}</p>
        </article>
      </section>

      <section className="card-surface rounded-3xl p-5 shadow-warm">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <input
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            placeholder="status"
            className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
          />
          <input
            value={updateClass}
            onChange={(event) => setUpdateClass(event.target.value)}
            placeholder="update class"
            className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
          />
          <input
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
            placeholder="repository"
            className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
          />
          <input
            value={repairMode}
            onChange={(event) => setRepairMode(event.target.value)}
            placeholder="repair mode"
            className="ring-focus rounded-xl border border-[var(--color-border)] bg-white px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={loadCases}
            className="ring-focus rounded-full border border-[var(--color-border)] px-4 py-2 text-xs font-semibold hover:border-terracotta"
          >
            Apply filters
          </button>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => setView("feed")}
              className={`ring-focus rounded-full px-3 py-1 text-xs font-semibold ${view === "feed" ? "bg-terracotta text-white" : "border border-[var(--color-border)]"}`}
            >
              Cards
            </button>
            <button
              type="button"
              onClick={() => setView("table")}
              className={`ring-focus rounded-full px-3 py-1 text-xs font-semibold ${view === "table" ? "bg-terracotta text-white" : "border border-[var(--color-border)]"}`}
            >
              Table
            </button>
          </div>
        </div>

        <div className="mb-4 flex flex-wrap gap-2 text-[11px]">
          <span className="rounded-full border px-3 py-1 font-semibold border-[#9aaabd]/55 bg-[#eef3f8] text-[#334d66]">Pending</span>
          <span className="rounded-full border px-3 py-1 font-semibold border-[#2f7fb5]/45 bg-[#e6f1fb] text-[#1f5f8a]">In progress</span>
          <span className="rounded-full border px-3 py-1 font-semibold border-[#2f9f80]/45 bg-[#e7f6f1] text-[#1f7b62]">Ready for review</span>
          <span className="rounded-full border px-3 py-1 font-semibold border-[#d06a6a]/45 bg-[#fbeeee] text-[#9f3f3f]">Failed</span>
          <span className="rounded-full border border-[var(--color-border)] bg-white/80 px-3 py-1 font-semibold text-muted">Active runs: {summary.activeJobs}</span>
        </div>

        {loading ? <p className="text-sm text-muted">Loading cases...</p> : null}
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        {empty ? <p className="text-sm text-muted">No cases match the current filters.</p> : null}

        {view === "feed" ? (
          <div className="grid gap-4 md:grid-cols-2">
            {items.map((item) => (
              <article key={item.case_id} className="card-interactive rounded-3xl border border-[var(--color-border)] bg-white/72 p-5">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${semanticTone(semanticFromCaseStatus(item.status))}`}>
                    {semanticLabel(semanticFromCaseStatus(item.status))}
                  </span>
                  <span className="text-xs text-muted">{formatDate(item.updated_at)}</span>
                </div>
                <h3 className="display-serif text-2xl">Case {shortId(item.case_id)}</h3>
                <p className="mt-1 text-sm text-muted">{item.repository.owner}/{item.repository.name}</p>
                <p className="mt-2 line-clamp-2 text-sm text-ink">{item.event.pr_title || "No PR title"}</p>
                <p className="mt-2 text-xs text-muted">{semanticHint(semanticFromCaseStatus(item.status))}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted">
                  <span className="rounded-full border border-[var(--color-border)] px-2 py-1">{item.event.update_class}</span>
                  <span className="rounded-full border border-[var(--color-border)] px-2 py-1">mode: {item.latest_repair_mode || "-"}</span>
                  <span className={`rounded-full border px-2 py-1 ${patchTone(item.latest_patch_status)}`}>patch: {item.latest_patch_status || "-"}</span>
                  <span className="rounded-full border border-[var(--color-border)] px-2 py-1">
                    phase: {item.active_job?.current_stage ? stageLabel(item.active_job.current_stage) : "-"}
                  </span>
                </div>
                <p className="mt-3 rounded-xl border border-[var(--color-border)] bg-white/80 px-3 py-2 text-xs text-ink">
                  {nextStepFromCase(item)}
                </p>
                <Link
                  href={`/cases/${item.case_id}`}
                  className="ring-focus mt-4 inline-block rounded-full bg-terracotta px-4 py-2 text-xs font-semibold text-white no-underline"
                >
                  {caseActionLabel(item)}
                </Link>
              </article>
            ))}
          </div>
        ) : (
          <div className="overflow-auto">
            <table className="min-w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-xs uppercase tracking-[0.15em] text-muted">
                  <th className="px-2 py-3">Case</th>
                  <th className="px-2 py-3">Repo</th>
                  <th className="px-2 py-3">Status</th>
                  <th className="px-2 py-3">Update class</th>
                  <th className="px-2 py-3">Active job</th>
                  <th className="px-2 py-3">Stage</th>
                  <th className="px-2 py-3">Next step</th>
                  <th className="px-2 py-3">Action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.case_id} className="border-b border-[var(--color-border)] transition-colors duration-200 hover:bg-white/65">
                    <td className="px-2 py-3">{shortId(item.case_id)}</td>
                    <td className="px-2 py-3">{item.repository.owner}/{item.repository.name}</td>
                    <td className="px-2 py-3">{semanticLabel(semanticFromCaseStatus(item.status))}</td>
                    <td className="px-2 py-3">{item.event.update_class}</td>
                    <td className="px-2 py-3">{item.active_job?.status || "-"}</td>
                    <td className="px-2 py-3">{item.active_job?.current_stage ? stageLabel(item.active_job.current_stage) : "-"}</td>
                    <td className="px-2 py-3">{nextStepFromCase(item)}</td>
                    <td className="px-2 py-3">
                      <Link href={`/cases/${item.case_id}`} className="ring-focus rounded-full border border-[var(--color-border)] px-3 py-1 text-xs no-underline hover:border-terracotta">
                        {caseActionLabel(item)}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
