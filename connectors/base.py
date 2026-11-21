"""
Abstract base class for all data connectors.
Every connector exposes a single .load() method that returns a DataFrame.
Adding a new source (Notion, Airtable, etc.) = add one subclass, zero agent changes.
"""

from __future__ import annotations

import datetime
import functools
import ipaddress
import re
import socket
import ssl
import zlib
from abc import ABC, abstractmethod

import anyio.to_thread
import certifi
import httpcore
import httpx
import pandas as pd

from orchestrator.state import ConnectorConfig, ConnectorType, DataContract


# Date-shaped text: ISO (2025-01, 2025-01-31, 2025-01-31 10:00, … -0500), US (1/31/2025),
# European (31.01.2025), either with a time (31/01/2025 14:30), "Jan 31, 2025".
_DATE_TEXT_RE = re.compile(
    r"^(\d{4}-\d{1,2}(-\d{1,2})?([ T][\d:.]+( ?(Z|[+-]\d{2}:?\d{2}))?)?|(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{4})( \d{1,2}:\d{2}(:\d{2})?)?"
    r"|[A-Za-z]{3,9} \d{1,2}, \d{4})$")
_FIRST_PART_RE = re.compile(r"^(\d{1,2})[/.]\d{1,2}[/.]\d{2,4}")
_OFFSET_RE = re.compile(r" ?(Z|[+-]\d{2}:?\d{2})$")


def _minutes(offset: str) -> int:
    """'-0500' / '+01:00' / 'Z' as minutes east of UTC."""
    if offset in ("Z", "z"):
        return 0
    digits = offset[1:].replace(":", "")
    return (1 if offset[0] == "+" else -1) * (int(digits[:2]) * 60 + int(digits[2:]))


def _looks_like_dates(s: pd.Series, share: float = 1.0) -> bool:
    """A text/object column whose values are dates, whatever its name: a SQL `DATE` column
    arrives as Python date objects, a query's `month` as '2025-01-01'. Checks that `share` of the
    first 50 non-null values are date-shaped (the gate); parse_text_dates checks the column."""
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return False
    vals = s.dropna().head(50).tolist()
    return bool(vals) and sum(
        isinstance(v, (datetime.date, pd.Timestamp))
        or (isinstance(v, str) and bool(_DATE_TEXT_RE.match(v.strip()))) for v in vals
    ) >= share * len(vals)


def parse_text_dates(s: pd.Series, share: float = 1.0) -> pd.Series | None:
    """The column as datetimes if it is text whose values are dates (`share` of them parse),
    else None. Decided by the values, never the name: `lifetime_value` stays a number. The load
    converts only at share 1.0 (lossless: a junk value would vanish unlogged); a column with some
    junk is still a date column (share 0.8) and Cleaning parses it with an audit entry.
    Day-first when the dates use dots (31.01.2025) or a first part above 12 appears."""
    if pd.api.types.is_datetime64_any_dtype(s) or not _looks_like_dates(s, share):
        return None
    text = s.dropna().astype(str).str.strip()
    first = text.str.extract(_FIRST_PART_RE)[0].dropna().astype(int)
    dayfirst = bool(text.str.contains(r"^\d{1,2}\.\d", regex=True).any() or (first > 12).any())
    try:
        parsed = pd.to_datetime(s.astype("string"), format="mixed",
                                dayfirst=dayfirst, errors="coerce")
    except ValueError:   # mixed UTC offsets
        text = s.astype("string").str.strip()
        offsets = {_minutes(m) for m in text.str.extract(_OFFSET_RE)[0].dropna().unique()}
        if len(offsets) == 2 and max(offsets) - min(offsets) == 60 and (max(offsets) <= 0 or min(offsets) >= 0):
            # one place across a daylight-saving change (Shopify's -0500 / -0400): local time
            parsed = pd.to_datetime(text.str.replace(_OFFSET_RE, "", regex=True), format="mixed",
                                    dayfirst=dayfirst, errors="coerce")
        else:   # several places (pickup -05:00, delivery -08:00): UTC keeps instants and durations
            parsed = pd.to_datetime(text, format="mixed", dayfirst=dayfirst, errors="coerce",
                                    utc=True)
    return parsed if parsed.notna().sum() >= share * s.notna().sum() else None


