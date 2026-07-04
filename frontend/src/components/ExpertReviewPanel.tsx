"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ExpertReview } from "@/types";

// PROMPT-9: principal-analyst review redesigned as full-width collapsibles.
// Default open: Gaps + Methodology Concerns — the two sections that actually
// require analyst attention before stakeholder delivery. Strengths and Next
// Investigations stay collapsed for scannability but their counts are still
// visible at a glance.

interface Props {
  review: ExpertReview;
}

type Accent = "emerald" | "amber" | "indigo" | "rose";

const ACCENT_CLASSES: Record<Accent, { text: string; badge: string; ring: string; bg: string }> = {
  emerald: { text: "text-emerald-700", badge: "bg-emerald-50 text-emerald-700 border-emerald-200", ring: "hover:bg-emerald-50/40", bg: "bg-emerald-50/30" },
  amber:   { text: "text-amber-800",   badge: "bg-amber-50 text-amber-800 border-amber-200",       ring: "hover:bg-amber-50/40",   bg: "bg-amber-50/30" },
  indigo:  { text: "text-indigo-700",  badge: "bg-indigo-50 text-indigo-700 border-indigo-200",    ring: "hover:bg-indigo-50/40",  bg: "bg-indigo-50/30" },
  rose:    { text: "text-rose-700",    badge: "bg-rose-50 text-rose-700 border-rose-200",          ring: "hover:bg-rose-50/40",    bg: "bg-rose-50/30" },
};

function CollapsibleSection({
  title,
  icon,
  items,
  accent,
  defaultOpen,
}: {
  title: string;
  icon: string;
  items: string[];
  accent: Accent;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const a = ACCENT_CLASSES[accent];
  const count = items.length;

  return (
    <div className="rounded-xl border border-zinc-200 bg-white overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center justify-between px-4 py-3 transition-colors ${a.ring}`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2.5">
          <span className="text-base">{icon}</span>
          <span className={`text-sm font-semibold ${a.text}`}>{title}</span>
          <span className={`text-[11px] font-medium px-1.5 py-0.5 rounded-md border ${a.badge}`}>
            {count}
          </span>
        </div>
        {open ? (
          <ChevronDown className="w-4 h-4 text-zinc-500" />
        ) : (
          <ChevronRight className="w-4 h-4 text-zinc-500" />
        )}
      </button>
      {open && (
        <div className={`border-t border-zinc-200 px-4 py-3 ${a.bg}`}>
          {count === 0 ? (
            <p className="text-xs italic text-zinc-500">(none)</p>
          ) : (
            <ul className="space-y-2">
              {items.map((item, i) => (
                <li key={i} className="text-sm text-zinc-800 leading-relaxed pl-4 relative">
                  <span className="absolute left-0 top-0.5 text-zinc-400">•</span>
                  {item}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function ExpertReviewPanel({ review }: Props) {
  return (
    <div className="w-full rounded-2xl border border-zinc-200 bg-gradient-to-br from-white to-purple-50/40 p-5 space-y-4 shadow-sm">
      <div className="flex items-center gap-2 text-xs text-zinc-500 uppercase tracking-wider font-semibold">
        <span className="text-purple-600">🎓</span>
        <span>Principal analyst review</span>
      </div>

      {review.review_summary && (
        <p className="text-sm text-zinc-800 leading-relaxed border-l-2 border-purple-400 pl-3 italic">
          {review.review_summary}
        </p>
      )}

      {/* PROMPT-9: full-width stack of collapsibles. Gaps + Methodology open by
          default; strengths + next investigations collapsed but their counts
          are visible in the header badge. Zero-item sections render as
          "(none)" inside the collapsed/expanded body — absence is information. */}
      <div className="space-y-2">
        <CollapsibleSection
          title="Strengths"
          icon="✅"
          items={review.strengths}
          accent="emerald"
          defaultOpen={false}
        />
        <CollapsibleSection
          title="Gaps"
          icon="⚠️"
          items={review.gaps}
          accent="amber"
          defaultOpen={true}
        />
        <CollapsibleSection
          title="Next investigations"
          icon="🔍"
          items={review.next_investigations}
          accent="indigo"
          defaultOpen={false}
        />
        <CollapsibleSection
          title="Methodology concerns"
          icon="🔴"
          items={review.methodology_concerns}
          accent="rose"
          defaultOpen={true}
        />
      </div>
    </div>
  );
}
