"""Which rows a number reads (rows_for, entity rows, ended records) and the marker columns
added to the frame analysis code gets."""
from __future__ import annotations

import pandas as pd

from .pack import Pack, ROW_KIND_COLUMN, _day, _snapshot_column, defaults_pack, pack_for
from .detect import main_currency, row_kinds, superseded
from .spell import _ended, _ended_undated, _spell_events, has_spell, window
from .kpi import counted, stock_rows


def version_roles(pack: Pack | None) -> set[str]:
    """The roles of a version chain (a claim's line and adjustment number): labels, never measures."""
    return {*pack.version_key, *([pack.version_order] if pack.version_order else [])} if pack else set()


def version_grain(contract) -> tuple[list[str], str] | None:
    """(the version chain's key columns, its order column) in this data (a strong match), or None:
    one row is one key at one order value (a claim and claimant at one valuation)."""
    pack = defaults_pack(contract)
    keys = [contract.roles[r] for r in (pack.version_key if pack else []) if r in contract.roles]
    order = contract.roles.get(pack.version_order) if pack and pack.version_order else None
    return (keys, order) if keys and order else None


def with_row_kind(df: pd.DataFrame, contract) -> tuple[pd.DataFrame, str | None]:
    """The frame with each row's kind as a column (sale, return, refunded, cancel, test,
    non_product), for code the model writes: it filters on the column, never re-derives the
    rules (the analysis smoke run kept 12 test orders it had been told to leave out). The name
    never replaces a column of the user's; no pack: the frame unchanged."""
    pack = pack_for(contract.domain) if contract is not None else None
    if pack is None:
        return df, None
    name = next(n for n in (ROW_KIND_COLUMN, f"_{ROW_KIND_COLUMN}", f"pack_{ROW_KIND_COLUMN}")
                if n not in df.columns)   # never a user's own column
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    kinds = row_kinds(df, roles, pack)
    keep, _, _ = main_currency(df, roles)
    out = df.assign(**{name: kinds.map(pack.code).where(keep, "other_currency")})
    return with_spell_events(out, pack, roles), name


def spell_event_columns(pack: Pack) -> tuple[str, str, str, str, str]:
    """THE names of the spell markers in the model's frame, from the pack's words: (start event
    date, end event date, end in window, in force now, ended with no date)."""
    w = pack.spell.words
    return (f"{w.start}_event_date", f"{w.end}_event_date", f"{w.end}_in_window", "in_force_now",
            w.unknown)