# Unix-epoch seconds or milliseconds, 1998-07 .. 2039-09 (a historian's timeInSeconds, a JS
# timestamp): THE epoch rule of ingestion's typing, cleaning and every reader
_EPOCHS = (("s", 9e8, 2.2e9), ("ms", 9e11, 2.2e12))
# a date needs a year: 'M01', 'Jan' or '12:30' parse with an invented one
HAS_YEAR_RE = re.compile(r"\d{4}|\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2}\b")


def epoch_times(s: pd.Series, share: float = 0.8) -> pd.Series | None:
    """Numbers as datetimes when `share` of them are epoch seconds or milliseconds (one unit for
    the column; a value outside its range is NaT), else None."""
    n = pd.to_numeric(s, errors="coerce")
    if not n.notna().any():
        return None
    for unit, lo, hi in _EPOCHS:
        inside = n.between(lo, hi, inclusive="left")
        if inside[n.notna()].mean() >= share:
            return pd.to_datetime(n.where(inside), unit=unit)
    return None


def as_times(s: pd.Series) -> pd.Series:
    """A column as datetimes by the load's one rule (offsets included); values that aren't
    dates are NaT. Already-parsed columns come back as they are."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if (epoch := epoch_times(s)) is not None:
        return epoch
    parsed = parse_text_dates(s, 0.0)   # all blank: None (no value looks like a date)
    if parsed is None:
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    text = s.astype("string").str.strip()
    dated = s.map(lambda v: isinstance(v, (datetime.date, pd.Timestamp))).astype(bool)
    return parsed.where(dated | text.str.contains(HAS_YEAR_RE, regex=True).fillna(False))


def _config_secrets(config: ConnectorConfig) -> list[str]:
    """Every piece of a config that is a credential, the strongest first: the connection string
    whole and its user-info part raw and decoded, the bare password, the API key, a service
    account, a value in the API URL's query whose key names a credential (`_secret_key`). How
    each masks text is `_cut`'s rule."""
    from urllib.parse import parse_qsl, unquote, urlparse
    conn = config.connection_string or ""
    secrets = []
    if (m := re.match(r"^[^:/]+://(.*)@[^@/]*(/|$)", conn)) is not None:
        userinfo = m.group(1)
        password = userinfo.split(":", 1)[1] if ":" in userinfo else ""
        secrets = [conn, userinfo, unquote(userinfo), password, unquote(password)]
    else:
        secrets.append(conn)
    secrets += [config.auth_value or "", config.service_account_json or ""]
    if config.url:
        try:
            query = parse_qsl(urlparse(config.url).query)
        except ValueError:   # a malformed URL ('[::1'): no credential to read, never a crash in
            query = []       # the error handler that called this (code review)
        secrets += [v for k, v in query if _secret_key(k)]
    return [s for s in dict.fromkeys(secrets) if s]


_CREDENTIAL_WORDS = frozenset("""key apikey token sig signature secret password passwd pwd pass
    auth credential credentials""".split())


def _secret_key(key: str) -> bool:
    """A query key naming a credential, read by its last word (`-`, `_`, camelCase): api_key,
    X-Amz-Security-Token, sessionToken, private_token. A substring read registered `revenue` for
    `region_code=revenue` and `Shakespeare` for `author=` (three reviewers); a whole-key read
    missed X-Amz-Security-Token (automated review). `code` only as the whole key (OAuth's)."""
    words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", key)
    return key.lower() == "code" or (bool(words) and words[-1].lower() in _CREDENTIAL_WORDS)
