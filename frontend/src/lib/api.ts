import type {
  BriefingData,
  ChartConfig,
  ExpertReview,
  InsightReport,
  SessionSummary,
  SessionResultsSnapshot,
} from "@/types";

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

export function getAuthHeaders(headers: Record<string, string> = {}): Record<string, string> {
  if (typeof window !== "undefined") {
    const saved = localStorage.getItem("analystai_user");
    if (saved) {
      try {
        const user = JSON.parse(saved);
        if (user?.token) {
          headers["Authorization"] = `Bearer ${user.token}`;
        }
      } catch (e) {
        console.error("Error reading auth token for headers", e);
      }
    }
  }
  return headers;
}

export async function fetchHealth() {
  const res = await fetch(`${API_URL}/health`);
  return res.json();
}

export async function getDashboard(userId = "default"): Promise<ChartConfig[]> {
  const res = await fetch(`${API_URL}/dashboard?user_id=${userId}`, {
    headers: getAuthHeaders(),
  });
  const data = await res.json();
  return (data.charts ?? data) as ChartConfig[];
}

export async function pinChart(sessionId: string, chart: object, userId = "default") {
  await fetch(`${API_URL}/dashboard/pin`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ session_id: sessionId, user_id: userId, chart }),
  });
}

export async function unpinChart(chartId: string, userId = "default") {
  await fetch(`${API_URL}/dashboard/${chartId}?user_id=${userId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
}

export async function getLatestAnalysis(): Promise<LatestAnalysis | null> {
  const res = await fetch(`${API_URL}/dashboard/latest`, {
    headers: getAuthHeaders(),
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
    headers: getAuthHeaders(),
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
  const res = await fetch(`${API_URL}/api/sessions`, { headers: getAuthHeaders() });
  if (!res.ok) return [];
  return (await res.json()) as SessionSummary[];
}

export async function getSessionResults(
  sessionId: string
): Promise<SessionResultsSnapshot | null> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}/results`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) return null;
  return (await res.json()) as SessionResultsSnapshot;
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  return res.ok;
}

export { API_URL };
