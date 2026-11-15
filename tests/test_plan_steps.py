"""D2-31, PR3-01 (process steps skipped): from 5.7b-3 on, every Phase 5.7 session note names each
plan-mode step of the ROADMAP 5.7 contract and the audit's stop rule. Limit: it reads the note's
words, not the work they describe."""
import re
from pathlib import Path

BASELINES = Path(__file__).resolve().parents[1] / "docs" / "baselines"
STEPS = ("brainstorming", "domain-modeling", "codebase-design", "research", "grilling", "red-team",
         "sizing", "stop rule")
SINCE = "2026-11-15"   # the session that wrote the audit procedure into the 5.7 contract


def _dated(text: str) -> str | None:
    """The note's date: the first ISO date in its first five lines."""
    m = re.search(r"\d{4}-\d{2}-\d{2}", "\n".join(text.splitlines()[:5]))
    return m.group() if m else None


def missing_steps(text: str) -> list[str]:
    low = text.lower()
    return [s for s in STEPS if s not in low]


def test_every_session_note_names_its_plan_steps_and_the_stop_rule():
    notes = {p.name: p.read_text() for p in sorted(BASELINES.glob("phase5_7*.md"))}
    due = {n: t for n, t in notes.items() if (_dated(t) or "") >= SINCE}
    assert "phase5_7b_3.md" in due   # no vacuous pass: the first note it binds
    assert {n: m for n, t in due.items() if (m := missing_steps(t))} == {}
    assert missing_steps("brainstorming, domain-modeling, codebase-design, red-team") == [
        "research", "grilling", "sizing", "stop rule"]
