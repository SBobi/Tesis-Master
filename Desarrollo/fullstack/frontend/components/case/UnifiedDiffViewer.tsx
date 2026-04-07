"use client";

import { useEffect, useMemo, useState } from "react";

type DiffRow = {
  oldNumber: number | null;
  newNumber: number | null;
  text: string;
  kind: "context" | "add" | "remove";
};

type DiffFile = {
  key: string;
  oldPath: string | null;
  newPath: string | null;
  added: number;
  removed: number;
  rows: DiffRow[];
};

function normalizePath(raw: string): string | null {
  const value = raw.trim();
  if (value === "/dev/null") return null;
  return value.replace(/^a\//, "").replace(/^b\//, "");
}

function parseUnifiedDiff(rawDiff: string): DiffFile[] {
  const lines = rawDiff.split(/\r?\n/);
  const files: DiffFile[] = [];

  let fileIndex = 0;
  let currentFile: DiffFile | null = null;
  let oldLine: number | null = null;
  let newLine: number | null = null;

  function startFile(oldPath: string | null = null, newPath: string | null = null) {
    currentFile = {
      key: `file-${fileIndex}`,
      oldPath,
      newPath,
      added: 0,
      removed: 0,
      rows: [],
    };
    fileIndex += 1;
    files.push(currentFile);
    oldLine = null;
    newLine = null;
  }

  function ensureFile(): DiffFile {
    if (!currentFile) {
      startFile();
    }
    return currentFile as DiffFile;
  }

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      const parts = line.split(" ");
      const oldPath = parts[2] ? normalizePath(parts[2]) : null;
      const newPath = parts[3] ? normalizePath(parts[3]) : null;

      startFile(oldPath, newPath);
      continue;
    }

    if (line.startsWith("--- ")) {
      const activeFile = ensureFile();
      if (activeFile.rows.length > 0) {
        startFile();
      }
      ensureFile().oldPath = normalizePath(line.slice(4));
      continue;
    }

    if (line.startsWith("+++ ")) {
      ensureFile().newPath = normalizePath(line.slice(4));
      continue;
    }

    const hunkMatch = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
    if (hunkMatch) {
      ensureFile();
      oldLine = Number(hunkMatch[1]);
      newLine = Number(hunkMatch[2]);
      continue;
    }

    if (line.startsWith("+") && !line.startsWith("+++")) {
      ensureFile().added += 1;
      ensureFile().rows.push({
        oldNumber: null,
        newNumber: newLine,
        text: line.slice(1),
        kind: "add",
      });
      if (newLine !== null) newLine += 1;
      continue;
    }

    if (line.startsWith("-") && !line.startsWith("---")) {
      ensureFile().removed += 1;
      ensureFile().rows.push({
        oldNumber: oldLine,
        newNumber: null,
        text: line.slice(1),
        kind: "remove",
      });
      if (oldLine !== null) oldLine += 1;
      continue;
    }

    if (line.startsWith("\\ No newline at end of file")) {
      continue;
    }

    if (!line.startsWith(" ") && oldLine === null && newLine === null) {
      continue;
    }

    const contextText = line.startsWith(" ") ? line.slice(1) : line;
    ensureFile().rows.push({
      oldNumber: oldLine,
      newNumber: newLine,
      text: contextText,
      kind: "context",
    });
    if (oldLine !== null) oldLine += 1;
    if (newLine !== null) newLine += 1;
  }

  return files;
}

function fileDisplayName(file: DiffFile): string {
  if (file.newPath && file.oldPath && file.newPath !== file.oldPath) {
    return `${file.oldPath} -> ${file.newPath}`;
  }
  return file.newPath || file.oldPath || "file";
}

function rowTone(kind: DiffRow["kind"]): string {
  if (kind === "add") return "bg-[var(--surface-low)]";
  if (kind === "remove") return "bg-[var(--surface-ink)]";
  return "bg-[var(--surface)]";
}

function markerTone(kind: DiffRow["kind"]): string {
  if (kind === "add") return "text-[var(--ink)]";
  if (kind === "remove") return "text-[var(--muted)]";
  return "text-[var(--muted)]";
}

