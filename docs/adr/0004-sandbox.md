# ADR-0004: Keep E2B for v0.1, put an AST/import allowlist in front of it, and treat data as untrusted

- Status: Proposed (Phase 0, 2026-10-27)
- Gaps addressed: G3, G15, G16

## Context
- LLM-generated Python goes straight to E2B with no static check (`sandbox/executor.py`).
- Julius gets its grounding from executed code [S6]. The code is the evidence, so it has to be
  safe and must reach the sandbox.
- Self-hosters may not want a third-party sandbox. A local sandbox is a meaningful amount of work.

## Decision
1. **Keep E2B** as the only executor for v0.1. Its interface (`run(code, data) -> ExecutionResult`)
   is already narrow.
2. **Static guard before E2B** (`guardrails/code.py`), using stdlib `ast`. Rejected code comes back
   with a reason that is fed into the one guided retry.
   - Import allowlist: `pandas, numpy, scipy, statsmodels, sklearn, math, statistics, json,
     datetime, re, collections, itertools, functools`.
   - Banned names and calls: `eval`, `exec`, `compile`, `open`, `__import__`, `getattr` on
     dunder strings, `os`, `sys`, `subprocess`, `socket`, `requests`, `httpx`, `pickle`.
3. **Untrusted data**: cell values and column names enter prompts only inside a delimited block
   labeled as data, and never as instructions. Injected-cell cases are part of the eval set
   ([ADR-0002](0002-eval-method.md)).
4. **Won't do before v0.1**: a local Docker or nsjail sandbox. Revisit after release if users ask.

## Consequences
- An `E2B_API_KEY` is still required to install, and the README must say so.
- The allowlist will reject some valid analyses. Each rejection is logged in the trace, so the list
  grows from evidence, not guesses.

---
Source keys ([P1], [S6], …) refer to the Sources list in `docs/research/2026-10-27-data-agents.md`.