# Every credential this process has been handed, masked where text reaches an operator: the log
# filter and the eval reports (Airflow's SecretsMasker). 5.8a and 5.8b masked per sink, and each
# new sink leaked (logs, three report writers, a query's key: P58-06). Registered where any
# connector is built (`get_connector`). Text a USER reads is masked with that config's own
# credentials only (`without_secrets`): one tenant's password masking another's text would tell
# them it guessed right (automated security review of the hardening). Bounded: the newest 4,096
# values of at most 8 KB, six characters at least (a short one would mask its letters
# everywhere), at most 8 from one registration (a config's credentials first), and registering
# again makes a value newest, so the connectors in use stay masked while old ones age out (one
# user's flood can't evict them: automated review). Matched by substring, longest first, from a
# list kept sorted on change (a regex of 4,096 values was a 32 MB compile). Swapped, never
# mutated, so a log line on another thread reads it while a run registers.
# ponytail: process-wide; a per-run scope (contextvar) when one process serves many tenants.
_MAX_SECRETS, _MAX_SECRET_CHARS, _PER_REGISTRATION = 4096, 8192, 8
_SECRETS: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())   # (newest last, longest first)


def register_secrets(*values: str) -> None:
    global _SECRETS
    new = [v for v in dict.fromkeys(values)
           if v and 6 <= len(v) <= _MAX_SECRET_CHARS][:_PER_REGISTRATION]
    known = _SECRETS[0]
    if not new or known[-len(new):] == tuple(new):   # nothing, or already the newest
        return
    fresh = set(new)
    kept = (*(v for v in known if v not in fresh), *new)[-_MAX_SECRETS:]
    _SECRETS = (kept, tuple(sorted(kept, key=len, reverse=True)))


def redact(text: str) -> str:
    """`text` with every registered credential as *** (operator sinks: logs, eval reports); a
    password inside its connection string goes with the string (longest first)."""
    for secret in _SECRETS[1]:
        if secret in text:
            text = _cut(text, secret)
    return text


def _cut(text: str, secret: str) -> str:
    """`text` with `secret` as ***. A credential form (`user:password`, a connection string) or a
    long value (≥ 12) wherever it stands; a short bare value only as a whole word: the password
    'postgres' masked 'postgresql' in every log (three reviewers), and dropping short values
    instead left a short password a driver echoed in plain sight (automated security review)."""
    if len(secret) >= 12 or ":" in secret or "@" in secret:
        return text.replace(secret, "***")
    return re.sub(rf"(?<!\w){re.escape(secret)}(?!\w)", "***", text)


def without_secrets(message: str, config: ConnectorConfig) -> str:
    """`message` with this config's credentials cut out: a driver's error can quote its URL or
    echo the password. Never another config's (see `_SECRETS`), and it registers nothing: a
    value typed into the connect form never masks the operator's logs (security review: a
    'password' of 'GuardrailViolation' hid it everywhere); `get_connector` registers real use."""
    for s in sorted((s for s in _config_secrets(config) if len(s) >= 3), key=len, reverse=True):
        message = _cut(message, s)
    return message


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")
_COMPATIBLE = ipaddress.ip_network("::/96")
_UNREADABLE = ipaddress.ip_address("0.0.0.0")   # noqa: S104 - a refused address, never bound


def _inside(ip) -> list:
    """The address(es) a connection to `ip` reaches: an IPv6 address carrying an IPv4 one (mapped,
    translated, compatible, NAT64, 6to4, both ends of Teredo) is the IPv4 address (5.8b: NAT64 to
    the cloud metadata address read `is_global`). A local-use NAT64 prefix embeds it at a
    length we can't know: 0.0.0.0, which every check refuses."""
    if ip.version == 4:
        return [ip]
    low = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.ipv4_mapped:
        return [ip.ipv4_mapped]
    if ip.teredo:
        return list(ip.teredo)
    if ip.sixtofour:
        return [ip.sixtofour]
    if ip in _NAT64 or ip in _TRANSLATED or (ip in _COMPATIBLE and int(ip) > 1):
        return [low]
    if ip in _NAT64_LOCAL:
        return [_UNREADABLE]
    return [ip]


def resolved(host: str) -> list:
    """The addresses `host` resolves to (ipaddress objects), each as the address it reaches
    (`_inside`); ValueError if it doesn't resolve. The one lookup behind every host check (a
    database's host, the API's and a share link's downloads); callers connect to these, never
    to the name again (DNS rebinding). ponytail: on a NAT64-only network an embedded IPv4 address
    is reached through the gateway; connecting to it directly there fails (keep both forms then)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{host}': {exc}") from exc
    out = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        out += [a for a in _inside(ip) if a not in out]
    return out


class RefusedHost(ValueError):
    """A fetch to a host that isn't on the public internet. The message names the host only."""


