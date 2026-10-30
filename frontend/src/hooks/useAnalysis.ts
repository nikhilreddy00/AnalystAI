"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL, downloadLatestReportPptx, getAuthHeaders } from "@/lib/api";
import type { AgentEvent, ApprovalRequiredEvent, BriefingData, ChatMessage, ChartConfig, Evidence, ExpertReview, InsightReport, Run, SessionResultsSnapshot, TargetedResponse } from "@/types";

// Phase 5.6a: a run is a record on the server. POST /chat returns its id; progress streams from
// GET /runs/{id}/events, which replays every event after Last-Event-ID. The active run is kept in
// localStorage, so a refreshed or reopened tab attaches again and rebuilds the same chat.
const ACTIVE_RUN = "analystai:activeRun";
const GATES = ["clarification", "high_null"];
const MAX_RECONNECTS = 8;

async function* parseSSEStream(response: Response): AsyncGenerator<{ id: number | null; event: AgentEvent }> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let id: number | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("id: ")) {
        id = Number(line.slice(4));
        continue;
      }
      if (!line.startsWith("data: ")) continue;
      const raw = line.slice(6).trim();
      if (!raw) continue;
      try {
        yield { id, event: JSON.parse(raw) as AgentEvent };
      } catch {
        // ignore malformed lines
      }
    }
  }
}

function remember(runId: string | null) {
  try {
    if (runId) localStorage.setItem(ACTIVE_RUN, runId);
    else localStorage.removeItem(ACTIVE_RUN);
  } catch {
    // storage blocked (private mode): the run still finishes, a reload just can't reattach
  }
}

function recalled(): string | null {
  try {
    return localStorage.getItem(ACTIVE_RUN);
  } catch {
    return null;
  }
}

