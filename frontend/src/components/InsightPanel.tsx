"use client";

import { useState } from "react";
import type { ExecutiveSummary, AnalystDetail, InsightReport, RecommendedAction, AnomalyAlert } from "@/types";

// PROMPT-3: tab toggle now renders two strictly-separated content blocks.
// Executive: plain English, business framing, no stats notation.
// Analyst:   methodology, statistical notes, caveats, code references.

const SEVERITY = {
  low: { bg: "bg-zinc-50 border-zinc-200", badge: "bg-zinc-100 text-zinc-700", dot: "bg-zinc-400" },
  medium: { bg: "bg-amber-50 border-amber-200", badge: "bg-amber-100 text-amber-800", dot: "bg-amber-500" },
  high: { bg: "bg-orange-50 border-orange-200", badge: "bg-orange-100 text-orange-800", dot: "bg-orange-500" },
  critical: { bg: "bg-red-50 border-red-200", badge: "bg-red-100 text-red-800", dot: "bg-red-500 animate-pulse" },
};

interface Props {
  report: InsightReport;
  onFollowUp?: (question: string) => void;
}

export default function InsightPanel({ report, onFollowUp }: Props) {
  const [view, setView] = useState<"executive" | "analyst">("executive");

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-5 space-y-4 max-w-2xl shadow-sm">
      <Header view={view} onChange={setView} />
      {view === "executive" ? (
        <ExecutiveView report={report} onFollowUp={onFollowUp} />
      ) : (
        <AnalystView report={report} />
      )}
    </div>
  );
}

