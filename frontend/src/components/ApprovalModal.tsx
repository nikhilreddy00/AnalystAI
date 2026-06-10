"use client";

import { useState } from "react";
import type { ApprovalRequiredEvent } from "@/types";

interface Props {
  event: ApprovalRequiredEvent;
  onSubmit: (response: string) => void;
  onDismiss: () => void;
  onLoadPrevious?: () => void;
  onRunFresh?: () => void;
  onCancel?: () => void;
}

const HIGH_NULL_OPTIONS = [
  { label: "Continue with imputation", sub: "Fill missing values using statistical methods (recommended)", value: "Continue with imputation", icon: "✓", accent: "emerald" },
  { label: "Drop high-null columns", sub: "Remove columns with too many missing values", value: "Drop those columns", icon: "✕", accent: "amber" },
  { label: "Cancel analysis", sub: "Stop and return without analyzing", value: "Cancel", icon: "✗", accent: "red" },
];

const ACCENT = {
  emerald: "border-emerald-500/30 hover:border-emerald-500/60 hover:bg-emerald-500/8 text-emerald-400",
  amber: "border-amber-500/30 hover:border-amber-500/60 hover:bg-amber-500/8 text-amber-400",
  red: "border-red-500/30 hover:border-red-500/60 hover:bg-red-500/8 text-red-400",
};

export default function ApprovalModal({ event, onSubmit, onDismiss, onLoadPrevious, onRunFresh, onCancel }: Props) {
  const [freeText, setFreeText] = useState("");
  const isHighNull = event.context === "high_null";
  const isRerunOverview = event.context === "rerun_overview";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md">
      <div className="bg-zinc-900 border border-zinc-700/60 rounded-2xl p-6 max-w-md w-full mx-4 space-y-5 shadow-2xl shadow-black/50">
        {/* Header */}
        <div className="flex items-start gap-3.5">
          <div className="w-9 h-9 rounded-xl bg-amber-500/15 border border-amber-500/25 flex items-center justify-center text-lg flex-shrink-0">
            {isHighNull ? "⚠" : isRerunOverview ? "🔄" : "❓"}
          </div>
          <div>
            <h3 className="text-white font-semibold text-sm">
              {event.context === "clarification" ? "Clarification needed" : isRerunOverview ? "Previous analysis found" : "Data quality warning"}
            </h3>
            {event.high_null_columns && event.high_null_columns.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {event.high_null_columns.map((col) => (
                  <span key={col} className="text-xs bg-amber-500/15 text-amber-300 px-2 py-0.5 rounded-lg border border-amber-500/20 font-mono">
                    {col}
                  </span>
                ))}
              </div>
            )}
            <p className="text-zinc-400 text-xs mt-2 leading-relaxed whitespace-pre-line">
              {isHighNull
                ? "These columns have more than 40% missing values. How should I proceed?"
                : event.question}
            </p>

          </div>
        </div>

        {/* Options */}
        {isRerunOverview ? (
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2 text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onLoadPrevious}
              className="px-5 py-2 text-sm border border-zinc-600 text-zinc-300 hover:border-zinc-400 hover:text-white rounded-xl transition-all"
            >
              Load previous
            </button>
            <button
              type="button"
              onClick={onRunFresh}
              className="px-5 py-2 text-sm bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white rounded-xl font-medium transition-all shadow-lg shadow-indigo-500/20"
            >
              Run fresh
            </button>
          </div>
        ) : isHighNull ? (
          <div className="space-y-2">
            {HIGH_NULL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => onSubmit(opt.value)}
                className={`w-full text-left flex items-start gap-3 bg-zinc-800/40 border rounded-xl px-4 py-3 transition-all duration-200 group ${ACCENT[opt.accent as keyof typeof ACCENT]}`}
              >
                <span className="text-base mt-0.5 flex-shrink-0">{opt.icon}</span>
                <div>
                  <p className="text-zinc-200 text-sm font-medium group-hover:text-white transition-colors">{opt.label}</p>
                  <p className="text-zinc-500 text-xs mt-0.5">{opt.sub}</p>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            <textarea
              rows={2}
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="Type your answer…"
              className="w-full rounded-xl bg-zinc-800/60 border border-zinc-700/50 text-zinc-200 text-sm p-3 resize-none placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40"
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={onDismiss}
                className="px-4 py-2 text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Dismiss
              </button>
              <button
                onClick={() => freeText.trim() && onSubmit(freeText.trim())}
                disabled={!freeText.trim()}
                className="px-5 py-2 text-sm bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 disabled:opacity-40 text-white rounded-xl font-medium transition-all shadow-lg shadow-indigo-500/20"
              >
                Send →
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
