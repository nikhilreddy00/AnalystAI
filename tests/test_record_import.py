"""The recording aid's scrub (5.8c): a vendor export holds people; what is committed holds none.
Fail-closed (5.8: allow lists over deny lists): the first rule wiped Shopify's order number and
product name, the second let Billing City through (security review)."""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_an_export_keeps_its_data_and_loses_its_people(tmp_path):
    from scripts.record_import import scrub_csv
    f = tmp_path / "orders.csv"
    f.write_text("Name,Email,Lineitem name,Billing City,Shipping Zip,Customer Name,Shipping,Total\n"
                 "#1001,ann@shop.test,Mug,Leeds,LS1,Ann Lee,4.00,12.50\n")
    scrub_csv(f)
    row = f.read_text().splitlines()[1].split(",")
    assert row[0] == "#1001" and row[2] == "Mug" and row[6:] == ["4.00", "12.50"]
    assert row[3] == row[4] == row[5] == "[scrubbed]"
    assert row[1].endswith("@mail.invalid") and "ann" not in row[1]
    once = f.read_text()
    scrub_csv(f)
    assert f.read_text() == once   # a scrubbed file scrubs to itself (its placeholders kept)


def test_an_unknown_header_that_may_be_a_person_is_scrubbed(tmp_path):
    """Fail closed: a header the safe list doesn't know is scrubbed when its words may mean a
    person or a place, whatever the vendor calls it."""
    from scripts.record_import import scrub_csv
    f = tmp_path / "x.csv"
    f.write_text("Recipient Name,Delivery Province,Amount\nBo,Ontario,3\n")
    scrub_csv(f)
    assert f.read_text().splitlines()[1] == "[scrubbed],[scrubbed],3"


def test_the_api_layers_import_without_the_processs_logging():
    """5.8c: the route tests imported api.main for its layers, which installed the log scrubber
    on pytest's capture handler; later tests' records lost exc_info (order-dependent). The layers
    live in api.middleware, which installs nothing."""
    import importlib
    from api.logging_scrub import ScrubFilter
    root = logging.getLogger()
    before = [f for h in root.handlers for f in h.filters if isinstance(f, ScrubFilter)]
    importlib.reload(importlib.import_module("api.middleware"))
    after = [f for h in root.handlers for f in h.filters if isinstance(f, ScrubFilter)]
    assert len(after) == len(before)
