// Hybrid landing page — light hero / dark pipeline / light footer.
// PROMPT-7: single purple accent (#534AB7) across pipeline icons + hero
// gradient + trust bar. Pipeline cards numbered 01-07 with chevron connectors
// to read as a sequential flow, not a feature list.

import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  Brush,
  ChevronRight,
  Code,
  Database,
  FileSpreadsheet,
  GraduationCap,
  LayoutDashboard,
  Lightbulb,
  PieChart,
  Search,
  Sheet,
  Sparkles,
} from "lucide-react";

// PROMPT-7 fix 1+3: every step shares the project accent so the strip reads as
// a single pipeline rather than seven independent features.
const ACCENT = "#534AB7";

const PIPELINE = [
  { Icon: Search,         name: "Intake",        desc: "Parses your question into a structured plan" },
  { Icon: Database,       name: "Ingest",        desc: "Loads data from CSV, SQL, Sheets, or API" },
  { Icon: Brush,          name: "Clean",         desc: "Imputes, dedupes, coerces types — every decision audited" },
  { Icon: BarChart3,      name: "Analyze",       desc: "Runs Python in a sandbox: stats, segments, anomalies" },
  { Icon: PieChart,       name: "Visualize",     desc: "Builds Recharts configs from the findings deterministically" },
  { Icon: Lightbulb,      name: "Insight",       desc: "Synthesizes an executive summary + recommended actions" },
  { Icon: GraduationCap,  name: "Expert review", desc: "Principal-analyst critique: gaps, next investigations" },
];

