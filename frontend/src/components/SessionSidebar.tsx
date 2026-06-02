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
      return "bg-green-100 text-green-800";
    case "failed":
      return "bg-red-100 text-red-800";
    default:
      return "bg-amber-100 text-amber-800";
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
    <div className="flex flex-col gap-2 p-3 border-t border-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">History</h3>
        <button
          type="button"
          onClick={onNewAnalysis}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
        >
          <PlusCircle size={14} />
          New
        </button>
      </div>

      {loading && sessions.length === 0 && (
        <p className="text-xs text-gray-400">Loading…</p>
      )}
      {!loading && sessions.length === 0 && (
        <p className="text-xs text-gray-400">No past analyses yet.</p>
      )}

      <ul className="flex flex-col gap-1">
        {sessions.map((s) => {
          const isActive = s.session_id === activeSessionId;
          return (
            <li
              key={s.session_id}
              className={`group rounded-md px-2 py-1.5 cursor-pointer ${
                isActive ? "bg-blue-50 border border-blue-200" : "hover:bg-gray-50"
              }`}
              onClick={() => onLoadSession(s.session_id)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-gray-800 truncate">
                  {s.dataset_name ?? "Untitled dataset"}
                </span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.session_id);
                  }}
                  className="text-gray-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
                  aria-label="Delete session"
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
                <span>{relativeTime(s.updated_at)}</span>
                <span className={`px-1.5 py-0.5 rounded ${statusPillClass(s.status)}`}>
                  {s.status}
                </span>
              </div>
              {s.row_count !== null && (
                <p className="text-xs text-gray-400">{s.row_count.toLocaleString()} rows</p>
              )}
              {s.original_query && (
                <p className="text-xs text-gray-500 truncate" title={s.original_query}>
                  {s.original_query}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
