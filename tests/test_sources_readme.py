"""The README's source list is generated from the connectors (5.8a): their formats, dialects and
caps, never a hand-kept copy that drifts when a source or a cap changes."""
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
START, END = "<!-- sources:start -->", "<!-- sources:end -->"


def test_the_readme_source_list_is_the_connectors_own():
    from connectors.base import sources_table
    text = README.read_text()
    assert START in text and END in text, f"README needs {START} … {END} around the source table"
    shown = text.split(START, 1)[1].split(END, 1)[0].strip()
    assert shown == sources_table().strip(), (
        "README sources differ from the connectors; paste the output of\n"
        "uv run python -c 'from connectors.base import sources_table; print(sources_table())'")


def test_every_dialect_and_upload_format_is_in_the_table():
    from connectors.base import sources_table
    from connectors.csv_connector import UPLOAD_SUFFIXES
    from connectors.sql_connector import DIALECTS
    table = sources_table()
    assert all(s in table for s in UPLOAD_SUFFIXES)
    assert all(f"`{name}://" in table for name in DIALECTS)
