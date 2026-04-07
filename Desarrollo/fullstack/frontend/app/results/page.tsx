"use client";

import { useEffect, useMemo, useState } from "react";

import { ResultsD3Panel, type MetricKey } from "@/components/reports/ResultsD3Panel";
import { getReportsComparison } from "@/lib/api";
import { REPAIR_MODES } from "@/lib/constants";
import { metric } from "@/lib/format";
import {
  THESIS_PRIMARY_MODE,
  THESIS_REPAIR_MODE_DETAILS,
  THESIS_REPAIR_MODE_LABELS,
  THESIS_REPAIR_MODE_ORDER,
  type RepairModeKey,
} from "@/lib/thesis-framework";
import { ReportsComparisonRow } from "@/lib/types";

type MetricDefinition = {
  key: MetricKey;
  shortLabel: string;
  title: string;
  thesisDefinition: string;
};

const METRIC_DEFINITIONS: MetricDefinition[] = [
  {
    key: "bsr",
    shortLabel: "BSR",
    title: "Build Success Rate",
    thesisDefinition: "Fraction of cases where post-repair validation flow finishes successfully.",
  },
  {
    key: "ctsr",
    shortLabel: "CTSR",
    title: "Cross-Target Success Rate",
    thesisDefinition: "Fraction of cases where all declared targets validate successfully.",
  },
  {
    key: "ffsr",
    shortLabel: "FFSR",
    title: "File Fix Success Rate",
    thesisDefinition: "Fraction of broken files that were repaired correctly.",
  },
  {
    key: "efr",
    shortLabel: "EFR",
    title: "Error Fix Rate",
    thesisDefinition: "Fraction of individual compile or test errors that were resolved.",
  },
  {
    key: "hit_at_1",
    shortLabel: "Hit@1",
    title: "Localization Hit@1",
    thesisDefinition: "Overlap between localized files and accepted files at rank position 1.",
  },
  {
    key: "hit_at_3",
    shortLabel: "Hit@3",
    title: "Localization Hit@3",
    thesisDefinition: "Overlap between localized files and accepted files inside the top 3 ranking positions.",
  },
  {
    key: "hit_at_5",
    shortLabel: "Hit@5",
    title: "Localization Hit@5",
    thesisDefinition: "Overlap between localized files and accepted files inside the top 5 ranking positions.",
  },
  {
    key: "source_set_accuracy",
    shortLabel: "SSA",
    title: "Source-set Attribution Accuracy",
    thesisDefinition: "Accuracy in correctly attributing evidence and repairs to shared/platform/build source sets.",
  },
];

const METRIC_FRAMEWORK_SECTIONS: Array<{
  id: string;
  shortLabel: string;
  title: string;
  description: string;
}> = [
  {
    id: "bsr",
    shortLabel: "BSR",
    title: "Build Success Rate",
    description: "Fraction of cases where post-repair validation flow finishes successfully.",
  },
  {
    id: "ctsr",
    shortLabel: "CTSR",
    title: "Cross-Target Success Rate",
    description: "Fraction of cases where all declared targets validate successfully.",
  },
  {
    id: "ffsr",
    shortLabel: "FFSR",
    title: "File Fix Success Rate",
    description: "Fraction of broken files that were repaired correctly.",
  },
  {
    id: "efr",
    shortLabel: "EFR",
    title: "Error Fix Rate",
    description: "Fraction of individual compile or test errors that were resolved.",
  },
  {
    id: "hit-at-k",
    shortLabel: "Hit@k",
    title: "Localization Hit@k",
    description:
      "Overlap between localized files and accepted files within top 1, top 3, and top 5 ranking positions.",
  },
  {
    id: "ssa",
    shortLabel: "SSA",
    title: "Source-set Attribution Accuracy",
    description: "Accuracy in correctly attributing evidence and repairs to shared/platform/build source sets.",
  },
];

function modeLabel(mode: string): string {
  if (Object.prototype.hasOwnProperty.call(THESIS_REPAIR_MODE_LABELS, mode)) {
    return THESIS_REPAIR_MODE_LABELS[mode as RepairModeKey];
  }
  return mode.toUpperCase();
}

function metricPercent(value: number | null): number {
  if (value === null || Number.isNaN(value)) return 0;
  return Math.round(Math.max(0, Math.min(1, value)) * 100);
}

