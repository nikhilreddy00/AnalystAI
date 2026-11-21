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
    get_connector(_sql_config())
    session = AnalysisSession(session_id="s", user_id="u")
    _fail(session, "ingestion", IngestionError("x", f"Couldn't open: dsn has {CANARY}"))
    assert CANARY not in session.error_message


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
