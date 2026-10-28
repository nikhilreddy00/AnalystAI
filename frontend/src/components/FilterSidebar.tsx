"use client";

import { useMemo, useState } from "react";
import { useDashboardData } from "@/context/DashboardDataContext";
import {
  useDashboardFilter,
  type FilterValue,
} from "@/context/DashboardFilterContext";
import { Slider } from "@/components/ui/slider";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import type { DateRange } from "react-day-picker";

type SetFilter = (key: string, value: FilterValue | null) => void;

interface ControlProps {
  col: string;
  rows: Record<string, unknown>[];
  filter: FilterValue | undefined;
  setFilter: SetFilter;
}

export function FilterSidebar() {
  const { columnRoles, rows, status } = useDashboardData();
  const { filters, setFilter } = useDashboardFilter();

  if (status !== "ok" || !columnRoles || rows.length === 0) return null;

  const binary = columnRoles.binary_cols ?? [];
  const lowCard = (columnRoles.low_card_cat ?? []).slice(0, 8);
  const continuous = (columnRoles.continuous ?? []).slice(0, 3);
  const datetime = columnRoles.datetime_cols ?? [];

  const total =
    binary.length + lowCard.length + continuous.length + datetime.length;
  if (total === 0) return null;

  return (
    <aside aria-label="Filters" className="w-full lg:w-60 flex-shrink-0 rounded-lg border border-border bg-card px-4 py-3 space-y-5 lg:rounded-none lg:border-0 lg:border-r lg:bg-transparent">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Filters
      </h3>
      {binary.map((col) => (
        <BinaryToggle
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {lowCard.map((col) => (
        <PillSelect
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {continuous.map((col) => (
        <RangeSliderControl
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
      {datetime.map((col) => (
        <DateRangeControl
          key={col}
          col={col}
          rows={rows}
          filter={filters[col]}
          setFilter={setFilter}
        />
      ))}
    </aside>
  );
}

// ── BinaryToggle ──────────────────────────────────────────────────────────────

function BinaryToggle({ col, rows, filter, setFilter }: ControlProps) {
  const values = useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) {
      const v = r[col];
      if (v == null || v === "") continue;
      seen.add(String(v));
      if (seen.size >= 2) break;
    }
    return Array.from(seen);
  }, [rows, col]);

  if (values.length < 2) return null;

  const current =
    filter?.kind === "equality" ? String(filter.value) : null;

  function pick(val: string | null) {
    if (val === null) setFilter(col, null);
    else setFilter(col, { kind: "equality", value: val });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">{col}</p>
      <div className="flex flex-wrap gap-1">
        <button
          type="button"
          onClick={() => pick(null)}
          className={`text-[11px] px-2 py-0.5 rounded-full border ${
            current === null
              ? "bg-primary border-primary/30 text-primary-foreground"
              : "border-border text-foreground hover:bg-muted"
          }`}
        >
          All
        </button>
        {values.map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => pick(v)}
            className={`text-[11px] px-2 py-0.5 rounded-full border ${
              current === v
                ? "bg-primary border-primary/30 text-primary-foreground"
                : "border-border text-foreground hover:bg-muted"
            }`}
          >
            {v}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── PillSelect ────────────────────────────────────────────────────────────────

function PillSelect({ col, rows, filter, setFilter }: ControlProps) {
  const values = useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) {
      const v = r[col];
      if (v == null || v === "") continue;
      seen.add(String(v));
      if (seen.size >= 12) break;
    }
    return Array.from(seen).sort();
  }, [rows, col]);

  const selected = useMemo(() => {
    if (filter?.kind === "in") return new Set(filter.values.map(String));
    return new Set<string>();
  }, [filter]);

  if (values.length === 0) return null;

  function toggle(v: string) {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    if (next.size === 0) setFilter(col, null);
    else setFilter(col, { kind: "in", values: Array.from(next) });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">{col}</p>
      <div className="flex flex-wrap gap-1">
        {values.map((v) => {
          const isSel = selected.has(v);
          return (
            <button
              key={v}
              type="button"
              onClick={() => toggle(v)}
              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                isSel
                  ? "bg-primary border-primary/30 text-primary-foreground"
                  : "border-border text-foreground hover:bg-muted"
              }`}
            >
              {v}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── RangeSliderControl ────────────────────────────────────────────────────────

function RangeSliderControl({ col, rows, filter, setFilter }: ControlProps) {
  const extent = useMemo(() => {
    let mn = Number.POSITIVE_INFINITY;
    let mx = Number.NEGATIVE_INFINITY;
    for (const r of rows) {
      const v = r[col];
      if (v == null) continue;
      const n = typeof v === "number" ? v : Number(v);
      if (!Number.isFinite(n)) continue;
      if (n < mn) mn = n;
      if (n > mx) mx = n;
    }
    return Number.isFinite(mn) && Number.isFinite(mx) ? [mn, mx] : null;
  }, [rows, col]);

  if (!extent) return null;
  const [mn, mx] = extent;
  if (mn === mx) return null;

  const current =
    filter?.kind === "range" ? [filter.min, filter.max] : [mn, mx];

  // @base-ui/react Slider: onValueChange receives (value, eventDetails)
  // value is the full array for range sliders (readonly number[])
  function commit(next: number | readonly number[]) {
    const arr = Array.isArray(next) ? (next as number[]) : [next as number];
    const [lo, hi] = arr;
    if (lo <= mn && hi >= mx) setFilter(col, null);
    else setFilter(col, { kind: "range", min: lo, max: hi });
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">{col}</p>
      <p className="text-[11px] text-muted-foreground">
        {current[0]} – {current[1]}
      </p>
      <Slider
        label={col}
        min={mn}
        max={mx}
        step={(mx - mn) / 100 || 1}
        value={current}
        onValueChange={commit}
      />
    </div>
  );
}

// ── DateRangeControl ──────────────────────────────────────────────────────────

function DateRangeControl({ col, rows, filter, setFilter }: ControlProps) {
  const [open, setOpen] = useState(false);

  const extent = useMemo(() => {
    let mn = Number.POSITIVE_INFINITY;
    let mx = Number.NEGATIVE_INFINITY;
    for (const r of rows) {
      const v = r[col];
      if (v == null) continue;
      const t = new Date(String(v)).getTime();
      if (!Number.isFinite(t) || isNaN(t)) continue;
      if (t < mn) mn = t;
      if (t > mx) mx = t;
    }
    if (!Number.isFinite(mn) || !Number.isFinite(mx)) return null;
    return { from: new Date(mn), to: new Date(mx) };
  }, [rows, col]);

  if (!extent) return null;

  const selected: DateRange | undefined =
    filter?.kind === "date_range"
      ? { from: new Date(filter.from), to: new Date(filter.to) }
      : undefined;

  const label =
    selected?.from && selected?.to
      ? `${selected.from.toISOString().slice(0, 10)} → ${selected.to.toISOString().slice(0, 10)}`
      : "Any date";

  // @base-ui/react Popover: onOpenChange receives (open, eventDetails)
  // We only need the first arg, so the signature is compatible.
  // PopoverTrigger renders a <button> natively — no asChild needed.
  // We pass button styling via className directly on PopoverTrigger.

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-foreground">{col}</p>
      <Popover open={open} onOpenChange={(nextOpen) => setOpen(nextOpen)}>
        <PopoverTrigger
          className="text-[11px] px-2 py-1 rounded border border-border text-foreground hover:bg-muted w-full text-left"
        >
          {label}
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0">
          <Calendar
            mode="range"
            selected={selected}
            onSelect={(range: DateRange | undefined) => {
              if (range?.from && range?.to) {
                setFilter(col, {
                  kind: "date_range",
                  from: range.from.toISOString().slice(0, 10),
                  to: range.to.toISOString().slice(0, 10),
                });
              }
            }}
            startMonth={extent.from}
            endMonth={extent.to}
          />
          <div className="flex justify-end p-2 border-t border-border">
            <button
              type="button"
              onClick={() => {
                setFilter(col, null);
                setOpen(false);
              }}
              className="text-[11px] text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
