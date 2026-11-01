"""Phase 5.7, domain 1 (sales / retail): the retail domain pack and the fixes it needed."""
import pandas as pd
import pytest

from connectors.base import parse_text_dates
from connectors.csv_connector import CSVConnector, _read_bytes
from domain_pack import Pack, compute_kpi, normalize_header, pack_for, row_kinds
from evals import datagen
from orchestrator.state import ConnectorConfig, ConnectorType


def _load(csv: str) -> pd.DataFrame:
    return _read_bytes(csv.encode(), ".csv")[0]


# ── Dates with UTC offsets (Shopify's `Created at`) ──────────────────────────────────────────

def test_mixed_offsets_parse_to_each_values_local_time():
    # -0500 in winter, -0400 in summer: one column, two offsets (a daylight-saving change).
    df = _load("Name,Created at\n#1001,2025-01-05 10:00:00 -0500\n#1002,2025-07-05 23:30:00 -0400\n")
    got = df["Created at"]
    assert pd.api.types.is_datetime64_any_dtype(got)
    assert got.tolist() == [pd.Timestamp("2025-01-05 10:00"), pd.Timestamp("2025-07-05 23:30")]


def test_one_offset_still_parses_and_plain_dates_are_unchanged():
    one = parse_text_dates(pd.Series(["2025-01-05 10:00:00 -0500", "2025-01-06 11:00:00 -0500"]))
    assert one is not None and one.notna().all()
    plain = parse_text_dates(pd.Series(["2025-01-05", "2025-02-06"]))   # "-05" is not an offset
    assert plain.tolist() == [pd.Timestamp("2025-01-05"), pd.Timestamp("2025-02-06")]


def test_offset_like_text_that_is_not_a_date_stays_text():
    assert parse_text_dates(pd.Series(["10:00 -0500", "ext -0400"])) is None


# ── The pack: schema, detection, KPIs ────────────────────────────────────────────────────────
RETAIL = pack_for("retail")


def _contract(df: pd.DataFrame):
    return CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)


def _gen(name: str, seed: int = 1) -> pd.DataFrame:
    return _load(datagen.make(name, seed)[0].to_csv(index=False))


_BASE = {"domain": "x", "signals": {"a": ["r"], "b": ["s"]},
         "roles": {"r": {"kind": "money", "headers": ["rev"]}, "s": {"kind": "count", "headers": ["q"]}}}


@pytest.mark.parametrize("change, error", [
    ({"roles": {"r": {"kind": "money", "headers": ["Rev"]}, "s": {"kind": "count", "headers": ["q"]}}},
     "not normalized"),
    ({"roles": {"r": {"kind": "money", "headers": ["rev"]}, "s": {"kind": "money", "headers": ["rev"]}}},
     "same kind"),
    ({"signals": {"a": ["r"]}}, "two groups"),
    ({"signals": {"a": ["r"], "b": ["nope"]}}, "unknown role nope"),
    ({"kpis": [{"name": "k", "label": "K", "num": [[{"kpi": "k"}]]}]}, "refers to itself"),
    ({"kpis": [{"name": "k", "label": "K", "num": [[{"sum": "zzz"}]]}]}, "unknown role zzz"),
    ({"kpis": [{"name": "k", "label": "K", "num": [[{"sum": "r", "nunique": "s"}]]}]}, "exactly one"),
    ({"kpis": [{"name": "k", "label": "K", "num": [[{"sum": ["r", "/", "s"]}]]}]}, "'\\*' or '-'"),
    ({"surprise": 1}, "Extra inputs"),
])
def test_an_invalid_pack_is_refused_with_its_reason(change, error):
    with pytest.raises(ValueError, match=error):
        Pack.model_validate({**_BASE, **change})


@pytest.mark.parametrize("raw, norm", [("Lineitem quantity", "lineitem_quantity"),
                                       ("Net Sales ($)", "net_sales"), ("NET_SALES", "net_sales"),
                                       ("UnitPrice", "unitprice"), ("Wk_End_Dt", "wk_end_dt"),
                                       ("unit-price [GBP]", "unit_price")])
def test_headers_normalize_across_conventions(raw, norm):
    assert normalize_header(raw) == norm


