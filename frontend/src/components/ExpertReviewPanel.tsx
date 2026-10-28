"use client";

import { ChevronRight } from "lucide-react";
import type { ExpertReview } from "@/types";

// PROMPT-9: the review as full-width collapsibles. Gaps + Methodology concerns open by
// default — the two that need attention before stakeholder delivery; the counts of the
// others stay visible. Zero-item sections still render "(none)": absence is information.

interface Props {
  review: ExpertReview;
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

export default function ExpertReviewPanel({ review }: Props) {
  return (
    <section className="space-y-3" aria-label="Reviewer notes">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Reviewer notes
      </h3>
      {review.review_summary && (
        <p className="max-w-[68ch] text-sm leading-relaxed text-foreground">{review.review_summary}</p>
      )}
      <div className="space-y-2">
        <Section title="Gaps" items={review.gaps} open />
        <Section title="Methodology concerns" items={review.methodology_concerns} open />
        <Section title="Next investigations" items={review.next_investigations} open={false} />
        <Section title="Strengths" items={review.strengths} open={false} />
      </div>
    </section>
  );
}
