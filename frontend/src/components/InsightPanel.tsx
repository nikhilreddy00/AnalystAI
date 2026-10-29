"use client";

import { useState } from "react";
import type { ExecutiveSummary, AnalystDetail, InsightReport, RecommendedAction, AnomalyAlert, Claim } from "@/types";

// PROMPT-3: tab toggle now renders two strictly-separated content blocks.
// Executive: plain English, business framing, no stats notation.
// Analyst:   methodology, statistical notes, caveats, code references.

// Severity is carried by a stripe colour and its label, never colour alone.
const SEVERITY = {
  low: "border-l-muted-foreground/50",
  medium: "border-l-warning",
  high: "border-l-warning",
  critical: "border-l-destructive",
};

interface Props {
  report: InsightReport;
  onFollowUp?: (question: string) => void;
  /** Phase 5: a finding's evidence chip opens that Evidence item. */
  onEvidence?: (id: string) => void;
}

export default function InsightPanel({ report, onFollowUp, onEvidence }: Props) {
  const [view, setView] = useState<"executive" | "analyst">("executive");

  return (
    <section className="space-y-4" aria-label="Analysis report">
      <Header view={view} onChange={setView} />
      {view === "executive" ? (
        <ExecutiveView report={report} onFollowUp={onFollowUp} onEvidence={onEvidence} />
      ) : (
        <AnalystView report={report} />
      )}
    </section>
  );
}

