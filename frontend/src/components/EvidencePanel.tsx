"use client";

import { useRef, useState } from "react";
import type { Evidence } from "@/types";

// Phase 3: every analysis finding carries its evidence (ADR-0001). The list shows each claim
// with its confidence; a row opens a drawer with the rows behind it, the assumptions, the
// result the code produced and the code itself. Native <dialog>: Esc, focus trap, backdrop.

const CONFIDENCE = {
  high: "bg-emerald-50 text-emerald-700 border-emerald-200",
  medium: "bg-amber-50 text-amber-800 border-amber-200",
  low: "bg-zinc-100 text-zinc-600 border-zinc-200",
};

const KIND = {
  correlation: "Correlation",
  trend: "Trend",
  segment: "Breakdown",
  anomaly: "Anomalies",
  test: "Test",
};

export default function EvidencePanel({ items }: { items: Evidence[] }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState<Evidence | null>(null);

  const show = (e: Evidence) => {
    setOpen(e);
    dialog.current?.showModal();
  };

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-5 max-w-2xl shadow-sm">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-zinc-900">Evidence</h3>
        <span className="text-xs text-zinc-500">
          {items.length} finding{items.length === 1 ? "" : "s"} · click one to see how it was computed
        </span>
      </div>
      <ul className="divide-y divide-zinc-100">
        {items.map((e) => (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => show(e)}
              className="w-full flex items-center gap-3 py-2 text-left hover:bg-zinc-50 focus-visible:outline-2 focus-visible:outline-indigo-500 rounded-md px-2 -mx-2"
            >
              <span className="text-[11px] uppercase tracking-wide text-zinc-400 w-20 shrink-0">
                {KIND[e.kind]}
              </span>
              <span className="text-sm text-zinc-800 flex-1 min-w-0 truncate">{e.claim}</span>
              <span className={`text-[11px] px-2 py-0.5 rounded-full border shrink-0 ${CONFIDENCE[e.confidence]}`}>
                {e.confidence}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <dialog
        ref={dialog}
        aria-labelledby="evidence-title"
        onClose={() => setOpen(null)}
        onClick={(ev) => ev.target === dialog.current && dialog.current?.close()}
        className="ml-auto mr-0 my-0 h-dvh max-h-dvh w-full max-w-xl bg-white p-0 shadow-2xl backdrop:bg-zinc-900/30"
      >
        {open && (
          <div className="flex h-full flex-col">
            <div className="flex items-start justify-between gap-4 border-b border-zinc-200 p-5">
              <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wide text-zinc-400">{KIND[open.kind]}</p>
                <h2 id="evidence-title" className="text-base font-semibold text-zinc-900 break-words">
                  {open.claim}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => dialog.current?.close()}
                aria-label="Close evidence"
                className="rounded-md px-2 py-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900"
              >
                ✕
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-5 text-sm">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                <dt className="text-zinc-500">Confidence</dt>
                <dd>
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${CONFIDENCE[open.confidence]}`}>
                    {open.confidence}
                  </span>{" "}
                  <span className="text-zinc-700">{open.confidence_reason}</span>
                </dd>
                <dt className="text-zinc-500">Rows used</dt>
                <dd className="text-zinc-800 tabular-nums">{open.row_count.toLocaleString()}</dd>
              </dl>
              {open.assumptions.length > 0 && (
                <section>
                  <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-1.5">Assumptions</h4>
                  <ul className="list-disc pl-5 space-y-1 text-zinc-700">
                    {open.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                </section>
              )}
              <section>
                <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-1.5">Result</h4>
                <pre className="whitespace-pre-wrap break-all rounded-lg bg-zinc-50 border border-zinc-200 p-3 text-xs text-zinc-800">
                  {open.result_excerpt}
                </pre>
              </section>
              <details className="group">
                <summary className="cursor-pointer text-xs font-semibold text-zinc-500 uppercase tracking-wide">
                  Code that produced it
                </summary>
                <pre className="mt-2 overflow-x-auto rounded-lg bg-zinc-900 p-3 text-xs text-zinc-100">
                  {open.code}
                </pre>
              </details>
            </div>
          </div>
        )}
      </dialog>
    </div>
  );
}
