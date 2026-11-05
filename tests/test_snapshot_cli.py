"""Retro Domain 5: `python -m evals.snapshot --help` wrote a 443 KB file named '--help' (any
unknown argument was an output path). Subcommands only; an unknown one writes nothing."""
import pytest

from evals import snapshot


def test_an_unknown_argument_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for argv in (["--help"], ["bogus"]):
        with pytest.raises(SystemExit):
            snapshot.main(argv)
    assert not list(tmp_path.iterdir())
