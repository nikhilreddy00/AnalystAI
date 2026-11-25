"""scripts/domain_diff.py runs the way CLAUDE.md says: `uv run python scripts/domain_diff.py main --show`."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_show_imports_its_text_diff_when_run_as_a_script():
    """Retro 5.7b-2b: run as a script (no repo root on sys.path), --show died on
    `from evals.snapshot import text_changes` after every dataset had been dumped. The import
    now comes first, so a bad ref stops at git, never at the import."""
    if not (ROOT / ".venv" / "bin" / "python").exists():   # mutmut's copy holds no .venv
        pytest.skip("the script runs the repo's .venv, which this copy hasn't")
    run = subprocess.run([sys.executable, "scripts/domain_diff.py", "no-such-ref-xyz", "--show"],
                         cwd=ROOT, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert "ModuleNotFoundError" not in run.stderr, run.stderr[-500:]
    assert "CalledProcessError" in run.stderr   # git worktree add on the bad ref


def test_snapshot_text_reads_a_list_item_by_item():
    """D7-19: a whole chart list compared as one string ran difflib for over an hour; a changed
    bar is now its own line, its path naming the item."""
    from evals.snapshot import text_changes
    a = [{"name": "UPS", "value": 1160}] * 400 + [{"name": "XPO", "value": 225}]
    b = [{"name": "UPS", "value": 1160}] * 400 + [{"name": "XPO", "value": 213}]
    assert [line.split(" |")[0] for _, line in text_changes(a, b, "d")] == ["d[400]/value"]


def _in_linked_worktree() -> bool:
    from scripts.suite import linked_worktree
    return linked_worktree(ROOT)


@pytest.mark.skipif(_in_linked_worktree(), reason="suite.py refuses a linked worktree by design "
                    "(tests/test_suite_script.py); inside suite.py's own worktree it can't nest")
def test_the_suite_runs_on_head_in_a_worktree_never_the_edited_tree():
    """Retro 5.7b-2b: a pack edited mid-run failed 330 tests. scripts/suite.py runs pytest in a
    worktree of HEAD, so the tests it runs are the committed ones; the pytest args pass through."""
    run = subprocess.run([sys.executable, "scripts/suite.py", "tests/test_domain_diff_script.py",
                          "-q", "-k", "show_imports", "-p", "no:cacheprovider", "--rootdir", "."],
                         cwd=ROOT, capture_output=True, text=True,   # a CI shard would deselect it
                         env={k: v for k, v in os.environ.items() if k != "CI_SHARD"})
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout[-500:] + run.stderr[-500:]
    assert "analystai-mutation-" in run.stdout   # the worktree's rootdir, not the repo
