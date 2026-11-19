"""THE way a long-running script keeps its output and its time (retro D7-19, class G: suite.py
logged to .live/ while mutation.py's survivors scrolled away; evals.snapshot text ran over an hour
twice with nothing to stop it, and macOS has no `timeout`).

    with live("mutation", minutes=90):   # .live/mutation-<sha>-<pid>.log, stopped after 90 min
        ...

Everything printed (stdout and stderr) also goes to the log, whose path is printed first. The
budget is `LIVE_MAX_MINUTES` when set, else the caller's; past it the script stops with exit 3 and
says so. tests/test_suite_script.py holds every long-running script to this."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.log.write(text)
        self.log.flush()   # a reader that tails sees it at once
        return self.stream.write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):   # isatty, encoding …: the real stream's
        return getattr(self.stream, name)


@contextmanager
def live(name: str, minutes: float):
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "nohead"
    # one log per process: a second run at the same commit (a --status, a nested suite) never
    # truncates a run still writing (review of this PR)
    log_path = ROOT / ".live" / f"{name}-{head}-{os.getpid()}.log"
    log_path.parent.mkdir(exist_ok=True)
    budget = float(os.environ.get("LIVE_MAX_MINUTES") or minutes)

    def over(*_):
        print(f"{name}: over its {budget:g} min budget (LIVE_MAX_MINUTES); log: {log_path}", file=sys.stderr)
        raise SystemExit(3)
    with log_path.open("w") as log:
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(out, log), _Tee(err, log)
        print(f"{name}: full output in {log_path} (budget {budget:g} min)", flush=True)
        previous = signal.signal(signal.SIGALRM, over)
        signal.alarm(max(1, int(budget * 60)))
        try:
            yield log_path
        except BaseException as exc:   # the crash's traceback into the log, before the streams go
            if not (isinstance(exc, SystemExit) and exc.code in (0, None)):
                import traceback
                traceback.print_exc()
            raise
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
            sys.stdout, sys.stderr = out, err
