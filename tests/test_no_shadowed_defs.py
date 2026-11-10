"""A module that defines the same top-level function twice silently uses the last one (Phase 5.5:
a new `_canonical` in guardrails.py shadowed the SQL metric check's and broke it). Ruff doesn't
flag it when the signatures differ; this does, for every first-party module. Phase 5.7: module
constants too (a second `_DEPTS` in evals/datagen.py replaced healthcare's departments)."""
import ast
import collections
from pathlib import Path

ROOT = Path(__file__).parent.parent
PACKAGES = ("agents", "api", "connectors", "evals", "llm", "memory", "orchestrator", "sandbox",
            "worker", "scripts", "domain_pack")


def test_no_module_defines_a_top_level_name_twice():
    files = [ROOT / "guardrails.py", ROOT / "semantic_layer.py",
             *(p for pkg in PACKAGES for p in (ROOT / pkg).rglob("*.py"))]
    dupes = {}
    for f in files:
        tree = ast.parse(f.read_text())
        names = collections.Counter(n.name for n in tree.body
                                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        # CONSTANT = … (and _CONSTANT): assigned once per module
        names.update(t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                     for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                     if isinstance(t, ast.Name) and t.id.lstrip("_").isupper())
        if bad := [n for n, k in names.items() if k > 1]:
            dupes[str(f.relative_to(ROOT))] = bad
    assert not dupes
