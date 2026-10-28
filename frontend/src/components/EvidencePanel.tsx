"use client";

import { useRef, useState } from "react";
import { X } from "lucide-react";
import type { Evidence } from "@/types";

// Phase 3: every analysis finding carries its evidence (ADR-0001). The list shows each claim
// with its confidence; a row opens a drawer with the rows behind it, the assumptions, the
// result the code produced and the code itself. Native <dialog>: Esc, focus trap, backdrop.

// Confidence reads as a dot plus its word, never colour alone.
const CONFIDENCE = {
  high: "bg-success",
  medium: "bg-warning",
  low: "bg-muted-foreground/60",
};

function Confidence({ level }: { level: Evidence["confidence"] }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
      <span className={`h-1.5 w-1.5 rounded-full ${CONFIDENCE[level]}`} aria-hidden />
      {level}
    </span>
  );
}

const KIND = {
  correlation: "Correlation",
  trend: "Trend",
  segment: "Breakdown",
  anomaly: "Anomalies",
  test: "Test",
  query: "SQL query",
};

export default function EvidencePanel({ items }: { items: Evidence[] }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState<Evidence | null>(null);

  const show = (e: Evidence) => {
    setOpen(e);
    dialog.current?.showModal();
  };

  return (
    <section aria-label="Evidence">
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Evidence</h3>
        <span className="text-xs text-muted-foreground">
          {items.length} check{items.length === 1 ? "" : "s"} · open one to see the code and rows behind it
        </span>
      </div>
      <ul className="divide-y divide-border rounded-md border border-border bg-card">
        {items.map((e) => (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => show(e)}
              className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-accent"
            >
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground w-20 shrink-0">
                {KIND[e.kind]}
              </span>
              <span className="text-sm text-foreground flex-1 min-w-0 truncate">{e.claim}</span>
              <span className="shrink-0"><Confidence level={e.confidence} /></span>
            </button>
          </li>
        ))}
      </ul>

      <dialog
        ref={dialog}
        aria-labelledby="evidence-title"
        onClose={() => setOpen(null)}
        onClick={(ev) => ev.target === dialog.current && dialog.current?.close()}
        className="ml-auto mr-0 my-0 h-dvh max-h-dvh w-full max-w-xl bg-card text-foreground p-0 shadow-2xl backdrop:bg-black/40"
      >
        {open && (
          <div className="flex h-full flex-col">
            <div className="flex items-start justify-between gap-4 border-b border-border p-5">
              <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{KIND[open.kind]}</p>
                <h2 id="evidence-title" className="text-base font-semibold text-foreground break-words">
                  {open.claim}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => dialog.current?.close()}
                aria-label="Close evidence"
                className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-5 text-sm">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
                <dt className="text-muted-foreground">Confidence</dt>
                <dd>
                  <Confidence level={open.confidence} />{" "}
                  <span className="text-foreground">{open.confidence_reason}</span>
                </dd>
                <dt className="text-muted-foreground">Rows used</dt>
                <dd className="text-foreground tabular-nums">{open.row_count.toLocaleString()}</dd>
              </dl>
              {open.assumptions.length > 0 && (
                <section>
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">Assumptions</h4>
                  <ul className="list-disc pl-5 space-y-1 text-foreground">
                    {open.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                </section>
              )}
              <section>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">Result</h4>
                <pre className="whitespace-pre-wrap break-all rounded-lg bg-muted border border-border p-3 text-xs text-foreground">
                  {open.result_excerpt}
                </pre>
              </section>
              <details className="group">
                <summary className="cursor-pointer text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                  Code that produced it
                </summary>
                <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs text-foreground">
                  {open.code}
                </pre>
              </details>
            </div>
          </div>
        )}
      </dialog>
    </section>
  );
}
