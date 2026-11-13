"""Did this branch change what the finished domains show? Dumps every user-facing output per
dataset (domain, roles, personal data, tiles, charts, filter options, filtered dashboards, KPI
and column answers, built-ins) in a worktree of the base ref and in this tree, and prints the
datasets and output paths that differ. Each difference is named in the review note as intended
(with its reason) or fixed: the golden pins only what the model sees and misses the rest
(retro Domain 5: a store-number slider replaced COGS on store_weeks, seen only by this diff).

    uv run python scripts/domain_diff.py main          # the base's PACK_DATASETS + non-pack ones
    uv run python scripts/domain_diff.py 76e5cd6 --datasets retail_lines,policies

Exit 1 when anything differs. $0: no model call."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OTHER = ["sales", "simpson", "subscriptions", "saas_accounts", "sensor_readings"]


def _probe(name: str) -> dict:
    """One dataset's outputs, through the public entry points the tests use (they exist in
    every tree since Domain 4). Chart ids are random: charts are keyed by position."""
    import domain_pack
    from agents import visualization_agent as va
    from tests.test_oracle import _built, answer, builtin

    def js(x):
        return json.loads(json.dumps(x, default=str))

    def safe(f):
        try:
            return js(f())
        except Exception as e:   # a failure is an output too (a crash after the branch)
            return {"ERROR": f"{type(e).__name__}: {e}"}

    _, clean, contract, _ = _built(name)
    out = {"domain": contract.domain, "confidence": contract.domain_confidence, "roles": contract.roles,
           "pii": contract.pii_columns, "phi": contract.phi, "rows": len(clean)}
    pack = domain_pack.pack_for(contract.domain)
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    ctx = va.build_context(clean, contract=contract)
    charts = va.pack_overview_charts(clean, ctx) + va.generate_dashboard_charts(clean, ctx.roles, ctx=ctx)
    pos = {c.id: i for i, c in enumerate(charts)}
    out["tiles"] = safe(lambda: [(k.label, k.value, k.context) for k in
                                 va.generate_smart_kpis(clean, ctx.roles, "overview", contract=contract)])
    out["charts"] = safe(lambda: [{"title": c.title, "type": str(c.chart_type), "x": c.x_key,
                                   "y": c.y_keys, "data": c.data} for c in charts])
    opts = safe(lambda: va.filter_options(clean, contract))
    out["filter_options"] = opts.get("options") if isinstance(opts, dict) else opts

    def filtered(f):
        o = va.filtered_dashboard(clean, contract, charts, f)
        return {**{k: v for k, v in o.items() if k not in ("charts", "titles")},
                "charts": {pos[k]: v for k, v in o.get("charts", {}).items()}}
    pills = [o for o in (opts.get("options", []) if isinstance(opts, dict) else [])
             if o["control"] in ("binary", "pills") and o.get("values")]
    out["filtered"] = {"none": safe(lambda: filtered({}))}
    for o in pills[:2]:
        for v in o["values"][:2]:
            out["filtered"][f"{o['column']}={v}"] = safe(lambda: filtered(
                {o["column"]: {"kind": "equality", "value": v}}))
    ans = {}
    group = next((roles[g] for g in pack.overview_groups if g in roles), None) if pack else None
    for k in pack.kpis if pack else []:
        ans[f"kpi:{k.name}"] = safe(lambda: answer(name, "segment_compare", kpi=k.name))
        if group:
            ans[f"kpi:{k.name}:by:{group}"] = safe(lambda: answer(name, "segment_compare", kpi=k.name,
                                                                  segment_column=group))
    nums = [c for c in clean.select_dtypes("number").columns if c not in contract.id_columns]
    money = next((c for c in nums if c in contract.metric_columns), nums[0] if nums else None)
    cat = group or (pills[0]["column"] if pills else None)
    if money and cat:
        ans[f"top_n:{money}:{cat}"] = safe(lambda: answer(name, "top_n", metric_column=money, group_by=cat, n=5))
        ans[f"seg:{money}:{cat}"] = safe(lambda: answer(name, "segment_compare", metric_column=money,
                                                        segment_column=cat, aggregation="sum"))
    out["answers"] = ans
    out["builtin"] = safe(lambda: builtin(name, [money] if money else [], [cat] if cat else [], ["eda"]))
    return out


def _dump(out: str, names: list[str]) -> None:
    warnings.filterwarnings("ignore")
    res = {}
    for n in names:
        try:
            res[n] = _probe(n)
        except Exception:
            res[n] = {"FATAL": traceback.format_exc()[-800:]}
    Path(out).write_text(json.dumps(res, indent=1, sort_keys=True, default=str))


def _run(tree: Path, out: Path, names: list[str] | None, env: dict) -> None:
    args = [str(ROOT / ".venv/bin/python"), str(Path(__file__).resolve()), "_dump", str(out)]
    subprocess.run(args + ([",".join(names)] if names else []), cwd=tree, check=True,
                   env={**env, "PYTHONPATH": str(tree)})


def _paths(a, b, at="") -> list[str]:
    """The output paths where two dumps differ (the first few per dataset)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for k in sorted(set(a) | set(b), key=str) for p in _paths(a.get(k), b.get(k), f"{at}.{k}")]
    return [] if a == b else [at or "."]


def main() -> None:
    if sys.argv[1:2] == ["_dump"]:   # inside a tree: its own PACK_DATASETS unless named
        names = sys.argv[3].split(",") if len(sys.argv) > 3 else None
        if names is None:
            from tests.test_dashboard_filters import PACK_DATASETS
            names = [*PACK_DATASETS, *OTHER]
        return _dump(sys.argv[2], names)
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="the ref before this branch (main, a SHA)")
    ap.add_argument("--datasets", help="comma-separated; default: the base's PACK_DATASETS + non-pack")
    ap.add_argument("--show", action="store_true",
                    help="print each changed value's text change (evals.snapshot.text_changes)")
    args = ap.parse_args()
    if args.show:   # this side reads this tree (each _dump gets its tree on PYTHONPATH): import
        sys.path.append(str(ROOT))   # now, never after minutes of dumps (retro 5.7b-2b)
        from evals.snapshot import text_changes
    key = subprocess.run([str(ROOT / ".venv/bin/python"), "-c", "from cryptography.fernet import Fernet;"
                          "print(Fernet.generate_key().decode())"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "ANALYSTAI_TODAY": "2026-11-01", "ENCRYPTION_KEY": key}   # one key: same pseudonyms
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "base"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), args.base],
                       check=True, capture_output=True)
        try:
            names = args.datasets.split(",") if args.datasets else None
            _run(wt, Path(tmp) / "before.json", names, env)
            before = json.loads((Path(tmp) / "before.json").read_text())
            _run(ROOT, Path(tmp) / "after.json", list(before), env)   # the base's datasets
            after = json.loads((Path(tmp) / "after.json").read_text())
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], check=False)
    changed = {n: _paths(before[n], after.get(n)) for n in before}
    changed = {n: p for n, p in changed.items() if p}
    for n in before:
        print(f"{n}: identical" if n not in changed else f"{n}: CHANGED {len(changed[n])} path(s)")
        if args.show and n in changed:   # the values: a rename reads as one word, a number as one
            seen: set = set()
            for core, line in text_changes(before[n], after.get(n)):
                if core not in seen:
                    seen.add(core)
                    print("    " + line[:240])
            continue
        for p in changed.get(n, [])[:8]:
            print("    " + re.sub(r"^\.", "", p))
    sys.exit(1 if changed else 0)


if __name__ == "__main__":
    main()
