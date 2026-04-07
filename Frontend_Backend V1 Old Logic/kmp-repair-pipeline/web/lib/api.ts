import { CaseDetail, CaseSummary, Job, ReportsComparisonRow } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Error ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getCases(filters?: {
  status?: string;
  update_class?: string;
  repo?: string;
  repair_mode?: string;
}): Promise<CaseSummary[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.update_class) params.set("update_class", filters.update_class);
  if (filters?.repo) params.set("repo", filters.repo);
  if (filters?.repair_mode) params.set("repair_mode", filters.repair_mode);

  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await request<{ items: CaseSummary[] }>(`/api/cases${query}`);
  return response.items;
}

export async function createCase(prUrl: string): Promise<CaseDetail> {
  return request<CaseDetail>("/api/cases", {
    method: "POST",
    body: JSON.stringify({ pr_url: prUrl }),
  });
}

export async function getCase(caseId: string): Promise<CaseDetail> {
  return request<CaseDetail>(`/api/cases/${caseId}`);
}

export async function getCaseHistory(caseId: string) {
  return request<{ transitions: unknown[]; jobs: Job[] }>(`/api/cases/${caseId}/history`);
}

export async function runStage(caseId: string, stage: string, params: Record<string, unknown> = {}): Promise<Job> {
  const response = await request<{ job: Job }>(`/api/cases/${caseId}/jobs/stage`, {
    method: "POST",
    body: JSON.stringify({ stage, params }),
  });
  return response.job;
}

export async function runPipeline(
  caseId: string,
  start_from_stage: string,
  params_by_stage: Record<string, Record<string, unknown>> = {},
): Promise<Job> {
  const response = await request<{ job: Job }>(`/api/cases/${caseId}/jobs/pipeline`, {
    method: "POST",
    body: JSON.stringify({ start_from_stage, params_by_stage }),
  });
  return response.job;
}

export async function cancelJob(jobId: string): Promise<Job> {
  const response = await request<{ job: Job }>(`/api/jobs/${jobId}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return response.job;
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await request<{ job: Job }>(`/api/jobs/${jobId}`);
  return response.job;
}

export async function getJobLogs(jobId: string): Promise<string[]> {
  const response = await request<{ lines: string[] }>(`/api/jobs/${jobId}/logs`);
  return response.lines;
}

export async function getArtifactContent(caseId: string, artifactPath: string): Promise<string> {
  const params = new URLSearchParams({ path: artifactPath });
  const response = await request<{ content: string }>(`/api/cases/${caseId}/artifact-content?${params.toString()}`);
  return response.content;
}

export async function getReportsComparison(modes?: string[]): Promise<ReportsComparisonRow[]> {
  const params = new URLSearchParams();
  if (modes?.length) {
    params.set("modes", modes.join(","));
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await request<{ comparison: ReportsComparisonRow[] }>(`/api/reports/compare${query}`);
  return response.comparison;
}

export function activeJobsSseUrl(): string {
  return `${API_BASE}/api/stream/active`;
}

export function jobSseUrl(jobId: string): string {
  return `${API_BASE}/api/jobs/${jobId}/stream`;
}
