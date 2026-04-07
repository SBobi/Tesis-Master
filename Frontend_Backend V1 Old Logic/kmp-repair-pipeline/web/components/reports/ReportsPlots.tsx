"use client";

import { useEffect, useMemo, useRef } from "react";
import * as Plot from "@observablehq/plot";

import { ReportsComparisonRow } from "@/lib/types";

type MetricKey =
  | "bsr"
  | "ctsr"
  | "ffsr"
  | "efr"
  | "hit_at_1"
  | "hit_at_3"
  | "hit_at_5"
  | "source_set_accuracy";

type SuccessDatum = {
  mode: string;
  metric: string;
  value: number;
};

type HeatmapNumericDatum = {
  mode: string;
  metric: string;
  value: number;
};

type HeatmapMissingDatum = {
  mode: string;
  metric: string;
};

type TradeoffDatum = {
  mode: string;
  efr: number;
  ffsr: number;
  cases: number;
};

const MODE_ORDER = ["full_thesis", "iterative_agentic", "context_rich", "raw_error"];

const MODE_LABELS: Record<string, string> = {
  full_thesis: "Full Thesis",
  iterative_agentic: "Iterative Agentic",
  context_rich: "Context Rich",
  raw_error: "Raw Error",
};

const METRIC_LABELS: Record<MetricKey, string> = {
  bsr: "BSR",
  ctsr: "CTSR",
  ffsr: "FFSR",
  efr: "EFR",
  hit_at_1: "Hit@1",
  hit_at_3: "Hit@3",
  hit_at_5: "Hit@5",
  source_set_accuracy: "SSA",
};

const SUCCESS_KEYS: MetricKey[] = ["bsr", "ctsr", "ffsr"];

const HEATMAP_KEYS: MetricKey[] = [
  "bsr",
  "ctsr",
  "ffsr",
  "efr",
  "hit_at_1",
  "hit_at_3",
  "hit_at_5",
  "source_set_accuracy",
];

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] || mode;
}

function orderRows(rows: ReportsComparisonRow[]): ReportsComparisonRow[] {
  const indexByMode = new Map(MODE_ORDER.map((mode, index) => [mode, index]));
  return [...rows].sort((left, right) => {
    const leftIndex = indexByMode.get(left.repair_mode) ?? 999;
    const rightIndex = indexByMode.get(right.repair_mode) ?? 999;
    return leftIndex - rightIndex;
  });
}

function chartStyle() {
  return {
    background: "transparent",
    color: "#102238",
    fontFamily: "var(--font-body), sans-serif",
  };
}

