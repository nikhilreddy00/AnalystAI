"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, deleteSession as apiDeleteSession } from "@/lib/api";
import type { SessionSummary } from "@/types";

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

  return { sessions, loading, refresh, deleteSession };
}
