"""The retro ledger stays countable: one row per finding, a known class, checks that exist."""
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "retro-ledger.md"
COLUMNS = ["id", "class", "domain(s)", "finding", "fix", "enforced-by", "recurrences"]
SUMMARY = ["class", "rows", "unenforced", "recurrences"]


def _tables() -> list[list[list[str]]]:
    tables, cur = [], []
    for line in LEDGER.read_text().splitlines() + [""]:
        if line.startswith("|"):
            cur.append([c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]])
        elif cur:
            tables.append(cur)
            cur = []
    return tables


def _table(header: list[str]) -> list[list[str]]:
    found = [t for t in _tables() if t[0] == header]
    assert len(found) == 1, f"expected one table with columns {header}"
    return [r for r in found[0][2:]]


def test_every_row_is_well_formed():
    rows = _table(COLUMNS)
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids)), [i for i, n in Counter(ids).items() if n > 1]
    for r in rows:
        assert len(r) == len(COLUMNS), r
        assert re.fullmatch(r"C[0-9]|[A-G]", r[1]), f"{r[0]}: class {r[1]!r}"
        assert re.fullmatch(r"\d+", r[6]), f"{r[0]}: recurrences {r[6]!r}"


def test_every_domain_and_pr_has_a_row():
    seen = {d.strip() for r in _table(COLUMNS) for d in r[2].split(",")}
    want = {f"D{i}" for i in range(1, 7)} | {f"PR{i}" for i in range(1, 4)}
    assert want <= seen, want - seen


def test_enforced_by_names_checks_that_exist():
    for r in _table(COLUMNS):
        if r[5] == "unenforced":
            continue
        for ref in r[5].split("; "):
            assert re.fullmatch(r"`[^`]+`", ref), f"{r[0]}: {ref!r}"
            path, _, name = ref.strip("`").partition("::")
            name = name.split("[")[0]
            assert (ROOT / path).exists(), f"{r[0]}: no {path}"
            if name:
                src = (ROOT / path).read_text()
                assert re.search(rf"\bdef {re.escape(name)}\b", src), f"{r[0]}: no {name} in {path}"


def test_summary_counts_match_the_rows():
    rows = _table(COLUMNS)
    want = {}
    for r in rows:
        n, u, rec = want.get(r[1], (0, 0, 0))
        want[r[1]] = (n + 1, u + (r[5] == "unenforced"), rec + int(r[6]))
    got = {r[0]: tuple(int(x) for x in r[1:]) for r in _table(SUMMARY)}
    assert got == want
