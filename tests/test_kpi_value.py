"""THE clause per Unavailable reason (5.7b-2a, C5): every answer and tile says why a figure is
missing in these words, so each is pinned here as written by hand from the pack words."""
from domain_pack import Unavailable, load_packs, unavailable_words
from guardrails import SMALL_CELL

PACKS = {p.domain: p for p in load_packs()}


def test_each_reason_says_its_own_clause():
    saas = PACKS["saas"]
    said = {
        ("role_missing", ("start_date", "mrr")): "the data has no start date and mrr",
        ("role_missing", ()): "the data has no column it needs",
        ("end_date_missing", ()): "the data has no end date, so customers who left can't be counted",
        ("blank_column", ("discount_pct",)): "discount pct is blank on every row",
        ("blank_column", ()): "a column it reads is blank on every row",
        ("no_rows", ()): "there are no rows to count",
        ("no_kind_rows", ()): "the data has no rows of the kind it counts",
        ("mixed_units", ()): "its parts are in different units, which never add up",
        ("undated_snapshots", ()): "the rows have no date, so the latest of each can't be told",
        ("zero_denominator", ()): "its base is 0",
        ("too_short", ()): "the data covers too short a span",
        ("outside_the_data", ()): "the period falls outside the data",
        ("small_cell", ()): f"fewer than {SMALL_CELL} people, so a figure can identify them",
    }
    for (reason, roles), words in said.items():
        assert unavailable_words(Unavailable(reason, roles), saas) == words, reason


def test_a_pack_without_spell_words_says_the_plain_end_date():
    retail = next(p for p in PACKS.values() if p.spell is None)
    assert unavailable_words(Unavailable("end_date_missing"), retail) == "the data has no end date"
