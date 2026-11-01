"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, deleteSession as apiDeleteSession, patchSession, type ThreadPatch } from "@/lib/api";
import type { SessionSummary } from "@/types";

const POLL_MS = 5000;
const RUNNING = ["queued", "running"];

export function useSessions() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  /** The last read failed: what is shown may be old, and an empty list means "unknown". */
  const [failed, setFailed] = useState(false);

  // A failed read keeps the list it had: a blip must not empty the sidebar.
  const read = useCallback(async () => {
    try {
      const s = await listSessions();
      setSessions(s);
      setFailed(false);
      return true;
    } catch {
      setFailed(true);
      return false;
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      await read();
    } finally {
      setLoading(false);
    }
  }, [read]);

  const deleteSession = useCallback(
    async (id: string) => {
      const ok = await apiDeleteSession(id).catch(() => false);
      if (ok) {
        setSessions((prev) => prev.filter((s) => s.session_id !== id));
      }
      return ok;
    },
    [],
  );

  /** Rename / pin / archive. Shown at once; if the server refuses, the list is read again (a
   *  snapshot taken before would undo another change made meanwhile), or, if that fails too,
   *  just the patched fields are put back. */
  const updateSession = useCallback(async (id: string, patch: ThreadPatch) => {
    let before: Partial<SessionSummary> = {};
    setSessions((prev) => prev.map((s) => {
      if (s.session_id !== id) return s;
      before = Object.fromEntries(Object.keys(patch).map((k) => [k, s[k as keyof ThreadPatch]]));
      return { ...s, ...patch };
    }));
    const saved = await patchSession(id, patch).catch(() => null);
    if (saved) {
      setSessions((prev) => prev.map((s) => (s.session_id === id ? { ...s, ...saved } : s)));
      return true;
    }
    if (!(await read())) {
      setSessions((prev) => prev.map((s) => (s.session_id === id ? { ...s, ...before } : s)));
    }
    return false;
  }, [read]);

  // Initial load: state is set only in the promise callbacks, never synchronously.
  useEffect(() => {
    let live = true;
    listSessions()
      .then((s) => live && setSessions(s))
      .catch(() => live && setFailed(true))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  // A status dot that says "running" keeps itself current; nothing polls when nothing runs.
  const anyRunning = sessions.some((s) => RUNNING.includes(s.run_status ?? ""));
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => void read(), POLL_MS);
    return () => clearInterval(t);
  }, [anyRunning, read]);

  return { sessions, loading, failed, refresh, deleteSession, updateSession };
}
