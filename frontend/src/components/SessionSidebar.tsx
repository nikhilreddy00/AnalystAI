"use client";

import { Trash2, PlusCircle } from "lucide-react";
import type { SessionSummary } from "@/types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  onLoadSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onNewAnalysis: () => void;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function statusPillClass(status: SessionSummary["status"]): string {
  switch (status) {
    case "completed":
      return "text-success";
    case "failed":
      return "text-destructive";
    default:
      return "text-warning";
  }
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  loading,
  onLoadSession,
  onDeleteSession,
  onNewAnalysis,
}: Props) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between px-1">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">History</h3>
        <button
          type="button"
          onClick={onNewAnalysis}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-accent"
        >
          <PlusCircle size={14} />
          New analysis
        </button>
      </div>

      {loading && sessions.length === 0 && (
        <p className="px-1 text-xs text-muted-foreground">Loading…</p>
      )}
      {!loading && sessions.length === 0 && (
        <p className="px-1 text-xs text-muted-foreground">No past analyses yet.</p>
      )}

      <ul className="flex flex-col gap-0.5">
        {sessions.map((s) => {
          const isActive = s.session_id === activeSessionId;
          return (
            <li key={s.session_id} className="group relative">
              <button
                type="button"
                onClick={() => onLoadSession(s.session_id)}
                aria-current={isActive ? "true" : undefined}
                className={`w-full rounded-md px-2.5 py-2 pr-8 text-left ${
                  isActive ? "bg-card shadow-[0_0_0_1px_var(--border)]" : "hover:bg-accent"
                }`}
              >
                <span className="block truncate text-sm font-medium text-foreground">
                  {s.dataset_name ?? "Untitled dataset"}
                </span>
                {s.original_query && (
                  <span className="block truncate text-xs text-muted-foreground" title={s.original_query}>
                    {s.original_query}
                  </span>
                )}
                <span className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span>{relativeTime(s.updated_at)}</span>
                  {s.row_count !== null && <span>{s.row_count.toLocaleString()} rows</span>}
                  <span className={statusPillClass(s.status)}>{s.status}</span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => onDeleteSession(s.session_id)}
                className="absolute right-1.5 top-2 rounded p-1 text-muted-foreground opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                aria-label={`Delete ${s.dataset_name ?? "session"}`}
              >
                <Trash2 size={14} />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
