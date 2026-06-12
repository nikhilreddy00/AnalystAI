"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getLatestDashboardData } from "@/lib/api";
import type { ColumnRoles } from "@/types";

type Status = "loading" | "ok" | "empty" | "too_large" | "error";

interface DashboardDataValue {
  status: Status;
  detail: string | null;
  rows: Record<string, unknown>[];
  columnRoles: ColumnRoles | null;
  sessionId: string | null;
  rowCount: number;
}

const Ctx = createContext<DashboardDataValue | null>(null);

const INITIAL: DashboardDataValue = {
  status: "loading",
  detail: null,
  rows: [],
  columnRoles: null,
  sessionId: null,
  rowCount: 0,
};

export function DashboardDataProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DashboardDataValue>(INITIAL);

  useEffect(() => {
    let cancelled = false;
    void getLatestDashboardData().then((result) => {
      if (cancelled) return;
      if (result.status === "ok") {
        setState({
          status: "ok",
          detail: null,
          rows: result.payload.rows,
          columnRoles: result.payload.column_roles,
          sessionId: result.payload.session_id,
          rowCount: result.payload.row_count,
        });
      } else if (result.status === "empty") {
        setState({ ...INITIAL, status: "empty" });
      } else {
        setState({ ...INITIAL, status: result.status, detail: result.detail });
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function useDashboardData(): DashboardDataValue {
  const v = useContext(Ctx);
  if (!v)
    throw new Error("useDashboardData must be used inside DashboardDataProvider");
  return v;
}
