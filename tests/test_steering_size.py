"""The always-loaded steering files stay small (retro 2026-11-06: CLAUDE.md was cut twice, 36 KB
→ 387 → 234 lines, and grew back each time: nothing capped it). Reference material for one branch
of the work belongs in a doc behind a one-line pointer (docs/architecture.md, docs/agent-contract.md)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPS = {"CLAUDE.md": 180, "CODING_STANDARDS.md": 120}


def test_steering_files_stay_under_their_caps():
    over = {f: n for f, cap in CAPS.items() if (n := len((ROOT / f).read_text().splitlines())) > cap}
    assert not over, f"over the cap {CAPS}: {over}; move reference behind a pointer"


def test_a_status_row_is_a_line_not_a_log():
    """Retro 5.7b-3: the 5.7 status row grew to 330 characters, one sub-phase at a time, past
    the line cap; the detail is ROADMAP's status log."""
    rows = [r for r in (ROOT / "CLAUDE.md").read_text().splitlines() if r.startswith("| ")]
    assert rows and not [r for r in rows if len(r) > 160], "keep the detail in docs/ROADMAP.md"
