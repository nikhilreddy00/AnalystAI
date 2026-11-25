#!/bin/bash
# PreToolUse(Bash): P58-32 (5.8b twice, 5.8c three times): `pytest … | tail -1 && git commit` commits on red,
# since a pipe's status is its last command's. A command that pipes pytest's output and commits
# is refused: run the tests as their own step, read the result, then commit.
# Wired in .claude/settings.local.json; tracked here so tests/test_shell_edit_hook.py holds it.
cmd=$(jq -r '.tool_input.command // empty')
cmd=${cmd//2>&1/}                       # a redirect, not a command separator
before=${cmd%%git commit*}              # only what runs before the commit (not its message)
if [ "$before" != "$cmd" ] && grep -qE 'pytest[^;&]*\|[^|]' <<<"$before" \
    && ! grep -qE 'pipefail' <<<"$before"; then
  jq -n '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny",
    permissionDecisionReason: "A piped pytest returns its pipe'"'"'s last status, so the commit after it runs on red (P58-32). Run the tests as their own step, read the result, then commit."}}'
fi
exit 0
