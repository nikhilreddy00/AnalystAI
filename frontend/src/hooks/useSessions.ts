"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, deleteSession as apiDeleteSession } from "@/lib/api";
import type { SessionSummary } from "@/types";

export function useSessions() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);

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

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { sessions, loading, refresh, deleteSession };
}
