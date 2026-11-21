"""No credential reaches any place text leaves the process (5.8b hardening, P58-06 closed by
construction): one registry of every secret a connector was handed (`connectors.base`, filled
where any connector is built) and one redactor, read by the log filter, the run's error message,
the connect form's answer and the eval report writer.

5.8a and 5.8b masked per sink, and each new sink leaked: httpx's log lines, three report writers,
a key in a URL's query. A canary given to a source must be in none of them."""
import io
import json
import logging

from agents.errors import IngestionError
from connectors.base import get_connector
from orchestrator.state import ConnectorConfig, ConnectorType

CANARY = "Canary-5_8b-x9Q7wL2"


def _sql_config() -> ConnectorConfig:
    return ConnectorConfig(type=ConnectorType.SQL,
                           connection_string=f"postgresql://reader:{CANARY}@db.invalid:5432/acme")


def test_a_log_line_quoting_a_connection_string_is_redacted_traceback_too():
    from api.logging_scrub import ScrubFilter
    get_connector(_sql_config())   # registered where the connector is built
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.addFilter(ScrubFilter())
    log = logging.getLogger("canary.test")
    log.addHandler(handler)
    try:
        try:
            raise RuntimeError(f"connect to postgresql://reader:{CANARY}@db.invalid failed")
        except RuntimeError:
            log.exception("driver said %s", f"password={CANARY}")
    finally:
        log.removeHandler(handler)
    assert CANARY not in out.getvalue() and "***" in out.getvalue()


def test_the_runs_error_message_is_redacted():
    from orchestrator.graph import _fail
    from orchestrator.state import AnalysisSession
    session = AnalysisSession(session_id="s", user_id="u", connector_config=_sql_config())
    _fail(session, "ingestion", IngestionError("x", f"Couldn't open: dsn has {CANARY}"))
    assert CANARY not in session.error_message


def test_one_users_credential_never_masks_another_users_text():
    """Automated security review: with one process-wide mask, a value another tenant typed would
    come back masked in this user's message, telling them it is someone's password. User-facing
    text is masked with its own config's credentials only; the logs (the operator's) with all."""
    from connectors.base import redact, without_secrets
    from orchestrator.graph import _fail
    from orchestrator.state import AnalysisSession
    get_connector(_sql_config())   # tenant A's password, registered
    theirs = ConnectorConfig(type=ConnectorType.SQL,
                             connection_string="postgresql://b:B-own-pw-77@db.invalid/x")
    said = f"value {CANARY} not found"
    assert without_secrets(said, theirs) == said
    session = AnalysisSession(session_id="s", user_id="b", connector_config=theirs)
    _fail(session, "ingestion", IngestionError("x", said))
    assert CANARY in session.error_message
    assert CANARY not in redact(said)   # the operator's log still masks it


def test_the_registry_is_bounded_and_a_flood_never_unmasks_a_connector_in_use():
    """Automated review: unbounded growth, and one user's flood evicting another tenant's
    credentials. At most 8 from one registration; using a connector registers it again (newest)."""
    import connectors.base as base
    base.register_secrets(*(f"one-call-{i:06d}" for i in range(100)))
    assert sum(v.startswith("one-call-") for v in base._SECRETS[0]) == 8
    base.register_secrets("x" * (base._MAX_SECRET_CHARS + 1))
    assert "x" * (base._MAX_SECRET_CHARS + 1) not in base._SECRETS[0]
    victim = _sql_config()
    for i in range(0, base._MAX_SECRETS + 800, 8):
        base.register_secrets(*(f"flood-{j:06d}" for j in range(i, i + 8)))
        if i % 1024 == 0:
            get_connector(victim)   # the victim's run goes on using its connector
    get_connector(victim)
    assert len(base._SECRETS[0]) == base._MAX_SECRETS
    assert CANARY not in base.redact(f"pw {CANARY}")


def test_a_report_holding_a_credential_anywhere_is_redacted():
    """Nested JSON text, a query key, a field name no regex knows: the registry sees them all."""
    from evals.run import without_credentials
    get_connector(_sql_config())
    report = json.dumps({"runs": {"c": [{"note": json.dumps({"whatever": CANARY}),
                                        "url": f"https://x.example/?weird={CANARY}"}]}})
    assert CANARY not in without_credentials(report)


def test_an_api_key_in_the_sources_url_is_registered():
    from connectors.base import redact
    get_connector(ConnectorConfig(type=ConnectorType.API,
                                  url=f"https://api.example/v1?api_key={CANARY}-api"))
    assert redact(f"GET https://api.example/v1?api_key={CANARY}-api 403") == \
        "GET https://api.example/v1?api_key=*** 403"


async def test_the_connect_forms_answer_is_redacted():
    from api.routes.connectors import test_connection
    r = await test_connection(_sql_config(), None)
    assert not r.success and CANARY not in r.message


def test_a_short_value_never_masks_text_everywhere():
    """Airflow's caveat: a password of 'a' would mask every 'a'. Six characters at least."""
    from connectors.base import redact, register_secrets
    register_secrets("abc", "12345")
    assert redact("abc 12345 abcdef") == "abc 12345 abcdef"
