"""Recurring bug class (Phase 3, Intake, Visualization, Insight, Ingestion): data-derived text
reached a prompt undelimited because delimiting was a per-builder convention. Every agent module
that calls the LLM must delimit what it sends and tell the model so; a new agent fails here."""
from pathlib import Path

AGENTS = Path(__file__).parent.parent / "agents"


def test_every_llm_calling_agent_delimits_untrusted_data():
    missing = [p.name for p in sorted(AGENTS.glob("*.py"))
               if ".complete(" in (src := p.read_text())
               and not ("delimit_untrusted(" in src and "UNTRUSTED_NOTE" in src)]
    assert missing == [], f"agents sending data to the LLM without delimiting it: {missing}"
