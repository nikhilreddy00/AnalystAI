import type {
  BriefingData,
  ChartConfig,
  DashboardDataPayload,
  ExpertReview,
  Dataset,
  DatasetDetail,
  InsightReport,
  SessionSummary,
  SessionResultsSnapshot,
  ThreadView,
  UploadResponse,
} from "@/types";

import { getAccessToken } from "@/lib/supabase";

export interface LatestAnalysis {
  session_id: string;
  updated_at: string;
  raw_query: string | null;
  charts: ChartConfig[];
  insight: InsightReport | null;
  briefing: BriefingData | null;
  expert_review: ExpertReview | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * The single place an access token is read. Async because Supabase refreshes
 * expired tokens on demand — never cache the result across requests.
 */
export async function getAuthHeaders(
  headers: Record<string, string> = {},
): Promise<Record<string, string>> {
  const token = await getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

export async function fetchHealth() {
  const res = await fetch(`${API_URL}/health`);
  return res.json();
}

export async function getDashboard(userId = "default"): Promise<ChartConfig[]> {
  const res = await fetch(`${API_URL}/dashboard?user_id=${userId}`, {
    headers: await getAuthHeaders(),
  });
  const data = await res.json();
  return (data.charts ?? data) as ChartConfig[];
}

export async function pinChart(sessionId: string, chart: object, userId = "default") {
  const res = await fetch(`${API_URL}/dashboard/pin`, {
    method: "POST",
    headers: await getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ session_id: sessionId, user_id: userId, chart }),
  });
  if (!res.ok) throw new Error(`Pin failed: ${res.status}`);
}

/** The user switched a chart's type: persist it on the stored chart and log the override.
 *  Returns the updated chart (with any lint note), or null when the server didn't keep it. */
export async function sendChartFeedback(
  sessionId: string,
  chartId: string,
  chartType: ChartConfig["chart_type"],
): Promise<ChartConfig | null> {
  const res = await fetch(`${API_URL}/dashboard/chart-feedback`, {
    method: "POST",
    headers: await getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ session_id: sessionId, chart_id: chartId, chart_type: chartType }),
  });
  if (!res.ok) return null;
  return ((await res.json()) as { chart: ChartConfig }).chart;
}

export async function unpinChart(chartId: string, userId = "default") {
  await fetch(`${API_URL}/dashboard/${chartId}?user_id=${userId}`, {
    method: "DELETE",
    headers: await getAuthHeaders(),
  });
}

export async function getLatestAnalysis(): Promise<LatestAnalysis | null> {
  const res = await fetch(`${API_URL}/dashboard/latest`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return (data?.latest ?? null) as LatestAnalysis | null;
}

/**
 * Phase 5: fetch the .pptx as a blob (auth-aware), then trigger a browser download.
 * We can't use a plain anchor link because that wouldn't carry the Authorization header.
 */
export async function downloadLatestReportPptx(): Promise<void> {
  const res = await fetch(`${API_URL}/dashboard/latest/report.pptx`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Report download failed: ${res.status} ${res.statusText}`);
  }
  // Read filename from Content-Disposition if present, fall back to a sane default
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] ?? "analystai_report.pptx";

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch(`${API_URL}/api/sessions`, { headers: await getAuthHeaders() });
  if (!res.ok) return [];
  return (await res.json()) as SessionSummary[];
}

export async function getSessionResults(
  sessionId: string
): Promise<SessionResultsSnapshot | null> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}/results`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) return null;
  return (await res.json()) as SessionResultsSnapshot;
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: await getAuthHeaders(),
  });
  return res.ok || res.status === 404;   // deleted in another tab: gone either way
}

// ── Phase 5.6b workspace ───────────────────────────────────────────────────────

export type ThreadPatch = Partial<Pick<SessionSummary, "title" | "pinned" | "archived">>;

export async function patchSession(sessionId: string, patch: ThreadPatch): Promise<SessionSummary | null> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: await getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  return res.ok ? ((await res.json()) as SessionSummary) : null;
}

/** Every question in a thread with the report it delivered, exactly as delivered. */
export async function getThread(sessionId: string): Promise<ThreadView | null> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}/thread`, { headers: await getAuthHeaders() });
  return res.ok ? ((await res.json()) as ThreadView) : null;
}

async function download(path: string, fallback: string): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, { headers: await getAuthHeaders() });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const name = (res.headers.get("Content-Disposition") ?? "").match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** One report as a PowerPoint, built from what it delivered. */
export const downloadReportPptx = (runId: string) => download(`/runs/${runId}/report.pptx`, "analystai_report.pptx");
export const downloadThreadMarkdown = (sessionId: string) =>
  download(`/api/sessions/${sessionId}/export.md`, "analysis.md");

export async function listDatasets(): Promise<Dataset[]> {
  const res = await fetch(`${API_URL}/api/datasets`, { headers: await getAuthHeaders() });
  return res.ok ? ((await res.json()) as Dataset[]) : [];
}

export async function getDataset(id: string): Promise<DatasetDetail | null> {
  const res = await fetch(`${API_URL}/api/datasets/${id}`, { headers: await getAuthHeaders() });
  return res.ok ? ((await res.json()) as DatasetDetail) : null;
}

export async function renameDataset(id: string, name: string): Promise<Dataset | null> {
  const res = await fetch(`${API_URL}/api/datasets/${id}`, {
    method: "PATCH",
    headers: await getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name }),
  });
  return res.ok ? ((await res.json()) as Dataset) : null;
}

export async function deleteDataset(id: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/datasets/${id}`, { method: "DELETE", headers: await getAuthHeaders() });
  return res.ok || res.status === 404;
}

/** "Keep as a separate dataset": undo an automatic new version. */
export async function splitVersion(versionId: string): Promise<Dataset | null> {
  const res = await fetch(`${API_URL}/api/datasets/versions/${versionId}/split`, {
    method: "POST",
    headers: await getAuthHeaders(),
  });
  return res.ok ? ((await res.json()) as Dataset) : null;
}

/** Upload a file; with `datasetId` it becomes a new version of that dataset. */
export async function uploadFile(file: File, datasetId?: string): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (datasetId) form.append("dataset_id", datasetId);
  const res = await fetch(`${API_URL}/connectors/upload`, { method: "POST", headers: await getAuthHeaders(), body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail || "Upload failed");
  return data as UploadResponse;
}

export type DashboardDataResult =
  | { status: "ok"; payload: DashboardDataPayload }
  | { status: "empty" }
  | { status: "too_large"; detail: string }
  | { status: "error"; detail: string };

export async function getLatestDashboardData(): Promise<DashboardDataResult> {
  const res = await fetch(`${API_URL}/dashboard/latest/data`, {
    headers: await getAuthHeaders(),
  });
  if (res.status === 404) return { status: "empty" };
  if (res.status === 422) {
    const body = await res.json().catch(() => ({}));
    return { status: "too_large", detail: body.detail ?? "Dataset too large." };
  }
  if (!res.ok) return { status: "error", detail: `${res.status} ${res.statusText}` };
  return { status: "ok", payload: (await res.json()) as DashboardDataPayload };
}

export { API_URL };
