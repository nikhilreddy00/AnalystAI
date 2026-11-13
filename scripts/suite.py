"""The tests on HEAD, in a throwaway worktree: an edit made while a long run goes can't race it
(5.7b-2b: 330 failures from a pack edited mid-run), and the run measures exactly what is
committed (PR7-06). Uncommitted changes are listed, never measured.

    uv run python scripts/suite.py                     # the full suite: pytest -q -n 8 -rx
    uv run python scripts/suite.py tests/test_saas.py -k moved   # any pytest arguments

Exit: pytest's. $0: no model call."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.mutation import ROOT, worktree  # noqa: E402


def main(args: list[str]) -> int:
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    if dirty := [ln[3:] for ln in dirty if ln]:
        print("not measured (uncommitted): " + " ".join(dirty), file=sys.stderr)
    with worktree() as wt:
        (wt / ".venv").symlink_to(ROOT / ".venv")   # scripts and tests call <root>/.venv/bin/…
        head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        print(f"suite at {head} in {wt}", flush=True)
        return subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest",
                               *(args or ["-q", "-n", "8", "-rx"])], cwd=wt, env=os.environ).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