def stock_population(df: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """The stock tile's rows (kpi.stock_rows) as a mask: the events the model's frame marks
    read the same people."""
    return pd.Series(df.index.isin(stock_rows(df, roles, pack).index), index=df.index)


def in_force_counted(df: pd.DataFrame, roles: dict[str, str], pack: Pack, as_of) -> pd.Index:
    """THE rows in force at `as_of` that the stock tile counts (kpi.stock_rows): the model's
    in_force_now marker and a level by group (MRR or pay by segment, utils._in_force_rows) read
    this, never their own copy (Domain 6 insight gate: MRR by segment added trials, test
    accounts and EUR)."""
    return stock_rows(df, roles, pack, as_of).index


def with_spell_events(df: pd.DataFrame, pack: Pack, roles: dict[str, str]) -> pd.DataFrame:
    """On spell data, the product's own events as columns for code the model writes: each
    spell's first row carries its hire (start) date, its last row its exit date (the end date,
    or on snapshots the first snapshot the person is missing from), up to the as-of; blank on
    every other row. Counting a window's exits is then counting rows with an end event date in
    it, by the row's own department (where the person left from): the model never re-derives a
    vanished leaver (Domain 5 smoke: it counted only dated exits, 20 of 49)."""
    if not has_spell(pack, roles):
        return df
    start_col, end_col, window_col, now_col, unknown_col = names = spell_event_columns(pack)
    if any(c in df.columns for c in names):
        return df
    w = window(pack, df, roles)
    if w is None:
        return df
    as_of, lo = w.as_of, w.lo
    counted = stock_population(df, roles, pack)
    out = df.copy()
    for col, which in ((start_col, "starts"), (end_col, "ends")):
        rows, when = _spell_events(df, roles, pack, which, as_of)
        rows, when = rows[counted.loc[rows.index]], when[counted.loc[rows.index]]
        out[col] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        out.loc[rows.index, col] = when.astype("datetime64[ns]")
        if which == "ends":   # the window the facts name (12 months to the as-of): marked, so a
            # model never forgets to filter it (Domain 5 gate: hr-1 counted every exit ever)
            out[window_col] = (out[col] > lo).map({True: "yes", False: ""})
        out[col] = out[col].dt.strftime("%Y-%m-%d")
    out[unknown_col] = _ended_undated(df, roles, pack).map({True: "yes", False: ""})
    out[now_col] = ""
    out.loc[in_force_counted(df, roles, pack, as_of), now_col] = "yes"
    return out


def rows_for(df: pd.DataFrame, contract, column: str,
             full: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """(rows, how): THE rows every answer, chart and drill-down of `column` reads (plan v2 R1:
    one entry point; tests/test_one_definition.py guards it). On a strong-matched pack's additive
    money or count column: sale and return rows in the main currency, as its tiles count them;
    a level (a balance) keeps every row with its value blanked where the row is left out, so
    `utils.aggregate` / `latest_rows` judge each entity's latest row first. Anything else: every
    row, no note."""
    pack = defaults_pack(contract)
    role = next((r for r, c in contract.roles.items() if c == column), None) if pack else None
    if role and pack.roles[role].over_time == "last" and (gone := ended_records(df, contract)).any():
        # a spell's snapshot row past its end (its exit record) holds no one: its level is
        # blank, so latest_rows never averages a leaver in (Domain 5 gate: hr-8)
        return df.assign(**{column: df[column].where(~gone)}), \
            f"{int(gone.sum()):,} {pack.spell.words.end} records ({pack.spell.words.after_end}) " \
            f"hold no {column}."
    if not (role and counted(pack, role)):
        return df, ""
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    keep, currency, _ = main_currency(df, roles)
    # `full`: the whole data when `df` is a filtered part of it (a row's kind can depend on rows
    # outside the part: a claim line replaced later)
    kinds = row_kinds(full, roles, pack).reindex(df.index) if full is not None \
        and df.index.isin(full.index).all() else row_kinds(df, roles, pack)
    kept = keep & kinds.isin(["sale", "return"])
    if kept.all():
        return df, ""
    # a level (a balance) keeps every row, its value blank where the row is left out: each
    # account's latest row is judged first (a loan charged off this month is out, never its last
    # month's balance; utils.latest_rows), exactly as the tiles do (KPI.at latest)
    out = df.assign(**{column: df[column].where(kept)}) if pack.roles[role].over_time == "last" \
        else df[kept]
    return out, pack.say("money_rows_note", currency=f" in {currency}" if currency else "",
                              n=f"{int((~kept).sum()):,}")


def ended_records(df: pd.DataFrame, contract) -> pd.Series:
    """True on a spell's snapshot rows that record its end: dated on or after its end date, or
    with an `ended` status and no end date (the state rule _in_force applies); all False
    elsewhere (rosters, packs without a spell)."""
    pack = defaults_pack(contract)
    none = pd.Series(False, index=df.index)
    roles = {r: c for r, c in contract.roles.items() if c in df.columns} if pack else {}
    snap = _snapshot_column(pack, roles) if pack and pack.spell else None
    if not snap or not has_spell(pack, roles):
        return none
    return _ended(df, roles, pack, _day(df[snap]))


def snapshot_entity(contract, columns) -> tuple[list[str], str] | None:
    """(entity columns, time column) when the data is snapshots: a level's (a balance's)."""
    lv = next((c for c in (contract.columns if contract is not None else [])
               if c.aggregation in ("last", "first") and c.aggregation_by and c.aggregation_time
               and all(x in columns for x in [*c.aggregation_by, c.aggregation_time])), None)
    return (lv.aggregation_by, lv.aggregation_time) if lv else None


def entity_rows(df: pd.DataFrame, contract) -> pd.DataFrame:
    """Rows for a statistic over entities (a correlation) on snapshot data: each entity's latest
    snapshot (any kind first), then only rows the tiles count (on the books, main currency);
    every snapshot would count an account 14 times. An account that stopped reporting keeps its
    last row (dropping it would bias toward survivors). Not snapshot data: the frame without a
    version chain's earlier versions (a claim's earlier valuations; none without a chain).
    Mirrored in the analysis sandbox's built-in correlation."""
    ent = snapshot_entity(contract, df.columns)
    if ent is None:
        return df[~superseded(df, contract)] if contract is not None else df
    from agents.utils import snapshot_rows
    rows = snapshot_rows(df, ent[0], ent[1], current=False)
    pack = defaults_pack(contract)   # row kinds on a strong match only, as rows_for
    if pack is None:
        return rows
    roles = {r: c for r, c in contract.roles.items() if c in rows.columns}
    keep, _, _ = main_currency(rows, roles)
    return rows[keep & row_kinds(rows, roles, pack).isin(["sale", "return"])]
