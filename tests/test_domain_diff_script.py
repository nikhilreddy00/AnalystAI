"""scripts/domain_diff.py runs the way CLAUDE.md says: `uv run python scripts/domain_diff.py main --show`."""
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