class HostNotFound(RefusedHost):
    """A host name that doesn't resolve: a typo, not a private address (5.8b audit)."""


FETCH_DEADLINE_S = 120   # a user URL's whole fetch, every page or hop (httpx's timeout is per read)


class BodyTooLarge(ValueError):
    """A response past the reader's byte cap."""


_ZLIB_AUTO = 32 + zlib.MAX_WBITS   # zlib reads a gzip or a zlib header, whichever it finds


async def capped_body(response: httpx.Response, cap: int) -> bytes:
    """A response's body, at most `cap` bytes once decoded, counted as it arrives (a size the
    server states can't be trusted). Asked for identity, a server may still compress: gzip and
    deflate are decoded here, each chunk to no more than the cap has left, so a bomb is refused
    as too large and never expanded (httpx would decode a chunk whole: 5.8b audit, zstd). 5.8b
    refused every compressed answer, and an API or CDN that gzips anyway (it loaded before)
    stopped loading (senior audit). zstd and brotli have no decoder here: said, not read."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        decoder = None
    elif encoding in ("gzip", "x-gzip", "deflate"):
        decoder = zlib.decompressobj(_ZLIB_AUTO)
    else:
        raise ValueError(f"it compressed its answer as {encoding}, which isn't read here")
    body, raw = bytearray(), 0
    raw_deflate_tried = False
    try:
        async for chunk in response.aiter_raw():
            raw += len(chunk)   # the sent bytes too, whatever the decoder holds back
            if raw > cap:
                raise BodyTooLarge
            if decoder is None:
                body += chunk
            while decoder is not None and chunk:
                try:
                    out = decoder.decompress(chunk, cap + 1 - len(body))
                except zlib.error:   # deflate sent without its zlib header, as some servers do
                    if encoding != "deflate" or body or raw_deflate_tried:
                        raise
                    raw_deflate_tried, decoder = True, zlib.decompressobj(-zlib.MAX_WBITS)
                    continue
                if decoder.unconsumed_tail:   # more would come out than the cap has left
                    raise BodyTooLarge
                body += out
                if len(body) > cap:
                    break
                # a member ended: the next one (gzip may be several, each read whole; the first
                # reader returned only the first: test analyst), or junk, which is refused
                chunk = decoder.unused_data if decoder.eof else b""
                if chunk:
                    decoder = zlib.decompressobj(_ZLIB_AUTO)
            if len(body) > cap:
                raise BodyTooLarge   # each caller says it in its own words (the link's, the API's)
        if decoder is not None and raw:
            body += decoder.flush(cap + 1 - len(body))
            if len(body) > cap:
                raise BodyTooLarge
            if not decoder.eof:   # cut short: never half a file read as the whole one
                raise ValueError(f"its {encoding} answer ended before it was complete")
    except zlib.error:
        raise ValueError(f"its {encoding} answer couldn't be unpacked") from None
    return bytes(body)


def public_addresses(host: str) -> list:
    """`host`'s addresses if every one is a public internet address (`is_global` after
    unwrapping: no loopback, private/LAN, link-local or metadata, CGNAT, unique-local or
    reserved), else RefusedHost. Unlike a database's host (a company's own LAN is allowed), a
    URL the server fetches for a user is never inside any network the server sits in."""
    try:
        ips = resolved(host)
    except ValueError:
        raise HostNotFound(f"The host '{host}' wasn't found.") from None
    if not ips or not all(ip.is_global for ip in ips):
        raise RefusedHost(f"The host '{host}' isn't on the public internet, so it can't be "
                          "fetched from this server.")
    return ips


_NETWORK = httpcore.AnyIOBackend   # the real sockets under the check (tests swap in a fake)


class _CheckedNetwork(httpcore.AsyncNetworkBackend):
    """Where every connection the guarded client makes starts (a retry and each redirect hop
    too): the URL's host is looked up once, checked, and the socket goes to a checked address;
    TLS still verifies the certificate against the host name (httpcore's server_hostname)."""

    def __init__(self) -> None:
        self._inner = _NETWORK()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        ips = await anyio.to_thread.run_sync(public_addresses, host)
        failed: Exception   # set by each failed try: ips is never empty here
        for ip in ips:   # every address, in order: no happy-eyeballs in httpcore
            try:
                return await self._inner.connect_tcp(str(ip), port, timeout, local_address,
                                                     socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failed = exc
        raise failed
    # no unix sockets (the base class refuses them) and no sleep: the client never retries


@functools.cache
def _tls_context() -> ssl.SSLContext:
    """certifi's CAs, loaded once (a context is read-only once built; 5.8b review: every fetch
    loaded the bundle twice)."""
    return ssl.create_default_context(cafile=certifi.where())


def public_client(timeout: float = 30) -> httpx.AsyncClient:
    """THE client for any URL a user gives (the REST API source, a share link). Redirects are
    not followed (a caller that follows them gets each hop checked at its connection anyway),
    proxies from the environment are ignored (they would carry the request past the check), and
    responses come uncompressed (a gzip body can expand far past any byte cap)."""
    # its pool (and retries: 0, the default) is replaced; verify: else it loads the CAs again
    transport = httpx.AsyncHTTPTransport(verify=_tls_context())
    # httpx 0.28 has no network_backend option: its pool is replaced by one on the checked
    # network (tests/test_public_http.py pins that this still holds)
    transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001
        ssl_context=_tls_context(), network_backend=_CheckedNetwork())
    return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False,
                             trust_env=False, headers={"Accept-Encoding": "identity"})


