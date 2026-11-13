"""scripts/domain_diff.py runs the way CLAUDE.md says: `uv run python scripts/domain_diff.py main --show`."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_show_imports_its_text_diff_when_run_as_a_script():
    """Retro 5.7b-2b: run as a script (no repo root on sys.path), --show died on
    `from evals.snapshot import text_changes` after every dataset had been dumped. The import
    now comes first, so a bad ref stops at git, never at the import."""
    run = subprocess.run([sys.executable, "scripts/domain_diff.py", "no-such-ref-xyz", "--show"],
                         cwd=ROOT, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert "ModuleNotFoundError" not in run.stderr, run.stderr[-500:]
    assert "CalledProcessError" in run.stderr   # git worktree add on the bad ref


def test_the_suite_runs_on_head_in_a_worktree_never_the_edited_tree():
    """Retro 5.7b-2b: a pack edited mid-run failed 330 tests. scripts/suite.py runs pytest in a
    worktree of HEAD, so the tests it runs are the committed ones; the pytest args pass through."""
    run = subprocess.run([sys.executable, "scripts/suite.py", "tests/test_domain_diff_script.py",
                          "-q", "-k", "show_imports", "-p", "no:cacheprovider", "--rootdir", "."],
                         cwd=ROOT, capture_output=True, text=True,   # a CI shard would deselect it
                         env={k: v for k, v in os.environ.items() if k != "CI_SHARD"})
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout[-500:] + run.stderr[-500:]
    assert "analystai-mutation-" in run.stdout   # the worktree's rootdir, not the repo
