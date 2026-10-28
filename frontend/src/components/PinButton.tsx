"use client";

import { useState } from "react";
import { Check, Pin } from "lucide-react";
import { pinChart } from "@/lib/api";
import type { ChartConfig } from "@/types";

/** Pins a chart to the dashboard and says so; failures are shown, not swallowed. */
export default function PinButton({ sessionId, chart }: { sessionId: string; chart: ChartConfig }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");

  async function pin() {
    setState("busy");
    try {
      await pinChart(sessionId, chart as unknown as object);
      setState("done");
    } catch {
      setState("error");
    }
  }

  return (
    <button
      type="button"
      onClick={pin}
      disabled={state === "busy" || state === "done"}
      className="inline-flex flex-shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-70"
    >
      {state === "done" ? <Check className="h-3.5 w-3.5 text-success" /> : <Pin className="h-3.5 w-3.5" />}
      {state === "done" ? "Pinned" : state === "error" ? "Pin failed, retry" : "Pin"}
    </button>
  );
}