function downloadText(filename: string, content: string, type = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function toCsv(rows: ReportsComparisonRow[]): string {
  const header = [
    "repair_mode",
    "cases",
    "bsr",
    "ctsr",
    "ffsr",
    "efr",
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "source_set_accuracy",
  ];

  const lines = rows.map((row) =>
    [
      row.repair_mode,
      row.cases,
      row.bsr,
      row.ctsr,
      row.ffsr,
      row.efr,
      row.hit_at_1,
      row.hit_at_3,
      row.hit_at_5,
      row.source_set_accuracy,
    ].join(","),
  );

  return [header.join(","), ...lines].join("\n");
}

function toMarkdown(rows: ReportsComparisonRow[]): string {
  const header =
    "| mode | cases | BSR | CTSR | FFSR | EFR | Hit@1 | Hit@3 | Hit@5 | SSA |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|";

  const lines = rows.map((row) => {
    return `| ${row.repair_mode} | ${row.cases} | ${metric(row.bsr)} | ${metric(row.ctsr)} | ${metric(
      row.ffsr,
    )} | ${metric(row.efr)} | ${metric(row.hit_at_1)} | ${metric(row.hit_at_3)} | ${metric(
      row.hit_at_5,
    )} | ${metric(row.source_set_accuracy)} |`;
  });

  return ["# kmp-repair aggregated reports", "", header, ...lines].join("\n");
}

export default function ResultsPage() {
  const [rows, setRows] = useState<ReportsComparisonRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedModes, setSelectedModes] = useState<string[]>([...REPAIR_MODES]);
  const [selectedMetric, setSelectedMetric] = useState<MetricKey>("ffsr");
  const [focusedMode, setFocusedMode] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);

    getReportsComparison(selectedModes)
      .then((value) => {
        if (mounted) setRows(value);
      })
      .catch((err) => {
        if (mounted) {
          setRows([]);
          setError(err instanceof Error ? err.message : "Could not load reports");
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [selectedModes]);

  const ordered = useMemo(() => {
    return [...rows].sort((a, b) => b.cases - a.cases);
  }, [rows]);

  useEffect(() => {
    if (!ordered.length) {
      setFocusedMode(null);
      return;
    }

    if (focusedMode && !ordered.some((row) => row.repair_mode === focusedMode)) {
      setFocusedMode(null);
    }
  }, [focusedMode, ordered]);

  const selectedMetricDefinition = useMemo(() => {
    return METRIC_DEFINITIONS.find((definition) => definition.key === selectedMetric) ?? METRIC_DEFINITIONS[0];
  }, [selectedMetric]);

  const toggleMode = (mode: string) => {
    setSelectedModes((prev) => {
      if (prev.includes(mode)) {
        const next = prev.filter((value) => value !== mode);
        return next.length ? next : prev;
      }
      return [...prev, mode];
    });
  };

  const download = (format: "csv" | "json" | "markdown" | "all") => {
    if (format === "csv") {
      downloadText("kmp-report.csv", toCsv(ordered), "text/csv;charset=utf-8");
      return;
    }

    if (format === "json") {
      downloadText("kmp-report.json", JSON.stringify(ordered, null, 2), "application/json;charset=utf-8");
      return;
    }

    if (format === "markdown") {
      downloadText("kmp-report.md", toMarkdown(ordered), "text/markdown;charset=utf-8");
      return;
    }

    download("csv");
    download("json");
    download("markdown");
  };

  return (
    <div className="page-shell py-16">
      <section className="pb-20 pt-8">
        <div>
          <p className="eyebrow mb-6">THESIS / Evaluation Metrics</p>
          <h1 className="editorial-title text-[clamp(2.8rem,7vw,5.7rem)] font-black text-[var(--ink)]">
            Understanding
            <br />
            the Results
          </h1>
          <p className="mt-10 max-w-2xl text-lg leading-relaxed text-[var(--muted)]">
            Results view for the THESIS pipeline. Metrics are loaded from backend aggregates and reflect
            multi-target stability, localization quality, and repair effectiveness.
          </p>
        </div>
      </section>

      <section className="border-y border-[var(--line-quiet)] bg-[var(--surface-low)] px-8 py-16 md:px-10">
        <div className="mb-12">
          <h2 className="editorial-title text-5xl font-black text-[var(--ink)]">Benchmark Modes</h2>
        </div>

        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          {THESIS_REPAIR_MODE_ORDER.map((mode, index) => {
            const details = THESIS_REPAIR_MODE_DETAILS[mode];
            const thesisPrimary = mode === THESIS_PRIMARY_MODE;

            return (
              <article
                key={mode}
                className={thesisPrimary ? "surface-card-dark min-h-[260px] p-8" : "surface-card min-h-[260px] p-8"}
              >
                <span className={thesisPrimary
                  ? "technical-font border border-white/20 px-2 py-1 text-[0.52rem] text-white/70"
                  : "technical-font border border-[var(--line-quiet)] px-2 py-1 text-[0.52rem] text-[var(--muted)]"}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3 className={thesisPrimary
                  ? "display-font mt-8 text-3xl font-bold text-white"
                  : "display-font mt-8 text-3xl font-bold text-[var(--ink)]"}
                >
                  {modeLabel(mode)}
                </h3>
                <p className={thesisPrimary
                  ? "technical-font mt-2 text-[0.52rem] text-white/65"
                  : "technical-font mt-2 text-[0.52rem] text-[var(--muted)]"}
                >
                  Pipeline key: {mode}
                </p>
                <p className={thesisPrimary
                  ? "mt-4 text-sm leading-relaxed text-white/82"
                  : "mt-4 text-sm leading-relaxed text-[var(--muted)]"}
                >
                  Context given to RepairAgent: {details.contextGivenToRepairAgent}
                </p>
                <p className={thesisPrimary
                  ? "technical-font mt-4 text-[0.52rem] text-white/72"
                  : "technical-font mt-4 text-[0.52rem] text-[var(--muted)]"}
                >
                  Retry budget: {details.retryBudget}
                </p>
                <p className={thesisPrimary
                  ? "mt-2 text-sm leading-relaxed text-white/82"
                  : "mt-2 text-sm leading-relaxed text-[var(--muted)]"}
                >
                  {details.notes}
                </p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="grid gap-16 py-24 lg:grid-cols-12">
        <div className="lg:col-span-4 lg:sticky lg:top-24 lg:h-fit">
          <h2 className="editorial-title text-5xl font-black text-[var(--ink)]">
            Metric
            <br />
            Framework
          </h2>
          <p className="mt-8 leading-relaxed text-[var(--muted)]">
            This framework goes beyond pass/fail and captures cross-target stability, localization quality,
            and repair effectiveness.
          </p>
        </div>

        <div className="space-y-16 lg:col-span-8">
          {METRIC_FRAMEWORK_SECTIONS.map((entry, index) => (
            <article key={entry.id}>
              <div className="mb-4 flex items-baseline gap-5">
                <span className="display-font text-6xl font-bold text-stone-200">{String(index + 1).padStart(2, "0")}</span>
                <h3 className="display-font text-4xl font-bold text-[var(--ink)]">
                  {entry.shortLabel}: {entry.title}
                </h3>
              </div>
              <p className="pl-20 text-lg leading-relaxed text-[var(--muted)]">{entry.description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="mb-24 rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] p-6 md:p-10">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-6 border-b border-[var(--line-quiet)] pb-6">
          <div>
            <h2 className="display-font text-4xl font-bold text-[var(--ink)]">Aggregated Reports</h2>
            <p className="technical-font mt-3 text-[0.55rem] text-[var(--muted)]">THESIS metrics snapshot from /api/reports/compare</p>
          </div>

          <div className="flex flex-wrap gap-3">
            <div className="rounded border border-[var(--line-quiet)] bg-white p-1">
              {REPAIR_MODES.map((mode) => {
                const active = selectedModes.includes(mode);
                return (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => toggleMode(mode)}
                    className={active ? "technical-font rounded px-4 py-2 text-[0.56rem] text-white bg-[var(--ink)]" : "technical-font rounded px-4 py-2 text-[0.56rem] text-[var(--muted)] hover:text-[var(--ink)]"}
                  >
                    {mode}
                  </button>
                );
              })}
            </div>

            <button type="button" onClick={() => download("all")} className="button-primary px-4 py-2">
              Export Bundle
            </button>
          </div>
        </div>

        <div className="mb-8 space-y-4 border-b border-[var(--line-quiet)] pb-6">
          <div className="flex flex-wrap items-center gap-3">
            <p className="technical-font text-[0.55rem] text-[var(--muted)]">Metric Lens</p>
            {METRIC_DEFINITIONS.map((definition) => {
              const active = selectedMetric === definition.key;
              return (
                <button
                  key={definition.key}
                  type="button"
                  onClick={() => setSelectedMetric(definition.key)}
                  className={active ? "technical-font rounded-md bg-[var(--ink)] px-3 py-2 text-[0.56rem] text-white" : "technical-font rounded-md border border-[var(--line-quiet)] bg-white px-3 py-2 text-[0.56rem] text-[var(--muted)] hover:text-[var(--ink)]"}
                >
                  {definition.shortLabel}
                </button>
              );
            })}
          </div>
        </div>

        {loading ? <p className="text-sm text-[var(--muted)]">Loading aggregated report rows...</p> : null}
        {error ? <p className="text-sm text-[var(--bad)]">{error}</p> : null}

        {!loading && !error ? (
          <div className="space-y-1">
            {ordered.length > 0 ? (
              ordered.map((row, index) => {
                const focused = focusedMode === row.repair_mode;
                const currentMetric = row[selectedMetric];
                const currentPercent = metricPercent(currentMetric);

                return (
                  <div key={`${row.repair_mode}-${index}`} className="border-b border-[var(--line-quiet)] px-4 py-6 hover:bg-white/70">
                    <article className="grid grid-cols-1 gap-5 md:grid-cols-12 md:items-center">
                      <div className="md:col-span-4">
                        <h3 className="display-font text-2xl font-bold text-[var(--ink)]">{modeLabel(row.repair_mode)}</h3>
                        <p className="technical-font mt-2 text-[0.58rem] text-[var(--muted)]">
                          {row.cases} case{row.cases === 1 ? "" : "s"}
                        </p>
                        <p className="technical-font mt-1 text-[0.52rem] text-[var(--muted)]">pipeline key: {row.repair_mode}</p>
                      </div>

                      <div className="md:col-span-3">
                        <span className="pill">
                          {selectedMetricDefinition.shortLabel} {metric(currentMetric)}
                        </span>
                      </div>

                      <div className="md:col-span-4 flex items-center gap-5">
                        <div className="metric-track w-full">
                          <div className="metric-fill" style={{ width: `${currentPercent}%` }} />
                        </div>
                        <span className="technical-font text-[0.65rem] text-[var(--ink)]">{currentPercent}%</span>
                      </div>

                      <div className="md:col-span-1 flex justify-end">
                        <button
                          type="button"
                          onClick={() => setFocusedMode((current) => (current === row.repair_mode ? null : row.repair_mode))}
                          className={focused ? "technical-font rounded-md bg-[var(--ink)] px-3 py-2 text-[0.55rem] text-white" : "technical-font rounded-md border border-[var(--line-quiet)] px-3 py-2 text-[0.55rem] text-[var(--muted)] hover:text-[var(--ink)]"}
                        >
                          {focused ? "Details open" : "View details"}
                        </button>
                      </div>
                    </article>

                    <div className="mt-4 flex flex-wrap gap-2">
                      {METRIC_DEFINITIONS.map((definition) => {
                        const value = row[definition.key];
                        const active = definition.key === selectedMetric;
                        return (
                          <span
                            key={`${row.repair_mode}-${definition.key}`}
                            className={active ? "technical-font rounded-full border border-[var(--ink)] bg-white px-3 py-1.5 text-[0.55rem] text-[var(--ink)]" : "technical-font rounded-full border border-[var(--line-quiet)] bg-white px-3 py-1.5 text-[0.55rem] text-[var(--muted)]"}
                          >
                            {definition.shortLabel} {metric(value)}
                          </span>
                        );
                      })}
                    </div>

                    {focused ? (
                      <div className="surface-soft mt-5 border border-[var(--line-quiet)] p-5">
                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                          {METRIC_DEFINITIONS.map((definition) => (
                            <div key={`expanded-${row.repair_mode}-${definition.key}`} className="rounded-lg border border-[var(--line-quiet)] bg-white p-3">
                              <p className="technical-font text-[0.52rem] text-[var(--muted)]">{definition.shortLabel}</p>
                              <p className="mt-1 text-[0.95rem] font-semibold leading-snug text-[var(--ink)]">{definition.title}</p>
                              <p className="display-font mt-2 text-xl font-bold text-[var(--ink)]">{metric(row[definition.key])}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })
            ) : (
              <p className="rounded border border-[var(--line-quiet)] bg-white px-4 py-3 text-sm text-[var(--muted)]">
                No rows available for selected modes.
              </p>
            )}
          </div>
        ) : null}

        {!loading && !error && ordered.length > 0 ? (
          <section className="mt-10 border-t border-[var(--line-quiet)] pt-8">
            <ResultsD3Panel
              rows={ordered}
              metricDefinitions={METRIC_DEFINITIONS.map((definition) => ({ key: definition.key, shortLabel: definition.shortLabel }))}
              selectedMetric={selectedMetric}
              focusedMode={focusedMode}
              modeLabel={modeLabel}
              onSelectMetric={setSelectedMetric}
              onSelectMode={setFocusedMode}
            />
          </section>
        ) : null}
      </section>
    </div>
  );
}
