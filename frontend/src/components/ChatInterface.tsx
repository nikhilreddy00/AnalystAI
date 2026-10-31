"use client";

// Phase 3.5 "Workbench" layout: the chat column (left) drives a report canvas (right).
// Below lg the two halves become Chat / Report tabs. Pipeline logic stays in useAnalysis.

import { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowUp, Database, History, X } from "lucide-react";
import { useAnalysis } from "@/hooks/useAnalysis";
import { useSessions } from "@/hooks/useSessions";
import AgentProgressStream from "./AgentProgressStream";
import AppHeader from "./AppHeader";
import ApprovalModal from "./ApprovalModal";
import ConnectorSetup from "./ConnectorSetup";
import ReportCanvas, { hasReport } from "./ReportCanvas";
import { SessionSidebar } from "@/components/SessionSidebar";
import { getSessionResults } from "@/lib/api";
import type { ChatMessage } from "@/types";

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
    resumed,
    setConnectorConfig,
    setUploadMeta,
    hydrateFromSnapshot,
    resetWorkspace,
  } = useAnalysis();
  const { sessions, loading: sessionsLoading, refresh: refreshSessions, deleteSession } = useSessions();

  const [input, setInput] = useState("");
  const [connectedSource, setConnected] = useState(false);
  // A reopened tab reattached to its run: the conversation continues where it was.
  const connected = connectedSource || resumed;
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

  const handleLoadSession = async (id: string) => {
    historyRef.current?.close();
    const snapshot = await getSessionResults(id);
    if (snapshot) {
      setSelectedIdx(null);
      setViewingHistory(true);
      setPane("report");
      setDatasetName(sessions.find((s) => s.session_id === id)?.dataset_name ?? null);
      hydrateFromSnapshot(snapshot);
    }
  };

  const handleNewAnalysis = () => {
    historyRef.current?.close();
    resetWorkspace();
    setConnected(false);
    setDatasetName(null);
    setSelectedIdx(null);
    setViewingHistory(false);
    setPane("report");
  };

  const handleConnectorReady = (payload: {
    connector_config: Record<string, unknown>;
    file_hash: string;
    dataset_name: string;
  }) => {
    setConnectorConfig(payload.connector_config);
    setUploadMeta(payload.file_hash, payload.dataset_name);
    setDatasetName(payload.dataset_name);
    setConnected(true);
    setViewingHistory(false);
    setPane("chat");
  };

  const handleDeleteSession = async (id: string) => {
    const ok = await deleteSession(id);
    if (ok && sessionId === id) {
      resetWorkspace();
      setConnected(false);
    }
  };

  const handleLoadPrevious = async () => {
    if (sessionId) {
      const snapshot = await getSessionResults(sessionId);
      if (snapshot) hydrateFromSnapshot(snapshot);
    }
    setPendingApproval(null);
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
            onClick={() => historyRef.current?.showModal()}
            aria-label="Open history"
            title="History"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <History className="h-4 w-4" />
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

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(320px,380px)_1fr]">
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
              onClick={() => {
                setConnected(false);
                setViewingHistory(false);
                setPane("report");
              }}
              className="flex-shrink-0 rounded px-1.5 py-0.5 text-primary hover:bg-accent"
            >
              {connected ? "Change" : "Connect"}
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
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
                <ConnectorSetup
                  onConnectorReady={handleConnectorReady}
                  onLoadExistingSession={(id) => void handleLoadSession(id)}
                />
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
        aria-label="History"
        onClick={(e) => e.target === historyRef.current && historyRef.current?.close()}
        className="m-0 h-dvh max-h-dvh w-full max-w-sm bg-sidebar p-0 text-foreground shadow-2xl backdrop:bg-black/40"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">History</h2>
            <button
              type="button"
              onClick={() => historyRef.current?.close()}
              aria-label="Close history"
              className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            <SessionSidebar
              sessions={sessions}
              activeSessionId={sessionId}
              loading={sessionsLoading}
              onLoadSession={handleLoadSession}
              onDeleteSession={handleDeleteSession}
              onNewAnalysis={handleNewAnalysis}
            />
          </div>
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
