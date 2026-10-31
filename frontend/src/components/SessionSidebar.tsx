"use client";

// Phase 5.6b: every analysis thread, grouped by date, searchable, with its latest run's status.
// Row actions: rename (inline), pin, archive, delete (confirmed). Titles come from the server
// (dataset name + first question, by code) until the user renames one.

import { useEffect, useMemo, useRef, useState } from "react";
import { MoreHorizontal, PlusCircle, Search } from "lucide-react";
import ConfirmDialog from "./ConfirmDialog";
import type { ThreadPatch } from "@/lib/api";
import type { SessionSummary } from "@/types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  onLoadSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onUpdateSession: (id: string, patch: ThreadPatch) => void;
  onNewAnalysis: () => void;
}

const TITLE_MAX = 120;
const DAY = 86_400_000;

const STATUS: Record<string, { label: string; dot: string }> = {
  queued: { label: "Running", dot: "bg-primary animate-pulse" },
  running: { label: "Running", dot: "bg-primary animate-pulse" },
  waiting_approval: { label: "Waiting for you", dot: "bg-warning" },
  failed: { label: "Failed", dot: "bg-destructive" },
  completed: { label: "Done", dot: "bg-success" },
  cancelled: { label: "Stopped", dot: "bg-muted-foreground" },
};

export const titleOf = (s: SessionSummary) => s.title || s.dataset_name || "Untitled analysis";

/** Today / Previous 7 days / Older, by the thread's last activity in the viewer's time zone. */
export function dateGroup(iso: string, now = new Date()): "Today" | "Previous 7 days" | "Older" {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = new Date(iso).getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - 7 * DAY) return "Previous 7 days";
  return "Older";
}

function matches(s: SessionSummary, q: string) {
  if (!q) return true;
  const hay = `${s.title ?? ""}\n${s.questions}\n${s.dataset_name ?? ""}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

function RowMenu({ s, onRename, onUpdate, onDelete }: {
  s: SessionSummary;
  onRename: () => void;
  onUpdate: (patch: ThreadPatch) => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = () => [...(ref.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
  useEffect(() => {
    if (!open) return;
    items()[0]?.focus();   // a menu opens on its first item
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const shut = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const item = (label: string, act: () => void, danger = false) => (
    <button
      type="button"
      role="menuitem"
      tabIndex={-1}
      onClick={() => {
        setOpen(false);
        act();
      }}
      className={`block w-full rounded px-2.5 py-1.5 text-left text-sm hover:bg-accent ${danger ? "text-destructive" : "text-foreground"}`}
    >
      {label}
    </button>
  );
  return (
    <div
      ref={ref}
      className="absolute right-1 top-1.5"
      onKeyDown={(e) => {
        if (!open) return;
        if (e.key === "Escape") {
          e.preventDefault();
          shut();
        } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const all = items();
          const i = all.indexOf(document.activeElement as HTMLButtonElement);
          all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length]?.focus();
        }
      }}
      onBlur={(e) => {
        if (open && !ref.current?.contains(e.relatedTarget as Node)) setOpen(false);   // tabbed away
      }}
    >
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Actions for ${titleOf(s)}`}
        onClick={() => setOpen((o) => !o)}
        className={`rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 ${open ? "opacity-100" : "opacity-100 lg:opacity-0"}`}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-40 rounded-md border border-border bg-popover p-1 shadow-lg">
          {item("Rename", onRename)}
          {item(s.pinned ? "Unpin" : "Pin", () => onUpdate({ pinned: !s.pinned }))}
          {item(s.archived ? "Unarchive" : "Archive", () => onUpdate({ archived: !s.archived }))}
          {item("Delete", onDelete, true)}
        </div>
      )}
    </div>
  );
}

