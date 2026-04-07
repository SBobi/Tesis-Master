"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";

import { ReportsComparisonRow } from "@/lib/types";

export type MetricKey =
  | "bsr"
  | "ctsr"
  | "ffsr"
  | "efr"
  | "hit_at_1"
  | "hit_at_3"
  | "hit_at_5"
  | "source_set_accuracy";

type MetricDefinition = {
  key: MetricKey;
  shortLabel: string;
};

type BarDatum = {
  mode: string;
  modeLabel: string;
  value: number;
};

type CellDatum = {
  mode: string;
  modeLabel: string;
  metricKey: MetricKey;
  metricLabel: string;
  value: number | null;
};

type ResultsD3PanelProps = {
  rows: ReportsComparisonRow[];
  metricDefinitions: MetricDefinition[];
  selectedMetric: MetricKey;
  focusedMode: string | null;
  modeLabel: (mode: string) => string;
  onSelectMetric: (metric: MetricKey) => void;
  onSelectMode: (mode: string) => void;
};

function normalizeMetric(value: number | null): number | null {
  if (value === null || Number.isNaN(value)) return null;
  return Math.max(0, Math.min(1, value));
}

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function ResultsD3Panel({
  rows,
  metricDefinitions,
  selectedMetric,
  focusedMode,
  modeLabel,
  onSelectMetric,
  onSelectMode,
}: ResultsD3PanelProps) {
  const barRef = useRef<HTMLDivElement | null>(null);
  const heatmapRef = useRef<HTMLDivElement | null>(null);
  const [barVisible, setBarVisible] = useState(false);

  const barData = useMemo<BarDatum[]>(() => {
    return rows
      .map((row) => {
        const normalized = normalizeMetric(row[selectedMetric]);
        if (normalized === null) return null;

        return {
          mode: row.repair_mode,
          modeLabel: modeLabel(row.repair_mode),
          value: normalized,
        };
      })
      .filter((item): item is BarDatum => item !== null);
  }, [modeLabel, rows, selectedMetric]);

  const heatmapData = useMemo<CellDatum[]>(() => {
    return rows.flatMap((row) => {
      return metricDefinitions.map((metricDefinition) => {
        return {
          mode: row.repair_mode,
          modeLabel: modeLabel(row.repair_mode),
          metricKey: metricDefinition.key,
          metricLabel: metricDefinition.shortLabel,
          value: normalizeMetric(row[metricDefinition.key]),
        };
      });
    });
  }, [metricDefinitions, modeLabel, rows]);

  useEffect(() => {
    const container = barRef.current;
    if (!container) return;

    container.innerHTML = "";
    if (!barData.length) return;

    const brand = cssVar("--brand", "#252626");
    const muted = cssVar("--muted", "#5b5957");
    const line = cssVar("--line", "#c9c6c4");

    const width = 940;
    const height = 340;
    const marginTop = 24;
    const marginRight = 28;
    const marginBottom = 74;
    const marginLeft = 72;

    const svg = d3
      .select(container)
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("class", "h-auto w-full");

    const x = d3
      .scaleBand<string>()
      .domain(barData.map((datum) => datum.modeLabel))
      .range([marginLeft, width - marginRight])
      .padding(0.24);

    const y = d3.scaleLinear().domain([0, 1]).range([height - marginBottom, marginTop]);

    const xAxis = d3.axisBottom(x).tickSizeOuter(0);
    const yAxis = d3
      .axisLeft(y)
      .tickValues([0, 0.25, 0.5, 0.75, 1])
      .tickFormat((value) => `${Math.round(Number(value) * 100)}%`);

    svg
      .append("g")
      .attr("transform", `translate(0,${height - marginBottom})`)
      .call(xAxis)
      .call((axis) => {
        axis.selectAll("path, line").attr("stroke", line);
        axis.selectAll("text").attr("fill", muted).attr("font-size", 11);
      });

    svg
      .append("g")
      .attr("transform", `translate(${marginLeft},0)`)
      .call(yAxis)
      .call((axis) => {
        axis.selectAll("path, line").attr("stroke", line);
        axis.selectAll("text").attr("fill", muted).attr("font-size", 11);
      });

    svg
      .append("g")
      .selectAll("line")
      .data([0.25, 0.5, 0.75, 1])
      .join("line")
      .attr("x1", marginLeft)
      .attr("x2", width - marginRight)
      .attr("y1", (value) => y(value))
      .attr("y2", (value) => y(value))
      .attr("stroke", line)
      .attr("stroke-opacity", 0.35)
      .attr("stroke-dasharray", "3 4");

    svg
      .append("g")
      .selectAll("rect")
      .data(barData)
      .join("rect")
      .attr("x", (datum) => x(datum.modeLabel) ?? 0)
      .attr("y", (datum) => y(datum.value))
      .attr("width", x.bandwidth())
      .attr("height", (datum) => y(0) - y(datum.value))
      .attr("rx", 6)
      .attr("fill", brand)
      .attr("fill-opacity", (datum) => (focusedMode === datum.mode ? 1 : 0.82))
      .style("cursor", "pointer")
      .on("click", (_event, datum) => {
        onSelectMode((datum as BarDatum).mode);
      });

    svg
      .append("g")
      .selectAll("text")
      .data(barData)
      .join("text")
      .attr("x", (datum) => (x(datum.modeLabel) ?? 0) + x.bandwidth() / 2)
      .attr("y", (datum) => y(datum.value) - 8)
      .attr("text-anchor", "middle")
      .attr("font-size", 11)
      .attr("fill", muted)
      .text((datum) => `${Math.round(datum.value * 100)}%`);
  }, [barData, focusedMode, onSelectMode]);

  useEffect(() => {
    const container = heatmapRef.current;
    if (!container) return;

    container.innerHTML = "";
    if (!heatmapData.length) return;

    const ink = cssVar("--ink", "#1c1b1b");
    const muted = cssVar("--muted", "#5b5957");
    const line = cssVar("--line", "#c9c6c4");
    const brand = cssVar("--brand", "#252626");
    const surfaceInk = cssVar("--surface-ink", "#ece7e6");

    const metricLabels = metricDefinitions.map((definition) => definition.shortLabel);
    const modeLabels = rows.map((row) => modeLabel(row.repair_mode));

    const width = Math.max(940, metricLabels.length * 108 + 180);
    const height = Math.max(340, modeLabels.length * 54 + 120);
    const marginTop = 24;
    const marginRight = 24;
    const marginBottom = 84;
    const marginLeft = 170;

    const svg = d3
      .select(container)
      .append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("class", "h-auto w-full");

    const x = d3
      .scaleBand<string>()
      .domain(metricLabels)
      .range([marginLeft, width - marginRight])
      .padding(0.12);

    const y = d3
      .scaleBand<string>()
      .domain(modeLabels)
      .range([marginTop, height - marginBottom])
      .padding(0.14);

    const colorScale = d3.scaleLinear<string>().domain([0, 1]).range([surfaceInk, brand]);

    svg
      .append("g")
      .attr("transform", `translate(0,${height - marginBottom})`)
      .call(d3.axisBottom(x).tickSizeOuter(0))
      .call((axis) => {
        axis.selectAll("path, line").attr("stroke", line);
        axis
          .selectAll("text")
          .attr("fill", muted)
          .attr("font-size", 11)
          .attr("transform", "rotate(-18)")
          .style("text-anchor", "end");
      });

    svg
      .append("g")
      .attr("transform", `translate(${marginLeft},0)`)
      .call(d3.axisLeft(y).tickSizeOuter(0))
      .call((axis) => {
        axis.selectAll("path, line").attr("stroke", line);
        axis.selectAll("text").attr("fill", muted).attr("font-size", 11);
      });

    svg
      .append("g")
      .selectAll("rect")
      .data(heatmapData)
      .join("rect")
      .attr("x", (datum) => x(datum.metricLabel) ?? 0)
      .attr("y", (datum) => y(datum.modeLabel) ?? 0)
      .attr("width", x.bandwidth())
      .attr("height", y.bandwidth())
      .attr("rx", 6)
      .attr("fill", (datum) => {
        if (datum.value === null) return "#ece7e6";
        return colorScale(datum.value);
      })
      .attr("stroke", (datum) => {
        if (datum.metricKey === selectedMetric) return ink;
        if (focusedMode === datum.mode) return brand;
        return line;
      })
      .attr("stroke-width", (datum) => (datum.metricKey === selectedMetric ? 2 : 1))
      .style("cursor", "pointer")
      .on("click", (_event, datum) => {
        const value = datum as CellDatum;
        onSelectMetric(value.metricKey);
        onSelectMode(value.mode);
        setBarVisible(true);
      });

    svg
      .append("g")
      .selectAll("text")
      .data(heatmapData)
      .join("text")
      .attr("x", (datum) => (x(datum.metricLabel) ?? 0) + x.bandwidth() / 2)
      .attr("y", (datum) => (y(datum.modeLabel) ?? 0) + y.bandwidth() / 2 + 4)
      .attr("text-anchor", "middle")
      .attr("font-size", 11)
      .attr("fill", (datum) => {
        if (datum.value === null) return muted;
        return datum.value >= 0.6 ? "#fdf8f7" : ink;
      })
      .text((datum) => {
        if (datum.value === null) return "N/A";
        return `${Math.round(datum.value * 100)}%`;
      });
  }, [focusedMode, heatmapData, metricDefinitions, modeLabel, onSelectMetric, onSelectMode, rows, selectedMetric]);

  return (
    <div className="space-y-8">
      <article className="surface-soft p-5">
        <h3 className="display-font text-2xl font-bold text-[var(--ink)]">Metric Matrix</h3>
        <p className="mt-2 text-sm text-[var(--muted)]">
          Select any cell to set both the active metric and the active mode.
        </p>
        <div ref={heatmapRef} className="mt-5 overflow-x-auto" />
      </article>

      {barVisible ? (
        <article className="surface-soft p-5">
          <h3 className="display-font text-2xl font-bold text-[var(--ink)]">Metric Comparison</h3>
          <p className="mt-2 text-sm text-[var(--muted)]">
            Bars reflect the metric currently selected in the matrix.
          </p>
          <div ref={barRef} className="mt-5" />
        </article>
      ) : null}
    </div>
  );
}
