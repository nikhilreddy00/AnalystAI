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


WALL = re.compile(r"^Wall clock: \d+(\.\d+)? h \(first commit .+ → merge .+\)", re.M)


def test_every_session_note_says_its_wall_clock():
    """Retro Domain 7 (D1-59): spend was tracked per session, time was not, so "are we slower?"
    took a dig through git (Domain 6 ~7.5 h, Domain 7 ~20.5 h). From Domain 7 on, each note says
    its hours, first commit to merge."""
    notes = {p.name: p.read_text() for p in sorted(BASELINES.glob("phase5_7*.md"))}
    due = {n for n, t in notes.items() if (_dated(t) or "") >= "2026-11-16"}
    assert "phase5_7_iot.md" in due
    assert sorted(n for n in due if not WALL.search(notes[n])) == []
    assert not WALL.search("Wall clock: about a day")


# ROADMAP 5.7 "Domain regression contract": the gates a domain's end check runs.
GATES = ("live browser at 1440 px", "finished domains' filter reads", "all-domain answer probe",
         "playwright specs")
GATE_LINE = re.compile(r"^- (.+?): (done|not done \(.+\))", re.M)


def gate_status(text: str) -> dict[str, str]:
    """{gate: 'done' | 'not done (why)'} from the note's '## Gates' section."""
    section = text.split("\n## Gates", 1)[1].split("\n## ", 1)[0] if "\n## Gates" in text else ""
    return {g: s for g, s in GATE_LINE.findall(section) if g in GATES}


def test_every_session_note_states_each_regression_gate():
    """Retro Domain 8: three contract gates were trimmed (the live 1440 px check on three
    sources, the finished domains' filter reads, the all-domain probe) and only a post-merge
    review found it. From Domain 8 on, the note says each gate: done, or not done and why."""
    notes = {p.name: p.read_text() for p in sorted(BASELINES.glob("phase5_7*.md"))}
    due = {n: t for n, t in notes.items() if (_dated(t) or "") >= "2026-11-18"}
    assert "phase5_7_logistics.md" in due
    assert {n: sorted(set(GATES) - set(gate_status(t))) for n, t in due.items()
            if set(gate_status(t)) != set(GATES)} == {}
    assert gate_status("# Note\n\n## Gates\n- playwright specs: done\n- live browser at 1440 px: skipped\n") == {
        "playwright specs": "done"}   # "skipped" says no reason: not a status
