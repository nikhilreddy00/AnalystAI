"""Every log line is scrubbed before a handler writes it (Phase 5.5): the current run's PII
values become their tokens (llm.client.pseudonymize) and email/phone/SSN/card/IP patterns are
masked, in the message and in a traceback, which can quote a cell. Installed at import time by the
API and the worker on every root handler and on uvicorn's own handlers (they don't propagate).
ponytail: costs one guard pass per emitted record (~10 ms at 200k guard values); the access
log's client address is kept, IPs anywhere else are masked."""
from __future__ import annotations

import logging
import re

SCRUB_FAILED = "[log scrub failed]"
_ADDR = re.compile(r"\[?[0-9a-fA-F.:]+\]?:\d{1,5}")   # uvicorn's "203.0.113.5:54866"


def scrub(text: str) -> str:
    from guardrails import mask_pii
    from llm.client import pseudonymize
    return mask_pii(pseudonymize(text)[0])


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.name == "uvicorn.access" and isinstance(record.args, tuple) and record.args:
                # uvicorn's access formatter unpacks the args (client, method, path, version,
                # status): keep them, each scrubbed; the status code stays a number.
                # The client address (first) is kept: operators need it against abuse.
                record.args = tuple(a if (isinstance(a, int) and not isinstance(a, bool)
                                          and 100 <= a <= 599) or (i == 0 and _ADDR.fullmatch(str(a)))
                                    else scrub(str(a)) for i, a in enumerate(record.args))
            else:   # anything else: formatted first, then scrubbed as text (a number can be a phone)
                record.msg, record.args = scrub(record.getMessage()), None
                record.__dict__.pop("color_message", None)   # uvicorn's coloured copy, unscrubbed
            if record.exc_text:
                record.exc_text = scrub(record.exc_text)
                record.exc_info = None   # a formatter re-reading exc_info would get the raw one
            if record.stack_info:
                record.stack_info = scrub(record.stack_info)
        except Exception:   # never drop a log line, never let it through unscrubbed
            record.msg, record.args, record.exc_text, record.stack_info = SCRUB_FAILED, None, None, None
            record.exc_info = None
        return True


def install() -> None:
    """Attach the filter to every handler that can write a record (idempotent). Warnings go
    through logging too (a warning can quote an exception's text)."""
    logging.captureWarnings(True)
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in
                                      ("uvicorn", "uvicorn.error", "uvicorn.access"))]
    for handler in (h for lg in loggers for h in lg.handlers):
        if not any(isinstance(f, ScrubFilter) for f in handler.filters):
            handler.addFilter(ScrubFilter())
