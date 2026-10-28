# Phase 3.5 — UI and layout (review note)

Status: **done** (2026-10-28). Signed off by the user on the before/after screenshots.
No API credits spent; evals unchanged (no backend or prompt changes).

## Direction

The user picked "B's layout with A's look" from three samples (audit + samples page:
https://claude.ai/artifact/SCvoS8yLHzQ1uPK2Ykwbma).

- **Layout (Workbench):** on `/chat` the chat column (left) drives a report canvas (right)
  with Summary / Evidence / Data / Review tabs. Below `lg` the halves become Chat / Report
  tabs. History is a drawer. `/chat` and `/dashboard` share one header.
- **Look (Ledger):** cool neutrals, one teal accent, IBM Plex Sans + Mono, light and dark.
  Colour lives in the charts.

## Built

| Area | Change |
|---|---|
| Theme | Tokens in `globals.css` for both themes; system default + toggle, applied before paint; focus ring; reduced motion |
| Font | Fixed: `--font-sans` pointed at itself, so everything rendered in Times |
| Chat | `ChatInterface` + new `ReportCanvas`, `AppHeader`, `ThemeToggle`, `PinButton`; progress as a checklist; errors as a card with Try again |
| Panels | Insight, Evidence, Review, Targeted, Briefing, Connector, Approval reworked on tokens; native `<dialog>` for modals and drawers |
| Charts | Validated categorical palette (`--chart-1..8`); scatter gets numeric axes; one colour for single-series bars; numeric categories sorted |
| Dashboard | Phone width works; filters collapse on phones; PNG export uses the page background |
| Landing, auth | Rebuilt on tokens; pipeline shown in its real graph order |
| Guards | `tests/test_frontend_tokens.py` (no raw palette classes or hex outside tokens); `npm run lint` in CI |
| UI checks | `scripts/ui_check/` mock API + Supabase + SSE fixtures for repeatable screenshots |

## Done-when checks

| Criterion | Result |
|---|---|
| User signs off on screenshots | yes (2026-10-28) |
| Light and dark both work | yes: chat, report, dashboard, landing checked in both |
| Every page works at phone width | yes: `scrollWidth` = 390 on a 390px viewport for landing, auth, chat (both tabs), dashboard (was 454 on dashboard) |
| Keyboard + screen-reader basics | no unnamed controls on chat or dashboard (scripted check); visible focus ring; Esc closes modal and drawers; focus moves into the modal; report tabs use `role=tab` with arrow keys; progress is `aria-live` |
| Contrast | text tokens ≥ 5.3:1 on every surface in both themes (lowest: destructive on card, light) |
| tsc / lint / tests | tsc clean; eslint 0 problems (was 6 on main); pytest 158 passed; ruff clean |
| CI green | yes: run 40618000163 (backend, frontend incl. lint) |

## Deviations

- **`lib/api.ts` changed**, which the plan said to avoid: `pinChart` ignored HTTP errors, so a
  failed pin showed as pinned. It now throws. No SSE or type changes.
- **Pre-existing lint errors fixed** (6 on main) so CI can run lint.
- **Backend chart colours:** the backend always sends Tableau-10 hex. The frontend keeps only
  meaningful ones (target green/red, ordinal ramp) and replaces the generic cycle with theme
  slots. The cleaner fix (backend sends `[]` for generic colours) touches the PPTX export,
  so it is left for Phase 5.
- **Not verified with real auth or backend:** all screenshots use the mock kit. The insight
  report and reviewer notes in the fixture are hand-written from a real e2e answer's numbers;
  the evidence, charts, KPIs and briefing are real computed output.

## Screenshots

Before: `before-*.jpg`. After: `after-*.jpg` in [`phase3_5/`](phase3_5/).

## Follow-up raised at sign-off

The user asked how chart choice can be trusted. Today the visualization agent picks charts
from column types only, not from the question or findings (e.g. the Segment B headline had no
chart behind it). Proposed scope for Phase 5 visualization: chart per finding, a "why this
chart" line linked to evidence, a chart linter, an analyst-labelled chart eval set, and user
overrides. Added to docs/ROADMAP.md Phase 5 (Visualization: chart trust) on 2026-10-28.