@pytest.mark.parametrize("name, roles", [
    ("retail_lines", {"order_id": "Name", "date": "Created at", "sku": "Lineitem sku",
                      "quantity": "Lineitem quantity", "unit_price": "Lineitem price",
                      "discount": "Discount Amount", "status": "Financial Status",
                      "currency": "Currency", "order_total": "Total", "email": "Email",
                      "country": "Shipping Country", "product": "Lineitem name"}),
    ("store_weeks", {"store": "Store_No", "date": "Wk_End_Dt", "category": "Dept",
                     "revenue": "Net Sales", "quantity": "Units", "cost": "COGS"}),
    ("b2b_invoices", {"order_id": "InvoiceNo", "date": "InvoiceDate", "sku": "StockCode",
                      "product": "Description", "quantity": "Quantity", "unit_price": "UnitPrice",
                      "customer_id": "CustomerID", "country": "Country"}),
    ("sales", {"order_id": "order_id", "date": "order_date", "revenue": "revenue",
               "quantity": "units", "discount_rate": "discount", "email": "customer_email"}),
])
def test_the_retail_datasets_are_retail_with_every_role(name, roles):
    c = _contract(_gen(name))
    assert c.domain == "retail" and c.roles == roles


@pytest.mark.parametrize("name", ["simpson", "subscriptions", "bank_balances", "encounters",
                                  "claims", "employees", "saas_accounts", "sensor_readings",
                                  "shipments"])
def test_no_other_domains_dataset_is_retail(name):
    c = _contract(_gen(name))
    assert c.domain is None and c.roles == {}


@pytest.mark.parametrize("csv", [
    # a bank's card transactions: an amount and a transaction id, no product or quantity
    "transaction_id,date,merchant,amount,account_id\nT10001,2025-01-02,Cafe,4.50,A1\n"
    "T10002,2025-01-03,Grocer,61.20,A2\nT10003,2025-01-04,Fuel,40.00,A1\n",
    # SaaS: a plan price, seats (not a quantity header)
    "account_id,plan,price,seats,mrr\nA1,Pro,99,5,495\nA2,Team,49,3,147\nA3,Pro,99,2,198\n",
    # HR, insurance, logistics money with no retail-specific column
    "employee_id,department,salary,hire_date\nE1,Sales,52000,2020-01-01\nE2,Ops,48000,2021-03-01\n",
    "policy_id,line,earned_premium,claim_amount\nP1,auto,1200,0\nP2,home,900,450\n",
    "shipment_id,carrier,shipment_cost,weight_kg\nS1,X,12.5,3\nS2,Y,40.0,11\n",
    # a quantity alone (inventory counts) is one signal, not two
    "sku,warehouse,qty_on_hand\nA-100,W1,40\nA-101,W2,12\n",
])
def test_one_signal_or_none_is_not_retail(csv):
    assert _contract(_load(csv)).domain is None


def test_a_person_named_column_is_never_an_id_role_and_names_stay_pii():
    df = _load("Name,qty,price\nAnn Smith,1,10\nBen Jones,2,12\nCara Moss,1,9\n")
    c = _contract(df)
    assert "order_id" not in c.roles and "Name" in c.pii_columns
    df = _load("Name,qty,price\n#1001,1,10\n#1002,2,12\n#1003,1,9\n")   # Shopify's order number
    c = _contract(df)
    assert c.roles.get("order_id") == "Name" and "Name" not in c.pii_columns


@pytest.mark.parametrize("csv, pii", [
    ("customer_name,qty\nsmith, ann,1\n", True), ("name,qty\nann smith,1\nben jones,2\n", True),
    ("NAME,qty\nANN SMITH,1\nBEN JONES,2\n", True), ("patient_name,qty\nPT-0001,1\nPT-0002,2\n", True),
    ("product_name,qty\nLinen Shirt,1\nWool Scarf,2\n", False),
    ("Lineitem name,qty\nLinen Shirt,1\nWool Scarf,2\n", False),
])
def test_names_in_every_convention_stay_pii_and_product_names_do_not(csv, pii):
    c = _contract(_load(csv))
    assert bool(c.pii_columns) is pii, c.pii_columns


def test_pack_ids_are_ids_not_metrics():
    c = _contract(_gen("store_weeks"))
    store = next(col for col in c.columns if col.name == "Store_No")
    assert store.is_id and not store.is_metric and "Store_No" not in c.metric_columns


def test_dates_dd_mm_with_a_time_parse():
    assert pd.api.types.is_datetime64_any_dtype(_gen("b2b_invoices")["InvoiceDate"])


# KPIs on hand-made frames: each rule once.
def _lines(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols)


ROLES = {"order_id": "o", "quantity": "q", "unit_price": "p", "discount": "d", "status": "s",
         "currency": "c", "email": "e", "sku": "k"}


def _r(df: pd.DataFrame, extra: dict | None = None) -> dict:   # the roles a frame has
    return {r: c for r, c in {**ROLES, **(extra or {})}.items() if c in df.columns}


