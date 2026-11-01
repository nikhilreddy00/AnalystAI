"""Every log line is scrubbed before a handler writes it (Phase 5.5): the current run's PII
values become their tokens (llm.client.pseudonymize) and email/phone/SSN/card/IP patterns are
masked, in the message and in a traceback, which can quote a cell. Installed at import time by the
API and the worker on every root handler and on uvicorn's own handlers (they don't propagate).
ponytail: costs one guard pass per emitted record; IPs in access logs are masked too."""
from __future__ import annotations

import logging

SCRUB_FAILED = "[log scrub failed]"


def scrub(text: str) -> str:
    from guardrails import mask_pii
    from llm.client import pseudonymize
    return mask_pii(pseudonymize(text)[0])


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.msg, record.args = scrub(record.getMessage()), None
            if record.exc_text:
                record.exc_text = scrub(record.exc_text)
            if record.stack_info:
                record.stack_info = scrub(record.stack_info)
        except Exception:   # never drop a log line, never let it through unscrubbed
            record.msg, record.args, record.exc_text, record.stack_info = SCRUB_FAILED, None, None, None
            record.exc_info = None
        return True


def install() -> None:
    """Attach the filter to every handler that can write a record (idempotent)."""
    loggers = [logging.getLogger(), *(logging.getLogger(n) for n in
                                      ("uvicorn", "uvicorn.error", "uvicorn.access"))]
    for handler in (h for lg in loggers for h in lg.handlers):
        if not any(isinstance(f, ScrubFilter) for f in handler.filters):
            handler.addFilter(ScrubFilter())
