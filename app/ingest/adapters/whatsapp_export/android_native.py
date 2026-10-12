from __future__ import annotations

import re

from app.ingest.adapters.base import ParseResult
from app.ingest.adapters.whatsapp_export.common import Rec, finalize, to24

HEADER = re.compile(
    r"^‎?(\d{1,2})/(\d{1,2})/(\d{2,4}), (\d{1,2}):(\d{2})[\s  ]?([ap]m) - (.*)$",
    re.IGNORECASE,
)


def parse(text: str, group_name: str | None = None) -> ParseResult:
    recs: list[Rec] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = HEADER.match(line)
        if not m:
            if recs:  # INC-05: continuation line
                recs[-1].lines.append(line)
            continue
        a, b, y, hh, mm, ap, rest = m.groups()
        sender, sep, body = rest.partition(": ")
        is_system = not sep
        rec = Rec(
            int(a),
            int(b),
            int(y),
            to24(int(hh), ap),
            int(mm),
            None,
            None if is_system else sender,
            line_no=n,
        )
        rec.lines.append(rest if is_system else body)
        recs.append(rec)
    return finalize(recs, "DMY", "android_native", group_name)
