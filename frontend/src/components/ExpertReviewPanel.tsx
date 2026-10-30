"use client";

import { ChevronRight } from "lucide-react";
import type { ExpertReview, ReviewFlag } from "@/types";
import { EvidenceChips } from "./InsightPanel";

// PROMPT-9: the review as full-width collapsibles. Gaps + Methodology concerns open by
// default — the two that need attention before stakeholder delivery; the counts of the
// others stay visible. Zero-item sections still render "(none)": absence is information.
// Phase 5 Quality: flagged claims come first, each with the evidence that disputes it.

interface Props {
  review: ExpertReview;
  onEvidence?: (id: string) => void;
}

const LABEL = { unsupported: "Not supported", contradicted: "Contradicted", overstated: "Overstated" };

function Flags({ flags, onEvidence }: { flags: ReviewFlag[]; onEvidence?: (id: string) => void }) {
  return (
    <section aria-label="Flagged claims" className="rounded-md border border-border bg-card">
      <h4 className="flex items-center gap-2 px-3 py-2.5 text-sm font-medium text-foreground">
        Flagged claims <span className="font-mono text-xs text-muted-foreground">{flags.length}</span>
      </h4>
      <ul className="divide-y divide-border border-t border-border">
        {flags.map((f) => (
          <li key={f.index} className="space-y-1 px-3 py-2.5 text-sm">
            <p className="flex flex-wrap items-center gap-2">
              <span className="rounded border border-warning px-1.5 py-px text-[11px] font-medium text-warning">
                {LABEL[f.label]}
              </span>
              <span className="text-xs text-muted-foreground">
                {f.source === "check" ? "Automatic check" : "Reviewer"} · {f.section}
              </span>
            </p>
            <blockquote className="border-l-2 border-border pl-2 text-foreground">{f.claim}</blockquote>
            <p className="text-muted-foreground">
              {f.reason}
              <EvidenceChips ids={f.evidence_ids} onEvidence={onEvidence} />
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Section({ title, items, open }: { title: string; items: string[]; open: boolean }) {
  return (
    <details open={open} className="group rounded-md border border-border bg-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90" />
        <span className="font-medium text-foreground">{title}</span>
        <span className="font-mono text-xs text-muted-foreground">{items.length}</span>
      </summary>
      <div className="border-t border-border px-3 py-2.5">
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground">(none)</p>
        ) : (
          <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-foreground">
            {items.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

export default function ExpertReviewPanel({ review, onEvidence }: Props) {
  const flags = review.flags ?? [];
  return (
    <section className="space-y-3" aria-label="Reviewer notes">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Reviewer notes
      </h3>
      {review.review_summary && (
        <p className="max-w-[68ch] text-sm leading-relaxed text-foreground">{review.review_summary}</p>
      )}
      <div className="space-y-2">
        {flags.length > 0 && <Flags flags={flags} onEvidence={onEvidence} />}
        <Section title="Gaps" items={review.gaps} open />
        <Section title="Methodology concerns" items={review.methodology_concerns} open />
        <Section title="Next investigations" items={review.next_investigations} open={false} />
        {review.strengths.length > 0 && <Section title="Strengths" items={review.strengths} open={false} />}
      </div>
    </section>
  );
}
