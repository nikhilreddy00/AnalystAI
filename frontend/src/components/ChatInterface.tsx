"use client";

import { useEffect, useRef, useState } from "react";
import { useAnalysis } from "@/hooks/useAnalysis";
import { useSessions } from "@/hooks/useSessions";
import { useAuth } from "@/context/AuthContext";
import AgentProgressStream from "./AgentProgressStream";
import ApprovalModal from "./ApprovalModal";
import BriefingPanel from "./BriefingPanel";
import ChartRenderer from "./ChartRenderer";
import ConnectorSetup from "./ConnectorSetup";
import ExpertReviewPanel from "./ExpertReviewPanel";
import { SessionSidebar } from "@/components/SessionSidebar";
import InsightPanel from "./InsightPanel";
import EvidencePanel from "./EvidencePanel";
import TargetedResponsePanel from "./TargetedResponsePanel";
import { pinChart, getSessionResults } from "@/lib/api";
import type { ChatMessage } from "@/types";

function MessageBubble({
  message,
  sessionId,
  onFollowUp,
}: {
  message: ChatMessage;
  sessionId: string | null;
  onFollowUp: (q: string) => void;
}) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} gap-3 group`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex-shrink-0 flex items-center justify-center text-xs font-bold text-white mt-1 shadow-lg shadow-indigo-500/20">
          AI
        </div>
      )}
      <div className={`max-w-2xl space-y-3 ${isUser ? "items-end" : "items-start"} flex flex-col`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-lg ${
            isUser
              ? "bg-gradient-to-br from-indigo-600 to-indigo-700 text-white rounded-br-sm shadow-indigo-500/20"
              : "bg-zinc-800/80 backdrop-blur-sm text-zinc-100 rounded-bl-sm border border-zinc-700/50"
          }`}
        >
          {message.content || (message.isStreaming ? (
            <span className="inline-flex gap-1.5 items-center">
              <span className="w-2 h-2 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-2 h-2 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-2 h-2 rounded-full bg-indigo-400 animate-bounce" style={{ animationDelay: "300ms" }} />
            </span>
          ) : "...")}
        </div>

        {message.briefing && !message.isStreaming && (
          <BriefingPanel data={message.briefing} />
        )}

        {message.charts && message.charts.length > 0 && (
          <div className="w-full space-y-3">
            {message.charts.map((chart) => (
              <div key={chart.id} className="relative group/chart">
                <ChartRenderer config={chart} />
                {sessionId && (
                  <button
                    onClick={() => pinChart(sessionId, chart as unknown as object)}
                    className="absolute top-3 right-3 opacity-0 group-hover/chart:opacity-100 transition-all duration-200 text-xs bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white px-3 py-1.5 rounded-lg shadow-lg shadow-indigo-500/30 font-medium"
                  >
                    📌 Pin to Dashboard
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {message.insight && !message.isStreaming && (
          <InsightPanel report={message.insight} onFollowUp={onFollowUp} />
        )}

        {message.evidence && !message.isStreaming && <EvidencePanel items={message.evidence} />}

        {message.expertReview && !message.isStreaming && (
          <ExpertReviewPanel review={message.expertReview} />
        )}

        {message.targetedResponse && !message.isStreaming && (
          <TargetedResponsePanel
            response={message.targetedResponse}
            sessionId={sessionId}
            onFollowUp={onFollowUp}
          />
        )}
      </div>
      {isUser && (
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-zinc-600 to-zinc-700 flex-shrink-0 flex items-center justify-center text-xs font-bold text-zinc-300 mt-1">
          U
        </div>
      )}
    </div>
  );
}

export default function ChatInterface() {
  const { user, logout } = useAuth();
  const {
    messages,
    sessionId,
    isLoading,
    agentEvents,
    pendingApproval,
    setPendingApproval,
    sendMessage,
    setConnectorConfig,
    setUploadMeta,
    hydrateFromSnapshot,
    resetWorkspace,
  } = useAnalysis();

  const { sessions, loading: sessionsLoading, refresh: refreshSessions, deleteSession } = useSessions();

  const handleLoadSession = async (id: string) => {
    const snapshot = await getSessionResults(id);
    if (snapshot) {
      hydrateFromSnapshot(snapshot);
    }
  };

  const handleNewAnalysis = () => {
    resetWorkspace();
    setConnected(false);
  };

  const handleConnectorReady = (payload: {
    connector_config: Record<string, unknown>;
    file_hash: string;
    dataset_name: string;
  }) => {
    setConnectorConfig(payload.connector_config);
    setUploadMeta(payload.file_hash, payload.dataset_name);
    setConnected(true);
  };

  const handleLoadExistingSession = (id: string) => handleLoadSession(id);

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
    const lastUserMessage =
      [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
    setPendingApproval(null);
    if (!lastUserMessage) return;
    void sendMessage(lastUserMessage, true, "rerun_overview");
  };

  const handleCancelApproval = () => {
    setPendingApproval(null);
  };

  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentEvents]);

  useEffect(() => {
    if (!isLoading && sessionId) {
      void refreshSessions();
    }
  }, [isLoading, sessionId, refreshSessions]);

  function handleSend() {
    const text = input.trim();
    if (!text || isLoading) return;
    setInput("");
    sendMessage(text);
  }

  function handleApproval(response: string) {
    setPendingApproval(null);
    sendMessage(response, true);
  }

  const QUICK_PROMPTS = [
    "Give me a complete overview of this dataset",
    "What are the column names and row count?",
    "Show me key trends and patterns",
    "Find anomalies and outliers",
    "What are the top correlations?",
  ];

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
      {/* C2: Sidebar — LIGHT (outputs/navigation lives in light surfaces) */}
      <aside className="w-72 flex-shrink-0 border-r border-zinc-200 flex flex-col bg-white text-zinc-900">
        {/* Logo */}
        <div className="p-5 border-b border-zinc-200">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-lg shadow-lg shadow-indigo-500/30">
              📊
            </div>
            <div>
              <h1 className="text-base font-bold text-zinc-900">AnalystAI</h1>
              <p className="text-xs text-zinc-500">Your AI data analyst</p>
            </div>
          </div>
        </div>

        {/* Connector / Status */}
        <div className="p-4 overflow-y-auto">
          {!connected ? (
            <ConnectorSetup
              onConnectorReady={handleConnectorReady}
              onLoadExistingSession={handleLoadExistingSession}
            />
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2.5 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2.5">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/40 animate-pulse" />
                <span className="text-sm font-medium text-emerald-700">Data source connected</span>
              </div>
              <button
                onClick={() => setConnected(false)}
                className="text-xs text-zinc-500 hover:text-zinc-800 transition-colors underline underline-offset-2"
              >
                Change data source
              </button>
              {sessionId && (
                <div className="text-xs text-zinc-500 font-mono bg-zinc-50 rounded-lg px-2.5 py-2 border border-zinc-200">
                  Session: {sessionId.slice(0, 8)}…
                </div>
              )}
            </div>
          )}
        </div>

        <SessionSidebar
          sessions={sessions}
          activeSessionId={sessionId}
          loading={sessionsLoading}
          onLoadSession={handleLoadSession}
          onDeleteSession={handleDeleteSession}
          onNewAnalysis={handleNewAnalysis}
        />

        {/* Nav links */}
        <div className="p-4 border-t border-zinc-200 space-y-3">
          <a
            href="/dashboard"
            className="flex items-center gap-2.5 text-sm text-zinc-700 hover:text-zinc-900 hover:bg-zinc-100 rounded-xl px-3 py-2.5 transition-all"
          >
            <span>📈</span>
            <span>Dashboard</span>
          </a>

          {user && (
            <div className="pt-2 border-t border-zinc-200 flex flex-col gap-3">
              <div className="flex items-center gap-2.5 px-1">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-100 to-purple-100 border border-indigo-200 flex items-center justify-center text-xs font-bold text-indigo-700">
                  {user.displayName?.slice(0, 2) || "US"}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-zinc-800 truncate">{user.displayName || "Active User"}</p>
                  <p className="text-[10px] text-zinc-500 truncate">{user.email || "user@example.com"}</p>
                </div>
              </div>
              <button
                onClick={() => logout()}
                className="w-full flex items-center justify-center gap-2 py-2 bg-zinc-50 hover:bg-red-50 hover:text-red-600 border border-zinc-200 hover:border-red-200 rounded-xl text-xs font-semibold text-zinc-600 transition-all active:scale-95"
              >
                🚪 Sign Out
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden bg-gradient-to-b from-zinc-950 to-zinc-950">
        {/* Header */}
        <div className="border-b border-zinc-800/60 px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-zinc-200">Analysis Chat</h2>
            <p className="text-xs text-zinc-500">
              {connected ? "Ask anything about your data" : "Connect a data source to begin"}
            </p>
          </div>
          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 rounded-full px-3 py-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
              Analyzing…
            </div>
          )}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.length === 0 && !isLoading && (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-6">
              <div className="relative">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-indigo-500/20 to-purple-600/20 border border-indigo-500/20 flex items-center justify-center text-4xl shadow-2xl shadow-indigo-500/10">
                  📊
                </div>
                <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-xs shadow-lg">
                  ✦
                </div>
              </div>
              <div>
                <h2 className="text-2xl font-bold text-zinc-100 mb-2">
                  {connected ? "What would you like to analyze?" : "Start with your data"}
                </h2>
                <p className="text-zinc-500 text-sm max-w-md">
                  {connected
                    ? "I'll run statistical analysis, find patterns, detect anomalies, and generate visualizations automatically."
                    : "Connect a CSV, SQL database, Google Sheets, or REST API using the panel on the left."}
                </p>
              </div>
              {connected && (
                <div className="grid grid-cols-1 gap-2 w-full max-w-lg">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => sendMessage(prompt)}
                      className="text-left text-sm bg-zinc-800/50 hover:bg-zinc-800 border border-zinc-700/50 hover:border-indigo-500/40 text-zinc-300 hover:text-white px-4 py-3 rounded-xl transition-all duration-200 group"
                    >
                      <span className="text-indigo-400 group-hover:text-indigo-300 mr-2">→</span>
                      {prompt}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map((msg, i) => (
            <MessageBubble
              key={i}
              message={msg}
              sessionId={sessionId}
              onFollowUp={(q) => sendMessage(q)}
            />
          ))}

          {isLoading && (
            <div className="flex justify-start">
              <AgentProgressStream events={agentEvents} isLoading={isLoading} />
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-zinc-800/60 p-4 bg-zinc-950/80 backdrop-blur-sm">
          <div className="flex gap-3 max-w-4xl mx-auto">
            <div className="flex-1 relative">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
                placeholder={
                  connected
                    ? "Ask a question about your data…"
                    : "Connect a data source first…"
                }
                disabled={isLoading || !connected}
                className="w-full bg-zinc-800/80 border border-zinc-700/60 hover:border-zinc-600 focus:border-indigo-500/60 rounded-xl px-4 py-3.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-40 transition-all"
              />
            </div>
            <button
              onClick={handleSend}
              disabled={isLoading || !connected || !input.trim()}
              className="px-5 py-3.5 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 disabled:opacity-40 text-white text-sm font-semibold rounded-xl transition-all duration-200 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 active:scale-95"
            >
              {isLoading ? (
                <span className="flex items-center gap-2">
                  <span className="w-1 h-1 rounded-full bg-white animate-bounce" />
                  <span className="w-1 h-1 rounded-full bg-white animate-bounce" style={{ animationDelay: "100ms" }} />
                  <span className="w-1 h-1 rounded-full bg-white animate-bounce" style={{ animationDelay: "200ms" }} />
                </span>
              ) : "Send"}
            </button>
          </div>
        </div>
      </main>

      {pendingApproval && (
        <ApprovalModal
          event={pendingApproval}
          onSubmit={handleApproval}
          onDismiss={() => setPendingApproval(null)}
          onLoadPrevious={handleLoadPrevious}
          onRunFresh={handleRunFresh}
          onCancel={handleCancelApproval}
        />
      )}
    </div>
  );
}
