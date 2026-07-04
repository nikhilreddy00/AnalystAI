"use client";

// PROMPT-10 (3): "Descriptive statistics" — continuous columns only, with
// skewness + currency formatting + a target-correlation row at the bottom.

interface Stats {
  mean?: number;
  median?: number;
  std?: number;
  min?: number;
  max?: number;
}

interface Props {
  describe: Record<string, Stats | Record<string, unknown>>;
  skewness?: Record<string, number>;
  columnRoles?: Record<string, string>;
  targetCorrelations?: Record<string, number>;
  targetCol?: string | null;
}

const _CURRENCY_RE = /(?:^|_|\s)(price|cost|fare|revenue|amount|salary|income|spend|budget|fee|total|usd|sales|profit)(?:$|_|\s)/i;

function isCurrencyColumn(name: string): boolean {
  return _CURRENCY_RE.test(name);
}

function fmt(v: unknown, currency: boolean): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  if (currency) return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return v.toFixed(2);
}

function getStat(stats: Stats | Record<string, unknown>, key: string): number | undefined {
  const v = (stats as Record<string, unknown>)[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function corrCellClass(r: number): string {
  return Math.abs(r) > 0.3 ? "text-emerald-700 font-semibold" : "text-zinc-500";
}

export default function DescriptiveStatsTable({
  describe,
  skewness = {},
  columnRoles = {},
  targetCorrelations = {},
  targetCol,
}: Props) {
  // Only include columns the role classifier flagged as continuous. Fallback
  // to "any column with mean/std" when role info is absent (older sessions).
  const hasRoles = Object.keys(columnRoles).length > 0;
  const cols = Object.keys(describe).filter((c) => {
    const stats = describe[c];
    if (!stats || typeof stats !== "object" || !("mean" in stats)) return false;
    if (!hasRoles) return true;
    return columnRoles[c] === "continuous";
  });

  if (cols.length === 0) return null;

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-4 space-y-2 shadow-sm">
      <div className="flex items-baseline justify-between">
        <h4 className="text-zinc-900 text-sm font-semibold">Descriptive statistics</h4>
        <span className="text-zinc-500 text-xs">{cols.length} continuous column{cols.length === 1 ? "" : "s"}</span>
      </div>
      <div className="overflow-x-auto -mx-1 px-1">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-zinc-200 text-zinc-500">
              <th className="text-left px-2 py-1.5 font-medium">Column</th>
              <th className="text-right px-2 py-1.5 font-medium">Mean</th>
              <th className="text-right px-2 py-1.5 font-medium">Median</th>
              <th className="text-right px-2 py-1.5 font-medium">Std</th>
              <th className="text-right px-2 py-1.5 font-medium">Min</th>
              <th className="text-right px-2 py-1.5 font-medium">Max</th>
              <th className="text-right px-2 py-1.5 font-medium">Skewness</th>
              {targetCol && (
                <th className="text-right px-2 py-1.5 font-medium whitespace-nowrap" title={`Pearson r vs ${targetCol}`}>
                  vs {targetCol}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {cols.map((c, i) => {
              const stats = describe[c];
              const currency = isCurrencyColumn(c);
              const std = getStat(stats, "std");
              const sk = skewness[c];
              const skewedBadge = typeof sk === "number" && Math.abs(sk) > 2;
              const constantBadge = std === 0;
              const corr = targetCorrelations[c];
              return (
                <tr key={c} className={i % 2 === 1 ? "bg-zinc-50/60" : ""}>
                  <td className="px-2 py-1.5 text-zinc-800 font-mono whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      {c}
                      {constantBadge && (
                        <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-600 border border-zinc-200">constant</span>
                      )}
                      {skewedBadge && (
                        <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-orange-50 text-orange-700 border border-orange-200">skewed</span>
                      )}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-zinc-800 font-mono">{fmt(getStat(stats, "mean"), currency)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-800 font-mono">{fmt(getStat(stats, "median"), currency)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-800 font-mono">{fmt(std, currency)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-800 font-mono">{fmt(getStat(stats, "min"), currency)}</td>
                  <td className="px-2 py-1.5 text-right text-zinc-800 font-mono">{fmt(getStat(stats, "max"), currency)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono ${skewedBadge ? "text-orange-700" : "text-zinc-800"}`}>
                    {typeof sk === "number" ? sk.toFixed(2) : "—"}
                  </td>
                  {targetCol && (
                    <td className={`px-2 py-1.5 text-right font-mono ${typeof corr === "number" ? corrCellClass(corr) : "text-zinc-400"}`}>
                      {typeof corr === "number" ? corr.toFixed(2) : "—"}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
