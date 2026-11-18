"""scripts/suite.py (retro Domain 8): run from a linked worktree it linked that worktree's .venv and
reported 371 failures that weren't there; it refuses, and names the main checkout."""
from scripts import suite
from scripts.mutation import ROOT, worktree


def test_the_main_checkout_is_no_linked_worktree():
    assert suite.linked_worktree(ROOT) is False


def test_a_linked_worktree_is_refused_with_the_main_checkout_named(monkeypatch, capsys):
    with worktree() as wt:
        assert suite.linked_worktree(wt) is True
        monkeypatch.setattr(suite, "ROOT", wt)   # suite.py as it runs from that worktree
        assert suite.main([]) == 2
    assert "main checkout" in capsys.readouterr().err
