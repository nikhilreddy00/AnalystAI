"""GLOSSARY Unit words: the unit a KPI's words are asked in is read beside those words only, and
a KPI asked in another unit is asked back with the KPIs of that unit sharing a word."""
from domain_pack import glossary_hits, pack_for, unit_clashes, unit_question, unit_said

SAAS = pack_for("saas")


def test_unit_said_reads_the_whole_text_by_default():
    assert unit_said("rate") == "percent"
    assert unit_said("rate churn", 5) is None   # 'rate' is before the words, not beside them


def test_unit_said_reads_only_up_to_the_word_after_the_end():
    assert unit_said("churn customers by rate", 0, 5) is None


def test_a_rate_named_elsewhere_in_the_question_is_another_kpis():
    for q in ("mrr and churn rate", "churn rate and mrr"):
        assert dict(glossary_hits(SAAS, q, [])) == {"mrr": "mrr", "logo_churn_rate": "churn rate"}, q


def test_a_kpi_asked_in_another_unit_offers_the_kpis_of_that_unit_sharing_a_word():
    """'customer' is Logo churn's by its glossary only ('customer churn'); 'revenue' the
    retentions' by their labels. No other rate shares a word."""
    clash = unit_clashes(SAAS, "What is our revenue per customer rate?", [])[0]
    want = ("Did you mean Logo churn (a rate), Net revenue retention (a rate), Gross revenue "
            "retention (a rate), or ARPA per paying customer (an amount)?")
    assert unit_question(SAAS, clash).endswith(want)
    # a rate with no glossary words is just not near
    bare = SAAS.model_copy(update={"glossary": {k: v for k, v in SAAS.glossary.items()
                                                if k != "recurring_share"}})
    assert unit_question(bare, clash).endswith(want)
