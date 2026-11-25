"""scripts/hooks/no_shell_edits.sh: the user's rule (repo files change through Edit/Write) refuses
a write whose target is a repo file, and nothing else (P58-38: a grep for "sed -i" in the ledger
and a scratchpad write naming tests/conftest.py in its text were refused)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "no_shell_edits.sh"
PY_WRITE_SCRATCH = ('uv run python - "$F" <<EOF\nimport pathlib, sys\np = pathlib.Path(sys.argv[1])\n'
                    't = p.read_text().replace("x", "see tests/conftest.py PEAK_MB")\np.write_text(t)\nEOF')
CASES = [
    ("allow", 'grep -n -i -e "sed -i" docs/retro-ledger.md'),
    ("allow", 'for k in "nightly" "sed -i"; do grep -n -e "$k" docs/retro-ledger.md; done'),
    ("allow", PY_WRITE_SCRATCH),
    ("allow", "cat tests/test_x.py > /tmp/scratch/copy.py"),
    ("allow", "git show HEAD:tests/conftest.py | grep -c PEAK_MB"),
    ("allow", "uv run pytest -q tests/test_share_links.py 2>&1 | tail -2"),
    ("deny", "sed -i '' 's/a/b/' tests/test_x.py"),
    ("deny", "sed -i.bak -e 's/a/b/' scripts/mutation.py"),
    ("deny", "cd repo && sed -E -i 's/a/b/' docs/ROADMAP.md"),
    ("deny", "echo x >> docs/ROADMAP.md"),
    ("deny", "printf x > ./tests/a.py"),
    ("deny", "cat > CLAUDE.md <<EOF\nx\nEOF"),
    ("deny", 'python - <<EOF\nfrom pathlib import Path\nPath("tests/conftest.py").write_text("x")\nEOF'),
    ("deny", "python -c \"open('agents/a.py','w').write('x')\""),
]


@pytest.mark.skipif(not shutil.which("jq"), reason="the hook reads its input with jq")
@pytest.mark.parametrize("want,cmd", CASES)
def test_the_hook_refuses_a_write_into_a_repo_file_and_nothing_else(want, cmd):
    out = subprocess.run(["bash", str(HOOK)], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True, check=True).stdout
    assert ("deny" if '"deny"' in out else "allow") == want, cmd


PIPED = HOOK.with_name("no_piped_test_commit.sh")


@pytest.mark.skipif(not shutil.which("jq"), reason="the hook reads its input with jq")
@pytest.mark.parametrize("want,cmd", [
    # P58-32, as it ran in 5.8c: the commit ran on the pipe's status, tail's
    ("deny", 'uv run pytest -q tests/a.py 2>&1 | tail -1 && git add a && git commit -m "x"'),
    ("deny", "uv run pytest -q tests/a.py | grep passed; git commit -qm x"),
    ("allow", "uv run pytest -q tests/a.py 2>&1 | tail -1"),
    ("allow", "uv run pytest -q tests/a.py && git commit -m x"),
    ("allow", "set -o pipefail; uv run pytest -q tests/a.py | tail -1 && git commit -m x"),
    ("allow", 'git commit -m "tests: pytest | tail once misled us"'),
])
def test_a_piped_test_run_never_commits(want, cmd):
    out = subprocess.run(["bash", str(PIPED)], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True, check=True).stdout
    assert ("deny" if '"deny"' in out else "allow") == want, cmd
