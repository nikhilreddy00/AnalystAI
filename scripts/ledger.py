"""Query docs/retro-ledger.md: a session's Step 0 rows without reading the whole ledger
(retro 5.7b-3: ~75k characters read for one table's worth of rows).

    uv run python scripts/ledger.py --class C8                  # every C8 row
    uv run python scripts/ledger.py --min-recurrences 2 --files guardrails.py agents/insight_agent.py
    uv run python scripts/ledger.py --unenforced --wide          # whole cells, not cut

Filters combine (AND); --files keeps a row naming any file's path or basename (the branch's own:
--files $(git diff --name-only main...))."""
import argparse
import re
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "docs" / "retro-ledger.md"
COLUMNS = ["id", "class", "domain(s)", "finding", "fix", "enforced-by", "recurrences"]


def tables(text: str) -> list[list[list[str]]]:
    """Each markdown table: its rows' cells (header and separator rows included)."""
    out, cur = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            cur.append([c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]])
        elif cur:
            out.append(cur)
            cur = []
    return out


def rows(text: str) -> list[dict]:
    found = [t for t in tables(text) if t[0] == COLUMNS]
    assert len(found) == 1, f"expected one table with columns {COLUMNS}"
    return [dict(zip(COLUMNS, r)) for r in found[0][2:]]


def select(rs: list[dict], cls: str | None = None, min_rec: int = 0,
           files: list[str] | None = None, unenforced: bool = False) -> list[dict]:
    names = {n for f in files or [] for n in (f, Path(f).name)}
    return [r for r in rs
            if (cls is None or r["class"] == cls) and int(r["recurrences"]) >= min_rec
            and (not unenforced or r["enforced-by"] == "unenforced")
            and (not names or any(n in " ".join(r.values()) for n in names))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="cls", help="C0-C9 or a process theme A-G")
    ap.add_argument("--min-recurrences", type=int, default=0)
    ap.add_argument("--files", nargs="+", help="rows naming any of these files")
    ap.add_argument("--unenforced", action="store_true", help="prose-only rows")
    ap.add_argument("--wide", action="store_true", help="whole cells (default: cut to 160)")
    args = ap.parse_args()
    got = select(rows(LEDGER.read_text()), args.cls, args.min_recurrences, args.files,
                 args.unenforced)
    def cut(s):
        return s if args.wide or len(s) <= 160 else s[:157] + "..."
    for r in got:
        print(f"{r['id']} [{r['class']}, rec {r['recurrences']}, {r['domain(s)']}] {cut(r['finding'])}"
              f"\n  fix: {cut(r['fix'])}\n  enforced-by: {cut(r['enforced-by'])}")
    print(f"{len(got)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
