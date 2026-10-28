"use client";

import { useEffect, useRef, useState } from "react";
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
  { label: "Continue with imputation", sub: "Fill missing values using statistical methods (recommended)", value: "Continue with imputation" },
  { label: "Drop high-null columns", sub: "Remove columns with too many missing values", value: "Drop those columns" },
  { label: "Cancel analysis", sub: "Stop and return without analyzing", value: "Cancel" },
];

const BTN = "rounded-md px-3 py-1.5 text-sm";
const PRIMARY = `${BTN} bg-primary font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40`;
const SECONDARY = `${BTN} border border-border text-foreground hover:bg-accent`;
const QUIET = `${BTN} text-muted-foreground hover:text-foreground`;

// Native <dialog>: focus is trapped, Esc closes (treated as dismiss / cancel).
export default function ApprovalModal({ event, onSubmit, onDismiss, onLoadPrevious, onRunFresh, onCancel }: Props) {
  const [freeText, setFreeText] = useState("");
  const ref = useRef<HTMLDialogElement>(null);
  const isHighNull = event.context === "high_null";
  const isRerunOverview = event.context === "rerun_overview";
  const close = isRerunOverview ? onCancel ?? onDismiss : onDismiss;

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby="approval-title"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-md rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl backdrop:bg-black/40"
    >
      <div className="space-y-4">
        <div className="space-y-2">
          <h2 id="approval-title" className="text-base font-semibold">
            {event.context === "clarification"
              ? "One question before I start"
              : isRerunOverview
              ? "This dataset was analysed before"
              : "Some columns are mostly empty"}
          </h2>
          {event.high_null_columns && event.high_null_columns.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {event.high_null_columns.map((col) => (
                <span key={col} className="rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 font-mono text-xs text-foreground">
                  {col}
                </span>
              ))}
            </div>
          )}
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {isHighNull ? "These columns have more than 40% missing values. How should I proceed?" : event.question}
          </p>
        </div>

        {isRerunOverview ? (
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={onCancel} className={QUIET}>
              Cancel
            </button>
            <button type="button" onClick={onLoadPrevious} className={SECONDARY}>
              Load previous
            </button>
            <button type="button" onClick={onRunFresh} className={PRIMARY} autoFocus>
              Run fresh
            </button>
          </div>
        ) : isHighNull ? (
          <div className="space-y-2">
            {HIGH_NULL_OPTIONS.map((opt, i) => (
              <button
                key={opt.value}
                type="button"
                autoFocus={i === 0}
                onClick={() => onSubmit(opt.value)}
                className="w-full rounded-md border border-border px-3 py-2.5 text-left hover:border-primary/50 hover:bg-accent"
              >
                <p className="text-sm font-medium text-foreground">{opt.label}</p>
                <p className="text-xs text-muted-foreground">{opt.sub}</p>
              </button>
            ))}
          </div>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (freeText.trim()) onSubmit(freeText.trim());
            }}
          >
            <textarea
              rows={2}
              value={freeText}
              autoFocus
              aria-label="Your answer"
              onChange={(e) => setFreeText(e.target.value)}
              placeholder="Type your answer…"
              className="w-full resize-none rounded-md border border-input bg-background p-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-ring focus:outline-none"
            />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onDismiss} className={QUIET}>
                Dismiss
              </button>
              <button type="submit" disabled={!freeText.trim()} className={PRIMARY}>
                Send
              </button>
            </div>
          </form>
        )}
      </div>
    </dialog>
  );
}
