"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, deleteSession as apiDeleteSession, patchSession, type ThreadPatch } from "@/lib/api";
import type { SessionSummary } from "@/types";

const POLL_MS = 5000;
const RUNNING = ["queued", "running"];

export function useSessions() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await listSessions());
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteSession = useCallback(
    async (id: string) => {
      const ok = await apiDeleteSession(id);
      if (ok) {
        setSessions((prev) => prev.filter((s) => s.session_id !== id));
      }
      return ok;
    },
    [],
  );

  /** Rename / pin / archive. Shown at once; put back if the server refuses. */
  const updateSession = useCallback(async (id: string, patch: ThreadPatch) => {
    let before: SessionSummary | undefined;
    setSessions((prev) => prev.map((s) => {
      if (s.session_id !== id) return s;
      before = s;
      return { ...s, ...patch };
    }));
    const saved = await patchSession(id, patch);
    setSessions((prev) => prev.map((s) => (s.session_id !== id ? s : saved ? { ...s, ...saved } : before ?? s)));
    return !!saved;
  }, []);

  // Initial load: state is set only in the promise callbacks, never synchronously.
  useEffect(() => {
    let live = true;
    listSessions()
      .then((s) => live && setSessions(s))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  // A status dot that says "running" keeps itself current; nothing polls when nothing runs.
  const anyRunning = sessions.some((s) => RUNNING.includes(s.run_status ?? ""));
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => {
      // listSessions() is [] on a network error; while a thread is running the list is never
      // really empty, so an empty answer is a blip, not "everything was deleted".
      listSessions().then((s) => s.length && setSessions(s)).catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(t);
  }, [anyRunning]);

  return { sessions, loading, refresh, deleteSession, updateSession };
}