function lineMarker(kind: DiffRow["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "remove") return "-";
  return " ";
}

type UnifiedDiffViewerProps = {
  rawDiff: string | null | undefined;
  hideFileNavigation?: boolean;
  hideFileHeader?: boolean;
};

export function UnifiedDiffViewer({
  rawDiff,
  hideFileNavigation = false,
  hideFileHeader = false,
}: UnifiedDiffViewerProps) {
  if (!rawDiff || !rawDiff.trim()) {
    return (
      <p className="rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--muted)]">
        No code diff is available for this case.
      </p>
    );
  }

  const files = useMemo(() => parseUnifiedDiff(rawDiff), [rawDiff]);
  const [activeFileIndex, setActiveFileIndex] = useState(0);

  useEffect(() => {
    setActiveFileIndex(0);
  }, [rawDiff]);

  useEffect(() => {
    if (files.length === 0) return;
    if (activeFileIndex >= files.length) {
      setActiveFileIndex(files.length - 1);
    }
  }, [activeFileIndex, files.length]);

  if (files.length === 0) {
    return (
      <pre className="max-h-[420px] overflow-auto rounded-xl border border-[var(--line)] bg-[var(--surface-low)] p-3 text-xs text-[var(--ink)]">
        {rawDiff}
      </pre>
    );
  }

  const activeFile = files[activeFileIndex] || files[0];
  const canGoPrevious = activeFileIndex > 0;
  const canGoNext = activeFileIndex < files.length - 1;

  return (
    <div className="space-y-3">
      {!hideFileNavigation && files.length > 1 ? (
        <div className="rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted">
              File {activeFileIndex + 1} of {files.length}
            </p>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setActiveFileIndex((current) => Math.max(0, current - 1))}
                disabled={!canGoPrevious}
                className={`ring-focus rounded-full px-3 py-1 text-xs font-semibold transition ${
                  canGoPrevious
                    ? "border border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--surface-low)]"
                    : "cursor-not-allowed border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)]"
                }`}
              >
                Previous file
              </button>

              <button
                type="button"
                onClick={() => setActiveFileIndex((current) => Math.min(files.length - 1, current + 1))}
                disabled={!canGoNext}
                className={`ring-focus rounded-full px-3 py-1 text-xs font-semibold transition ${
                  canGoNext
                    ? "border border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--surface-low)]"
                    : "cursor-not-allowed border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)]"
                }`}
              >
                Next file
              </button>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {files.map((file, index) => {
              const isActive = index === activeFileIndex;
              return (
                <button
                  key={`${file.key}-tab`}
                  type="button"
                  onClick={() => setActiveFileIndex(index)}
                  className={`ring-focus max-w-[280px] rounded-full border px-3 py-1 text-xs font-semibold transition ${
                    isActive
                      ? "border-[var(--line)] bg-[var(--surface-low)] text-[var(--ink)]"
                      : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-low)]"
                  }`}
                  title={fileDisplayName(file)}
                >
                  <span className="block truncate">{fileDisplayName(file)}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      <section className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)]">
        {!hideFileHeader ? (
          <div className="border-b border-[var(--line)] bg-[var(--surface-low)] px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-ink">{fileDisplayName(activeFile)}</p>
              <div className="flex items-center gap-2 text-xs">
                <span className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-2 py-1 text-[var(--muted)]">
                  -{activeFile.removed}
                </span>
                <span className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-2 py-1 text-[var(--muted)]">
                  +{activeFile.added}
                </span>
              </div>
            </div>
          </div>
        ) : null}

        <div className="max-h-[360px] overflow-auto">
          <table className="w-full border-collapse text-[12px]">
            <tbody>
              {activeFile.rows.map((row, rowIndex) => (
                <tr key={`${activeFile.key}-row-${rowIndex}`} className="align-top">
                  <td
                    className={`w-10 border-r border-[var(--line-quiet)] px-2 py-1 text-right font-mono text-[11px] text-[var(--muted)] ${rowTone(
                      row.kind,
                    )}`}
                  >
                    {row.oldNumber ?? ""}
                  </td>
                  <td
                    className={`w-10 border-r border-[var(--line-quiet)] px-2 py-1 text-right font-mono text-[11px] text-[var(--muted)] ${rowTone(
                      row.kind,
                    )}`}
                  >
                    {row.newNumber ?? ""}
                  </td>
                  <td
                    className={`px-2 py-1 font-mono whitespace-pre-wrap break-words ${rowTone(row.kind)}`}
                  >
                    <span className={`mr-2 inline-block w-3 font-semibold ${markerTone(row.kind)}`}>
                      {lineMarker(row.kind)}
                    </span>
                    {row.text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