async function getRun(runId: string): Promise<Run | null> {
  const res = await fetch(`${API_URL}/runs/${runId}`, { headers: await getAuthHeaders() });
  return res.ok ? res.json() : null;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Artifacts = {
  charts: ChartConfig[];
  insight: InsightReport | null;
  briefing: BriefingData | null;
  expertReview: ExpertReview | null;
  targetedResponse: TargetedResponse | null;
  evidence: Evidence[];
};

const noArtifacts = (): Artifacts => ({
  charts: [], insight: null, briefing: null, expertReview: null, targetedResponse: null, evidence: [],
});

export function useAnalysis() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequiredEvent | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [resumed, setResumed] = useState(false);
  const connectorConfig = useRef<Record<string, unknown> | null>(null);
  const fileHashRef = useRef<string | null>(null);
  const datasetNameRef = useRef<string | null>(null);
  const run = useRef<{ id: string | null; seq: number }>({ id: null, seq: 0 });
  const acc = useRef<Artifacts>(noArtifacts());
  const sending = useRef(false);   // a double click must not post twice

  const patchLast = (patch: (m: ChatMessage) => ChatMessage) =>
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? patch(m) : m)));

  const startBubble = () => {
    acc.current = noArtifacts();
    setMessages((prev) => [...prev, { role: "assistant", content: "", isStreaming: true }]);
  };

  const finish = () => {
    const a = acc.current;
    patchLast((m) => ({
      ...m,
      isStreaming: false,
      briefing: a.briefing ?? undefined,
      charts: a.charts.length > 0 ? a.charts : undefined,
      insight: a.insight ?? undefined,
      expertReview: a.expertReview ?? undefined,
      targetedResponse: a.targetedResponse ?? undefined,
      evidence: a.evidence.length > 0 ? a.evidence : undefined,
    }));
  };

  const fail = (content: string, runId?: string) =>
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.isStreaming) {
        return prev.map((m, i) => (i === prev.length - 1 ? { ...m, content, runId, isStreaming: false } : m));
      }
      return [...prev, { role: "assistant", content, runId }];
    });

  /** One handler for live and replayed events, so a reopened tab renders what a live one did. */
  const handle = useCallback((event: AgentEvent, replay: boolean) => {
    const a = acc.current;
    switch (event.type) {
      case "session_id":
        setSessionId(event.value);
        if (replay) {
          if (!event.is_approval && event.message) {
            setMessages((prev) => [...prev, { role: "user", content: event.message! }]);
          }
          startBubble();
        }
        break;
      case "approval_answered":
        // The gate's answer continues this run in a new bubble (live and on replay alike).
        setPendingApproval(null);
        setAgentEvents([]);
        setIsLoading(true);
        startBubble();
        break;
      case "run_resumed":
        setIsLoading(true);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "assistant" && last.isStreaming) return prev;
          acc.current = noArtifacts();   // a retry after an error starts a fresh answer
          return [...prev, { role: "assistant", content: "", isStreaming: true }];
        });
        setAgentEvents((prev) => [...prev, event]);
        break;
      case "agent_start":
      case "agent_complete":
      case "quality_retry":
        // FEAT-02: a quality retry shows as a banner; the pipeline is still working.
        setAgentEvents((prev) => [...prev, event]);
        break;
      case "briefing":
        a.briefing = event.data;
        break;
      case "export":
        if (event.format === "pptx" && !replay) {
          downloadLatestReportPptx().catch((err) => {
            console.error("PPTX download failed:", err);
          });
        }
        break;
      case "charts":
        a.charts = event.data;
        break;
      case "evidence":
        a.evidence = event.data;
        break;
      case "insight":
        a.insight = event.data;
        patchLast((m) => ({ ...m, content: event.data.executive_summary }));
        break;
      case "expert_review":
        a.expertReview = event.data;
        break;
      case "targeted_response":
        // Phase A: compact answer for targeted intents — answer_text shows in the bubble at once.
        a.targetedResponse = event.data;
        patchLast((m) => ({ ...m, content: event.data.answer_text }));
        break;
      case "approval_required":
        // The run waits here (persisted on the server); the answer continues it.
        finish();
        setPendingApproval(event);
        setIsLoading(false);
        break;
      case "suggested_questions":
        setSuggestedQuestions(event.questions);
        break;
      case "error":
        patchLast((m) => ({ ...m, content: `Error: ${event.message}`, isStreaming: false,
                            runId: event.retryable === false ? undefined : run.current.id ?? undefined }));
        break;
      case "done":
        finish();
        setIsLoading(false);
        break;
    }
    // The helpers it calls only use state setters and refs, which never change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Stream a run's events after `after`, reconnecting with Last-Event-ID until it stops. */
  const attach = useCallback(async (runId: string, after: number, replay: boolean) => {
    run.current = { id: runId, seq: after };
    remember(runId);
    let failures = 0;
    while (run.current.id === runId) {
      try {
        const seq = run.current.seq;
        const res = await fetch(`${API_URL}/runs/${runId}/events`, {
          headers: await getAuthHeaders(seq > 0 ? { "Last-Event-ID": String(seq) } : {}),
        });
        if (res.status === 404) {
          remember(null);
          setIsLoading(false);
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        for await (const { id, event } of parseSSEStream(res)) {
          if (run.current.id !== runId) return;   // the user moved on to another run
          if (id !== null) {
            if (id <= run.current.seq) continue;  // already shown: every event appears once
            run.current.seq = id;
          }
          failures = 0;
          handle(event, replay);
        }
        const status = (await getRun(runId))?.status;
        if (status === "queued" || status === "running") continue;   // cut short: reconnect
        if (status !== "waiting_approval") remember(null);
        return;
      } catch (err) {
        if (++failures > MAX_RECONNECTS) {
          fail(`Connection error: ${err}`);
          setIsLoading(false);
          return;
        }
        await sleep(Math.min(500 * 2 ** failures, 8000));
      }
    }
  }, [handle]);

  // A refreshed or reopened tab picks up its run where the server has it. `live` stops a
  // second mount (React dev mounts twice) from starting a second replay of the same run.
  useEffect(() => {
    const runId = recalled();
    if (!runId) return;
    let live = true;
    void (async () => {
      const r = await getRun(runId);
      if (!live) return;
      if (!r) {
        remember(null);
        return;
      }
      setResumed(true);
      setSessionId(r.session_id);
      setIsLoading(r.status === "queued" || r.status === "running");
      await attach(runId, 0, true);
    })();
    return () => {
      live = false;
    };
  }, [attach]);

  const sendMessage = useCallback(
    async (content: string, isApproval = false, approvalContext: string | null = null) => {
      if (sending.current) return;
      sending.current = true;
      setIsLoading(true);
      setPendingApproval(null);
      // A gate's answer continues the waiting run; its bubble opens on approval_answered.
      const gateAnswer = isApproval && GATES.includes(approvalContext ?? "");
      if (!gateAnswer) {
        setAgentEvents([]);
        if (!isApproval) setMessages((prev) => [...prev, { role: "user", content }]);
        startBubble();
      }

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
            client_request_id: crypto.randomUUID(),   // a double submit starts one run
          }),
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => null))?.detail;
          throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
        }
        const r: Run = await res.json();
        setSessionId(r.session_id);
        sending.current = false;   // posted: the run exists, the next question may follow it
        const sameRun = gateAnswer && run.current.id === r.run_id;
        await attach(r.run_id, sameRun ? run.current.seq : 0, false);
      } catch (err) {
        fail(`Error: ${err instanceof Error ? err.message : err}`);
        setIsLoading(false);
      } finally {
        sending.current = false;
      }
    },
    [sessionId, attach]
  );

  const cancelRun = useCallback(async () => {
    const id = run.current.id;
    if (!id) return;
    await fetch(`${API_URL}/runs/${id}/cancel`, { method: "POST", headers: await getAuthHeaders() });
  }, []);

  /** "Retry from where it stopped": the run resumes from its last finished step. */
  const retryRun = useCallback(async (runId: string) => {
    setIsLoading(true);
    setAgentEvents([]);
    const res = await fetch(`${API_URL}/runs/${runId}/retry`, { method: "POST", headers: await getAuthHeaders() });
    if (!res.ok) {
      const detail = (await res.json().catch(() => null))?.detail;
      fail(`Error: ${typeof detail === "string" ? detail : `HTTP ${res.status}`}`);
      setIsLoading(false);
      return;
    }
    await attach(runId, run.current.id === runId ? run.current.seq : 0, false);
  }, [attach]);

  const hydrateFromSnapshot = useCallback((snapshot: SessionResultsSnapshot) => {
    run.current = { id: null, seq: 0 };
    remember(null);
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
          evidence: snapshot.evidence?.length ? snapshot.evidence : undefined,
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
    resumed,
    sendMessage,
    cancelRun,
    retryRun,
    setConnectorConfig: (cfg: Record<string, unknown>) => {
      connectorConfig.current = cfg;
    },
    setUploadMeta: (hash: string | null, name: string | null) => {
      fileHashRef.current = hash;
      datasetNameRef.current = name;
    },
    hydrateFromSnapshot,
    resetWorkspace: () => {
      run.current = { id: null, seq: 0 };
      remember(null);
      setMessages([]);
      setSessionId(null);
      setIsLoading(false);
      setAgentEvents([]);
      setPendingApproval(null);
      setSuggestedQuestions([]);
      setResumed(false);
      connectorConfig.current = null;
      fileHashRef.current = null;
      datasetNameRef.current = null;
    },
  };
}
