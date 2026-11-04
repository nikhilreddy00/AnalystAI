"use client";

import { formatDay, parseDay } from "@/lib/localDate";
import { useMemo, useState, type ReactNode } from "react";
import type { FilterOption } from "@/types";
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
  option: FilterOption;
  filter: FilterValue | undefined;
  setFilter: SetFilter;
}

/** The server's filter options, drawn as they come (binary, pills, ranges, dates, in its order):
 * no row is read here. */
export function FilterSidebar() {
  const { options, status } = useDashboardData();
  const { filters, setFilter } = useDashboardFilter();

  if (status !== "ok" || options.length === 0) return null;

  return (
    <aside aria-label="Filters" className="w-full lg:w-60 flex-shrink-0 rounded-lg border border-border bg-card px-4 py-3 space-y-5 lg:rounded-none lg:border-0 lg:border-r lg:bg-transparent">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Filters
      </h3>
      {options.map((option) => {
        const Control = CONTROLS[option.control];
        return Control ? (
          <Control
            key={option.column}
            option={option}
            filter={filters[option.column]}
            setFilter={setFilter}
          />
        ) : null;
      })}
    </aside>
  );
}

// ── BinaryToggle ──────────────────────────────────────────────────────────────

function BinaryToggle({ option, filter, setFilter }: ControlProps) {
  const col = option.column;
  const values = option.values ?? [];
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
            {v === "" ? "(blank)" : v}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── PillSelect ────────────────────────────────────────────────────────────────

function PillSelect({ option, filter, setFilter }: ControlProps) {
  const col = option.column;
  const values = option.values ?? [];
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
              {v === "" ? "(blank)" : v}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── RangeSliderControl ────────────────────────────────────────────────────────

function RangeSliderControl({ option, filter, setFilter }: ControlProps) {
  const col = option.column;
  const { min: mn, max: mx } = option;
  if (mn == null || mx == null || mn === mx) return null;
  const [lo0, hi0] = [mn, mx];   // narrowed for the closure below

  const current =
    filter?.kind === "range" ? [filter.min, filter.max] : [mn, mx];

  // @base-ui/react Slider: onValueChange receives (value, eventDetails)
  // value is the full array for range sliders (readonly number[])
  function commit(next: number | readonly number[]) {
    const arr = Array.isArray(next) ? (next as number[]) : [next as number];
    const [lo, hi] = arr;
    if (lo <= lo0 && hi >= hi0) setFilter(col, null);
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

function DateRangeControl({ option, filter, setFilter }: ControlProps) {
  const col = option.column;
  const [open, setOpen] = useState(false);
  if (!option.from || !option.to) return null;
  const extent = { from: parseDay(option.from), to: parseDay(option.to) };

  const selected: DateRange | undefined =
    filter?.kind === "date_range"
      ? { from: parseDay(filter.from), to: parseDay(filter.to) }
      : undefined;

  const label =
    selected?.from && selected?.to
      ? `${formatDay(selected.from)} → ${formatDay(selected.to)}`
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
                  from: formatDay(range.from),
                  to: formatDay(range.to),
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

const CONTROLS: Record<FilterOption["control"], (p: ControlProps) => ReactNode> = {
  binary: BinaryToggle,
  pills: PillSelect,
  range: RangeSliderControl,
  dates: DateRangeControl,
};