def test_net_sales_aov_and_the_rows_they_leave_out():
    df = _lines(o=["1", "1", "2", "3", "4", "5", "6"], q=[1, 2, 1, 1, 1, 1, -1],
                p=[10.0, 5.0, 20.0, 30.0, 40.0, 50.0, 10.0], d=[2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                s=["paid", "paid", "paid", "refunded", "voided", "paid", "paid"],
                c=["USD"] * 6 + ["USD"], e=["a@x.com"] * 5 + ["test@shop.dev", "b@x.com"],
                k=["A", "B", "A", "A", "A", "A", "A"])
    kinds = row_kinds(df, _r(df), RETAIL).tolist()
    assert kinds == ["sale", "sale", "sale", "refunded", "cancel", "test", "return"]
    # order 1: 10 + 10 - 2 (once, not per line); order 2: 20; the return: -10
    assert compute_kpi(df, _r(df), RETAIL, "net_sales") == pytest.approx(28.0)
    assert compute_kpi(df, _r(df), RETAIL, "orders") == 2
    assert compute_kpi(df, _r(df), RETAIL, "aov") == pytest.approx(14.0)
    assert compute_kpi(df, _r(df), RETAIL, "units") == 4
    # returned value 10 + refunded 30, over gross (20 + 20 + 30)
    assert compute_kpi(df, _r(df), RETAIL, "return_rate") == pytest.approx(40 / 70)


def test_a_per_line_discount_is_summed_per_line():
    df = _lines(o=["1", "1"], q=[1, 1], p=[10.0, 10.0], d=[1.0, 3.0])
    assert compute_kpi(df, _r(df), RETAIL, "net_sales") == pytest.approx(16.0)


def test_money_kpis_use_the_main_currency_only():
    df = _lines(o=["1", "2", "3"], q=[1, 1, 1], p=[10.0, 10.0, 999.0], c=["USD", "USD", "CAD"])
    assert compute_kpi(df, _r(df), RETAIL, "net_sales") == 20.0
    assert compute_kpi(df, _r(df), RETAIL, "orders") == 2


@pytest.mark.parametrize("df, kpi", [
    (_lines(o=["1"], q=[1], p=[10.0]).iloc[:0], "net_sales"),        # empty
    (_lines(o=["1"], q=[-1], p=[10.0]), "aov"),                       # no sale orders: den 0
    (_lines(o=["1"], q=[1], p=[10.0]), "gross_margin_pct"),           # no cost or profit
    (_lines(o=["1"], q=[float("nan")], p=[float("nan")]), "net_sales"),   # nothing to add
    (_lines(o=["1"], q=[1], p=[10.0]), "return_rate"),                # nothing returned
    (_lines(o=["1"], q=[1], p=[10.0]), "no_such_kpi"),
])
def test_an_unavailable_kpi_is_none_never_zero_or_infinite(df, kpi):
    assert compute_kpi(df, _r(df), RETAIL, kpi) is None


def test_kpis_by_group_drop_groups_with_a_zero_denominator():
    df = _lines(o=["1", "2", "3"], q=[1, 1, -1], p=[10.0, 30.0, 5.0], k=["A", "B", "C"])
    by = compute_kpi(df, _r(df), RETAIL, "aov", by="k")
    assert by.to_dict() == {"A": 10.0, "B": 30.0}


def test_margin_from_profit_or_cost_is_a_ratio_of_sums():
    df = _lines(r=[100.0, 300.0], cogs=[90.0, 150.0])
    roles = {"revenue": "r", "cost": "cogs"}
    assert compute_kpi(df, roles, RETAIL, "gross_margin_pct") == pytest.approx(160 / 400)
    df = _lines(r=[100.0, 300.0], pr=[10.0, 150.0])
    assert compute_kpi(df, {"revenue": "r", "profit": "pr"}, RETAIL, "gross_margin_pct") == \
        pytest.approx(160 / 400)


@pytest.mark.parametrize("name, kpi, key", [("retail_lines", "aov", "aov"),
                                            ("b2b_invoices", "return_rate", "return_rate_pct")])
def test_kpis_match_the_planted_truth_once_cleaned(name, kpi, key):
    """On the generator's own frame (no duplicates, order fields carried down an order) the pack
    gets the truth computed independently in datagen. Cleaning makes the upload this frame."""
    raw, truth = datagen.make(name, 1)
    df = _load(raw.drop_duplicates().to_csv(index=False))
    c = _contract(df)
    if name == "retail_lines":
        order_level = ["Discount Amount", "Financial Status", "Email", "Shipping Country"]
        df[order_level] = df.groupby("Name")[order_level].ffill()
        df["Total"] = df["Total"].where(df["Name"].ne(df["Name"].shift()))
    if name == "b2b_invoices":
        df["UnitPrice"] = df["UnitPrice"].str.lstrip("£").astype(float).replace(99999.0, float("nan"))
    got = compute_kpi(df, c.roles, RETAIL, kpi)
    want = truth[key] / (100 if key.endswith("_pct") else 1)
    assert got == pytest.approx(want, rel=1e-6)
