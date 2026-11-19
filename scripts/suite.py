"""The tests on HEAD, in a throwaway worktree: an edit made while a long run goes can't race it
(5.7b-2b: 330 failures from a pack edited mid-run), and the run measures exactly what is
committed (PR7-06). Uncommitted changes are listed, never measured.

    uv run python scripts/suite.py                     # the full suite: pytest -q -n 8 -rxfE
    uv run python scripts/suite.py tests/test_saas.py -k moved   # any pytest arguments

From the main checkout only (a linked worktree's .venv isn't the repo's: exit 2). The whole
output, every failure line included, goes to .live/suite-<sha>-<pid>.log as well (scripts/live.py).
Exit: pytest's. $0: no model call."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.live import live  # noqa: E402
from scripts.mutation import ROOT, worktree  # noqa: E402


def linked_worktree(path: Path) -> bool:
    """True in a `git worktree add` checkout: its .venv isn't the repo's (retro Domain 8)."""
    git = lambda *a: subprocess.run(["git", "-C", str(path), "rev-parse", *a],  # noqa: E731
                                    capture_output=True, text=True, check=True).stdout.strip()
    return Path(path, git("--git-dir")).resolve() != Path(path, git("--git-common-dir")).resolve()


def main(args: list[str]) -> int:
    if linked_worktree(ROOT):
        print("suite.py makes its own worktree of HEAD: run it from the main checkout "
              f"(this is a linked worktree, {ROOT})", file=sys.stderr)
        return 2
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    if dirty := [ln[3:] for ln in dirty if ln]:
        print("not measured (uncommitted): " + " ".join(dirty), file=sys.stderr)
    with worktree() as wt, live("suite", minutes=40):   # every failure line in .live/, 40 min
        (wt / ".venv").symlink_to(ROOT / ".venv")   # scripts and tests call <root>/.venv/bin/…
        print(f"suite in {wt}", flush=True)
        run = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "pytest",
                                *(args or ["-q", "-n", "8", "-rxfE"])], cwd=wt, env=os.environ,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for line in run.stdout:
                sys.stdout.write(line)
            return run.wait()
        finally:   # past the budget (or any stop): no pytest left running in a removed worktree
            if run.poll() is None:
                run.kill()
                run.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
