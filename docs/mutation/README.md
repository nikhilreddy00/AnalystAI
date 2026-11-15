# The mutation gate

`gate.json` is written by `scripts/mutation.py --record`; the pre-push hook and CI run
`--check`. The commands and their limits are in the docstring at the top of the script.

## Closing survivors in parallel

One agent per module group, each with `isolation: worktree` and its own test files. Give the
group's survivors to it as a file (the `--lines` run prints each one as `file:line id`, `- old`,
`+ new`).

The agent's worktree starts on main, not on the branch. Every brief carries these lines:

```
Branch <branch>, SHA <sha>. First: `git reset --hard <sha>`, then
`uv sync --all-extras --all-groups` (pytest-asyncio is missing otherwise).
Survivors: <scratchpad>/survivors_<group>.txt. Close each by exactly one of:
1. a test that kills it, proven by `uv run python scripts/mutation.py --plant <file> '<old>'
   '<new>' --tests <file> -k <test>` printing "caught";
2. dead code deleted;
3. a named equivalent: report its exact old line, new line and a one-sentence reason (I add it
   to docs/mutation/gate.json; you never edit it).
Tests you may edit: <files>. Run only them, plus `uv run ruff check`.
Commit, staging by explicit path. Report the commit SHA and a table: mutant → how closed.
```

After each report:

```
git cherry-pick <sha>
git worktree remove -f -f <worktree path>
git branch -D <its branch>     # Claude Code leaves it behind
```

Then rerun the gate with `--record` on the merged branch: a test can kill a mutant alone and
still miss it in the gate (it runs on a cold cache, without `tests/test_one_definition.py`).
