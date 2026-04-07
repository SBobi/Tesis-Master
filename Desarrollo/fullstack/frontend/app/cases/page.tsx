"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getCases } from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";
import { CaseSummary } from "@/lib/types";
import { stageLabel } from "@/lib/ui";

type FilterPreset =
  | "plugin-toolchain"
  | "platform-integration"
  | "direct-library"
  | "transitive"
  | "unknown";

const PRESET_BUTTONS: Array<{ id: FilterPreset; label: string }> = [
  { id: "plugin-toolchain", label: "PLUGIN_TOOLCHAIN" },
  { id: "platform-integration", label: "PLATFORM_INTEGRATION" },
  { id: "direct-library", label: "DIRECT_LIBRARY" },
  { id: "transitive", label: "TRANSITIVE" },
  { id: "unknown", label: "UNKNOWN" },
];

const PRESET_TO_UPDATE_CLASS: Partial<Record<FilterPreset, string>> = {
  "plugin-toolchain": "plugin_toolchain",
  "platform-integration": "platform_integration",
  "direct-library": "direct_library",
  transitive: "transitive",
  unknown: "unknown",
};

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

function isFilterPreset(value: string | null): value is FilterPreset {
  return PRESET_BUTTONS.some((button) => button.id === value);
}

export default function CasesPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const presetParam = searchParams.get("preset");
  const preset = isFilterPreset(presetParam) ? presetParam : null;
  const preservedCasesQuery = searchParams.toString();

  function onTogglePreset(nextPreset: FilterPreset) {
    const params = new URLSearchParams(searchParams.toString());

    if (preset === nextPreset) {
      params.delete("preset");
    } else {
      params.set("preset", nextPreset);
    }

    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  async function loadCases() {
    setLoading(true);
    setError(null);
    try {
      const updateClass = preset ? PRESET_TO_UPDATE_CLASS[preset] : undefined;

      const data = await getCases({
        update_class: updateClass,
      });

      const ordered = [...data].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
      setItems(ordered);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load cases");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCases();
  }, [preset]);

  const empty = !loading && items.length === 0;

  return (
    <div className="page-shell py-16">
      <header className="mb-16 grid gap-8 lg:grid-cols-12 lg:items-end">
        <div className="lg:col-span-7">
          <p className="eyebrow mb-4">Repository Archive / 2024</p>
          <h1 className="editorial-title text-[clamp(2.9rem,7.6vw,5.6rem)] font-extrabold text-[var(--ink)]">
            Selected
            <br />
            <span className="text-stone-400">Cases.</span>
          </h1>
        </div>

        <div className="lg:col-span-5">
          <p className="max-w-md text-lg leading-relaxed text-[var(--muted)]">
            A curated collection of multiplatform repair cycles analyzed and verified by the curation engine.
          </p>
          <div className="mt-6 h-px w-full bg-[var(--line)]" />
        </div>
      </header>

      <section className="mb-14 border-b border-[var(--line-quiet)] pb-8">
        <div className="mb-4 flex flex-wrap items-center gap-x-8 gap-y-4">
          <span className="technical-font text-[0.58rem] text-[var(--muted)]">Filter Archive</span>
          <div className="hide-scrollbar flex max-w-full gap-2 overflow-x-auto pb-1">
            {PRESET_BUTTONS.map((button) => {
              const active = preset === button.id;
              return (
                <button
                  key={button.id}
                  type="button"
                  onClick={() => onTogglePreset(button.id)}
                  className={active ? "technical-font whitespace-nowrap rounded-full bg-[var(--ink)] px-4 py-1.5 text-[0.57rem] text-white" : "technical-font whitespace-nowrap rounded-full border border-[var(--line)] bg-white px-4 py-1.5 text-[0.57rem] text-[var(--muted)] hover:text-[var(--ink)]"}
                >
                  {button.label}
                </button>
              );
            })}
          </div>
        </div>
      </section>

      <section className="space-y-1">
        {loading ? <p className="text-sm text-[var(--muted)]">Loading cases...</p> : null}
        {error ? <p className="text-sm text-[var(--bad)]">{error}</p> : null}
        {empty ? <p className="text-sm text-[var(--muted)]">No cases match current filters.</p> : null}

        {items.map((item) => {
          const tone = statusTone(item.status);
          const caseTitle = item.event.pr_title || `${item.repository.name || "repository"} repair`;

          return (
            <Link
              key={item.case_id}
              href={preservedCasesQuery ? `/cases/${item.case_id}?${preservedCasesQuery}` : `/cases/${item.case_id}`}
              className="group relative grid grid-cols-12 gap-4 items-center rounded-xl border-b border-[var(--line-quiet)] px-4 py-7 transition hover:bg-white hover:shadow-[0_16px_48px_rgba(0,0,0,0.06)]"
            >
              <div className="col-span-12 text-sm text-[var(--muted)] md:col-span-1">0x{shortId(item.case_id, 3).toUpperCase()}</div>

              <div className="col-span-12 md:col-span-6">
                <h2 className="display-font text-[1.9rem] font-bold text-[var(--ink)] transition group-hover:text-[var(--brand-dim)]">
                  {caseTitle}
                </h2>
                <p className="technical-font mt-1 text-[0.54rem] text-[var(--muted)]">
                  Repo: {item.repository.owner}/{item.repository.name}
                </p>
                <p className="technical-font mt-2 text-[0.54rem] text-[var(--muted)]">
                  Updated: {formatDate(item.updated_at)}
                </p>
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
        })}
      </section>
    </div>
  );
}
