# Tool facts for Phase 5.7b-1: Hypothesis, import-linter, mutmut 3

Researched 2026-11-09. Sources: PyPI JSON API (latest versions), the released wheels' source
(downloaded with `pip download --no-deps`, read directly; GitHub paths given for each file), the
official docs, and three throwaway experiments in a scratch venv (marked **[verified locally]**:
Python 3.11, pandas 3.0.3, pytest 9.0.3, pytest-xdist 3.8.0, i.e. this repo's pins).
"unconfirmed" = no primary source found.

## What the repo pins today

- `requires-python = ">=3.11"`; the venv runs 3.11.14 (`pyproject.toml` line 6).
- `pandas>=2.2` and the lock has **pandas 3.0.3**; pytest 9.0.3; pytest-xdist 3.8.0 (dependency-group `dev`).
- **No** hypothesis, import-linter, grimp or mutmut in `pyproject.toml` or `uv.lock`.
- `[tool.pytest.ini_options]` only sets `asyncio_mode = "auto"` (no `addopts`, so no `-n` is forced).
- `domain_pack.py` is a **single top-level module**, with `PACKS = Path(__file__).parent / "domain_packs"` (line 20).
  `agents, api, connectors, evals, llm, memory, orchestrator, sandbox, scripts, tests` are packages
  (have `__init__.py`); `guardrails.py`, `semantic_layer.py` are top-level modules too.

Latest versions (https://pypi.org/pypi/<name>/json, fetched 2026-11-09):

| package | latest | requires-python |
|---|---|---|
| hypothesis | 6.170.15 | >=3.11 |
| import-linter | 2.16 (needs `grimp>=3.19`, `click>=7`, `rich>=14.2.0`) | >=3.11 |
| grimp | 3.19 | >=3.11 |
| mutmut | 3.8.1 (needs `libcst>=1.9`, `coverage>=7.3`, `textual>=1.0`, `setproctitle`, `pytest>=6.2.5`) | >=3.10 |

---

## A. Hypothesis 6.170.15

Source: https://github.com/HypothesisWorks/hypothesis/tree/master/hypothesis-python/src/hypothesis
(read from the 6.170.15 wheel). Docs: https://hypothesis.readthedocs.io/en/latest/reference/strategies.html#hypothesis-pandas,
https://hypothesis.readthedocs.io/en/latest/reference/api.html. Python 3.11–3.15 classifiers; `pandas` extra is `pandas>=1.5`
(wheel METADATA). No upper pandas bound; works with pandas 3.0.3 **[verified locally]**.

### A1. `hypothesis.extra.pandas` (source: `extra/pandas/impl.py`)

```python
range_indexes(min_size: int = 0, max_size: int | None = None,
              name: SearchStrategy[str | None] = st.none()) -> SearchStrategy[pd.RangeIndex]
indexes(*, elements=None, dtype=None, min_size=0, max_size=None, unique=True,
        name=st.none()) -> SearchStrategy[pd.Index]
series(*, elements=None, dtype=None, index=None, fill=None, unique=False,
       name=st.none()) -> SearchStrategy[pd.Series]
@dataclass class column: name: str|int|None = None; elements=None; dtype=None; fill=None; unique=False
columns(names_or_number: int | Sequence[str], *, dtype=None, elements=None, fill=None,
        unique=False) -> list[column]
data_frames(columns: Sequence[column] | None = None, *,
            rows: SearchStrategy[dict | Sequence] | None = None,
            index: SearchStrategy | None = None) -> SearchStrategy[pd.DataFrame]
```

- `range_indexes` default `max_size = min_size + 10` (`DEFAULT_MAX_SIZE = 10`), so frames default to ≤10 rows.
  Pass `index=range_indexes(min_size=..., max_size=...)` for bigger frames.
- `elements` vs `dtype` (`elements_and_dtype`): at least one is required. With only `dtype`, elements come from
  `hypothesis.extra.numpy.from_dtype` (full range: huge floats, inf, NaN; datetimes over the whole int64 range plus NaT).
  With both, each drawn element is coerced via `np.array([value], dtype=dtype)[0]`; a value that cannot convert raises
  `InvalidArgument` ("Cannot convert draw(elements)=..."). Object dtype skips coercion.
- **Categorical dtype is rejected**: `"dtype is categorical, which is currently unsupported"` **[verified locally]**.
  Pattern: `column("plan", elements=st.sampled_from([...]) | st.none(), dtype=object)` then
  `.map(lambda df: df.astype({"plan": "category"}))`.
- Passing `datetime`/`timedelta` classes as dtype is an error; use `dtype="datetime64[ns]"` (source message).
- Datetimes with NaT: `column("start", dtype="datetime64[ns]")` produces NaT (from_dtype: `st.integers(...) | st.just("NaT")`,
  `extra/numpy.py`) **[verified locally]**, but values span years 1677–2262. For realistic dates:
  `column("start", elements=st.datetimes(min, max) | st.none(), dtype="datetime64[ns]")` (None coerces to NaT; unconfirmed beyond the coercion rule above).
  tz-aware `DatetimeTZDtype` needs pandas ≥ 2.1 (`PANDAS_GE_21`), values include NaT unless `elements` excludes it (docstring).
- Nullable floats: only pandas `IntegerDtype` (`"Int64"` etc.) gets nullable treatment (`st.none() | elements`);
  `Float64Dtype` is not special-cased. Use `column("mrr", elements=st.floats(0, 1e6) | st.none(), dtype=float)`:
  None becomes NaN, dtype stays `float64` **[verified locally]**.
- `rows=` (docstring): rows must be dicts (name→value) or sequences (position→value); a scalar is an error even with one
  column. Given both `columns` and `rows`: names/dtypes come from columns, `unique` is enforced, missing values use the
  column's `fill`, extra keys/items raise `InvalidArgument`; "the column dtype must still be specified and will not be
  inferred from the rows". Without `columns`, dtypes can vary between examples. Docs: "In general you should prefer
  using columns to rows ... better performance and example quality". Cross-column constraints (start ≤ end, a spell)
  are what `rows=` (or `st.composite`) is for.
- Column names must be hashable and unique (`InvalidArgument` otherwise).

### A2. Settings and profiles (source: `_settings.py`)

- `settings.register_profile(name: str, parent: settings | None = None, **kwargs)`,
  `settings.load_profile(name)`, `settings.get_profile(name)`, `settings.get_current_profile_name()`.
  Registering inside an `@settings`-decorated context is deprecated (since 2025-11-15): register at module level (conftest).
- `settings(...)` kwargs: `parent, max_examples, derandomize, database, verbosity, phases, stateful_step_count,
  report_multiple_bugs, suppress_health_check, deadline, print_blob, backend`.
- **Built-in profiles** `default` and `ci`. Docstring: "If the `CI` environment variable is set to any value, the `ci`
  profile is active by default" (plus vendor vars: CIRCLECI, CIRRUS_CI, GITLAB_CI, ...; `is_in_ci()`).
  - `default`: `max_examples=100, derandomize=False, database=<.hypothesis/examples dir>, phases=tuple(Phase),
    deadline=200ms, print_blob=False, suppress_health_check=()`.
  - `ci`: `derandomize=True, deadline=None, database=None, print_blob=True, suppress_health_check=[HealthCheck.too_slow]`.
  - GitHub Actions sets `CI=true`, so CI runs are already deterministic with no DB (unconfirmed for our runner, but GHA sets CI by documented default).
- Extending ci: `settings.register_profile("ci", settings.get_profile("ci"), max_examples=1000)` (docstring example).
  Env-var pattern in conftest: `settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))` works **[verified locally]**
  (note: an explicit `load_profile` overrides the CI auto-load, so default it to `"ci"` when `CI` is set).
  Pytest flag alternative: `--hypothesis-profile=<name>`, plus `--hypothesis-seed=<int>`,
  `--hypothesis-show-statistics`, `--hypothesis-verbosity`, `--hypothesis-explain` (`_hypothesis_pytestplugin.py`;
  https://hypothesis.readthedocs.io/en/latest/reference/integrations.html).
- `database=None` disables the example DB; default DB is `DirectoryBasedExampleDatabase` at `.hypothesis/examples` in cwd.
  `deadline`: int/float ms, timedelta, or None. `print_blob=True` prints an `@reproduce_failure` blob.
- `Phase` members: `explicit, reuse, generate, target, shrink, explain`.
- `HealthCheck` members: `data_too_large, filter_too_much, too_slow, large_base_example, function_scoped_fixture,
  differing_executors, nested_given` (`return_value`, `not_a_test_method` deprecated). `HealthCheck.all()` is
  deprecated → `list(HealthCheck)`. All but `function_scoped_fixture` and `differing_executors` are performance checks.

### A3. pytest-xdist and `xfail(strict=True)`

- No Hypothesis doc mentions xdist (integrations page, checked). `pytest -n 2` with `CI=true` ran fine **[verified locally]**.
  Each worker runs its own engine; with the `ci` profile (`derandomize=True`, `database=None`) results don't depend on
  which worker runs a test.
- `@pytest.mark.xfail(strict=True)` on an `@given` test **[verified locally]**: a failing property reports **xfailed**;
  a property that never fails reports **FAILED [XPASS(strict)]**. Hypothesis raises only after generate+shrink, so shrinking
  still runs on each xfailed run (it is not documented, it follows from the engine re-raising the minimal example; cost is
  generation + shrink time per run). To bound the cost: `@settings(phases=[Phase.explicit, Phase.generate])` or a lower
  `max_examples` on the xfail tests. Hazard: with `derandomize=False` (local default), a rare-input bug can make an xfail test
  pass by luck → XPASS(strict) flake; the `ci` profile's `derandomize=True` makes it stable.
- Pin known counterexamples with `@example(...)` so the xfail fails on the explicit phase every time.

### A4. Small APIs (sources: `core.py`, `control.py`, `strategies/_internal/core.py`)

- `@example(*args, **kwargs)`: always try this input first; `.xfail(condition=True, *, reason="", raises=BaseException)`
  and `.via(whence)` modifiers.
- `@st.composite` (`composite(f) -> Callable[..., SearchStrategy]`): `f(draw, ...)` builds a strategy from many draws.
- `assume(condition) -> True`: reject the current example if falsy (counts toward `filter_too_much`).
- `note(value) -> None`: print `value` with the final failing example.
- `event(value: str, payload="") -> None`: tally for `--hypothesis-show-statistics`.
- `target(observation: int | float, *, label="") -> int | float`: guide search toward maximising a metric.

---

## B. import-linter 2.16 (grimp 3.19)

Source: https://github.com/seddonym/import-linter/tree/master/src/importlinter (read from the 2.16 wheel).
Docs: https://import-linter.readthedocs.io/en/stable/ (contract pages under `/contract_types/<type>/`).

### B1. pyproject shape

```toml
[tool.importlinter]
root_packages = ["agents", "evals", "domain_pack"]   # or root_package = "x" (normalised to a 1-item list)
include_external_packages = false                    # optional
exclude_type_checking_imports = false                # optional (grimp: drops `if TYPE_CHECKING:` imports)

[[tool.importlinter.contracts]]
name = "..."          # human label
type = "forbidden"    # forbidden | layers | independence | protected | acyclic_siblings | <custom>
id = "..."            # optional; used by `lint-imports --contract <id>` (unconfirmed exact key name, the CLI says "contract identifier")
# per-type keys below
```

(`application/use_cases.py`: `root_package` → `root_packages`, `include_external_packages`, `exclude_type_checking_imports`.)

### B2. Contract types and keys (`contracts/*.py`)

Shared optional keys on every type: `ignore_imports` (set of `importer -> imported` expressions, wildcards allowed),
`unmatched_ignore_imports_alerting` (`none|warn|error`, default `error`: an ignore that matches nothing breaks the run),
`broken_contract_guidance` (text shown on failure).

- **forbidden**: `source_modules`, `forbidden_modules`, `allow_indirect_imports` (default **false**: indirect chains count),
  `as_packages` (default true). Overlapping source/forbidden pairs are skipped (so `mypackage.*` may be forbidden from one of its own members).
  `forbidden_modules` may name an external package when `include_external_packages = true` (docs).
- **layers**: `layers` (ordered high→low; `"a | b"` = independent siblings on one layer, `"a : b"` = siblings may import
  each other; mixing is invalid; `"(x)"` = optional layer), `containers` (optional), `exhaustive` (default false; only with
  containers), `exhaustive_ignores`. https://import-linter.readthedocs.io/en/stable/contract_types/layers/
- **independence**: `modules` (no imports in either direction, direct or indirect).
- **protected**: `protected_modules`, `allowed_importers`, `as_packages` (default true). "Protected contracts prevent modules
  from being directly imported, except by modules in an allow-list" (https://import-linter.readthedocs.io/en/stable/contract_types/protected/).
  **Direct imports only** (`find_modules_that_directly_import`); descendants of a protected package may import each other.
- **acyclic_siblings**: `ancestors` (required), `depth` (default 10), `skip_descendants`. "forbids dependency cycles between
  siblings" (https://import-linter.readthedocs.io/en/stable/contract_types/acyclic_siblings/).
- Wildcards (`domain/imports.py`): `mypackage.*` = direct children, `mypackage.**` = all descendants; `*` and `**` cannot be
  mixed in one expression. Docs example: `mypackage.**.models`.

### B3. What it can and cannot express

- It checks **imports between modules, never definitions or names**. `from domain_pack import _window` is recorded as an
  import of the module `domain_pack` **[verified locally]**: grimp's graph has module nodes only. So "only `spell.py`
  defines the window" is **not expressible**; that needs our own AST check (e.g. in `tests/test_one_definition.py`).
  What it CAN say:
  - "nothing outside `domain_pack` imports `domain_pack.spell` directly" (protected, `allowed_importers=["domain_pack"]`);
  - "`evals.reference_kpis` imports nothing from `domain_pack`, directly or indirectly" (forbidden, default indirect);
  - "`domain_pack.ledger` does not import `domain_pack.kpi`" / layers inside `domain_pack` (layers with `containers=["domain_pack"]`).
  - Caveat: if `domain_pack/__init__.py` re-exports from `spell`, callers import `domain_pack` (the `__init__`), so a protected
    contract on `domain_pack.spell` passes trivially for them: it guards the module path, not the names.
- **Root must be a package**: `_validate_root_package_names` raises `'X' is a module, not a package. root_packages should only
  contain packages (directories with __init__.py)` **[verified locally]**. Today's `domain_pack.py` cannot be a root; after the
  split, `domain_pack/` can. Multiple top-level roots (`["agents", "evals", "domain_pack", ...]`) work **[verified locally]**.
- Top-level *modules* that are not in any root (`guardrails.py`, `semantic_layer.py`, today's `domain_pack.py`) are not scanned
  as importers. With `include_external_packages = true` they appear as squashed external nodes that can be protected/forbidden
  targets (verified locally: `protected_modules=["dp"]` caught `agents.a -> dp`). Grimp docs: externals are "a single squashed
  module" and their own imports are not analysed (https://grimp.readthedocs.io/en/stable/usage.html).
- **Imports inside functions are seen** **[verified locally]** (a `from dp import _secret` inside `def f()` was reported at its
  line). `if TYPE_CHECKING:` imports are included unless `exclude_type_checking_imports = true` (grimp docs).
- CLI `lint-imports` (`cli.py`): `--config FILE`, `--contract ID` (repeatable), `--cache-dir DIR`, `--no-cache`, `--verbose`,
  `--show-timings`, `--debug`, `--no-logo`. Exit codes: `0` all contracts kept, `1` any broken or error
  (`EXIT_STATUS_SUCCESS/ERROR`). Default cache dir `.import_linter_cache` (`configuration.py`) → add to `.gitignore`.
  The package must be importable (run from repo root / `uv run lint-imports`).

---

## C. mutmut 3.8.1

Source: https://github.com/boxed/mutmut/tree/main/src/mutmut (read from the 3.8.1 wheel);
README: https://github.com/boxed/mutmut/blob/main/README.rst.

### C1. `[tool.mutmut]` keys (complete list from `configuration.py::_load_config`)

| key | default | note |
|---|---|---|
| `source_paths` | guessed (`lib`, `src`, dir named like cwd, ...) | **new name**; `paths_to_mutate` still read but warns "deprecated, rename to source_paths" |
| `only_mutate` | `[]` | fnmatch globs on file paths, must end in `.py` or `*` |
| `do_not_mutate` | `[]` | same glob rule |
| `do_not_mutate_patterns` | `[]` | regex on source lines |
| `also_copy` | `[]` | always added: `tests/`, `test/`, `setup.cfg`, `pyproject.toml`, `uv.lock`, other lockfiles, `test*.py` |
| `pytest_add_cli_args` | `[]` | added to every pytest run |
| `pytest_add_cli_args_test_selection` | `[]` | test paths/args for the run; `tests_dir` is deprecated and folded in here |
| `mutate_only_covered_lines` | false | uses coverage.py |
| `max_stack_depth` | -1 | |
| `type_check_command` | `[]` | README: only `mypy` and `pyrefly` supported |
| `timeout_multiplier` / `timeout_constant` | 15.0 / 1.0 | README: "(duration_of_original_tests + timeout_constant) * timeout_multiplier", marked unstable |
| `debug` | false | prints the pytest command lines |
| `use_setproctitle` | false on macOS, true elsewhere | |
| `track_dependencies`, `dependency_tracking_depth` | true, -1 | |
| `cache_invalidation_files`, `cache_invalidation_exclude`, `on_dependency_change` | `[]`, `[]`, `"warn"` | |
| `use_git_change_detection` | true | |
| `process_isolation` | `"fork"` | or `"forkserver"` |
| `forkserver_warmup`, `max_forkserver_restarts`, `preload_modules_file` | `"collect"`, 3, None | |
| `log_to_file`, `log_file_path` | false, `mutants/mutmut-debug.log` | |

### C2. How it works (what breaks)

- `mutants/` holds everything (README). `copy_src_dir` copies only `source_paths` into `mutants/`, then `also_copy`
  (`utils/file_utils.py`). `setup_source_paths` puts `mutants/` (and `mutants/src`, `mutants/source`) first on `sys.path` and
  **deletes the original repo root from `sys.path`** "so the original code CANNOT be imported". Tests run with
  **cwd = `mutants/`** via in-process `pytest.main(["--rootdir=.", "--tb=native", "-x", "-q", "-p", "no:randomly", ...])`
  (`runners/harness.py`), in a forked child per mutant.
- Each mutated function becomes a decorated trampoline: `@_mutmut_mutated(mutants_x_fn__mutmut) def fn(...)` dispatching to
  `x_fn__mutmut_orig` / `x_fn__mutmut_N` (seen in `mutants/dp.py` **[verified locally]**). Only code inside functions/methods
  is mutated (README: "If you want to mutate code outside of functions, you can try using mutmut 2").
- Consequences for us:
  - Every module the tests import that is not under `source_paths` must be in `also_copy` (agents, evals, llm, ...; the root is
    removed from `sys.path`). Simplest: `source_paths` = the files to mutate, plus `also_copy` for the rest; or run in a copy.
  - `Path(__file__)` data: `domain_pack.py` resolves `Path(__file__).parent / "domain_packs"` → `mutants/domain_packs` must
    exist → `also_copy = ["domain_packs/", ...]`. Same for any fixture/dataset dir tests read by relative path (cwd is `mutants/`).
  - A test importing a module both as `domain_pack` and via a file path would load two copies (only one is trampolined):
    unconfirmed, inferred from the sys.path setup.
  - `pyproject.toml` is copied, so our `[tool.pytest.ini_options]` applies inside `mutants/`.

### C3. Selecting functions, results, scores

- Names: `<module>.x_<function>__mutmut_<N>`; methods `<module>.xǁ<Class>ǁ<method>__mutmut_<N>` (U+01C1) **[verified locally]**.
  `mutmut run "dp.x_window*"` ran only that function's 3 mutants; matching is `fnmatch` on the key (`__main__.py`); README:
  `mutmut run "my_module.my_function*"`. Quote the glob in the shell.
- CLI (`__main__.py`): `run [--max-children N] [NAMES...]`, `results [--all true]` (`--all` takes a value, not a flag),
  `show <name>` (diff), `apply <name>` (writes mutant to source; README warns to commit first), `browse [--show-killed]` (TUI),
  `tests-for-mutant <name>`, `print-time-estimates [NAMES...]`, `export-cicd-stats` → `mutants/mutmut-cicd-stats.json`
  (`killed, survived, total, no_tests, skipped, suspicious, timeout, check_was_interrupted_by_user, segfault`: totals only),
  `badge --input ... --output ... --label ...`.
- Storage **[verified locally]**: `mutants/<path>.meta` JSON per source file:
  `exit_code_by_key {mutant: int|null}`, `hash_by_function_name`, `type_check_error_by_key`, `durations_by_key`,
  `estimated_durations_by_key`. `mutants/mutmut-stats.json`: `tests_by_mangled_function_name`, `duration_by_test`,
  `function_hashes`, `function_dependencies`, `config_fingerprint`, `git_commit`, ...
- Exit code → status (`stats.py`): `1, 3` killed; `0` survived; `5, 33` no tests; `34` skipped; `35` suspicious;
  `36, 24, -24, 152, 255` timeout; `37` caught by type check; `-11, -9` segfault; `2` interrupted; `null` not checked.
- **Per-function kill score** (no built-in command): read every `mutants/**/*.meta`, group keys by
  `key.rsplit("__mutmut_", 1)[0]`, score = killed / (checked − skipped), counting timeouts as killed or not by our choice
  (state it). ~10 lines of Python in `scripts/mutation.py`.

### C4. Limitations

- Needs `os.fork` (POSIX; Windows only via WSL). macOS works **[verified locally]**; README warns some CoreFoundation APIs are
  not fork-safe on macOS (segfaults in children) → `process_isolation = "forkserver"` is the escape hatch.
- Before mutants run, mutmut runs the selected tests once under trampolines to map tests → functions (stats), then a clean run
  that must exit 0 ("Failed to run clean test" → exit 1), then a forced-fail check. Only tests that hit a function run for its
  mutants. pytest exit 4 (usage error) aborts.
- pytest-xdist: not mentioned in the README; mutmut parallelises itself with forked children (`--max-children`, default
  `os.cpu_count()`), and stats are recorded in-process by the trampolines, so passing `-n` through `pytest_add_cli_args` would
  break stats collection (inferred from source, unconfirmed). Do not pass `-n`.
- Incremental: re-tests only mutants in functions whose source hash changed; delete `mutants/` for a full run (README).
- Hypothesis tests inside mutmut run once per mutant with the active profile: a small `max_examples` profile for mutation runs keeps cost bounded.

---

## Decisions this implies for us

1. **Pins** (dev dependency-group, next to pytest-xdist): `hypothesis>=6.170`, `import-linter>=2.16` (brings `grimp>=3.19`),
   `mutmut>=3.8.1`. No `[pandas]` extra needed (pandas already a main dep). All support Python 3.11.
2. **Hypothesis profiles in `tests/conftest.py`**, at module level: `dev` (fast, local), `ci` = `get_profile("ci")` +
   our `max_examples`, `mutation` (tiny `max_examples`, `phases` without shrink); load from
   `HYPOTHESIS_PROFILE`, defaulting to `"ci"` when `CI` is set. CI is then derandomized, DB-less, no deadline.
3. **Frames**: build with `column(...)` + `range_indexes(min_size=1, max_size=N)`; dates via
   `elements=st.datetimes(...) | st.none()`, `dtype="datetime64[ns]"`; categories as object + `.map(astype("category"))`;
   nullable floats as `dtype=float` with `st.none() | st.floats(...)`. Spells (start ≤ end) via `st.composite` or `rows=`.
4. **xfail-strict properties**: add `@example(...)` with the known counterexample so the failure is deterministic and cheap;
   rely on the `ci` profile's `derandomize=True` to avoid XPASS flakes.
5. **import-linter**: config goes in `pyproject.toml`; `root_packages` must list packages only, so the contracts on
   `domain_pack` land **with or after the split** into `domain_pack/`. Contracts express "who may import `domain_pack.spell`
   / `ledger`" (protected) and "`evals.reference_kpis` never reaches `domain_pack`" (forbidden). "Only spell.py *defines* the
   window" needs an AST guard in `tests/test_one_definition.py`, not import-linter. Watch the re-export caveat. Add
   `.import_linter_cache/` to `.gitignore`; run `lint-imports` in CI and pre-push (exit 1 = broken).
6. **mutmut**: use `source_paths` (not `paths_to_mutate`), `pytest_add_cli_args_test_selection` (not `tests_dir`),
   `also_copy` for `domain_packs/` and every other top-level package/module the selected tests import; never pass `-n`.
   `scripts/mutation.py` runs `mutmut run "<module>.x_<fn>*"` in a throwaway worktree and computes the per-function score
   from `mutants/**/*.meta` (state how timeouts count). Add `mutants/` to `.gitignore`.
7. After the split, `domain_pack/<x>.py` sits one level deeper: `PACKS = Path(__file__).parent / "domain_packs"` must become
   `.parent.parent` (or move the constant), and mutmut's `also_copy` follows.