// PROMPT-7 fix 2: replaces the redundant three-card feature strip. One row of
// supported sources says the same thing in a quarter of the space.
const TRUST_SOURCES = [
  { Icon: FileSpreadsheet, label: "CSV" },
  { Icon: Database,        label: "SQL" },
  { Icon: Sheet,           label: "Google Sheets" },
  { Icon: Code,            label: "REST APIs" },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-zinc-50">
      {/* ── Hero (LIGHT) ─────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden bg-gradient-to-b from-white to-zinc-50">
        <div className="absolute inset-0 -z-10 [background-image:radial-gradient(theme(colors.zinc.200)_1px,transparent_1px)] [background-size:24px_24px] opacity-60" />
        <div className="max-w-6xl mx-auto px-6 pt-24 pb-32 text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white border border-zinc-200 shadow-sm text-xs font-medium text-zinc-700 mb-8">
            <Sparkles className="w-3.5 h-3.5" style={{ color: ACCENT }} />
            Multi-agent AI analyst
          </div>
          <h1 className="text-5xl sm:text-6xl font-bold tracking-tight text-zinc-900 mb-5">
            Your data analyst,
            <span
              className="block bg-clip-text text-transparent"
              style={{
                backgroundImage: `linear-gradient(90deg, ${ACCENT} 0%, #7C73DD 50%, ${ACCENT} 100%)`,
              }}
            >
              on demand.
            </span>
          </h1>
          <p className="text-lg text-zinc-600 max-w-2xl mx-auto mb-10 leading-relaxed">
            Ask a question in plain English. Seven specialized agents handle ingest, cleaning,
            statistical analysis, visualization, and synthesis — then deliver a boardroom-ready
            answer with charts, tables, and a senior-expert critique.
          </p>

          {/* PROMPT-7 fix 4: primary CTA gets extra padding + accent shadow that
              picks up on hover. Secondary CTA gets a subtler hover. */}
          <div className="flex flex-wrap gap-3 justify-center items-center">
            <Link
              href="/chat"
              className="group inline-flex items-center gap-2 px-7 py-3.5 bg-zinc-900 hover:bg-zinc-800 text-white text-base font-semibold rounded-xl transition-all duration-200 shadow-xl shadow-zinc-900/20 hover:shadow-2xl hover:-translate-y-0.5"
              style={{ boxShadow: `0 14px 40px -10px ${ACCENT}66, 0 4px 12px rgba(0,0,0,0.12)` }}
            >
              Start analyzing
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 px-6 py-3 bg-white hover:bg-zinc-50 text-zinc-800 font-medium rounded-xl transition-colors border border-zinc-200 hover:border-zinc-300 shadow-sm hover:shadow-md"
            >
              <LayoutDashboard className="w-4 h-4" />
              View dashboard
            </Link>
          </div>
        </div>
      </section>

      {/* ── Pipeline (DARK) ──────────────────────────────────────────────── */}
      <section className="bg-zinc-950 text-zinc-100">
        <div className="max-w-6xl mx-auto px-6 py-24">
          <div className="text-center mb-14">
            <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "#A89FF0" }}>
              How it works
            </p>
            <h2 className="text-3xl sm:text-4xl font-bold text-white mb-3">
              From raw data to boardroom-ready, in 7 steps
            </h2>
            <p className="text-zinc-400 max-w-2xl mx-auto">
              Every stage is a specialized agent. The orchestrator routes between them based on
              your question — overview, deep dive, or targeted answer.
            </p>
          </div>

          {/* PROMPT-7 fix 1: every card uses one accent color; step numbers sit
              ABOVE each card; chevron connectors only appear on the lg row so
              the eye reads left-to-right as a sequential pipeline. */}
          <ol className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            {PIPELINE.map((step, i) => (
              <li key={step.name} className="relative">
                <div className="text-[10px] font-mono mb-1.5 tracking-wider" style={{ color: "#A89FF0" }}>
                  {String(i + 1).padStart(2, "0")}
                </div>
                <div className="relative rounded-2xl border border-zinc-800 bg-zinc-900/60 backdrop-blur-sm p-5 hover:border-zinc-700 transition-colors h-full">
                  <div
                    className="w-10 h-10 rounded-xl flex items-center justify-center mb-3"
                    style={{ backgroundColor: `${ACCENT}26`, color: "#C9C3F5" }}
                  >
                    <step.Icon className="w-5 h-5" />
                  </div>
                  <h3 className="text-sm font-semibold text-white mb-1">{step.name}</h3>
                  <p className="text-xs text-zinc-500 leading-relaxed">{step.desc}</p>
                </div>
                {i < PIPELINE.length - 1 && (
                  <div
                    aria-hidden
                    className="hidden lg:flex absolute top-1/2 -right-2.5 translate-x-full -translate-y-1/2 items-center justify-center w-5 h-5 z-10"
                    style={{ color: "#6B5FCB" }}
                  >
                    <ChevronRight className="w-5 h-5" strokeWidth={2.5} />
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Footer CTA (LIGHT) ───────────────────────────────────────────── */}
      <section className="bg-white border-t border-zinc-200">
        <div className="max-w-6xl mx-auto px-6 py-20">
          {/* PROMPT-7 fix 2: trust bar replaces the three duplicate feature cards. */}
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 mb-16 pb-10 border-b border-zinc-100">
            <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Supports</span>
            {TRUST_SOURCES.map((src) => (
              <span key={src.label} className="inline-flex items-center gap-2 text-sm text-zinc-700">
                <src.Icon className="w-4 h-4" style={{ color: ACCENT }} />
                {src.label}
              </span>
            ))}
          </div>

          <div className="text-center">
            <h2 className="text-2xl sm:text-3xl font-bold text-zinc-900 mb-3">
              Ready to replace your analyst?
            </h2>
            <p className="text-zinc-600 mb-6 max-w-md mx-auto">
              Self-hosted, open source, runs on your own infra. No data leaves your environment.
            </p>
            <Link
              href="/chat"
              className="group inline-flex items-center gap-2 px-7 py-3.5 bg-zinc-900 hover:bg-zinc-800 text-white text-base font-semibold rounded-xl transition-all duration-200 hover:-translate-y-0.5"
              style={{ boxShadow: `0 14px 40px -10px ${ACCENT}66, 0 4px 12px rgba(0,0,0,0.12)` }}
            >
              Get started
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
