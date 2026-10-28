// Landing page (Phase 3.5, "Ledger" look): one theme-aware surface, the pipeline in
// its real graph order (ingestion runs before intake), and a sample report row that
// shows what "every finding has evidence" means.

import Link from "next/link";
import { ArrowRight, BarChart3, Code, Database, FileSpreadsheet, Sheet } from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";

const PIPELINE = [
  { name: "Load", desc: "Reads CSV, Excel, SQL, Sheets or an API and profiles every column." },
  { name: "Understand", desc: "Turns your question into a plan, using the real column names." },
  { name: "Clean", desc: "Dedupes, imputes and fixes types. Every change is logged." },
  { name: "Analyse", desc: "Writes Python, runs it in a sandbox, checks the results." },
  { name: "Chart", desc: "Picks charts from the findings by fixed rules, no guessing." },
  { name: "Report", desc: "Writes a summary for managers and the detail for analysts." },
  { name: "Review", desc: "A critic checks every number against the computed results." },
];

const SOURCES = [
  { Icon: FileSpreadsheet, label: "CSV and Excel" },
  { Icon: Database, label: "SQL" },
  { Icon: Sheet, label: "Google Sheets" },
  { Icon: Code, label: "REST APIs" },
];

// Sample figures from the bundled synthetic sales dataset (evals/datagen.py).
const SAMPLE_KPIS = [
  ["Orders", "3,000", "25 duplicates removed"],
  ["Avg order", "$102.72", "A leads at $121.29"],
  ["Segment B, MoM", "−12.6%", "spend +31.2%"],
];

export default function LandingPage() {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-4 sm:px-6">
        <span className="flex items-center gap-2 font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <BarChart3 className="h-3.5 w-3.5" />
          </span>
          AnalystAI
        </span>
        <div className="flex-1" />
        <ThemeToggle />
        <Link href="/auth" className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground">
          Sign in
        </Link>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl gap-10 px-4 pb-16 pt-10 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:pt-16">
          <div className="space-y-6">
            <h1 className="text-4xl font-semibold leading-tight tracking-tight text-balance sm:text-5xl">
              Ask your data a question. Get an answer you can check.
            </h1>
            <p className="max-w-[56ch] text-lg leading-relaxed text-muted-foreground">
              AnalystAI loads, cleans and analyses your data with a team of agents, then writes a report where
              every finding links to the code and rows behind it. Self-hosted and open source.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                href="/chat"
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                Start analysing <ArrowRight className="h-4 w-4" />
              </Link>
              <Link
                href="/dashboard"
                className="inline-flex items-center rounded-md border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground hover:bg-accent"
              >
                View dashboard
              </Link>
            </div>
          </div>

          {/* A slice of a real report, rendered as plain HTML. */}
          <figure className="m-0 space-y-3 rounded-lg border border-border bg-card p-4 shadow-sm" aria-label="Sample report">
            <p className="text-xs text-muted-foreground">Report · sales_nov.csv</p>
            <p className="text-base font-semibold leading-snug">Segment B is spending more to earn less.</p>
            <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border border-border bg-border">
              {SAMPLE_KPIS.map(([label, value, sub]) => (
                <div key={label} className="min-w-0 bg-card px-3 py-2">
                  <p className="truncate text-xs text-muted-foreground">{label}</p>
                  <p className="font-mono text-lg tabular-nums">{value}</p>
                  <p className="truncate text-xs text-muted-foreground">{sub}</p>
                </div>
              ))}
            </div>
            <div className="flex items-start justify-between gap-3 border-t border-border pt-3 text-sm">
              <span>Orders with more marketing spend bring in more revenue (r = 0.57).</span>
              <span className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
                evidence
              </span>
            </div>
            <figcaption className="text-xs text-muted-foreground">Sample output on the bundled synthetic dataset.</figcaption>
          </figure>
        </section>

        <section className="border-y border-border bg-card" aria-labelledby="how-h">
          <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
            <h2 id="how-h" className="mb-1 text-xl font-semibold">How a question becomes a report</h2>
            <p className="mb-8 text-sm text-muted-foreground">Seven agents, run in this order for every overview.</p>
            <ol className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
              {PIPELINE.map((step, i) => (
                <li key={step.name} className="space-y-1 bg-card p-4">
                  <p className="font-mono text-xs text-muted-foreground">{i + 1}</p>
                  <h3 className="text-sm font-semibold">{step.name}</h3>
                  <p className="text-sm text-muted-foreground">{step.desc}</p>
                </li>
              ))}
              <li className="hidden bg-card p-4 lg:block" aria-hidden />
            </ol>
          </div>
        </section>

        <section className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-3 px-4 py-10 sm:px-6">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Connects to</span>
          {SOURCES.map(({ Icon, label }) => (
            <span key={label} className="inline-flex items-center gap-2 text-sm">
              <Icon className="h-4 w-4 text-primary" />
              {label}
            </span>
          ))}
        </section>
      </main>
    </div>
  );
}
