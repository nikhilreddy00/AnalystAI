"use client";

// Phase 3.5 "Workbench" layout: the chat column (left) drives a report canvas (right).
// Below lg the two halves become Chat / Report tabs. Pipeline logic stays in useAnalysis.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertCircle, ArrowUp, Database, PanelLeft, X } from "lucide-react";
import { forgetRun, getRun, recalledRun, useAnalysis } from "@/hooks/useAnalysis";
import { useSessions } from "@/hooks/useSessions";
import AgentProgressStream from "./AgentProgressStream";
import AppHeader from "./AppHeader";
import ApprovalModal from "./ApprovalModal";
import ConnectorSetup from "./ConnectorSetup";
import ReportCanvas, { hasReport } from "./ReportCanvas";
import { SessionSidebar, titleOf } from "@/components/SessionSidebar";
import { getDataset, getSessionResults, getThread, splitVersion } from "@/lib/api";
import type { ChatMessage, DataSource, UploadResponse } from "@/types";

const SIDEBAR_KEY = "analystai:sidebarCollapsed";
const wide = () => typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches;

// The desktop sidebar's collapsed state, kept in localStorage (memory if storage is blocked).
// An external store: the server renders it open, the browser reads storage, no mismatch.
let collapsedMem: boolean | null = null;
const collapsedSubs = new Set<() => void>();
function readCollapsed(): boolean {
  if (collapsedMem !== null) return collapsedMem;
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}
function writeCollapsed(v: boolean) {
  collapsedMem = v;
  try {
    localStorage.setItem(SIDEBAR_KEY, v ? "1" : "0");
  } catch {
    // storage blocked: the choice lasts until reload
  }
  collapsedSubs.forEach((f) => f());
}
const subscribeCollapsed = (f: () => void) => {
  collapsedSubs.add(f);
  return () => {
    collapsedSubs.delete(f);
  };
};

// The thread's address, with the browser's history API (Next.js keeps useSearchParams in sync).
const setUrl = (path: string, push = false) =>
  push ? window.history.pushState(null, "", path) : window.history.replaceState(null, "", path);

/** How an upload was filed: a new version can be undone while it has no analyses (D2). */
function UploadNotice({ upload, onSplit }: { upload: UploadResponse; onSplit: () => void }) {
  if (upload.status === "new_dataset") return null;
  return (
    <div role="status" className="space-y-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-sm">
      {upload.status === "existing" ? (
        <p className="text-foreground">
          Same file as version {upload.version} of <span className="font-medium">{upload.dataset_name}</span>
          {upload.dataset.analyses > 0 && <> ({upload.dataset.analyses} earlier {upload.dataset.analyses === 1 ? "analysis" : "analyses"})</>}.{" "}
          <Link href={`/datasets?d=${upload.dataset_id}`} className="text-primary underline-offset-2 hover:underline">
            Open dataset
          </Link>
        </p>
      ) : (
        <>
          <p className="text-foreground">
            Saved as version {upload.version} of <span className="font-medium">{upload.dataset_name}</span> (same name and columns).
          </p>
          <button type="button" onClick={onSplit} className="rounded-md border border-border px-2.5 py-1 text-xs text-foreground hover:bg-accent">
            Keep as a separate dataset
          </button>
        </>
      )}
    </div>
  );
}

const QUICK_PROMPTS = [
  "Give me a complete overview of this dataset",
  "Show me key trends and patterns",
  "Find anomalies and outliers",
  "What are the top correlations?",
];

// useAnalysis writes failures into the message text with this prefix.
const ERROR_PREFIX = "Error: ";

