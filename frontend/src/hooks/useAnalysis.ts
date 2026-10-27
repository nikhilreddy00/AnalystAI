"use client";

import { useCallback, useRef, useState } from "react";
import { API_URL, downloadLatestReportPptx, getAuthHeaders } from "@/lib/api";
import type { AgentEvent, ApprovalRequiredEvent, BriefingData, ChatMessage, ChartConfig, ExpertReview, InsightReport, SessionResultsSnapshot, TargetedResponse } from "@/types";

async function* parseSSEStream(response: Response): AsyncGenerator<AgentEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const raw = line.slice(6).trim();
      if (!raw) continue;
      try {
        yield JSON.parse(raw) as AgentEvent;
      } catch {
        // ignore malformed lines
      }
    }
  }
}

export function useAnalysis() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequiredEvent | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const connectorConfig = useRef<Record<string, unknown> | null>(null);
  const fileHashRef = useRef<string | null>(null);
  const datasetNameRef = useRef<string | null>(null);

  const sendMessage = useCallback(
    async (content: string, isApproval = false, approvalContext: string | null = null) => {
      setIsLoading(true);
      setAgentEvents([]);
      setPendingApproval(null);

      if (!isApproval) {
        setMessages((prev) => [...prev, { role: "user", content }]);
      }

      let charts: ChartConfig[] = [];
      let insight: InsightReport | null = null;
      let briefing: BriefingData | null = null;
      let expertReview: ExpertReview | null = null;
      let targetedResponse: TargetedResponse | null = null;
      const assistantMsg: ChatMessage = { role: "assistant", content: "", isStreaming: true };
      setMessages((prev) => [...prev, assistantMsg]);

      try {
        const res = await fetch(`${API_URL}/chat`, {
          method: "POST",
          headers: await getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            message: content,
            session_id: sessionId,
            is_approval: isApproval,
            approval_context: approvalContext,
            connector_config: connectorConfig.current,
            file_hash: fileHashRef.current,
            dataset_name: datasetNameRef.current,
          }),
        });

        for await (const event of parseSSEStream(res)) {
          switch (event.type) {
            case "session_id":
              setSessionId(event.value);
              break;
            case "agent_start":
            case "agent_complete":
              setAgentEvents((prev) => [...prev, event]);
              break;
            case "briefing":
              briefing = event.data;
              break;
            case "export":
              if (event.format === "pptx") {
                downloadLatestReportPptx().catch((err) => {
                  console.error("PPTX download failed:", err);
                });
              }
              break;
            case "charts":
              charts = event.data;
              break;
            case "insight":
              insight = event.data;
              setMessages((prev) =>
                prev.map((m, i) =>
                  i === prev.length - 1
                    ? { ...m, content: insight!.executive_summary }
                    : m
                )
              );
              break;
            case "expert_review":
              // Phase 6: senior-expert critique fires only on first-run auto-overview
              expertReview = event.data;
              break;
            case "targeted_response":
              // Phase A: compact answer for targeted intents — surfaces answer_text
              // immediately in the bubble; chart/table render below via TargetedResponsePanel.
              targetedResponse = event.data;
              setMessages((prev) =>
                prev.map((m, i) =>
                  i === prev.length - 1
                    ? { ...m, content: targetedResponse!.answer_text }
                    : m
                )
              );
              break;
            case "approval_required":
              setPendingApproval(event);
              // BUG-10: Allow user to interact with approval modal / dismiss it
              setIsLoading(false);
              break;
            case "suggested_questions":
              // FEAT-01: Handle proactive question suggestions
              setSuggestedQuestions(event.questions);
              break;
            case "quality_retry":
              // FEAT-02: quality_check rejected the report and is re-running analysis.
              // Push into agentEvents so the progress stream can show a retry banner;
              // intentionally leave isLoading=true since pipeline is still working.
              setAgentEvents((prev) => [...prev, event]);
              break;
            case "error":
              setMessages((prev) =>
                prev.map((m, i) =>
                  i === prev.length - 1
                    ? { ...m, content: `Error: ${event.message}`, isStreaming: false }
                    : m
                )
              );
              break;
            case "done":
              setMessages((prev) =>
                prev.map((m, i) =>
                  i === prev.length - 1
                    ? {
                        ...m,
                        isStreaming: false,
                        briefing: briefing ?? undefined,
                        charts: charts.length > 0 ? charts : undefined,
                        insight: insight ?? undefined,
                        expertReview: expertReview ?? undefined,
                        targetedResponse: targetedResponse ?? undefined,
                      }
                    : m
                )
              );
              setIsLoading(false);
              break;
          }
        }
      } catch (err) {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? { ...m, content: `Connection error: ${err}`, isStreaming: false }
              : m
          )
        );
        setIsLoading(false);
      }
    },
    [sessionId]
  );

  const hydrateFromSnapshot = useCallback((snapshot: SessionResultsSnapshot) => {
    setSessionId(snapshot.session_id);
    setIsLoading(false);
    setPendingApproval(null);
    setSuggestedQuestions([]);
    setAgentEvents([]);

    // Replay conversation as immutable history; attach the final analysis artifacts
    // to the most recent assistant message so the same panels render as a live run.
    const replay: ChatMessage[] = snapshot.conversation_history.map((m) => ({
      role: m.role === "user" ? "user" : "assistant",
      content: m.content,
    }));

    if (replay.length > 0) {
      let lastAssistantIdx = -1;
      for (let i = replay.length - 1; i >= 0; i--) {
        if (replay[i].role === "assistant") {
          lastAssistantIdx = i;
          break;
        }
      }
      if (lastAssistantIdx >= 0) {
        replay[lastAssistantIdx] = {
          ...replay[lastAssistantIdx],
          charts: snapshot.chart_configs.length ? snapshot.chart_configs : undefined,
          insight: snapshot.insight_report ?? undefined,
          expertReview: snapshot.expert_review ?? undefined,
          targetedResponse: snapshot.targeted_response ?? undefined,
        };
      }
    }
    setMessages(replay);
  }, []);

  return {
    messages,
    sessionId,
    isLoading,
    agentEvents,
    pendingApproval,
    setPendingApproval,
    suggestedQuestions,
    sendMessage,
    setConnectorConfig: (cfg: Record<string, unknown>) => {
      connectorConfig.current = cfg;
    },
    setUploadMeta: (hash: string | null, name: string | null) => {
      fileHashRef.current = hash;
      datasetNameRef.current = name;
    },
    hydrateFromSnapshot,
    resetWorkspace: () => {
      setMessages([]);
      setSessionId(null);
      setIsLoading(false);
      setAgentEvents([]);
      setPendingApproval(null);
      setSuggestedQuestions([]);
      connectorConfig.current = null;
      fileHashRef.current = null;
      datasetNameRef.current = null;
    },
  };
}
