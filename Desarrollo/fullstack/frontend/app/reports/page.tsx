"use client";

import { useEffect, useState } from "react";

import { ReportsPlots } from "@/components/reports/ReportsPlots";
import { getReportsComparison } from "@/lib/api";
import { metric } from "@/lib/format";
import { ReportsComparisonRow } from "@/lib/types";

const DEFAULT_MODES = ["full_thesis", "raw_error", "context_rich", "iterative_agentic"];

const MODE_ORDER = ["full_thesis", "iterative_agentic", "context_rich", "raw_error"];

const MODE_LABELS: Record<string, string> = {
  full_thesis: "Full Thesis",
  iterative_agentic: "Iterative Agentic",
  context_rich: "Context Rich",
  raw_error: "Raw Error",
};

const MODE_TONES: Record<string, string> = {
  full_thesis: "border-[#1f6fb2]/35 bg-gradient-to-br from-[#1f6fb2]/18 to-white",
  iterative_agentic: "border-[#2f9f80]/35 bg-gradient-to-br from-[#2f9f80]/18 to-white",
  context_rich: "border-[#c86b3c]/35 bg-gradient-to-br from-[#c86b3c]/16 to-white",
  raw_error: "border-[#8b5fc0]/35 bg-gradient-to-br from-[#8b5fc0]/16 to-white",
};

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] || mode;
}

function modeTone(mode: string): string {
  return MODE_TONES[mode] || "border-[var(--color-border)] bg-white";
}

function orderRows(rows: ReportsComparisonRow[]): ReportsComparisonRow[] {
  const indexByMode = new Map(MODE_ORDER.map((mode, index) => [mode, index]));
  return [...rows].sort((left, right) => {
    const leftIndex = indexByMode.get(left.repair_mode) ?? 999;
    const rightIndex = indexByMode.get(right.repair_mode) ?? 999;
    return leftIndex - rightIndex;
  });
}

export default function ReportsPage() {
  const [rows, setRows] = useState<ReportsComparisonRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const response = await getReportsComparison(DEFAULT_MODES);
      setRows(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load reports");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const orderedRows = orderRows(rows);

  return (
    <div className="space-y-8 pb-24">
      <section className="relative overflow-hidden rounded-[2rem] border border-[#b8c8dd] bg-[linear-gradient(130deg,#f3f8ff_0%,#eaf2fb_48%,#edf8f7_100%)] p-6 shadow-warm sm:p-8">
        <div className="absolute -right-20 -top-16 h-72 w-72 rounded-full bg-[radial-gradient(circle,rgba(47,159,128,0.22)_0%,transparent_68%)]" aria-hidden />
        <div className="absolute -left-20 -bottom-16 h-72 w-72 rounded-full bg-[radial-gradient(circle,rgba(31,111,178,0.18)_0%,transparent_70%)]" aria-hidden />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-2 text-xs uppercase tracking-[0.24em] text-[#285f8d]">Reports</p>
            <h1 className="display-serif text-5xl leading-[0.96]">Visual strategy benchmark</h1>
            <p className="mt-2 max-w-2xl text-sm text-muted">
              Observable Plot charts to quickly compare strategy performance and spot missing evidence.
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="ring-focus rounded-full border border-[#89a8c8] bg-white/85 px-5 py-2 text-xs font-semibold uppercase tracking-[0.08em] text-[#15314d] hover:border-[#2f7fb5]"
          >
            Reload
          </button>
        </div>
      </section>

      <section className="card-surface rounded-3xl p-5 shadow-warm">
        <h2 className="display-serif mb-4 text-3xl">Mode summary</h2>

        {loading ? <p className="text-sm text-muted">Loading comparison...</p> : null}
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        {!loading && !error && rows.length === 0 ? (
          <p className="rounded-2xl border border-[var(--color-border)] bg-white/75 px-3 py-2 text-sm text-muted">
            No comparison rows available to visualize.
          </p>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {orderedRows.map((row) => (
            <article key={row.repair_mode} className={`card-interactive rounded-2xl border p-4 ${modeTone(row.repair_mode)}`}>
              <h3 className="display-serif mb-1 text-2xl">{modeLabel(row.repair_mode)}</h3>
              <p className="mb-2 text-xs text-muted">mode: {row.repair_mode}</p>
              <p className="mb-2 text-xs text-muted">cases: {row.cases}</p>
              <ul className="space-y-1 text-xs text-ink">
                <li>BSR: {metric(row.bsr)}</li>
                <li>CTSR: {metric(row.ctsr)}</li>
                <li>FFSR: {metric(row.ffsr)}</li>
                <li>EFR: {metric(row.efr)}</li>
                <li>Hit@1: {metric(row.hit_at_1)}</li>
                <li>Hit@3: {metric(row.hit_at_3)}</li>
                <li>Hit@5: {metric(row.hit_at_5)}</li>
                <li>source_set_accuracy: {metric(row.source_set_accuracy)}</li>
              </ul>
            </article>
          ))}
        </div>
      </section>

      {!loading && !error && rows.length > 0 ? <ReportsPlots rows={orderedRows} /> : null}

      <section className="card-surface rounded-3xl p-5 shadow-warm">
        <h2 className="display-serif mb-3 text-3xl">Metrics table</h2>
        <div className="overflow-auto">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-xs uppercase tracking-[0.14em] text-muted">
                <th className="px-2 py-3">Mode</th>
                <th className="px-2 py-3">BSR</th>
                <th className="px-2 py-3">CTSR</th>
                <th className="px-2 py-3">FFSR</th>
                <th className="px-2 py-3">EFR</th>
                <th className="px-2 py-3">Hit@1</th>
                <th className="px-2 py-3">Hit@3</th>
                <th className="px-2 py-3">Hit@5</th>
                <th className="px-2 py-3">SSA</th>
              </tr>
            </thead>
            <tbody>
              {orderedRows.map((row) => (
                <tr key={row.repair_mode} className="border-b border-[var(--color-border)] transition-colors duration-200 hover:bg-white/65">
                  <td className="px-2 py-3 font-semibold">{modeLabel(row.repair_mode)}</td>
                  <td className="px-2 py-3">{metric(row.bsr)}</td>
                  <td className="px-2 py-3">{metric(row.ctsr)}</td>
                  <td className="px-2 py-3">{metric(row.ffsr)}</td>
                  <td className="px-2 py-3">{metric(row.efr)}</td>
                  <td className="px-2 py-3">{metric(row.hit_at_1)}</td>
                  <td className="px-2 py-3">{metric(row.hit_at_3)}</td>
                  <td className="px-2 py-3">{metric(row.hit_at_5)}</td>
                  <td className="px-2 py-3">{metric(row.source_set_accuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
