"""Dashboard filters (user report, 2026-11-03): ChartRenderer kept `useState(original)` — a copy of
its whole chart prop — so new data (a filter's) never reached the chart. A component keeps only
what the user changed in state; data always comes from props."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "frontend" / "src"


def test_no_component_freezes_a_chart_prop_in_state():
    bad = [f"{p.relative_to(SRC)}:{i}" for p in SRC.rglob("*.tsx")
           for i, line in enumerate(p.read_text().splitlines(), 1)
           if re.search(r"useState(?:<[^>]*>)?\((?:original|config|chart|props)\b", line)]
    assert bad == [], "derive from props; keep only the user's override in state"