function Row({ s, active, onOpen, onUpdate, onDelete }: {
  s: SessionSummary;
  active: boolean;
  onOpen: () => void;
  onUpdate: (patch: ThreadPatch) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const status = s.run_status ? STATUS[s.run_status] : null;
  const openBtn = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!editing && refocus.current) {   // after a rename, focus is back on the thread
      refocus.current = false;
      openBtn.current?.focus();
    }
  }, [editing]);

  function stop() {
    refocus.current = true;
    setEditing(false);
  }

  function save() {
    const t = draft.split(/\s+/).join(" ").trim();
    stop();
    if (t && t !== s.title && t.length <= TITLE_MAX) onUpdate({ title: t });
  }

  return (
    <li className="group relative" data-testid="thread-row">
      {editing ? (
        <input
          autoFocus
          aria-label="Thread title"
          value={draft}
          maxLength={TITLE_MAX}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            // Consumed here: focus moves to the thread's button at once, and the same key
            // must not then open the thread (or close the phone drawer).
            if (e.key === "Enter" || e.key === "Escape") e.preventDefault();
            if (e.key === "Enter") save();
            if (e.key === "Escape") stop();
          }}
          className="w-full rounded-md border border-ring bg-background px-2.5 py-2 text-sm text-foreground focus:outline-none"
        />
      ) : (
        <button
          ref={openBtn}
          type="button"
          onClick={onOpen}
          aria-current={active ? "true" : undefined}
          className={`w-full rounded-md px-2.5 py-2 pr-8 text-left ${active ? "bg-card shadow-[0_0_0_1px_var(--border)]" : "hover:bg-accent"}`}
        >
          <span className="flex min-w-0 items-center gap-2">
            {status && (
              <span className={`h-2 w-2 flex-shrink-0 rounded-full ${status.dot}`} aria-hidden title={status.label} />
            )}
            <span className="truncate text-sm font-medium text-foreground" title={titleOf(s)}>{titleOf(s)}</span>
          </span>
          <span className="mt-0.5 block truncate pl-4 text-xs text-muted-foreground">
            {s.dataset_name ?? "No dataset"}
            {status && <span className="sr-only">, {status.label}</span>}
          </span>
        </button>
      )}
      {!editing && (
        <RowMenu
          s={s}
          onRename={() => {
            setDraft(titleOf(s));
            setEditing(true);
          }}
          onUpdate={onUpdate}
          onDelete={onDelete}
        />
      )}
    </li>
  );
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  loading,
  onLoadSession,
  onDeleteSession,
  onUpdateSession,
  onNewAnalysis,
}: Props) {
  const [query, setQuery] = useState("");
  const [deleting, setDeleting] = useState<SessionSummary | null>(null);

  const groups = useMemo(() => {
    const hits = sessions.filter((s) => matches(s, query.trim()));
    const out: [string, SessionSummary[]][] = [
      ["Pinned", hits.filter((s) => s.pinned && !s.archived)],
    ];
    for (const g of ["Today", "Previous 7 days", "Older"] as const) {
      out.push([g, hits.filter((s) => !s.pinned && !s.archived && dateGroup(s.updated_at) === g)]);
    }
    return { live: out.filter(([, items]) => items.length), archived: hits.filter((s) => s.archived), any: hits.length };
  }, [sessions, query]);

  const row = (s: SessionSummary) => (
    <Row
      key={s.session_id}
      s={s}
      active={s.session_id === activeSessionId}
      onOpen={() => onLoadSession(s.session_id)}
      onUpdate={(patch) => onUpdateSession(s.session_id, patch)}
      onDelete={() => setDeleting(s)}
    />
  );

  return (
    <nav aria-label="Analyses" className="flex flex-col gap-3">
      <button
        type="button"
        onClick={onNewAnalysis}
        className="flex items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground hover:bg-accent"
      >
        <PlusCircle size={15} />
        New analysis
      </button>

      <label className="flex items-center gap-2 rounded-md border border-input bg-background px-2.5 py-1.5 focus-within:border-ring">
        <Search size={14} className="flex-shrink-0 text-muted-foreground" aria-hidden />
        <span className="sr-only">Search analyses</span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles, questions, datasets"
          className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
      </label>

      {loading && sessions.length === 0 && <p className="px-1 text-xs text-muted-foreground">Loading…</p>}
      {!loading && sessions.length === 0 && (
        <p className="px-1 text-xs text-muted-foreground">No analyses yet. Connect data to start one.</p>
      )}
      {sessions.length > 0 && groups.any === 0 && (
        <p className="px-1 text-xs text-muted-foreground">No analyses match “{query}”.</p>
      )}

      {groups.live.map(([label, items]) => (
        <section key={label} aria-label={label}>
          <h3 className="px-1 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</h3>
          <ul className="flex flex-col gap-0.5">{items.map(row)}</ul>
        </section>
      ))}

      {groups.archived.length > 0 && (
        <details>
          <summary className="cursor-pointer px-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Archived · {groups.archived.length}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">{groups.archived.map(row)}</ul>
        </details>
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete this analysis?"
          body={
            <>
              <span className="font-medium text-foreground">{titleOf(deleting)}</span> and every report in it
              will be deleted, with its run history. The dataset stays. This can&apos;t be undone.
            </>
          }
          confirmLabel="Delete"
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            onDeleteSession(deleting.session_id);
            setDeleting(null);
          }}
        />
      )}
    </nav>
  );
}
