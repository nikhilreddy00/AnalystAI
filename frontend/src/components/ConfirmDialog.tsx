"use client";

import { useEffect, useRef } from "react";

interface Props {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

// Native <dialog> (as ApprovalModal): focus is trapped, Esc cancels, Cancel has focus first so
// Enter never deletes by accident.
export default function ConfirmDialog({ title, body, confirmLabel, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    const opener = document.activeElement as HTMLElement | null;
    if (d && !d.open) d.showModal();
    return () => {
      d?.close();
      opener?.focus?.();   // focus goes back where the user was
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby="confirm-title"
      aria-describedby="confirm-body"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-sm rounded-lg border border-border bg-card p-5 text-foreground shadow-2xl backdrop:bg-black/40"
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <h2 id="confirm-title" className="text-base font-semibold">{title}</h2>
          <div id="confirm-body" className="text-sm text-muted-foreground">{body}</div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            autoFocus
            onClick={onCancel}
            className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-destructive px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-destructive/90"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
