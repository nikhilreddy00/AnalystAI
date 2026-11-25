"""scripts/suite.py (retro Domain 8): run from a linked worktree it linked that worktree's .venv and
reported 371 failures that weren't there; it refuses, and names the main checkout."""
from scripts import suite
from scripts.mutation import ROOT, worktree


def test_the_main_checkout_is_no_linked_worktree():
    """The code's claim, from wherever the tests run: suite.py runs them in its own worktree, where
    ROOT is one (retro D7-19: asserting on ROOT made this red on every suite.py run)."""
    import subprocess
    from pathlib import Path
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert suite.linked_worktree(Path(common).parent) is False


# Scripts that run for minutes: each keeps its output in .live/ and stops at a time budget, one
# way (scripts/live.py; retro D7-19: suite.py logged, mutation.py's survivors scrolled away, and
# evals.snapshot text ran over an hour twice).
LONG_RUNNING = ("scripts/suite.py", "scripts/mutation.py", "scripts/domain_diff.py", "evals/snapshot.py")


def test_every_long_running_script_keeps_its_output_and_its_time():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = {p: (root / p).read_text() for p in LONG_RUNNING}
    assert [p for p, s in src.items() if "from scripts.live import live" not in s or ' live("' not in s] == []


def test_a_script_over_its_budget_stops_and_its_output_is_kept(monkeypatch, capsys):
    import time

    import pytest

    from scripts.live import live
    monkeypatch.setenv("LIVE_MAX_MINUTES", "0.02")   # ~1 s
    with pytest.raises(SystemExit) as stop:
        with live("budget-test", minutes=30) as log:
            print("written before the stop")
            time.sleep(5)
    assert stop.value.code == 3 and "written before the stop" in log.read_text()
    log.unlink()


def test_a_long_run_holds_the_mac_awake_beside_it_never_around_it(monkeypatch):
    """Retro 5.8c: macOS idle sleep paused a mutation run and its budget; wrapping the run in
    `caffeinate` (a SIP binary) stripped DYLD_LIBRARY_PATH, so the suite lost SQL Server's OpenSSL.
    live() starts caffeinate itself, watching this process, and stops it at the end."""
    import os
    import sys

    from scripts import live as live_mod
    started = []

    class Fake:
        def terminate(self):
            started.append("stopped")

    real = live_mod.subprocess.Popen

    def popen(cmd, **kw):   # git rev-parse still runs; caffeinate is recorded
        if cmd[0] != "caffeinate":
            return real(cmd, **kw)
        started.append(cmd)
        return Fake()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(live_mod.subprocess, "Popen", popen)
    with live_mod.live("awake-test", minutes=1) as log:
        assert started == [["caffeinate", "-i", "-w", str(os.getpid())]]
    assert started[-1] == "stopped"
    log.unlink()


def test_the_suite_names_the_database_tests_it_cannot_run(monkeypatch):
    """Retro 5.8c Part 3: two failures only CI saw were database tests skipped locally."""
    for v in suite.DB_URLS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TEST_PG_URL", "postgresql://x")
    assert suite.missing_dbs() == [v for v in suite.DB_URLS if v != "TEST_PG_URL"]


def test_a_crash_leaves_its_traceback_in_the_log():
    """Review of this PR: the streams were restored before the uncaught error printed, so the
    one line a reader of .live/ needs was missing."""
    import pytest

    from scripts.live import live
    with pytest.raises(KeyError):
        with live("crash-test", minutes=1) as log:
            raise KeyError("the column a run could not find")
    assert "the column a run could not find" in log.read_text() and "Traceback" in log.read_text()
    log.unlink()


def test_a_linked_worktree_is_refused_with_the_main_checkout_named(monkeypatch, capsys):
    with worktree() as wt:
        assert suite.linked_worktree(wt) is True
        monkeypatch.setattr(suite, "ROOT", wt)   # suite.py as it runs from that worktree
        assert suite.main([]) == 2
    assert "main checkout" in capsys.readouterr().err
