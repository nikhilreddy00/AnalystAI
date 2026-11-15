# Phase 5.7b-3 — prose, charts and the audit itself (2026-11-15)

Plan: `~/.claude/plans/pasted-content-id-0a38-start-phase-expressive-barto.md`. It checks off
every plan-mode step of the ROADMAP 5.7 contract by name:
- brainstorming, with the scope fixed by ROADMAP 5.7b-3 steps 1–4;
- the `domain-modeling` term ledger (typed claim, causal claim, benchmark verdict, free text,
  bridge), GLOSSARY entries and ADR-0009;
- `codebase-design` (the deletion test dropped a proposed `overview_bridge` field: the bridge is
  derived from `ledger_kpi` / `stock_kpi`);
- research (not needed: in-repo precedent);
- grilling, answered as a senior engineer;
- one red-team subagent: 4 blockers, 18 majors and 8 minors, all folded in;
- sizing (one PR; the split point after step 2 was not needed).

The audit procedure this session wrote into the 5.7 contract ran on this branch; its stop rule
is properties, the mutation gate and the ledger checks green.

(Results below are filled in at the session's end.)