export function ReportsPlots({ rows }: { rows: ReportsComparisonRow[] }) {
  const successRef = useRef<HTMLDivElement | null>(null);
  const heatmapRef = useRef<HTMLDivElement | null>(null);
  const tradeoffRef = useRef<HTMLDivElement | null>(null);

  const orderedRows = useMemo(() => orderRows(rows), [rows]);

  const successData = useMemo<SuccessDatum[]>(() => {
    return orderedRows.flatMap((row) => {
      return SUCCESS_KEYS.flatMap((key) => {
        const value = row[key];
        if (value === null) return [];
        return [
          {
            mode: modeLabel(row.repair_mode),
            metric: METRIC_LABELS[key],
            value,
          },
        ];
      });
    });
  }, [orderedRows]);

  const heatmapNumeric = useMemo<HeatmapNumericDatum[]>(() => {
    return orderedRows.flatMap((row) => {
      return HEATMAP_KEYS.flatMap((key) => {
        const value = row[key];
        if (value === null) return [];
        return [
          {
            mode: modeLabel(row.repair_mode),
            metric: METRIC_LABELS[key],
            value,
          },
        ];
      });
    });
  }, [orderedRows]);

  const heatmapMissing = useMemo<HeatmapMissingDatum[]>(() => {
    return orderedRows.flatMap((row) => {
      return HEATMAP_KEYS.flatMap((key) => {
        if (row[key] !== null) return [];
        return [
          {
            mode: modeLabel(row.repair_mode),
            metric: METRIC_LABELS[key],
          },
        ];
      });
    });
  }, [orderedRows]);

  const tradeoffData = useMemo<TradeoffDatum[]>(() => {
    return orderedRows.flatMap((row) => {
      if (row.efr === null || row.ffsr === null) return [];
      return [
        {
          mode: modeLabel(row.repair_mode),
          efr: row.efr,
          ffsr: row.ffsr,
          cases: row.cases,
        },
      ];
    });
  }, [orderedRows]);

  useEffect(() => {
    const container = successRef.current;
    if (!container) return;

    container.innerHTML = "";
    if (successData.length === 0) return;

    const chart = Plot.plot({
      width: 860,
      height: 300,
      marginLeft: 56,
      marginBottom: 52,
      marginTop: 16,
      style: chartStyle(),
      x: { label: null },
      y: {
        domain: [0, 1],
        grid: true,
        label: "Score",
        tickFormat: (value: number) => `${Math.round(value * 100)}%`,
      },
      fx: { label: null },
      color: {
        domain: SUCCESS_KEYS.map((key) => METRIC_LABELS[key]),
        range: ["#2f7fb5", "#2f9f80", "#d9962c"],
      },
      marks: [
        Plot.ruleY([0]),
        Plot.barY(successData, {
          x: "mode",
          fx: "metric",
          y: "value",
          fill: "metric",
          inset: 0.2,
          title: (datum: SuccessDatum) => {
            return `${datum.metric}\n${datum.mode}: ${Math.round(datum.value * 100)}%`;
          },
        }),
        Plot.text(successData, {
          x: "mode",
          fx: "metric",
          y: "value",
          text: (datum: SuccessDatum) => `${Math.round(datum.value * 100)}%`,
          dy: -8,
          fontSize: 10,
          fill: "#102238",
        }),
      ],
    });

    container.append(chart);
    return () => chart.remove();
  }, [successData]);

  useEffect(() => {
    const container = heatmapRef.current;
    if (!container) return;

    container.innerHTML = "";
    if (heatmapNumeric.length === 0 && heatmapMissing.length === 0) return;

    const chart = Plot.plot({
      width: 860,
      height: 340,
      marginLeft: 120,
      marginBottom: 74,
      marginTop: 16,
      style: chartStyle(),
      x: {
        label: null,
        tickRotate: -26,
      },
      y: {
        label: null,
      },
      color: {
        label: "Score",
        domain: [0, 1],
        range: ["#e3edf7", "#1f6fb2"],
      },
      marks: [
        Plot.cell(heatmapMissing, {
          x: "metric",
          y: "mode",
          fill: "#e8edf5",
          stroke: "#c8d3e2",
        }),
        Plot.cell(heatmapNumeric, {
          x: "metric",
          y: "mode",
          fill: "value",
          title: (datum: HeatmapNumericDatum) => {
            return `${datum.mode}\n${datum.metric}: ${datum.value.toFixed(3)}`;
          },
        }),
        Plot.text(heatmapNumeric, {
          x: "metric",
          y: "mode",
          text: (datum: HeatmapNumericDatum) => datum.value.toFixed(2),
          fill: (datum: HeatmapNumericDatum) => (datum.value > 0.58 ? "#f7fbff" : "#173659"),
          fontSize: 11,
        }),
        Plot.text(heatmapMissing, {
          x: "metric",
          y: "mode",
          text: () => "N/A",
          fill: "#6b7f99",
          fontSize: 11,
        }),
      ],
    });

    container.append(chart);
    return () => chart.remove();
  }, [heatmapMissing, heatmapNumeric]);

  useEffect(() => {
    const container = tradeoffRef.current;
    if (!container) return;

    container.innerHTML = "";
    if (tradeoffData.length === 0) return;

    const chart = Plot.plot({
      width: 860,
      height: 320,
      marginLeft: 56,
      marginBottom: 52,
      marginTop: 16,
      style: chartStyle(),
      x: {
        label: "EFR",
        domain: [0, 1],
        grid: true,
      },
      y: {
        label: "FFSR",
        domain: [0, 1],
        grid: true,
      },
      color: {
        domain: tradeoffData.map((datum) => datum.mode),
        range: ["#1f6fb2", "#2f9f80", "#c86b3c", "#8b5fc0"],
      },
      marks: [
        Plot.dot(tradeoffData, {
          x: "efr",
          y: "ffsr",
          r: (datum: TradeoffDatum) => 7 + Math.min(datum.cases, 12),
          fill: "mode",
          stroke: "#ffffff",
          strokeWidth: 1.6,
          title: (datum: TradeoffDatum) => {
            return `${datum.mode}\nEFR: ${datum.efr.toFixed(3)}\nFFSR: ${datum.ffsr.toFixed(3)}\nCases: ${datum.cases}`;
          },
        }),
        Plot.text(tradeoffData, {
          x: "efr",
          y: "ffsr",
          text: (datum: TradeoffDatum) => datum.mode,
          dy: -14,
          fontSize: 10,
          fill: "#11253f",
        }),
      ],
    });

    container.append(chart);
    return () => chart.remove();
  }, [tradeoffData]);

  return (
    <div className="space-y-6">
      <section className="rounded-3xl border border-[var(--color-border)] bg-white/75 p-5">
        <h3 className="display-serif text-2xl">Success by mode and metric</h3>
        <p className="mt-1 text-xs text-muted">Observable Plot small multiples for BSR, CTSR, and FFSR.</p>
        {successData.length > 0 ? (
          <div className="mt-4 overflow-x-auto">
            <div ref={successRef} className="min-w-[760px]" />
          </div>
        ) : (
          <p className="mt-4 rounded-xl border border-[var(--color-border)] bg-white/70 px-3 py-2 text-sm text-muted">
            No numeric data available to render the success chart.
          </p>
        )}
      </section>

      <section className="rounded-3xl border border-[var(--color-border)] bg-white/75 p-5">
        <h3 className="display-serif text-2xl">Metrics heatmap</h3>
        <p className="mt-1 text-xs text-muted">Consolidated view by mode. Missing values are shown as N/A.</p>
        {heatmapNumeric.length > 0 || heatmapMissing.length > 0 ? (
          <div className="mt-4 overflow-x-auto">
            <div ref={heatmapRef} className="min-w-[760px]" />
          </div>
        ) : (
          <p className="mt-4 rounded-xl border border-[var(--color-border)] bg-white/70 px-3 py-2 text-sm text-muted">
            Not enough data to render the heatmap.
          </p>
        )}
      </section>

      <section className="rounded-3xl border border-[var(--color-border)] bg-white/75 p-5">
        <h3 className="display-serif text-2xl">Correction vs completeness balance</h3>
        <p className="mt-1 text-xs text-muted">EFR vs FFSR scatter plot to compare trade-offs by mode.</p>
        {tradeoffData.length > 0 ? (
          <div className="mt-4 overflow-x-auto">
            <div ref={tradeoffRef} className="min-w-[760px]" />
          </div>
        ) : (
          <p className="mt-4 rounded-xl border border-[var(--color-border)] bg-white/70 px-3 py-2 text-sm text-muted">
            No rows with both EFR and FFSR are available for the scatter plot.
          </p>
        )}
      </section>
    </div>
  );
}
