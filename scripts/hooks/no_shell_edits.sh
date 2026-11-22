#!/bin/bash
# PreToolUse(Bash): the user's rule "use the Edit tool for code changes, not python/sed replace
# scripts" (retro 5.7b-2a). Refuses a command whose WRITE targets a repo source file: sed -i on
# it, a redirect into it, python's Path(it)/open(it, "w") beside a write. A repo path read or
# quoted elsewhere is fine (retro 5.8b hardening: a grep for "sed -i" in the ledger, and a
# scratchpad write whose text named tests/conftest.py, were refused). Scratchpad files are untouched.
# Wired in .claude/settings.local.json; tracked here so tests/test_shell_edit_hook.py holds it.
cmd=$(jq -r '.tool_input.command // empty')
src='(agents|api|domain_pack|domain_packs|evals|llm|memory|orchestrator|scripts|tests|docs|frontend/src)/[A-Za-z0-9_./-]+\.(py|ya?ml|md|ts|tsx|toml)|(GLOSSARY|CLAUDE|CODING_STANDARDS)\.md|pyproject\.toml'
lead='(^|[[:space:];|&(`])'   # a command word starts here, never inside a quoted pattern
sed_i="${lead}sed +(-[A-Za-z]* +)*-i[^|;&]*[[:space:]\"'](\./)?($src)"
redirect=">>? *[\"']?(\./)?($src)"
py_target="(Path|open)\( *[\"'](\./)?($src)"
py_write="write_text\(|write_bytes\(|open\([^)]*['\"][wa]b?['\"]"
if grep -qE "$sed_i|$redirect" <<<"$cmd" || { grep -qE "$py_target" <<<"$cmd" && grep -qE "$py_write" <<<"$cmd"; }; then
  jq -n '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny",
    permissionDecisionReason: "Repo files change through the Edit/Write tools, not sed -i, redirects or python write scripts (user rule). Scratchpad files are fine."}}'
fi
exit 0
