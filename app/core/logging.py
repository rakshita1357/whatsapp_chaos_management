"""PII-safe logging: phone numbers and message text are masked before emission."""

from __future__ import annotations

import logging
import re

_PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s-]{8,}\d)(?!\d)")


def mask(text: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        return f"***{digits[-2:]}"

    return _PHONE.sub(_sub, text)


class PiiFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(str(record.getMessage()))
        record.args = None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(PiiFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
