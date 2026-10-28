"use client";

interface Props {
  title?: string;
  caption?: string;
  rows: Record<string, unknown>[];
  // Optional: pin a specific column order. If absent, uses keys of the first row.
  columns?: string[];
  // Optional: caps the visible rows. Hidden count is shown in the footer.
  maxRows?: number;
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "—";
    return Number.isInteger(v) ? v.toString() : v.toFixed(2);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

export default function TableRenderer({ title, caption, rows, columns, maxRows = 10 }: Props) {
  if (!rows || rows.length === 0) return null;

  const cols = columns ?? Object.keys(rows[0]);
  const visible = rows.slice(0, maxRows);
  const hidden = rows.length - visible.length;

  // C2: light report-card treatment — used in chat (over dark canvas) and the
  // upcoming light dashboard (D1). Works in both contexts.
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2 shadow-sm overflow-hidden">
      {title && (
        <div className="flex items-baseline justify-between">
          <h4 className="text-foreground text-sm font-semibold">{title}</h4>
          {caption && <span className="text-muted-foreground text-xs">{caption}</span>}
        </div>
      )}
      <div className="overflow-x-auto -mx-1 px-1">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-border">
              {cols.map((c) => (
                <th
                  key={c}
                  className="text-left font-medium text-muted-foreground px-2 py-1.5 whitespace-nowrap"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={i}
                className="border-b border-border hover:bg-muted transition-colors"
              >
                {cols.map((c) => (
                  <td
                    key={c}
                    className="text-foreground px-2 py-1.5 font-mono whitespace-nowrap"
                  >
                    {formatCell(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <p className="text-xs text-muted-foreground italic">+ {hidden} more rows hidden</p>
      )}
    </div>
  );
}