# A capped load names its cap: the source evidence carries this note and the report's alerts find
# it by this start (agents/insight_agent._alerts). One wording for every connector.
TRUNCATED = "Only the first "


def truncation_note(kept: int, total: int | None, limit: str) -> str:
    """'Only the first 50,000 of 61,234 rows were loaded (…)'; `total` None: the source has
    more, how many is unknown."""
    of = f" of {total:,}" if total else ""
    more = "" if total else "; the source has more"
    return f"{TRUNCATED}{kept:,}{of} rows were loaded ({limit}{more})."


_FLOAT_EXACT = 2**53   # past it a float64 can't hold every whole number: two ids would become one


def exact_whole_numbers(values: pd.Series) -> pd.Series | None:
    """A column of whole numbers that a float would round (ids, order numbers past 2**53), as
    exact Int64, or as its digits past int64; None when it isn't one (a float holds it, or a
    value isn't a whole number). One rule for the file reader (an id column with a blank reads as
    float64) and the databases (NUMERIC(19,0)) (5.8b hardening: two ids read as one)."""
    present = values.dropna()
    if present.empty:
        return None
    digits = present.map(lambda v: str(v).strip())
    # a leading zero makes it a code (an ICCID, an account number), whatever it holds: text
    if not digits.str.fullmatch(r"[-+]?(?:0|[1-9]\d*)").all():
        return None
    whole = digits.map(int)
    if whole.map(abs).max() <= _FLOAT_EXACT:
        return None
    if whole.map(lambda v: -2**63 <= v < 2**63).all():
        exact = [whole[i] if i in whole.index else pd.NA for i in values.index]   # no NaN: it
        return pd.Series(pd.array(exact, dtype="Int64"), index=values.index)    # goes via float
    return digits.reindex(values.index).astype(object).where(values.notna(), None)


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """The one load rule after any source's read (ADR-0010), so a table reads the same from each:
    '' is a blank; a text column whose values are all dates becomes datetimes (5.8a: dates read
    from SQL and the API stayed text), at one resolution (us); -0.0 is 0.0. The file reader, the
    databases, the Sheet and the API all end here. Columns by position: names are made unique
    later (ingestion), and a SQL join can return two `id` columns (audit pass 2)."""
    for i in range(df.shape[1]):
        s = df.iloc[:, i]
        if not pd.api.types.is_numeric_dtype(s) and (blank := s.eq("")).any():
            s = s.mask(blank)   # '' is a blank, as a CSV reads it (MySQL, JSON keep it)
        if (parsed := parse_text_dates(s)) is not None:
            s = parsed
        if pd.api.types.is_datetime64_any_dtype(s):
            s = s.dt.as_unit("us")   # one resolution: a DATE column read as [s]
        elif pd.api.types.is_float_dtype(s):
            s = s + 0.0   # -0.0 → 0.0: a CSV keeps "-0.0", JSON and SQL don't ("-$0.00")
        df.isetitem(i, s)
    return df