function Bubble({
  message,
  selected,
  onView,
  onRetry,
}: {
  message: ChatMessage;
  selected: boolean;
  onView?: () => void;
  onRetry?: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-lg rounded-br-sm border border-border bg-muted px-3 py-2 text-sm text-foreground">
        {message.content}
      </div>
    );
  }
  if (message.content.startsWith(ERROR_PREFIX)) {
    return (
      <div role="alert" className="space-y-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2.5 text-sm">
        <p className="flex items-center gap-1.5 font-medium text-destructive">
          <AlertCircle className="h-4 w-4" /> The analysis stopped
        </p>
        <p className="text-foreground">{message.content.slice(ERROR_PREFIX.length)}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          >
            {message.runId ? "Retry from where it stopped" : "Try again"}
          </button>
        )}
      </div>
    );
  }
  if (!message.content && message.isStreaming) return null; // progress shows instead
  return (
    <div className="space-y-2 text-sm text-foreground">
      <p className="line-clamp-[8] leading-relaxed whitespace-pre-line">{message.content || "Done."}</p>
      {onView && (
        <button
          type="button"
          onClick={onView}
          aria-pressed={selected}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            selected
              ? "border-primary/40 bg-brand-soft text-primary"
              : "border-border text-muted-foreground hover:bg-accent hover:text-foreground"
          }`}
        >
          {selected ? "Showing in report" : "View report"}
        </button>
      )}
    </div>
  );
}

export default function ChatInterface() {
  const {
    messages,
    sessionId,
    isLoading,
    agentEvents,
    pendingApproval,
    setPendingApproval,
    sendMessage,
    cancelRun,
    retryRun,
    runsStarted,
    detach,
    setSource,
    hydrateThread,
    hydrateFromSnapshot,
    resetWorkspace,
  } = useAnalysis();
  const { sessions, loading: sessionsLoading, refresh: refreshSessions, deleteSession, updateSession } = useSessions();
  const params = useSearchParams();
  const threadParam = params.get("t");
  const datasetParam = params.get("dataset");
  // The thread in memory: the URL loads a thread only when it names another one.
  const loaded = useRef<string | null>(null);
  const [threadDataset, setThreadDataset] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const collapsed = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => false);

  const [input, setInput] = useState("");
  const [connectedSource, setConnected] = useState(false);
  const connected = connectedSource;
  const [datasetName, setDatasetName] = useState<string | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [pane, setPane] = useState<"chat" | "report">("report");
  // A past session opened from History shows its report without a live connection.
  const [viewingHistory, setViewingHistory] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const historyRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, agentEvents]);

  useEffect(() => {
    if (!isLoading && sessionId) void refreshSessions();
  }, [isLoading, sessionId, refreshSessions]);

  // A run just started (its thread may be new): the sidebar shows it, with its "running" dot.
  useEffect(() => {
    if (runsStarted) void refreshSessions();
  }, [runsStarted, refreshSessions]);

  // A tab closed mid-run and reopened on plain /chat: open that run's thread (all of it).
  useEffect(() => {
    const runId = recalledRun();
    if (!runId || new URLSearchParams(window.location.search).has("t")) return;
    let live = true;
    void getRun(runId).then((r) => {
      if (!r) forgetRun();   // purged: stop asking for it on every visit
      else if (live && !loaded.current) setUrl(`/chat?t=${r.session_id}`);
    });
    return () => {
      live = false;
    };
  }, []);

  // A new thread gets its URL as soon as the server names it, so a refresh or a shared tab
  // reopens it. `loaded` is set first: the URL change must not reload what is on screen.
  useEffect(() => {
    if (sessionId && sessionId !== threadParam) {
      loaded.current = sessionId;
      setUrl(`/chat?t=${sessionId}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const openThread = useCallback(async (id: string) => {
    detach();   // the click wins over a stream or a POST still answering for the old screen
    loaded.current = id;
    setMissing(false);
    setUpload(null);
    setSource(null);   // a reopened thread has its data; an earlier upload is not its
    const view = await getThread(id);
    if (loaded.current !== id) return;   // another thread was opened meanwhile
    if (!view) {
      loaded.current = null;
      resetWorkspace();
      setConnected(false);
      setMissing(true);
      setUrl("/chat");
      return;
    }
    setSelectedIdx(null);
    setPane("report");
    setDatasetName(view.dataset_name);
    setThreadDataset(view.dataset_id);
    setViewingHistory(true);
    setConnected(true);   // a reopened thread takes follow-up questions
    if (view.turns.length) {
      hydrateThread(view);
    } else {
      const snapshot = await getSessionResults(id);   // a thread from before Phase 5.6b
      if (snapshot && loaded.current === id) hydrateFromSnapshot(snapshot);
    }
  }, [hydrateThread, hydrateFromSnapshot, resetWorkspace, setSource, detach]);

  // On a change of the URL's thread only (never on a re-render: a new thread has its id a moment
  // before its URL). Back to plain /chat (the browser's back button) clears the screen; our own
  // moves to /chat clear `loaded` first.
  const prevParam = useRef(threadParam);
  useEffect(() => {
    const was = prevParam.current;
    prevParam.current = threadParam;
    if (threadParam && threadParam !== loaded.current) void openThread(threadParam);
    else if (!threadParam && was && loaded.current) clearWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadParam]);

  // /chat?dataset=<id> (the Datasets page's "New analysis"): a new thread on it, no upload.
  useEffect(() => {
    if (!datasetParam) return;
    detach();
    let live = true;
    void getDataset(datasetParam).then((ds) => {
      if (!live) return;
      setUrl("/chat");
      if (!ds) return;
      loaded.current = null;
      resetWorkspace();
      setSource({ dataset_id: ds.id, dataset_name: ds.name });
      setDatasetName(ds.name);
      setThreadDataset(ds.id);
      setConnected(true);
      setViewingHistory(false);
      setUpload(null);
      setPane("chat");
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetParam]);


  // The canvas shows the chosen report, else the newest one.
  let latestReport = -1;
  messages.forEach((m, i) => {
    if (m.role === "assistant" && hasReport(m)) latestReport = i;
  });
  const reportIdx = selectedIdx !== null && messages[selectedIdx] && hasReport(messages[selectedIdx]) ? selectedIdx : latestReport;
  const report = reportIdx >= 0 ? messages[reportIdx] : null;
  const questionFor = (i: number) => [...messages.slice(0, i)].reverse().find((m) => m.role === "user")?.content ?? "";
  const followUps = report?.insight?.proactive_questions ?? [];

  function ask(text: string) {
    const q = text.trim();
    if (!q || isLoading || !connected) return;
    setSelectedIdx(null);
    setPane("chat");
    void sendMessage(q);
  }

  function handleSend() {
    if (!input.trim()) return;
    ask(input);
    setInput("");
  }

  const closeDrawer = () => historyRef.current?.close();

  const handleLoadSession = (id: string) => {
    closeDrawer();
    if (id === loaded.current && id === sessionId) {
      setPane("report");
      return;
    }
    setUrl(`/chat?t=${id}`, true);
  };

  function clearWorkspace() {
    loaded.current = null;
    resetWorkspace();
    setConnected(false);
    setDatasetName(null);
    setThreadDataset(null);
    setSelectedIdx(null);
    setViewingHistory(false);
    setUpload(null);
    setMissing(false);
    setPane("report");
  }

  const handleNewAnalysis = () => {
    closeDrawer();
    clearWorkspace();
    setUrl("/chat");
  };

  const handleConnectorReady = (source: DataSource, filed?: UploadResponse) => {
    // One dataset per thread: new data always starts a new analysis.
    loaded.current = null;
    resetWorkspace();
    setSource(source);
    setDatasetName(source.dataset_name);
    setThreadDataset(filed?.dataset_id ?? null);
    setUpload(filed ?? null);
    setConnected(true);
    setViewingHistory(false);
    setMissing(false);
    setPane("chat");
    setUrl("/chat");
  };

  const handleSplit = async () => {
    if (!upload) return;
    const ds = await splitVersion(upload.dataset_version_id);
    if (ds) {
      setThreadDataset(ds.id);
      setUpload(null);
    }
  };

  const handleDeleteSession = async (id: string) => {
    const ok = await deleteSession(id);
    if (ok && (sessionId === id || loaded.current === id)) handleNewAnalysis();
  };

  /** "Re-run on current data": the same question on the dataset's newest version, as a new
   *  report beside the old one (which never changes). */
  const handleRerun = (question: string) => {
    if (!threadDataset || isLoading) return;
    setSelectedIdx(null);
    setPane("chat");
    void sendMessage(question, false, null, { dataset_id: threadDataset });
  };

  const toggleSidebar = () => {
    if (!wide()) {
      historyRef.current?.showModal();
      return;
    }
    writeCollapsed(!collapsed);
  };

  /** null: deleted; undefined: not known yet (the list is loading). */
  const titleFor = (sid: string) => {
    const s = sessions.find((x) => x.session_id === sid);
    return s ? titleOf(s) : sessionsLoading ? undefined : null;
  };

  const sidebar = (
    <SessionSidebar
      sessions={sessions}
      activeSessionId={sessionId ?? threadParam}
      loading={sessionsLoading}
      onLoadSession={handleLoadSession}
      onDeleteSession={(id) => void handleDeleteSession(id)}
      onUpdateSession={(id, patch) => void updateSession(id, patch)}
      onNewAnalysis={handleNewAnalysis}
    />
  );

  const handleLoadPrevious = async () => {
    setPendingApproval(null);
    if (sessionId) {
      loaded.current = null;
      await openThread(sessionId);
    }
  };

  const handleRunFresh = () => {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
    setPendingApproval(null);
    if (!lastUserMessage) return;
    void sendMessage(lastUserMessage, true, "rerun_overview");
  };

  function handleApproval(response: string) {
    // The gate travels with the answer: a high-null approval keeps the plan (and the question).
    const context = pendingApproval?.context ?? null;
    setPendingApproval(null);
    sendMessage(response, true, context);
  }

  const paneTab = (id: "chat" | "report", label: string) => (
    <button
      type="button"
      onClick={() => setPane(id)}
      aria-pressed={pane === id}
      className={`flex-1 border-b-2 py-2 text-sm ${
        pane === id ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      <AppHeader
        start={
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label="Analyses"
            title="Analyses"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        }
      >
        {connected && datasetName && (
          <span className="hidden max-w-[220px] items-center gap-1.5 truncate rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground md:inline-flex">
            <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-success" aria-hidden />
            <span className="truncate">{datasetName}</span>
          </span>
        )}
      </AppHeader>

      <div className="flex border-b border-border bg-card px-3 lg:hidden">
        {paneTab("chat", "Chat")}
        {paneTab("report", report ? "Report" : connected ? "Report" : "Data")}
      </div>

      <div className={`grid min-h-0 flex-1 ${collapsed ? "lg:grid-cols-[minmax(320px,380px)_1fr]" : "lg:grid-cols-[260px_minmax(320px,380px)_1fr]"}`}>
        {/* Phase 5.6b: the threads, always there on desktop (collapsible); a drawer on phones. */}
        {!collapsed && (
          <aside aria-label="Analyses sidebar" className="hidden min-h-0 overflow-y-auto border-r border-border bg-sidebar p-3 lg:block">
            {sidebar}
          </aside>
        )}
        {/* Chat column */}
        <section
          aria-label="Chat"
          className={`min-h-0 flex-col border-border bg-card lg:flex lg:border-r ${pane === "chat" ? "flex" : "hidden"}`}
        >
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2 text-xs text-muted-foreground">
            <span className="flex min-w-0 items-center gap-1.5">
              <Database className="h-3.5 w-3.5 flex-shrink-0" />
              <span className="truncate">{connected ? datasetName ?? "Data connected" : "No data connected"}</span>
            </span>
            <button
              type="button"
              onClick={handleNewAnalysis}
              title={connected ? "Other data starts a new analysis" : undefined}
              className="flex-shrink-0 rounded px-1.5 py-0.5 text-primary hover:bg-accent"
            >
              {connected ? "Change" : "Connect"}
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
            {missing && (
              <p role="status" className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-foreground">
                That analysis no longer exists. It may have been deleted.
              </p>
            )}
            {upload && messages.length === 0 && <UploadNotice upload={upload} onSplit={() => void handleSplit()} />}
            {messages.length === 0 && !isLoading && (
              <div className="space-y-3 pt-6 text-sm">
                <p className="font-medium text-foreground">
                  {connected ? "What would you like to know?" : "Connect your data to start"}
                </p>
                <p className="text-muted-foreground">
                  {connected
                    ? "Ask in plain English. The report builds on the right with charts, evidence and a reviewer's notes."
                    : "Upload a file or connect SQL, Google Sheets or an API. Your report appears on the right."}
                </p>
                {connected && (
                  <div className="flex flex-col gap-1.5 pt-1">
                    {QUICK_PROMPTS.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => ask(p)}
                        className="rounded-md border border-border px-3 py-2 text-left text-sm text-foreground hover:bg-accent"
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {messages.map((m, i) => (
              <Bubble
                key={i}
                message={m}
                selected={i === reportIdx}
                onView={
                  m.role === "assistant" && hasReport(m)
                    ? () => {
                        setSelectedIdx(i);
                        setPane("report");
                      }
                    : undefined
                }
                onRetry={
                  connected && !isLoading
                    ? () => (m.runId ? void retryRun(m.runId) : ask(questionFor(i)))
                    : undefined
                }
              />
            ))}

            {isLoading && (
              <div className="space-y-2">
                <AgentProgressStream events={agentEvents} isLoading={isLoading} />
                <button
                  type="button"
                  onClick={() => void cancelRun()}
                  className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  Stop
                </button>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="space-y-2 border-t border-border p-3">
            {connected && !isLoading && followUps.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {followUps.slice(0, 3).map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => ask(q)}
                    className="rounded-full border border-border px-2.5 py-1 text-left text-xs text-foreground hover:bg-accent"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
            <form
              className="flex items-center gap-2 rounded-lg border border-input bg-background py-1 pl-3 pr-1 focus-within:border-ring"
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
            >
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                aria-label="Ask a question about your data"
                placeholder={connected ? "Ask a question about your data…" : "Connect a data source first"}
                disabled={isLoading || !connected}
                className="min-w-0 flex-1 bg-transparent py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={isLoading || !connected || !input.trim()}
                aria-label="Send"
                className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
            </form>
          </div>
        </section>

        {/* Report canvas */}
        <section
          aria-label="Report"
          className={`min-h-0 overflow-y-auto p-4 sm:p-6 lg:block ${pane === "report" ? "block" : "hidden"}`}
        >
          {!connected && !viewingHistory ? (
            <div className="mx-auto max-w-xl space-y-4 pt-4">
              <div>
                <h2 className="text-lg font-semibold text-foreground">Connect your data</h2>
                <p className="text-sm text-muted-foreground">
                  CSV, Excel, Parquet, a SQL database, Google Sheets or a REST API.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <ConnectorSetup onConnectorReady={handleConnectorReady} />
              </div>
            </div>
          ) : report ? (
            <ReportCanvas
              key={reportIdx}
              message={report}
              question={questionFor(reportIdx)}
              sessionId={sessionId}
              isLatest={reportIdx === latestReport}
              onFollowUp={ask}
              onRerun={threadDataset && !isLoading ? () => handleRerun(questionFor(reportIdx)) : undefined}
              titleFor={titleFor}
              onDraft={(q) => {
                setInput(q);
                setPane("chat");
                inputRef.current?.focus();
              }}
            />
          ) : (
            <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-2 text-center">
              <p className="text-sm font-medium text-foreground">
                {isLoading ? "Working on your report" : "Your report appears here"}
              </p>
              <p className="text-sm text-muted-foreground">
                {isLoading
                  ? "Progress is in the chat. Charts, findings and evidence land here when the run finishes."
                  : "Ask a question in the chat to get a summary, charts, the evidence behind each finding and a reviewer's notes."}
              </p>
            </div>
          )}
        </section>
      </div>

      <dialog
        ref={historyRef}
        aria-label="Analyses"
        onClick={(e) => e.target === historyRef.current && closeDrawer()}
        className="m-0 h-dvh max-h-dvh w-full max-w-sm bg-sidebar p-0 text-foreground shadow-2xl backdrop:bg-black/40"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Analyses</h2>
            <button
              type="button"
              onClick={closeDrawer}
              aria-label="Close analyses"
              className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3">{sidebar}</div>
        </div>
      </dialog>

      {pendingApproval && (
        <ApprovalModal
          event={pendingApproval}
          onSubmit={handleApproval}
          onDismiss={() => setPendingApproval(null)}
          onLoadPrevious={handleLoadPrevious}
          onRunFresh={handleRunFresh}
          onCancel={() => setPendingApproval(null)}
        />
      )}
    </div>
  );
}
