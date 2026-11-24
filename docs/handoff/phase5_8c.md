# Phase 5.8c handoff: the work left, in order (written 2026-11-24)

**Why this file exists:** 5.8c ran past 20 h in one session. On 2026-11-24 the user split what was left into
parts, one fresh session each, in the order below. **Start every session by reading this file**, do only the
next unchecked part, tick it here in the part's last commit, and stop. Don't start 5.8d (Snowflake + BigQuery)
until every part here is ticked and the user signs off.

**State at handoff:** branch `5.8c-imports`, head `a76d61b`, not pushed and not merged. Everything is built
(uploads to 100 MB, Stripe and Shopify imports with Refresh, oracle, matrix, canary sinks, paid E2E k=1, live
check, README, ADR, audit pass 1); see `docs/baselines/phase5_8c.md`. The 5.8 status rows in CLAUDE.md and
ROADMAP say "built, finishing in parts" until Part 5.
`docs/mutation/gate.json` has uncommitted rows from an earlier partial `--record`: leave them, the next
`--record` rewrites them; the equivalents you add go in that file.

## Standing rules (unchanged; also in CLAUDE.md and the user's brief)
- Never commit keys, tokens or connection strings; keys stay in the gitignored `.env`, never pasted in chat.
- Repo files change only through Edit/Write (hook `scripts/hooks/no_shell_edits.sh`).
- No `pkill -f`. Stop stacks with `scripts/live_check`, databases with `scripts/test_dbs.sh down`.
- Run tests as their own step, then commit (a piped pytest followed by a commit is refused, P58-32).
- Never push with `--no-verify`. CI green (`gh run list`) before any merge. The user approves every merge.
- Eval cap $2.50 per part that pays: ask at $2.20, hard stop at $2.35; estimate first, `--max-usd` ≈ 1.3×.
- PRs through `mattpocock-skills:pr`; the body ends with "🤖 Generated with [Claude Code](https://claude.com/claude-code)".

