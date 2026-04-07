"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ReactNode, useEffect, useMemo, useState } from "react";

import { LiveJobConsole } from "@/components/LiveJobConsole";
import { UnifiedDiffViewer } from "@/components/case/UnifiedDiffViewer";
import { cancelJob, getArtifactContent, getCase, runPipeline, runStage } from "@/lib/api";
import {
  BASELINE_MODES,
  PATCH_STRATEGIES,
  PROVIDERS,
  REPAIR_MODES,
  TARGETS,
} from "@/lib/constants";
import { formatDate, metric, shortId } from "@/lib/format";
import { CaseDetail } from "@/lib/types";
import { caseStatusLabel, stageLabel, validationLabel } from "@/lib/ui";

const LIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

type FlowStatus = "NOT_STARTED" | "RUNNING" | "COMPLETED" | "FAILED";

const PROCESS_FLOW: Array<{ label: string; stages: string[] }> = [
  { label: "Discover & Ingest", stages: ["ingest"] },
  { label: "Build Generation", stages: ["build-case"] },
  { label: "Run Before/After", stages: ["run-before-after"] },
  { label: "Analyze Structure", stages: ["analyze-case"] },
  { label: "Localize Impact", stages: ["localize"] },
  { label: "Repair Synthesis", stages: ["repair"] },
  { label: "Validation", stages: ["validate"] },
  { label: "Explain", stages: ["explain"] },
  { label: "Metrics/Report", stages: ["metrics", "report"] },
];

function summarizeFlowStatus(stages: string[], map: Map<string, FlowStatus>): FlowStatus {
  const statuses = stages.map((stage) => map.get(stage) ?? "NOT_STARTED");

  if (statuses.includes("RUNNING")) return "RUNNING";
  if (statuses.includes("FAILED")) return "FAILED";
  if (statuses.every((status) => status === "COMPLETED")) return "COMPLETED";
  return "NOT_STARTED";
}

function toTimestampMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function modeSort(mode: string): number {
  const idx = REPAIR_MODES.findIndex((value) => value === mode);
  return idx === -1 ? 999 : idx;
}

const RUN_BATCH_GAP_MS = 150000;

function jobReferenceTime(job: CaseDetail["jobs"][number]): number | null {
  return toTimestampMs(job.created_at) ?? toTimestampMs(job.queued_at) ?? toTimestampMs(job.started_at) ?? toTimestampMs(job.finished_at);
}

function extractRepairModeFromJob(job: CaseDetail["jobs"][number]): string | null {
  const fromStageParams = (source: Record<string, unknown> | null | undefined): string | null => {
    if (!source) return null;
    const repair = source.repair;
    if (!repair || typeof repair !== "object" || Array.isArray(repair)) return null;

    const mode = (repair as Record<string, unknown>).mode;
    if (typeof mode === "string" && mode.trim().length > 0) return mode;
    return null;
  };

  const fromCommand = (): string | null => {
    if (!job.command_preview) return null;
    const match = job.command_preview.match(/--mode\s+([A-Za-z0-9_-]+)/);
    return match?.[1] || null;
  };

  return fromStageParams(job.effective_params) || fromStageParams(job.params) || fromCommand();
}

function latestJob(detail: CaseDetail | null) {
  if (!detail || detail.jobs.length === 0) return null;
  return [...detail.jobs].sort((a, b) => Date.parse(b.created_at || b.queued_at) - Date.parse(a.created_at || a.queued_at))[0];
}

function activeJob(detail: CaseDetail | null) {
  if (!detail) return null;
  return detail.jobs.find((job) => LIVE_JOB_STATUSES.has(job.status)) || null;
}

type Signal = "PASS" | "FAIL" | "PENDING" | "N/A";

type TargetTone = "success" | "error" | "pending";

function isSuccessStatus(status: string): boolean {
  return status === "SUCCESS_REPOSITORY_LEVEL" || status === "PARTIAL_SUCCESS";
}

function isFailureStatus(status: string): boolean {
  return status === "FAILED_BUILD" || status === "FAILED_TESTS";
}

function inferTaskTarget(taskName: string): "shared" | "android" | "ios" | null {
  const name = taskName.toLowerCase();
  if (name.includes("ios")) return "ios";
  if (name.includes("android") || name.includes("jvm")) return "android";
  if (name.includes("common") || name.includes("metadata") || name.includes("shared")) return "shared";
  return null;
}

function inferTaskColumn(taskName: string): "build" | "compile" | "unit" | "ui" | null {
  const name = taskName.toLowerCase();
  if (name.includes("androidtest") || name.includes("ui") || name.includes("instrument")) return "ui";
  if (name.includes("test")) return "unit";
  if (name.includes("assemble") || name.includes("bundle") || name.includes("build")) return "build";
  if (name.includes("compile")) return "compile";
  return null;
}

function aggregateSignal(statuses: string[]): Signal {
  if (statuses.length === 0) return "N/A";
  if (statuses.some(isFailureStatus)) return "FAIL";
  if (statuses.every(isSuccessStatus)) return "PASS";
  return "PENDING";
}

function targetTone(status: string): TargetTone {
  if (isSuccessStatus(status)) return "success";
  if (isFailureStatus(status)) return "error";
  return "pending";
}

function targetToneDotClass(tone: TargetTone): string {
  if (tone === "success") return "dot dot-ok";
  if (tone === "error") return "dot dot-bad";
  return "dot dot-warn";
}

function targetToneCardClass(tone: TargetTone): string {
  if (tone === "success") return "surface-card p-4";
  if (tone === "error") return "surface-card p-4";
  return "surface-card p-4";
}

function artifactPath(path: string | null | undefined): string {
  if (!path) return "-";
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/");
  return parts.slice(Math.max(0, parts.length - 3)).join("/");
}