function Header({ view, onChange }: { view: "executive" | "analyst"; onChange: (v: "executive" | "analyst") => void }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-base text-indigo-600">✦</span>
        <span className="text-xs font-semibold text-zinc-700 uppercase tracking-widest">Analysis Report</span>
      </div>
      <div className="flex rounded-xl overflow-hidden border border-zinc-200 p-0.5 gap-0.5 bg-zinc-50">
        {(["executive", "analyst"] as const).map((v) => (
          <button
            key={v}
            onClick={() => onChange(v)}
            className={`px-3 py-1 text-xs font-medium rounded-lg transition-all duration-200 ${
              view === v
                ? "bg-gradient-to-r from-indigo-600 to-purple-600 text-white shadow-sm"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            {v === "executive" ? "Executive" : "Analyst"}
          </button>
        ))}
      </div>
    </div>
  );
}

function SectionLabel({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest flex items-center gap-1.5">
      <span>{icon}</span> {children}
    </h4>
  );
}

function ExecutiveView({ report, onFollowUp }: { report: InsightReport; onFollowUp?: (q: string) => void }) {
  const ev: ExecutiveSummary = report.executive_view ?? {
    headline: "",
    paragraphs: report.executive_summary ? [report.executive_summary] : [],
    findings: [],
    action_items: report.recommended_actions.map((a: RecommendedAction) => ({
      action: a.action,
      expected_impact: a.metric_impact ?? a.reasoning ?? "",
    })),
  };

  return (
    <div className="space-y-4">
      {/* Headline + paragraphs */}
      {(ev.headline || ev.paragraphs.length > 0) && (
        <div className="bg-gradient-to-br from-indigo-50 to-purple-50 border border-indigo-100 rounded-xl p-4 space-y-2">
          {ev.headline && (
            <p className="text-zinc-900 text-sm font-semibold leading-snug">{ev.headline}</p>
          )}
          {ev.paragraphs.length > 0 && (
            <p className="text-zinc-700 text-sm leading-relaxed">{ev.paragraphs.join(" ")}</p>
          )}
        </div>
      )}

      {/* Plain-language findings */}
      {ev.findings.length > 0 && (
        <div className="space-y-2">
          <SectionLabel icon="✓">Key findings</SectionLabel>
          <ul className="space-y-1.5">
            {ev.findings.map((f, i) => (
              <li key={i} className="text-sm text-zinc-800 flex gap-2.5 bg-zinc-50 rounded-xl px-3 py-2.5 border border-zinc-200">
                <span className="text-indigo-600 mt-0.5 flex-shrink-0 font-bold">·</span>
                <span className="leading-relaxed">{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Alerts — PROMPT-4: scannable format, max 5, severity-sorted */}
      {report.anomaly_alerts.length > 0 && (
        <div className="space-y-2">
          <SectionLabel icon="⚠">Alerts</SectionLabel>
          {report.anomaly_alerts.map((alert: AnomalyAlert, i: number) => {
            const s = SEVERITY[alert.severity];
            return (
              <div key={i} className={`rounded-xl border p-3 space-y-1 ${s.bg}`}>
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${s.dot}`} />
                  <span className="text-sm font-semibold text-zinc-900 truncate">{alert.title}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-md font-medium ${s.badge}`}>{alert.severity}</span>
                </div>
                <p className="text-xs text-zinc-700 leading-relaxed pl-4">{alert.description}</p>
                {alert.suggested_investigation && (
                  <p className="text-xs text-zinc-900 leading-relaxed pl-4">
                    <span className="font-semibold text-indigo-700">→ </span>{alert.suggested_investigation}
                  </p>
                )}
              </div>
            );
          })}
          {report.additional_issues_note && (
            <p className="text-xs text-zinc-500 italic pl-1">
              {report.additional_issues_note}
            </p>
          )}
        </div>
      )}

      {/* Action items */}
      {ev.action_items.length > 0 && (
        <div className="space-y-2">
          <SectionLabel icon="⚡">Recommended actions</SectionLabel>
          {ev.action_items.map((item, i) => (
            <div key={i} className="flex gap-3 text-sm bg-zinc-50 rounded-xl p-3 border border-zinc-200">
              <span className="flex-shrink-0 w-5 h-5 rounded-lg bg-gradient-to-br from-indigo-600 to-purple-600 text-white text-xs flex items-center justify-center font-bold shadow-sm">
                {i + 1}
              </span>
              <div className="space-y-0.5">
                <p className="text-zinc-900 font-medium leading-tight">{item.action}</p>
                {item.expected_impact && (
                  <p className="text-emerald-600 text-xs font-medium">↑ {item.expected_impact}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Follow-ups */}
      {report.proactive_questions.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel icon="💬">Suggested follow-ups</SectionLabel>
          {report.proactive_questions.map((q: string, i: number) => (
            <button
              key={i}
              onClick={() => onFollowUp?.(q)}
              className="block w-full text-left text-xs text-indigo-700 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 border border-indigo-100 hover:border-indigo-200 rounded-xl px-3.5 py-2.5 transition-all duration-200 group"
            >
              <span className="text-indigo-500 group-hover:text-indigo-600 mr-2">→</span>
              {q}
            </button>
          ))}
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
    <div className="space-y-4">
      {/* Statistical notes */}
      {ay.statistical_notes.length > 0 && (
        <div className="space-y-2">
          <SectionLabel icon="📐">Statistical findings</SectionLabel>
          <ul className="space-y-2">
            {ay.statistical_notes.map((s: string, i: number) => (
              <li key={i} className="text-sm text-zinc-800 flex gap-2.5 bg-zinc-50 rounded-xl px-3 py-2.5 border border-zinc-200 font-mono">
                <span className="text-indigo-600 mt-0.5 flex-shrink-0 font-bold not-italic">▸</span>
                <span className="leading-relaxed">{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Anomaly detail */}
      {report.anomaly_alerts.length > 0 && (
        <div className="space-y-2">
          <SectionLabel icon="🔍">Anomaly detail</SectionLabel>
          {report.anomaly_alerts.map((alert: AnomalyAlert, i: number) => {
            const s = SEVERITY[alert.severity];
            return (
              <div key={i} className={`rounded-xl border p-3.5 space-y-2 ${s.bg}`}>
                <div className="flex items-center gap-2">
                  <span className={`text-xs px-1.5 py-0.5 rounded-md font-medium ${s.badge}`}>{alert.severity}</span>
                  <span className="text-zinc-900 font-semibold text-sm">{alert.title}</span>
                </div>
                <p className="text-zinc-700 text-xs leading-relaxed">{alert.description}</p>
                <p className="text-xs text-zinc-500">
                  Metric: <span className="text-zinc-800 font-mono">{alert.affected_metric}</span>
                </p>
                <p className="text-xs text-zinc-500">
                  Investigate: <span className="text-zinc-800">{alert.suggested_investigation}</span>
                </p>
              </div>
            );
          })}
          {report.additional_issues_note && (
            <p className="text-xs text-zinc-500 italic pl-1">
              {report.additional_issues_note}
            </p>
          )}
        </div>
      )}

      {/* Methodology */}
      {ay.methodology && (
        <div className="space-y-1.5">
          <SectionLabel icon="🔬">Methodology</SectionLabel>
          <p className="text-xs text-zinc-700 leading-relaxed bg-zinc-50 rounded-xl px-3.5 py-3 border border-zinc-200">
            {ay.methodology}
          </p>
        </div>
      )}

      {/* Caveats */}
      {ay.caveats.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel icon="⚠️">Caveats &amp; assumptions</SectionLabel>
          <ul className="space-y-1">
            {ay.caveats.map((c: string, i: number) => (
              <li key={i} className="text-xs text-zinc-700 leading-relaxed bg-amber-50 border border-amber-100 rounded-xl px-3 py-2">
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Code references — dark block with comment/code split for legibility. */}
      {ay.code_references.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel icon="〈〉">Reproducibility</SectionLabel>
          <ul className="space-y-1.5">
            {ay.code_references.map((c: string, i: number) => (
              <li
                key={i}
                className="text-xs leading-relaxed bg-zinc-950 rounded-xl px-3.5 py-2.5 font-mono whitespace-pre-wrap border border-zinc-800"
              >
                {c.split("\n").map((line, j) => {
                  const isComment = line.trimStart().startsWith("#");
                  return (
                    <div
                      key={j}
                      className={isComment ? "text-zinc-500" : "text-zinc-100"}
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