## Mutation gate: lessons from this session (read before Parts 1–2)
- Wrap every long run in `caffeinate -i`: macOS idle sleep pauses the run and its time limit.
- Set `LIVE_MAX_MINUTES` (the script's default is 120) and `ENCRYPTION_KEY=x`.
- Take the function list from `uv run python scripts/mutation.py --check main`'s printed `run:` line, never
  by hand (a hand list missed `Role.reads` and `detect`).
- zsh doesn't split `$VAR` into words: write `${=A}` or paste the list.
- Narrow runs (`--tests`, no `--record`) find survivors fast. Leave `tests/test_oracle.py` and
  `tests/test_properties.py` out of them: the oracle is ~6 of the 8.5 min the narrow files take, and a
  4 h narrow run with it never finished (its results are lost when it's cut).
- Only survivors can be named in `gate.json` (`{old, new, n, reason}`, the exact lines from the log);
  timeouts and no_tests count as unexplained.
- Survivor lines: `awk '/^  connectors\//{print; getline; print; getline; print}' <log>`.

## Parts (in order)

### [x] Part 1a: close the survivors in `connectors/imports.py`
Done 2026-11-24 (`8464f10`): 35 survivors → 11 killed by three tests in `tests/test_import_stripe.py`,
24 named in gate.json; the rerun at 8464f10 says "24, 24 of them named equivalents". Left for Part 2: 6
timeouts (`_gql` mutmut_39; `_shopify_orders` 92, 99, 124, 175, 181). A `--lines` run passes them, but
`--record` counts a timeout as unexplained, so `_gql` and `_shopify_orders` won't record until those
mutants fail fast (`mutmut show` each in the run's worktree while it runs).

- Input: the narrow run started 2026-11-24 ~14:45 (both runs in one background job):
  `/private/tmp/claude-501/-Users-uvnikhil-Projects-AnalystAI/fc4cf7d6-bfe9-4056-9507-1da101118e31/scratchpad/narrowA.log`,
  full output in `.live/mutation-a76d61b-*.log` (newest). If neither exists (reboot), rerun it:
  `LIVE_MAX_MINUTES=150 ENCRYPTION_KEY=x caffeinate -i uv run python scripts/mutation.py <the imports.py names from --check's run: line> --lines 4ca1bad0318180ba4159959f2e672bc67b9a841f --tests tests/test_import_engine.py tests/test_import_stripe.py tests/test_import_shopify.py tests/test_imports.py tests/test_imports_audit.py tests/test_connect_matrix.py tests/test_stripe_exports.py`
- Each survivor: a test that kills it (red first) or a named equivalent with its reason. Expected equivalents
  from narrow run 1: `get_json`'s "get" method (httpx upper-cases); `fetch`'s header-case mutants
  ("STRIPE-SHOULD-RETRY", "RETRY-AFTER") and "XXXX" as the retry-after default; `refresh` break→return and
  lost=None; `Table.frame` `truncated` None/True (`QueryResult.frame` ignores it); `_stripe_auth` and
  `X-Shopify-Access-Token` header case; `_stripe_test` `what="Account"` and `_stripe_auth(None)`;
  `_stripe_check` and `stripe_money` "XXXX" defaults. Check each against the new log; never name one unread.
- Done when: a narrow rerun of the same functions shows 0 unexplained survivors; committed.

### [x] Part 1b: close the survivors in the reader (`connectors/csv_connector.py`, `connectors/base.py`)
Done 2026-11-24 (`87ad07e`, `84848f3`): 64 survivors → 47 killed by tests in `tests/test_reader_refusals.py`
(CSV and parquet cell bounds, the parquet footer's unpacked size, the nested bound, leading zeros re-read in
six layouts, the decimal mark, low_memory, JSON dtype and its ValueError), 3 gone with the dead `utf-8-sig`
codec line (pandas drops a BOM itself), 13 named in gate.json (a 14th, on the codec line, went with it).
The rerun at 84848f3 says "13, 13 of them named equivalents", 0 timeouts. Hashes for
`_read_text_table` are stale after that line's change: Part 2's `--record` rewrites them.
- Input: `narrowB.log` beside `narrowA.log` (same job, runs second). Rerun command as 1a with the reader
  names (`normalize_frame sources_table _first_lines _layout _read_text_table _json_records _readable_size
  _read_parquet _read_bytes`) and `--tests tests/test_reader_refusals.py tests/test_connectors_sources.py
  tests/test_share_links.py tests/test_sources_readme.py tests/test_ingestion.py tests/test_imports.py
  tests/test_big_uploads.py`.
- Expected equivalents: `_json_records` "UTF-8" ×2 (codec names ignore case); `sources_table` `[+1]` and
  `split(None)`; `_first_lines` head-size / `size > len(text)` mutants if they still survive.
- Done when: as 1a; committed.

### [ ] Part 2: the full gate, recorded
- `uv run python scripts/mutation.py --check main` → copy its `run:` line; run it with `--record`, no
  `--tests`, under `LIVE_MAX_MINUTES=600 ENCRYPTION_KEY=x caffeinate -i` in the background (~6 h, overnight).
- Survivors the narrow runs missed (reached only by the oracle or the full suite): close them as in Part 1,
  then record again.
- Done when: the record is committed and `--check main` passes.

### [ ] Part 3: full suite, push, CI
- `uv run python scripts/suite.py` on the final commit (worktree, background, ~12 min); push (pre-push hook);
  `gh run list --branch 5.8c-imports` until green; read a red by job (`gh run view <id> --json jobs`).
- Done when: the suite passes on the final commit and CI is green.

### [ ] Part 4: the baseline note and the PR
- `docs/baselines/phase5_8c.md`: the mutation gate line (mutants, survivors, named), the full-suite line, CI,
  wall clock, a Retro line for the gate lessons above (sleep and caffeinate, the oracle in narrow runs) as
  a ledger row (new or recurrence: check `uv run python scripts/ledger.py --min-recurrences 1`).
- Open the PR with `mattpocock-skills:pr`. The user approves the merge.
- Done when: PR open, CI green on it.

### [ ] Part 5: after the merge
- Quick audit on main (report + recurrences), per the user's rule. Set the 5.8 rows in CLAUDE.md and ROADMAP
  to "5.8c ✅ <date>, PR #<n>". Say "Phase 5.8c done"; remind the user to type `/retro` and to delete the
  analystai-seed Stripe test key.

### [ ] Part 6: senior review and retest of each source, one source per session, before 5.8d
The user's rule (2026-11-24): one source at a time. For each: a senior-developer review of that source's code,
commits and PRs with every review skill and open-source tool, then tests and a paid eval of that source. Any
bug: plan mode, research, the efficient fix at its cause (red test first, ledger row). Move to the next source
only when this one meets its bar. One branch and one PR per source (`5.8c-review-<source>`), merged before
the next starts.

**The bar for each source (all must hold):**
- 0 open findings at major or above from: a senior audit (normal inputs, real failure modes), `code-review`
  (high), `pr-review-toolkit:silent-failure-hunter`, `pr-test-analyzer`, `security-review`,
  `type-design-analyzer` where it holds state; tools: semgrep, zizmor, actionlint, gitleaks 8.24.3 full
  history, pip-audit (install the missing ones first; 5.8c's audit skipped them).
- Its rows of `tests/test_oracle.py` and `tests/test_connect_matrix.py` green; its own tests green.
- Its paid E2E cases pass at k=1 (any failure: fix, then k=3 on that case); spend within the cap.
- Mutation `--check main` passes on what the fix touched.
- A live browser check of the source at 1440 px (the user watches, doesn't click).

Order:
- [ ] **6a Stripe**, with the open rows it owns: P58-46 (subscription item rows counted as subscriptions),
  P58-47 (money summed across currencies), P58-51 (a table's stale / cut / empty state never reaches the
  analysis; shared with Shopify, fixed once here). All three are pinned xfail-strict in
  `tests/test_import_answers.py`. E2E cases e2e-105..108 (2 of 4 passed at handoff).
- [ ] **6b Shopify**: P58-47 and P58-51 checked for orders (multi-currency shops; the 60-day window); the
  retail pack reading the `Test` column.
- [ ] **6c Uploads at 100 MB** (CSV/TSV, xlsx, JSON, parquet; the must-read corpus; `peak_mb()` bounds).
- [ ] **6d (optional, the user decides at 6c's end)**: the earlier sources, retested only (oracle, matrix,
  one k=1 each): share links, Postgres, MySQL, MariaDB, SQL Server, SQLite, REST API, Sheets.

Then: the user signs off → Phase 5.8d.