function Header({ view, onChange }: { view: "executive" | "analyst"; onChange: (v: "executive" | "analyst") => void }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Report</h3>
      <div className="flex rounded-md border border-border p-0.5 gap-0.5 bg-muted" role="group" aria-label="Report view">
        {(["executive", "analyst"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            aria-pressed={view === v}
            className={`px-2.5 py-1 text-xs font-medium rounded ${
              view === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {v === "executive" ? "Executive" : "Analyst"}
          </button>
        ))}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{children}</h4>
  );
}

function Alerts({ report, detail }: { report: InsightReport; detail?: boolean }) {
  if (report.anomaly_alerts.length === 0) return null;
  return (
    <div className="space-y-2">
      <SectionLabel>{detail ? "Anomaly detail" : "Alerts"}</SectionLabel>
      {report.anomaly_alerts.map((alert: AnomalyAlert, i: number) => (
        <div key={i} className={`rounded-md border border-border border-l-[3px] bg-card px-3 py-2.5 space-y-1 ${SEVERITY[alert.severity]}`}>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-sm font-medium text-foreground">{alert.title}</span>
            <span className="text-xs text-muted-foreground">{alert.severity}</span>
          </div>
          <p className="text-sm text-muted-foreground">{alert.description}</p>
          {detail && (
            <p className="text-xs text-muted-foreground">
              Metric: <span className="text-foreground font-mono">{alert.affected_metric}</span>
            </p>
          )}
          {alert.suggested_investigation && (
            <p className="text-sm text-foreground">Next: {alert.suggested_investigation}</p>
          )}
        </div>
      ))}
      {report.additional_issues_note && (
        <p className="text-xs text-muted-foreground">{report.additional_issues_note}</p>
      )}
    </div>
  );
}

// The label a reader understands, from the evidence id's prefix (agents/analysis_agent.py ids).
const SOURCE: Record<string, string> = {
  corr: "Correlation",
  trend: "Trend",
  seg: "Breakdown",
  anom: "Anomalies",
  test: "Test",
  eda: "Summary stats",
  clean: "Cleaning",
  query: "SQL query",
};

function EvidenceChips({ ids, onEvidence }: { ids?: string[]; onEvidence?: (id: string) => void }) {
  if (!onEvidence || !ids?.length) return null;
  return (
    <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
      {ids.map((id, i) => {
        const kind = id.split(":")[0];
        // Number repeated kinds ("Trend 1", "Trend 2") so the chips can be told apart.
        const same = ids.filter((x) => x.split(":")[0] === kind);
        const label = SOURCE[kind] ?? "Evidence";
        return (
          <button
            key={id}
            type="button"
            onClick={() => onEvidence(id)}
            aria-label={`Open evidence ${id}`}
            className="rounded border border-border px-1.5 py-px text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {same.length > 1 ? `${label} ${ids.slice(0, i + 1).filter((x) => x.split(":")[0] === kind).length}` : label}
          </button>
        );
      })}
    </span>
  );
}

// Sessions stored before Phase 5 hold plain strings.
const text = (c: Claim | string) => (typeof c === "string" ? c : c.text);
const ids = (c: Claim | string) => (typeof c === "string" ? [] : c.evidence_ids);

function ExecutiveView({ report, onFollowUp, onEvidence }: { report: InsightReport; onFollowUp?: (q: string) => void; onEvidence?: (id: string) => void }) {
  const ev: ExecutiveSummary = report.executive_view ?? {
    headline: "",
    paragraphs: report.executive_summary ? [{ text: report.executive_summary, evidence_ids: [] }] : [],
    findings: [],
    action_items: report.recommended_actions.map((a: RecommendedAction) => ({
      action: a.action,
      expected_impact: a.metric_impact ?? a.reasoning ?? "",
    })),
  };

  return (
    <div className="space-y-5">
      {(ev.headline || ev.paragraphs.length > 0) && (
        <div className="space-y-2">
          {ev.headline && (
            <p className="text-lg font-semibold leading-snug text-foreground text-balance">{ev.headline}</p>
          )}
          {ev.paragraphs.length > 0 && (
            <p className="text-sm leading-relaxed text-muted-foreground max-w-[68ch]">{ev.paragraphs.map(text).join(" ")}</p>
          )}
        </div>
      )}

      {ev.findings.length > 0 && (
        <div className="space-y-1">
          <SectionLabel>Key findings</SectionLabel>
          <ul className="divide-y divide-border">
            {ev.findings.map((f, i) => (
              <li key={i} className="py-2 text-sm text-foreground leading-relaxed">
                {text(f)}
                <EvidenceChips ids={ids(f)} onEvidence={onEvidence} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <Alerts report={report} />

      {/* Ordered by priority, so the numbers carry meaning. */}
      {ev.action_items.length > 0 && (
        <div className="space-y-2">
          <SectionLabel>Recommended actions</SectionLabel>
          <ol className="space-y-2">
            {ev.action_items.map((item, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="flex-shrink-0 w-5 h-5 rounded bg-brand-soft text-primary text-xs flex items-center justify-center font-semibold font-mono">
                  {i + 1}
                </span>
                <div className="space-y-0.5">
                  <p className="text-foreground font-medium leading-snug">
                    {item.action}
                    <EvidenceChips ids={item.evidence_ids} onEvidence={onEvidence} />
                  </p>
                  {item.expected_impact && <p className="text-muted-foreground text-xs">{item.expected_impact}</p>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {onFollowUp && report.proactive_questions.length > 0 && (
        <div className="space-y-2">
          <SectionLabel>Suggested follow-ups</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {report.proactive_questions.map((q: string, i: number) => (
              <button
                key={i}
                type="button"
                onClick={() => onFollowUp(q)}
                className="text-left text-xs text-foreground bg-card hover:bg-accent border border-border rounded-full px-3 py-1.5"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AnalystView({ report }: { report: InsightReport }) {
  const ay: AnalystDetail = report.analyst_view ?? {
    methodology: report.methodology_notes ?? "",
    statistical_notes: report.technical_findings ?? [],
    caveats: [],
    code_references: [],
  };

  return (
    <div className="space-y-5">
      {/* Statistical notes */}
      {ay.statistical_notes.length > 0 && (
        <div className="space-y-2">
          <SectionLabel>Statistical findings</SectionLabel>
          <ul className="divide-y divide-border">
            {ay.statistical_notes.map((s: string, i: number) => (
              <li key={i} className="text-[13px] text-foreground font-mono leading-relaxed py-1.5">{s}</li>
            ))}
          </ul>
        </div>
      )}

      <Alerts report={report} detail />

      {/* Methodology */}
      {ay.methodology && (
        <div className="space-y-1.5">
          <SectionLabel>Methodology</SectionLabel>
          <p className="text-sm text-foreground leading-relaxed max-w-[68ch]">
            {ay.methodology}
          </p>
        </div>
      )}

      {/* Caveats */}
      {ay.caveats.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel>Caveats &amp; assumptions</SectionLabel>
          <ul className="list-disc pl-5 space-y-1">
            {ay.caveats.map((c: string, i: number) => (
              <li key={i} className="text-sm text-foreground leading-relaxed">
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Code references — dark block with comment/code split for legibility. */}
      {ay.code_references.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel>Reproducibility</SectionLabel>
          <ul className="space-y-1.5">
            {ay.code_references.map((c: string, i: number) => (
              <li
                key={i}
                className="text-xs leading-relaxed bg-muted rounded-md px-3 py-2 font-mono whitespace-pre-wrap overflow-x-auto"
              >
                {c.split("\n").map((line, j) => {
                  const isComment = line.trimStart().startsWith("#");
                  return (
                    <div
                      key={j}
                      className={isComment ? "text-muted-foreground" : "text-foreground"}
                    >
                      {line || " "}
                    </div>
                  );
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