function artifactRequestCandidates(path: string, caseId: string): string[] {
  const normalized = path.replace(/\\/g, "/").trim();
  const candidates: string[] = [];

  function add(candidate: string | null | undefined) {
    const value = candidate?.trim();
    if (!value) return;
    if (!candidates.includes(value)) candidates.push(value);
  }

  add(normalized);
  add(normalized.replace(/^\.\//, ""));

  const casePrefix = `${caseId}/`;
  if (normalized.startsWith(casePrefix)) {
    add(normalized.slice(casePrefix.length));
  }

  const dataArtifactsPrefix = `data/artifacts/${caseId}/`;
  const dataArtifactsIndex = normalized.indexOf(dataArtifactsPrefix);
  if (dataArtifactsIndex >= 0) {
    add(normalized.slice(dataArtifactsIndex + dataArtifactsPrefix.length));
  }

  const artifactsMarker = `/artifacts/${caseId}/`;
  const artifactsMarkerIndex = normalized.lastIndexOf(artifactsMarker);
  if (artifactsMarkerIndex >= 0) {
    add(normalized.slice(artifactsMarkerIndex + artifactsMarker.length));
  }

  const caseTokenIndex = normalized.lastIndexOf(casePrefix);
  if (caseTokenIndex > 0) {
    add(normalized.slice(caseTokenIndex + casePrefix.length));
  }

  return candidates;
}

async function fetchArtifactContentWithFallback(caseId: string, path: string): Promise<string> {
  const candidates = artifactRequestCandidates(path, caseId);
  let lastError: unknown = null;

  for (const candidate of candidates) {
    try {
      return await getArtifactContent(caseId, candidate);
    } catch (err) {
      lastError = err;
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Could not load artifact content.");
}

type DiffChunk = {
  path: string;
  raw: string;
};

type ArtifactEntry = {
  path: string;
  label: string;
  kind: "diff" | "markdown" | "text";
};

function normalizeDiffPath(raw: string): string | null {
  const value = raw.trim();
  if (!value || value === "/dev/null") return null;
  return value.replace(/^a\//, "").replace(/^b\//, "");
}

function parseDiffChunks(rawDiff: string): DiffChunk[] {
  const lines = rawDiff.split(/\r?\n/);
  const gitStarts: number[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    if (lines[index].startsWith("diff --git ")) {
      gitStarts.push(index);
    }
  }

  const starts = gitStarts.length > 0
    ? gitStarts
    : lines
      .map((line, index) => {
        const next = lines[index + 1] || "";
        return line.startsWith("--- ") && next.startsWith("+++ ") ? index : -1;
      })
      .filter((index) => index >= 0);

  if (starts.length === 0) {
    const rawPathLine = lines.find((line) => line.startsWith("+++ ")) || lines.find((line) => line.startsWith("--- "));
    const fallbackPath = rawPathLine ? normalizeDiffPath(rawPathLine.slice(4)) : null;
    return [{ path: fallbackPath || "patch.diff", raw: rawDiff }];
  }

  const chunksByPath = new Map<string, string[]>();

  for (let index = 0; index < starts.length; index += 1) {
    const start = starts[index];
    const end = index + 1 < starts.length ? starts[index + 1] : lines.length;
    const chunkLines = lines.slice(start, end);
    const headerParts = chunkLines[0].split(" ");

    const oldPath = chunkLines[0].startsWith("diff --git ")
      ? (headerParts[2] ? normalizeDiffPath(headerParts[2]) : null)
      : (() => {
        const oldPathLine = chunkLines.find((line) => line.startsWith("--- "));
        return oldPathLine ? normalizeDiffPath(oldPathLine.slice(4)) : null;
      })();

    const newPath = chunkLines[0].startsWith("diff --git ")
      ? (headerParts[3] ? normalizeDiffPath(headerParts[3]) : null)
      : (() => {
        const newPathLine = chunkLines.find((line) => line.startsWith("+++ "));
        return newPathLine ? normalizeDiffPath(newPathLine.slice(4)) : null;
      })();

    const rawPathLine = chunkLines.find((line) => line.startsWith("+++ ")) || chunkLines.find((line) => line.startsWith("--- "));

    const filePath = newPath || oldPath || (rawPathLine ? normalizeDiffPath(rawPathLine.slice(4)) : null) || `file-${index + 1}`;

    const current = chunksByPath.get(filePath) || [];
    current.push(chunkLines.join("\n"));
    chunksByPath.set(filePath, current);
  }

  return [...chunksByPath.entries()].map(([path, chunks]) => ({ path, raw: chunks.join("\n") }));
}

function artifactKindFromPath(path: string): ArtifactEntry["kind"] {
  const lowerPath = path.toLowerCase();
  if (lowerPath.endsWith(".diff") || lowerPath.endsWith(".patch")) return "diff";
  if (lowerPath.endsWith(".md") || lowerPath.endsWith(".markdown")) return "markdown";
  return "text";
}

function stripMarkdownInline(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1");
}

function addTwoHashesToHeadings(markdown: string): string {
  return markdown.replace(/^(#{1,6})(\s+)/gm, (_match, hashes: string, whitespace: string) => {
    const depth = Math.min(6, hashes.length + 2);
    return `${"#".repeat(depth)}${whitespace}`;
  });
}

function renderMarkdownEditorial(markdown: string): ReactNode {
  const normalizedMarkdown = addTwoHashesToHeadings(markdown);

  const blocks = normalizedMarkdown
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  return blocks.map((block, index) => {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length === 0) return null;

    if (lines.every((line) => /^[-*]\s+/.test(line))) {
      return (
        <ul key={index} className="mt-4 list-disc space-y-1 pl-5 text-[var(--muted)]">
          {lines.map((line, lineIndex) => (
            <li key={`${index}-${lineIndex}`}>{stripMarkdownInline(line.replace(/^[-*]\s+/, ""))}</li>
          ))}
        </ul>
      );
    }

    const first = lines[0];
    const body = lines.slice(1).join(" ");
    const headingMatch = first.match(/^(#{1,6})\s+(.+)$/);

    if (headingMatch) {
      const level = headingMatch[1].length;
      const headingText = stripMarkdownInline(headingMatch[2]);
      const headingClass =
        level <= 2
          ? "display-font text-2xl font-bold text-[var(--ink)]"
          : level === 3
            ? "display-font text-xl font-bold text-[var(--ink)]"
            : level === 4
              ? "display-font text-lg font-bold text-[var(--ink)]"
              : "display-font text-base font-bold text-[var(--ink)]";
      const wrapperClass = level <= 3 ? "mt-4" : "mt-3";

      return (
        <div key={index} className={wrapperClass}>
          <h3 className={headingClass}>{headingText}</h3>
          {body ? <p className="mt-2 leading-relaxed text-[var(--muted)]">{stripMarkdownInline(body)}</p> : null}
        </div>
      );
    }

    return (
      <p key={index} className="mt-3 leading-relaxed text-[var(--muted)]">
        {stripMarkdownInline(lines.join(" "))}
      </p>
    );
  });
}

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>();
  const searchParams = useSearchParams();
  const caseId = String(params.caseId);
  const casesQuery = searchParams.toString();
  const casesListHref = casesQuery ? `/cases?${casesQuery}` : "/cases";

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedModes, setSelectedModes] = useState<string[]>(["iterative_agentic"]);
  const [runAllBaselines, setRunAllBaselines] = useState(false);
  const [selectedTargets, setSelectedTargets] = useState<string[]>([...TARGETS]);
  const [timeoutS, setTimeoutS] = useState(600);
  const [freshReset, setFreshReset] = useState(false);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [provider, setProvider] = useState<string>("vertex");
  const [model, setModel] = useState<string>("");
  const [localizeTopK, setLocalizeTopK] = useState(10);
  const [repairTopK, setRepairTopK] = useState(5);
  const [patchStrategy, setPatchStrategy] = useState<string>("single_diff");
  const [forcePatchAttempt, setForcePatchAttempt] = useState(true);
  const [attemptForValidate, setAttemptForValidate] = useState<string>("");
  const [artifactBase, setArtifactBase] = useState("");

  const [selectedExecutedMode, setSelectedExecutedMode] = useState<string>("iterative_agentic");
  const [diffContent, setDiffContent] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [selectedDiffFile, setSelectedDiffFile] = useState<string>("");
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string>("");
  const [selectedArtifactContent, setSelectedArtifactContent] = useState<string | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);

  const [explanationMarkdown, setExplanationMarkdown] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [queuedJobs, setQueuedJobs] = useState<string[]>([]);

  const isNoErrorsCase = detail?.case.status === "NO_ERRORS_TO_FIX";
  const active = activeJob(detail);
  const latest = latestJob(detail);
  const consoleJobId = active?.job_id || latest?.job_id || null;

  const latestRunJobs = useMemo(() => {
    if (!detail || detail.jobs.length === 0) return [] as CaseDetail["jobs"];

    const sorted = [...detail.jobs].sort((a, b) => {
      const left = jobReferenceTime(a) ?? 0;
      const right = jobReferenceTime(b) ?? 0;
      return right - left;
    });

    const anchor = sorted[0];
    const anchorTime = jobReferenceTime(anchor);
    if (anchorTime === null) return [anchor];

    const batch: CaseDetail["jobs"] = [];
    for (const job of sorted) {
      const ts = jobReferenceTime(job);
      if (ts === null) continue;

      if (anchorTime - ts <= RUN_BATCH_GAP_MS) {
        batch.push(job);
        continue;
      }

      break;
    }

    return batch.length > 0 ? batch : [anchor];
  }, [detail]);

  const latestRunWindow = useMemo(() => {
    if (latestRunJobs.length === 0) return null;

    const starts = latestRunJobs
      .map((job) => toTimestampMs(job.started_at) ?? toTimestampMs(job.queued_at) ?? jobReferenceTime(job))
      .filter((value): value is number => value !== null);

    if (starts.length === 0) return null;

    const hasLive = latestRunJobs.some((job) => LIVE_JOB_STATUSES.has(job.status));
    const ends = latestRunJobs
      .map((job) => toTimestampMs(job.finished_at) ?? toTimestampMs(job.started_at) ?? toTimestampMs(job.queued_at) ?? jobReferenceTime(job))
      .filter((value): value is number => value !== null);

    const start = Math.min(...starts);
    const end = hasLive ? Date.now() : (ends.length > 0 ? Math.max(...ends) : Date.now());

    return {
      startMs: start - 120000,
      endMs: end + 120000,
    };
  }, [latestRunJobs]);

  const scopedEvidence = useMemo(() => {
    if (!detail) return null;
    const evidence = detail.evidence;
    if (!latestRunWindow) return evidence;

    const inWindow = (value: string | null | undefined): boolean => {
      const ts = toTimestampMs(value);
      if (ts === null) return false;
      return ts >= latestRunWindow.startMs && ts <= latestRunWindow.endMs;
    };

    const patchAttempts = evidence.patch_attempts.filter((attempt) => inWindow(attempt.created_at));
    const patchAttemptIds = new Set(patchAttempts.map((attempt) => attempt.id));

    return {
      ...evidence,
      patch_attempts: patchAttempts,
      validation_by_target: evidence.validation_by_target.filter(
        (row) => patchAttemptIds.has(row.patch_attempt_id) || inWindow(row.started_at) || inWindow(row.ended_at),
      ),
      explanations: evidence.explanations.filter((item) => inWindow(item.created_at)),
      agent_logs: evidence.agent_logs.filter((item) => inWindow(item.created_at)),
      metrics: evidence.metrics.filter((item) => inWindow(item.updated_at)),
      execution_before_after: evidence.execution_before_after.filter(
        (item) => inWindow(item.started_at) || inWindow(item.ended_at),
      ),
    };
  }, [detail, latestRunWindow]);

  async function load(showLoader = true) {
    if (showLoader) {
      setLoading(true);
      setError(null);
    }
    try {
      const payload = await getCase(caseId);
      setDetail(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el caso");
    } finally {
      if (showLoader) setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [caseId]);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      load(false);
    }, 2800);
    return () => clearInterval(timer);
  }, [active?.job_id]);

  const attemptsByMode = useMemo(() => {
    const map = new Map<string, CaseDetail["evidence"]["patch_attempts"]>();
    if (!scopedEvidence) return map;

    for (const attempt of scopedEvidence.patch_attempts) {
      const current = map.get(attempt.repair_mode) || [];
      current.push(attempt);
      map.set(attempt.repair_mode, current);
    }

    for (const [mode, attempts] of map) {
      map.set(
        mode,
        [...attempts].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)),
      );
    }

    return map;
  }, [scopedEvidence]);

  const executedModes = useMemo(() => {
    const modes = new Set<string>(attemptsByMode.keys());

    for (const job of latestRunJobs) {
      const mode = extractRepairModeFromJob(job);
      if (mode) modes.add(mode);
    }

    return [...modes].sort((a, b) => {
      const diff = modeSort(a) - modeSort(b);
      return diff !== 0 ? diff : a.localeCompare(b);
    });
  }, [attemptsByMode, latestRunJobs]);

  useEffect(() => {
    if (executedModes.length === 0) {
      setSelectedExecutedMode("iterative_agentic");
      return;
    }
    if (!executedModes.includes(selectedExecutedMode)) {
      setSelectedExecutedMode(executedModes[0]);
    }
  }, [executedModes, selectedExecutedMode]);

  const modeAttempts = useMemo(() => {
    return attemptsByMode.get(selectedExecutedMode) || [];
  }, [attemptsByMode, selectedExecutedMode]);

  const selectedAttempt = useMemo(() => {
    return modeAttempts[0] || null;
  }, [modeAttempts]);

  const latestExplanation = useMemo(() => {
    if (!scopedEvidence || scopedEvidence.explanations.length === 0) return null;
    return [...scopedEvidence.explanations].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  }, [scopedEvidence]);

  useEffect(() => {
    let cancelled = false;

    async function loadDiff() {
      if (!selectedAttempt) {
        setDiffContent(detail?.evidence.update_evidence.raw_diff || null);
        return;
      }

      if (!selectedAttempt.diff_path) {
        setDiffContent(selectedAttempt.diff_preview || detail?.evidence.update_evidence.raw_diff || null);
        return;
      }

      setDiffLoading(true);
      try {
        const content = await fetchArtifactContentWithFallback(caseId, selectedAttempt.diff_path);
        if (!cancelled) setDiffContent(content);
      } catch {
        if (!cancelled) {
          setDiffContent(selectedAttempt.diff_preview || detail?.evidence.update_evidence.raw_diff || null);
        }
      } finally {
        if (!cancelled) setDiffLoading(false);
      }
    }

    loadDiff();

    return () => {
      cancelled = true;
    };
  }, [caseId, detail?.evidence.update_evidence.raw_diff, selectedAttempt]);

  const diffChunks = useMemo(() => {
    if (!diffContent || !diffContent.trim()) return [];
    return parseDiffChunks(diffContent);
  }, [diffContent]);

  const changedFiles = useMemo(() => {
    const fromDiff = diffChunks
      .map((chunk) => chunk.path)
      .filter((path) => path && path !== "patch.diff");
    const touchedFiles = selectedAttempt?.touched_files || [];

    const combined = [...fromDiff, ...touchedFiles];
    if (combined.length > 0) {
      return [...new Set(combined)];
    }

    if (diffChunks.length > 0) {
      return [...new Set(diffChunks.map((chunk) => chunk.path))];
    }

    return [];
  }, [diffChunks, selectedAttempt]);

  useEffect(() => {
    if (changedFiles.length === 0) {
      setSelectedDiffFile("");
      return;
    }

    if (!selectedDiffFile || !changedFiles.includes(selectedDiffFile)) {
      setSelectedDiffFile(changedFiles[0]);
    }
  }, [changedFiles, selectedDiffFile]);

  const activeDiffContent = useMemo(() => {
    if (diffChunks.length === 0) return diffContent;
    const selectedChunk = diffChunks.find((chunk) => chunk.path === selectedDiffFile);
    return (selectedChunk || diffChunks[0]).raw;
  }, [diffChunks, diffContent, selectedDiffFile]);

  const artifactEntries = useMemo(() => {
    if (!scopedEvidence) return [] as ArtifactEntry[];

    const entries = new Map<string, ArtifactEntry>();

    function addEntry(path: string | null | undefined, label: string) {
      if (!path || entries.has(path)) return;
      entries.set(path, {
        path,
        label,
        kind: artifactKindFromPath(path),
      });
    }

    addEntry(selectedAttempt?.diff_path, `diff: ${artifactPath(selectedAttempt?.diff_path)}`);
    addEntry(latestExplanation?.markdown_path, `explain.md: ${artifactPath(latestExplanation?.markdown_path)}`);
    addEntry(latestExplanation?.json_path, `explain.json: ${artifactPath(latestExplanation?.json_path)}`);

    for (const log of scopedEvidence.agent_logs.slice(-8)) {
      if (log.prompt_path?.includes(caseId)) {
        addEntry(log.prompt_path, `prompt ${log.agent_type} #${log.call_index}`);
      }
      if (log.response_path?.includes(caseId)) {
        addEntry(log.response_path, `response ${log.agent_type} #${log.call_index}`);
      }
    }

    return [...entries.values()];
  }, [caseId, latestExplanation, scopedEvidence, selectedAttempt]);

  useEffect(() => {
    if (artifactEntries.length === 0) {
      setSelectedArtifactPath("");
      return;
    }

    if (!selectedArtifactPath || !artifactEntries.some((entry) => entry.path === selectedArtifactPath)) {
      setSelectedArtifactPath(artifactEntries[0].path);
    }
  }, [artifactEntries, selectedArtifactPath]);

  const selectedArtifact = useMemo(() => {
    return artifactEntries.find((entry) => entry.path === selectedArtifactPath) || artifactEntries[0] || null;
  }, [artifactEntries, selectedArtifactPath]);

  useEffect(() => {
    let cancelled = false;

    async function loadArtifact() {
      if (!selectedArtifact?.path) {
        setSelectedArtifactContent(null);
        setArtifactError(null);
        return;
      }

      setArtifactLoading(true);
      setArtifactError(null);

      try {
        const content = await fetchArtifactContentWithFallback(caseId, selectedArtifact.path);
        if (!cancelled) setSelectedArtifactContent(content);
      } catch (err) {
        if (!cancelled) {
          setSelectedArtifactContent(null);
          setArtifactError(err instanceof Error ? err.message : "Could not load artifact content.");
        }
      } finally {
        if (!cancelled) setArtifactLoading(false);
      }
    }

    loadArtifact();

    return () => {
      cancelled = true;
    };
  }, [caseId, selectedArtifact?.path]);

  useEffect(() => {
    let cancelled = false;

    async function loadExplanation() {
      if (!latestExplanation) {
        setExplanationMarkdown(null);
        return;
      }

      if (latestExplanation.markdown_path) {
        try {
          const content = await fetchArtifactContentWithFallback(caseId, latestExplanation.markdown_path);
          if (!cancelled) setExplanationMarkdown(content);
        } catch {
          if (!cancelled) setExplanationMarkdown(latestExplanation.markdown_preview || null);
        }
      } else {
        setExplanationMarkdown(latestExplanation.markdown_preview || null);
      }
    }

    loadExplanation();

    return () => {
      cancelled = true;
    };
  }, [caseId, latestExplanation]);

  const validationRows = useMemo(() => {
    if (!scopedEvidence || !selectedAttempt) return [];

    const source = scopedEvidence.validation_by_target.filter((row) => {
      return row.patch_attempt_id === selectedAttempt.id;
    });

    const byTarget = new Map<string, (typeof source)[number]>();
    for (const row of source) {
      const current = byTarget.get(row.target);
      if (!current || Date.parse(row.started_at || row.ended_at || "1970-01-01") > Date.parse(current.started_at || current.ended_at || "1970-01-01")) {
        byTarget.set(row.target, row);
      }
    }

    return [...byTarget.values()];
  }, [scopedEvidence, selectedAttempt]);

  const metricsByMode = useMemo(() => {
    if (!scopedEvidence) return new Map<string, CaseDetail["evidence"]["metrics"][number]>();

    const orderedMetrics = [...scopedEvidence.metrics].sort((a, b) => Date.parse(a.updated_at) - Date.parse(b.updated_at));
    return new Map(orderedMetrics.map((row) => [row.repair_mode, row]));
  }, [scopedEvidence]);

  const selectedMetric = metricsByMode.get(selectedExecutedMode) || null;

  function toggleMode(mode: string) {
    setRunAllBaselines(false);
    setSelectedModes((prev) => {
      if (prev.includes(mode)) {
        const next = prev.filter((item) => item !== mode);
        return next.length ? next : prev;
      }
      return [...prev, mode];
    });
  }

  function toggleTarget(target: string) {
    setSelectedTargets((prev) => {
      if (prev.includes(target)) {
        const next = prev.filter((item) => item !== target);
        return next.length ? next : prev;
      }
      return [...prev, target];
    });
  }

  function validateRunInputs(): string | null {
    if (!Number.isInteger(timeoutS) || timeoutS <= 0) {
      return "Los timeouts deben ser enteros positivos";
    }

    if (provider && !PROVIDERS.includes(provider as (typeof PROVIDERS)[number])) {
      return "provider solo puede ser anthropic o vertex";
    }

    if (!PATCH_STRATEGIES.includes(patchStrategy as (typeof PATCH_STRATEGIES)[number])) {
      return "patch_strategy solo puede ser single_diff o chain_by_file";
    }

    if (selectedTargets.some((target) => !TARGETS.includes(target as (typeof TARGETS)[number]))) {
      return "targets solo puede incluir shared, android o ios";
    }

    return null;
  }

  async function onRunSelectedModes() {
    if (!detail) return;
    if (isNoErrorsCase) {
      setRunError("Este caso está en NO_ERRORS_TO_FIX. No necesitas repair/validate; continúa con metrics.");
      return;
    }

    const inputError = validateRunInputs();
    if (inputError) {
      setRunError(inputError);
      return;
    }

    const modes = runAllBaselines ? [...BASELINE_MODES] : selectedModes;
    if (modes.length === 0) {
      setRunError("Selecciona al menos un modo o usa Run all baselines.");
      return;
    }

    setBusy(true);
    setRunError(null);
    setQueuedJobs([]);

    try {
      const queued: string[] = [];
      const startFrom = freshReset ? "build-case" : "repair";
      const artifact = artifactBase.trim() || undefined;

      for (const mode of modes) {
        const paramsByStage: Record<string, Record<string, unknown>> = {
          repair: {
            mode,
            top_k: repairTopK,
            provider: provider || undefined,
            model: model || undefined,
            patch_strategy: patchStrategy,
            force_patch_attempt: forcePatchAttempt,
            artifact_base: artifact,
          },
          validate: {
            targets: selectedTargets,
            timeout_s: timeoutS,
            artifact_base: artifact,
          },
          explain: {
            provider: provider || undefined,
            model: model || undefined,
            artifact_base: artifact,
          },
          metrics: {},
          report: {
            format: "all",
            modes: [mode],
            cases: [caseId],
          },
        };

        if (freshReset) {
          paramsByStage["build-case"] = {
            artifact_base: artifact,
            overwrite: true,
          };
          paramsByStage["run-before-after"] = {
            targets: selectedTargets,
            timeout_s: timeoutS,
            artifact_base: artifact,
          };
          paramsByStage["analyze-case"] = {};
          paramsByStage["localize"] = {
            top_k: localizeTopK,
            provider: provider || undefined,
            model: model || undefined,
            artifact_base: artifact,
          };
        }

        const job = await runPipeline(caseId, startFrom, paramsByStage);
        queued.push(job.job_id);
      }

      setQueuedJobs(queued);
      await load(false);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "No se pudo encolar la ejecución");
    } finally {
      setBusy(false);
    }
  }

  async function onValidateAttempt() {
    if (!attemptForValidate) {
      setRunError("Selecciona attempt_id para validar.");
      return;
    }

    if (isNoErrorsCase) {
      setRunError("Este caso no requiere validate: estado NO_ERRORS_TO_FIX.");
      return;
    }

    const inputError = validateRunInputs();
    if (inputError) {
      setRunError(inputError);
      return;
    }

    try {
      setBusy(true);
      setRunError(null);
      const job = await runStage(caseId, "validate", {
        attempt_id: attemptForValidate,
        targets: selectedTargets,
        timeout_s: timeoutS,
        artifact_base: artifactBase.trim() || undefined,
      });
      setQueuedJobs([job.job_id]);
      await load(false);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "No se pudo encolar validate");
    } finally {
      setBusy(false);
    }
  }

  async function onCancelActive() {
    if (!active) return;
    try {
      setBusy(true);
      await cancelJob(active.job_id);
      await load(false);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "No se pudo cancelar el job activo");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="page-shell py-16">
        <p className="text-sm text-[var(--muted)]">Loading case detail...</p>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="page-shell py-16">
        <p className="text-sm text-[var(--bad)]">{error || "Case not found"}</p>
      </div>
    );
  }

  const timelineMap = new Map<string, FlowStatus>();
  for (const entry of detail.timeline) {
    timelineMap.set(entry.stage, entry.status as FlowStatus);
  }

  if (active?.current_stage) {
    timelineMap.set(active.current_stage, "RUNNING");
  }

  const flowStatuses = PROCESS_FLOW.map((step) => summarizeFlowStatus(step.stages, timelineMap));
  const activeStateIndex = flowStatuses.findIndex((status) => status === "RUNNING");
  const firstChange = detail.evidence.update_evidence.changes[0] || null;
  const revisionPrefix = selectedAttempt
    ? `validation_${String(selectedAttempt.attempt_number).padStart(3, "0")}_${selectedExecutedMode}`
    : null;
  const relevantValidationRuns = (scopedEvidence?.execution_before_after || []).filter((run) => {
    const revision = (run.revision_type || "").toLowerCase();
    if (!revision.startsWith("validation_")) return false;
    if (revisionPrefix) return revision === revisionPrefix.toLowerCase();
    return revision.endsWith(`_${selectedExecutedMode.toLowerCase()}`);
  });

  const taskSignals = {
    shared: { build: [] as string[], compile: [] as string[], unit: [] as string[], ui: [] as string[] },
    android: { build: [] as string[], compile: [] as string[], unit: [] as string[], ui: [] as string[] },
    ios: { build: [] as string[], compile: [] as string[], unit: [] as string[], ui: [] as string[] },
  };

  for (const run of relevantValidationRuns) {
    for (const task of run.tasks) {
      const target = inferTaskTarget(task.task_name);
      const column = inferTaskColumn(task.task_name);
      if (!target || !column) continue;
      taskSignals[target][column].push(task.status);
    }
  }

  const targetValidation = (["shared", "android", "ios"] as const).map((target) => {
    const row = validationRows.find((item) => item.target.toLowerCase() === target);
    const status = row?.status || "NOT_RUN_YET";
    const tasksForTarget = relevantValidationRuns.flatMap((run) =>
      run.tasks.filter((task) => inferTaskTarget(task.task_name) === target),
    );
    const failedTaskNames = tasksForTarget
      .filter((task) => isFailureStatus(task.status))
      .map((task) => task.task_name)
      .slice(0, 2);

    return {
      target,
      row,
      status,
      tone: targetTone(status),
      totalTasks: tasksForTarget.length,
      failedTaskNames,
      signals: {
        build: aggregateSignal(taskSignals[target].build),
        compile: aggregateSignal(taskSignals[target].compile),
        unit: aggregateSignal(taskSignals[target].unit),
        ui: aggregateSignal(taskSignals[target].ui),
      },
    };
  });

  return (
    <div className="page-shell py-12">
      <div className="mb-6">
        <Link
          href={casesListHref}
          className="technical-font focus-ring inline-flex items-center rounded-md border border-[var(--line)] bg-white px-3 py-1.5 text-[0.58rem] text-[var(--muted)] hover:text-[var(--ink)]"
        >
          ← Volver a casos
        </Link>
      </div>

      <header className="mb-10 grid gap-8 lg:grid-cols-12 lg:items-end">
        <div className="lg:col-span-8">
          <p className="technical-font mb-4 text-[0.58rem] text-[var(--muted)]">Project Identifier: {shortId(detail.case.case_id).toUpperCase()}_REPAIR</p>
          <h1 className="editorial-title text-[clamp(2.6rem,7vw,5rem)] font-extrabold text-[var(--ink)]">
            {detail.case.repository.name || "kmp-case"}
          </h1>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <span className="pill">
              {firstChange
                ? `${firstChange.dependency_group}: ${firstChange.before} → ${firstChange.after}`
                : detail.case.event.update_class}
            </span>
            <span className="pill">
              <span className={isNoErrorsCase ? "dot dot-warn" : "dot dot-ok"} />
              STATE: {caseStatusLabel(detail.case.status)}
            </span>
          </div>

          <p className="mt-4 text-sm text-[var(--muted)]">
            case_id: {detail.case.case_id} · created: {formatDate(detail.case.created_at)} · updated: {formatDate(detail.case.updated_at)}
          </p>
        </div>

        <div className="lg:col-span-4 lg:justify-self-end">
          <p className="technical-font text-[0.58rem] text-[var(--muted)]">Analysis Duration</p>
          <p className="display-font mt-1 text-5xl font-bold text-[var(--ink)]">
            {latest?.finished_at && latest.started_at
              ? `${Math.max(1, Math.round((Date.parse(latest.finished_at) - Date.parse(latest.started_at)) / 60000))}m`
              : "--"}
          </p>
        </div>
      </header>

      <section className="mb-12 grid gap-4 lg:grid-cols-12">
        <div className="rounded-xl border border-[var(--line-quiet)] bg-[var(--bg)] px-3 py-6 lg:col-span-9">
          <div className="relative">
            <div className="absolute left-8 right-8 top-4 h-px bg-[var(--line)]" />
            <div className="relative grid grid-cols-2 gap-5 md:grid-cols-3 lg:grid-cols-9">
              {PROCESS_FLOW.map((step, index) => {
                const status = flowStatuses[index] || "NOT_STARTED";
                const activeState = status === "RUNNING" && activeStateIndex === index;
                const pointClass = status === "COMPLETED"
                  ? "h-2.5 w-2.5 rounded-full bg-[var(--ok)]"
                  : status === "FAILED"
                    ? "h-2.5 w-2.5 rounded-full bg-[var(--bad)]"
                    : "h-2.5 w-2.5 rounded-full bg-[var(--ink)]";

                return (
                  <div key={step.label} className="flex flex-col items-center gap-2 bg-[var(--bg)] px-2">
                    <div className={activeState
                      ? "flex h-8 w-8 items-center justify-center rounded-full border-2 border-[var(--ink)] bg-white ring-4 ring-black/5"
                      : status === "FAILED"
                        ? "flex h-8 w-8 items-center justify-center rounded-full border border-[var(--bad)] bg-[var(--surface-low)]"
                        : status === "COMPLETED"
                          ? "flex h-8 w-8 items-center justify-center rounded-full border border-[var(--ok)]/35 bg-[var(--surface-low)]"
                          : "flex h-8 w-8 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--bg)]"}
                    >
                      <span className={pointClass} />
                    </div>
                    <span className={activeState
                      ? "technical-font min-h-[2.25rem] max-w-[6.6rem] text-center text-[0.52rem] leading-[1.35] text-[var(--ink)]"
                      : "technical-font min-h-[2.25rem] max-w-[6.6rem] text-center text-[0.52rem] leading-[1.35] text-[var(--muted)]"}
                    >
                      {step.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <aside className="surface-card p-5 lg:col-span-3">
          <p className="technical-font text-[0.56rem] text-[var(--muted)]">Viewing Mode</p>
          {executedModes.length > 0 ? (
            <select
              value={selectedExecutedMode}
              onChange={(event) => setSelectedExecutedMode(event.target.value)}
              className="focus-ring mt-3 w-full rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm"
            >
              {executedModes.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          ) : (
            <p className="mt-3 rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm text-[var(--muted)]">
              No executed repair modes yet.
            </p>
          )}
        </aside>
      </section>

      <section id="validation" className="mb-14">
        <div className="mb-6 flex items-center gap-4">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Patch Outcome</h2>
          <span className="h-px flex-1 bg-[var(--line)]" />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.45fr,1fr]">
          <div className="grid gap-4">
            {targetValidation.map(({ target, row, status, tone, totalTasks, failedTaskNames, signals }) => {

              return (
                <article key={target} className={targetToneCardClass(tone)}>
                  <p className="technical-font text-[0.68rem] font-semibold text-[var(--muted)]">
                    {target === "shared" ? "Shared (Common)" : target.toUpperCase()}
                  </p>

                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <span className={targetToneDotClass(tone)} />
                    <p className="display-font text-2xl font-bold text-[var(--ink)]">{validationLabel(status)}</p>
                    {signals.compile !== "N/A" ? <span className="pill">Compile: {signals.compile}</span> : null}
                  </div>

                  <div className="mt-2 flex flex-wrap gap-2">
                    {([
                      ["Build", signals.build],
                      ["Unit", signals.unit],
                      ["UI", signals.ui],
                    ] as const)
                      .filter(([, value]) => value !== "N/A")
                      .map(([label, value]) => (
                        <span key={label} className="pill">
                          {label}: {value}
                        </span>
                      ))}
                  </div>

                  {failedTaskNames.length > 0 ? (
                    <p className="mt-2 text-xs text-[var(--bad)]">Failed: {failedTaskNames.join(", ")}</p>
                  ) : null}

                  {totalTasks === 0 && !row?.unavailable_reason ? (
                    <p className="mt-2 text-xs text-[var(--muted)]">No validation evidence yet.</p>
                  ) : null}

                  {row?.unavailable_reason ? (
                    <p className="mt-2 text-xs text-[var(--muted)]">{row.unavailable_reason}</p>
                  ) : null}
                </article>
              );
            })}
          </div>

          <article className="surface-card p-5">
            <p className="technical-font text-[0.56rem] text-[var(--muted)]">Execution Metrics</p>

            {selectedMetric ? (
              <div className="mt-4 divide-y divide-[var(--line-quiet)] rounded-lg border border-[var(--line-quiet)] bg-[var(--surface-low)] px-4">
                {[
                  ["BSR", selectedMetric.bsr],
                  ["CTSR", selectedMetric.ctsr],
                  ["FFSR", selectedMetric.ffsr],
                  ["EFR", selectedMetric.efr],
                  ["Hit@k", selectedMetric.hit_at_1],
                  ["source_set_accuracy", selectedMetric.source_set_accuracy],
                ].map(([name, value]) => (
                  <div key={name} className="flex items-center justify-between gap-3 py-3">
                    <p className="technical-font text-[0.62rem] font-semibold text-[var(--muted)]">{name}</p>
                    <p className="display-font text-lg font-semibold text-[var(--ink)]">{metric(value as number | null)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-[var(--muted)]">
                No metric rows yet for mode {selectedExecutedMode}.
              </p>
            )}
          </article>
        </div>

        <article id="explain" className="surface-card mt-6 p-6">
          <p className="technical-font text-[0.56rem] text-[var(--muted)]">Explanation</p>
          {explanationMarkdown ? (
            <div className="mt-4 max-h-[430px] overflow-auto pr-2">
              {renderMarkdownEditorial(explanationMarkdown)}
            </div>
          ) : (
            <p className="mt-4 text-sm text-[var(--muted)]">No markdown explanation available yet.</p>
          )}
        </article>

        <article id="attempts" className="surface-card mt-6 p-6">
          <div className="grid gap-4 lg:grid-cols-[0.9fr,2.1fr]">
            <div className="rounded-lg border border-[var(--line)] bg-[var(--surface-low)] p-4">
              <p className="technical-font text-[0.56rem] text-[var(--muted)]">Changed files</p>
              <div className="mt-3 space-y-1">
                {changedFiles.length > 0 ? (
                  changedFiles.map((filePath) => {
                    const activeFile = filePath === selectedDiffFile;
                    return (
                      <button
                        key={filePath}
                        type="button"
                        onClick={() => setSelectedDiffFile(filePath)}
                        className={activeFile
                          ? "focus-ring block w-full rounded border border-[var(--line)] bg-white px-3 py-2 text-left text-sm text-[var(--ink)]"
                          : "focus-ring block w-full rounded border border-transparent px-3 py-2 text-left text-sm text-[var(--muted)] hover:border-[var(--line)] hover:bg-white/70"}
                        title={filePath}
                      >
                        <span className="block truncate">{filePath}</span>
                      </button>
                    );
                  })
                ) : (
                  <p className="text-sm text-[var(--muted)]">Not available</p>
                )}
              </div>
            </div>

            <div className="min-w-0">
              {diffLoading ? <p className="mt-3 text-sm text-[var(--muted)]">Loading diff artifact...</p> : null}
              <div className={diffLoading ? "mt-3" : ""}>
                <UnifiedDiffViewer rawDiff={activeDiffContent} hideFileNavigation hideFileHeader />
              </div>
            </div>
          </div>
        </article>
      </section>

      <section id="artifacts" className="mb-12">
        <div className="mb-6 flex items-center gap-4">
          <h2 className="display-font text-3xl font-bold text-[var(--ink)]">Artifacts</h2>
          <span className="h-px flex-1 bg-[var(--line)]" />
        </div>

        <article className="surface-card p-6">
          <div className="grid gap-4 lg:grid-cols-[0.9fr,2.1fr]">
            <div className="rounded-lg border border-[var(--line)] bg-[var(--surface-low)] p-4">
              <p className="technical-font text-[0.56rem] text-[var(--muted)]">Available artifacts</p>
              <div className="mt-3 space-y-1">
                {artifactEntries.length > 0 ? (
                  artifactEntries.map((entry) => {
                    const activeArtifact = selectedArtifact?.path === entry.path;
                    return (
                      <button
                        key={entry.path}
                        type="button"
                        onClick={() => setSelectedArtifactPath(entry.path)}
                        className={activeArtifact
                          ? "focus-ring block w-full rounded border border-[var(--line)] bg-white px-3 py-2 text-left text-sm text-[var(--ink)]"
                          : "focus-ring block w-full rounded border border-transparent px-3 py-2 text-left text-sm text-[var(--muted)] hover:border-[var(--line)] hover:bg-white/70"}
                        title={entry.path}
                      >
                        <span className="block truncate">{entry.label}</span>
                      </button>
                    );
                  })
                ) : (
                  <p className="text-sm text-[var(--muted)]">No artifacts available.</p>
                )}
              </div>
            </div>

            <div className="min-w-0">
              {artifactLoading ? <p className="text-sm text-[var(--muted)]">Loading artifact...</p> : null}

              {!artifactLoading && artifactError ? (
                <p className="rounded-lg border border-[var(--line)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--bad)]">
                  {artifactError}
                </p>
              ) : null}

              {!artifactLoading && !artifactError && selectedArtifact && selectedArtifactContent && selectedArtifact.kind === "diff" ? (
                <UnifiedDiffViewer rawDiff={selectedArtifactContent} hideFileNavigation hideFileHeader />
              ) : null}

              {!artifactLoading && !artifactError && selectedArtifact && selectedArtifactContent && selectedArtifact.kind === "markdown" ? (
                <div className="max-h-[420px] overflow-auto rounded-lg border border-[var(--line)] bg-[var(--surface-low)] px-4 py-3">
                  {renderMarkdownEditorial(selectedArtifactContent)}
                </div>
              ) : null}

              {!artifactLoading && !artifactError && selectedArtifact && selectedArtifactContent && selectedArtifact.kind === "text" ? (
                <pre className="max-h-[420px] overflow-auto rounded-lg border border-[var(--line)] bg-[var(--surface-low)] p-4 text-xs text-[var(--ink)] whitespace-pre-wrap">
                  {selectedArtifactContent}
                </pre>
              ) : null}

              {!artifactLoading && !artifactError && selectedArtifact && !selectedArtifactContent ? (
                <p className="rounded-lg border border-[var(--line)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--muted)]">
                  Empty artifact.
                </p>
              ) : null}

              {!artifactLoading && !artifactError && !selectedArtifact ? (
                <p className="rounded-lg border border-[var(--line)] bg-[var(--surface-low)] px-4 py-3 text-sm text-[var(--muted)]">
                  Select an artifact to review its content.
                </p>
              ) : null}
            </div>
          </div>
        </article>
      </section>
    </div>
  );
}
