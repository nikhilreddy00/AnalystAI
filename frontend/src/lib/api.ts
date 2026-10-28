import type {
  BriefingData,
  ChartConfig,
  DashboardDataPayload,
  ExpertReview,
  InsightReport,
  SessionSummary,
  SessionResultsSnapshot,
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
  return res.ok;
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