class DataConnector(ABC):
    read_info: dict = {}   # how the last load read the source (file connector); see source evidence

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    async def load(self) -> pd.DataFrame:
        """The source's table, read and then normalised by the one load rule."""
        return normalize_frame(await self._read())

    @abstractmethod
    async def _read(self) -> pd.DataFrame:
        """Read the table from the source as it stores it."""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test that the source is reachable and credentials are valid.
        Returns (success, message).
        """

    def build_data_contract(self, df: pd.DataFrame) -> DataContract:
        """Profile a DataFrame into a DataContract (roles, PII, masked samples, summary,
        fingerprint, source evidence). The ingestion agent owns the rules; every connector and
        every eval harness gets the same contract from the same frame."""
        from agents.ingestion_agent import build_contract
        return build_contract(df, self.config.type, self.read_info)


def sources_table() -> str:
    """README's "Data sources and limits" table, built from the connectors' own formats,
    dialects and caps (tests/test_sources_readme.py keeps the README equal to it)."""
    from connectors import api_connector as api
    from connectors import sql_connector as sql
    from connectors.csv_connector import MAX_UPLOAD_MB, UPLOAD_SUFFIXES
    from connectors import link
    rows = [("File upload", f"{', '.join(sorted(UPLOAD_SUFFIXES))} in the UI",
             f"{MAX_UPLOAD_MB} MB per file; old .xls: save as .xlsx first"),
            ("Share link", "a public Google Sheets, Google Drive or Dropbox link, or any https "
             "CSV/xlsx URL, pasted under the upload; OneDrive/SharePoint 'Anyone' links too "
             "(tested on mocked links only; a 1drv.ms short link is untested)",
             f"copied once as a dataset version (paste again to refresh); {MAX_UPLOAD_MB} MB, "
             f"{link.MAX_HOPS} redirects, public hosts only")]
    for key, d in sql.DIALECTS.items():
        if key == "sqlite":
            rows.append(("SQLite", "`sqlite:///path.db`",
                         "development only; refused when `APP_ENV=production`"))
            continue
        rows += [(f"{d.title}, ask questions", f"`{key}://user:password@host:port/database`"
                  f"{d.needs}, no table name: the SQL agent writes one read-only query "
                  "per question and aggregates in the database",
                  f"{sql.MAX_ROWS:,} result rows, {sql.TIMEOUT_S} s per query"),
                 (f"{d.title}, one table", "the same + a table name",
                  f"first {sql.LOAD_MAX_ROWS:,} rows, {sql.TIMEOUT_S} s, read-only")]
    rows += [("Google Sheets", "Spreadsheet ID + sheet name + service-account JSON",
              "read as the sheet's CSV export"),
             ("REST API", "URL + auth (Bearer, API key with a configurable header, Basic) + "
              "optional pagination key",
              f"{api.MAX_PAGES} pages or {api.MAX_RECORDS:,} records; public hosts only")]
    return ("| Source | How to connect | Limits |\n|--------|----------------|--------|\n"
            + "\n".join(f"| **{a}** | {b} | {c} |" for a, b, c in rows))


def get_connector(config: ConnectorConfig) -> DataConnector:
    """Factory function — returns the right connector for the given config type."""
    from connectors.csv_connector import CSVConnector
    from connectors.sql_connector import SQLConnector
    from connectors.sheets_connector import SheetsConnector
    from connectors.api_connector import APIConnector

    mapping = {
        ConnectorType.CSV: CSVConnector,
        ConnectorType.SQL: SQLConnector,
        ConnectorType.SHEETS: SheetsConnector,
        ConnectorType.API: APIConnector,
    }
    cls = mapping.get(config.type)
    if not cls:
        raise ValueError(f"Unsupported connector type: {config.type}")
    register_secrets(*_config_secrets(config))   # every source's credentials, before any use
    return cls(config)
