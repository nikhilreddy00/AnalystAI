"use client";

// PROMPT-10 (1): "First 5 rows" — dtype badges under headers, alignment by
// dtype, alternating row shading, truncated strings with hover tooltip,
// datetime formatted as YYYY-MM-DD, wide tables collapsed to first 6 + last 2.

interface Props {
  rows: Record<string, unknown>[];
  dtypes: Record<string, string>;
}

const MAX_STRING_LEN = 30;
const VISIBLE_COLS_WHEN_TRUNCATED = { head: 6, tail: 2 };

function isNumericDtype(dt: string | undefined): boolean {
  if (!dt) return false;
  return /^(int|uint|float|bool)/i.test(dt) || /^number/i.test(dt);
}

function isDatetimeDtype(dt: string | undefined): boolean {
  if (!dt) return false;
  return /^datetime/i.test(dt) || /^timestamp/i.test(dt);
}

function formatCell(v: unknown, dtype: string | undefined): { display: string; full: string } {
  if (v === null || v === undefined || v === "") return { display: "—", full: "" };

  if (isDatetimeDtype(dtype)) {
    const s = String(v);
    // Take just the date portion of "2025-04-12T08:31:22..." or pass through.
    const match = s.match(/^\d{4}-\d{2}-\d{2}/);
    return { display: match ? match[0] : s, full: s };
  }

  if (typeof v === "number") {
    if (!Number.isFinite(v)) return { display: "—", full: String(v) };
    const display = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
    return { display, full: String(v) };
  }

  if (typeof v === "boolean") {
    return { display: v ? "true" : "false", full: String(v) };
  }

  const s = String(v);
  if (s.length > MAX_STRING_LEN) {
    return { display: s.slice(0, MAX_STRING_LEN) + "…", full: s };
  }
  return { display: s, full: s };
}

export default function DataPreviewTable({ rows, dtypes }: Props) {
  if (!rows || rows.length === 0) return null;

  const allCols = Object.keys(rows[0]);
  const tooMany = allCols.length > 8;
  const head = tooMany ? allCols.slice(0, VISIBLE_COLS_WHEN_TRUNCATED.head) : allCols;
  const tail = tooMany ? allCols.slice(-VISIBLE_COLS_WHEN_TRUNCATED.tail) : [];
  const middleCount = tooMany ? allCols.length - head.length - tail.length : 0;

  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2 shadow-sm">
      <div className="flex items-baseline justify-between">
        <h4 className="text-foreground text-sm font-semibold">First 5 rows</h4>
        <span className="text-muted-foreground text-xs">
          {rows.length} preview row{rows.length === 1 ? "" : "s"} · {allCols.length} column{allCols.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="overflow-x-auto -mx-1 px-1">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-border">
              {head.map((c) => (
                <HeaderCell key={c} name={c} dtype={dtypes[c]} />
              ))}
              {tooMany && middleCount > 0 && (
                <th className="px-2 py-1.5 text-center text-muted-foreground italic whitespace-nowrap font-normal">
                  … +{middleCount} more
                </th>
              )}
              {tail.map((c) => (
                <HeaderCell key={c} name={c} dtype={dtypes[c]} />
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 1 ? "bg-muted" : ""}>
                {head.map((c) => (
                  <BodyCell key={c} value={row[c]} dtype={dtypes[c]} />
                ))}
                {tooMany && middleCount > 0 && (
                  <td className="px-2 py-1.5 text-muted-foreground text-center">·</td>
                )}
                {tail.map((c) => (
                  <BodyCell key={c} value={row[c]} dtype={dtypes[c]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HeaderCell({ name, dtype }: { name: string; dtype: string | undefined }) {
  const align = isNumericDtype(dtype) ? "text-right" : "text-left";
  return (
    <th className={`${align} px-2 py-1.5 align-bottom whitespace-nowrap`}>
      <div className="text-foreground font-semibold">{name}</div>
      {dtype && (
        <div className="text-[10px] text-muted-foreground font-mono font-normal mt-0.5">{dtype}</div>
      )}
    </th>
  );
}

function BodyCell({ value, dtype }: { value: unknown; dtype: string | undefined }) {
  const { display, full } = formatCell(value, dtype);
  const align = isNumericDtype(dtype) ? "text-right" : "text-left";
  return (
    <td
      className={`${align} text-foreground px-2 py-1.5 font-mono whitespace-nowrap`}
      title={full && full !== display ? full : undefined}
    >
      {display}
    </td>
  );
}
