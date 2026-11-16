"""scripts/record_run_fixture.py (D3-10, PR14): the recorder runs the case as the eval harness
does, pseudonym key included (it crashed without one), and a failed run never overwrites the
committed fixture."""
import asyncio

import pytest

from api.config import settings
from scripts import record_run_fixture as rec


def test_the_recorder_sets_the_eval_key_and_never_writes_a_failed_run(tmp_path, monkeypatch):
    seen = {}

    async def failed_case(case):
        seen["key"] = settings.encryption_key
        return {"approvals": [], "error": "The analysis step stopped unexpectedly.", "checks": [],
                "hard": []}
    monkeypatch.setattr(settings, "encryption_key", "")
    monkeypatch.setattr(rec, "ClaudeClient", lambda: None)
    monkeypatch.setattr(rec, "run_e2e_case", failed_case)
    out = tmp_path / "e2e-01.json"
    with pytest.raises(SystemExit):
        asyncio.run(rec.main("e2e-01", str(out)))
    assert seen["key"] == rec.EVAL_ENCRYPTION_KEY and not out.exists()
