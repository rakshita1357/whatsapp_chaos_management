"""Synthetic samples built from the formats in the build brief (real fixtures not yet supplied)."""

from datetime import UTC, datetime

from app.ingest.adapters.whatsapp_export import alt_md, alt_txt, android_native
from app.ingest.adapters.whatsapp_export.detect import (
    detect_format,
    group_name_from_filename,
)
from app.ingest.keys import assign_logical_keys, mark_album_members
from app.ingest.text import normalize_text, sender_key_for

NATIVE = (
    "09/10/2026, 1:27\u202fpm - Messages and calls are end-to-end encrypted.\n"
    '09/10/2026, 1:27\u202fpm - Asha created group "G"\n'
    "09/10/2026, 1:28\u202fpm - +91 88489 91765: \u2068Tv installed\u2069\n"
    "in lounge\n"
    "09/10/2026, 1:29\u202fpm - Ravi: IMG-20261009-WA0013.jpg (file attached)\n"
    "NO TV INSTALLED\n"
    "09/10/2026, 1:30\u202fpm - Ravi: IMG-20261009-WA0014.jpg (file attached)\n"
    "Service Report 1.jpg\n"
    "09/10/2026, 1:31\u202fpm - Ravi: <Media omitted>\n"
    "09/10/2026, 1:32\u202fpm - Ravi: This message was deleted\n"
    "09/10/2026, 1:33\u202fpm - Ravi: fixed it <This message was edited>\n"
)


def test_native_basic() -> None:
    r = android_native.parse(NATIVE, "G")
    assert r.date_order == "DMY"
    e = r.envelopes
    assert e[0].message_type == "system" and e[1].message_type == "system"
    assert e[2].sender_key == "918848991765"
    assert e[2].normalized_text == "Tv installed\nin lounge"  # INC-04 marks stripped, INC-05 joined
    assert "\u2068" in (e[2].raw_text or "")  # raw preserved
    assert e[3].attachments[0].export_filename == "IMG-20261009-WA0013.jpg"
    assert e[3].normalized_text == "NO TV INSTALLED"
    assert e[4].attachments[0].original_filename == "Service Report 1.jpg"  # INC-13
    assert e[4].normalized_text == ""
    assert e[5].attachments[0].availability == "omitted_by_export"
    assert e[6].deleted and e[6].message_type == "deleted"
    assert e[7].edited and e[7].normalized_text == "fixed it"
    assert e[2].sent_at == datetime(2026, 10, 9, 7, 58, tzinfo=UTC)  # 13:28 IST


def test_date_order_from_day_gt_12_and_default() -> None:
    mdy = "10/13/2026, 1:00 pm - A: x\n"
    assert android_native.parse(mdy).date_order == "MDY"
    dmy = "13/10/2026, 1:00 pm - A: x\n"
    assert android_native.parse(dmy).date_order == "DMY"


def test_date_order_ambiguous_goes_to_review() -> None:
    # no day > 12 and default order breaks monotonicity
    t = "05/04/2026, 1:00 pm - A: x\n04/04/2026, 2:00 pm - A: y\n"
    r = android_native.parse(t)
    assert r.needs_review and r.envelopes == [] and r.date_order is None
    # no evidence at all (single symmetric date)
    assert android_native.parse("05/05/2026, 1:00 pm - A: x\n").needs_review


def test_system_lines_excluded_from_monotonic_check() -> None:
    t = (
        "09/10/2026, 1:00 pm - A: x\n"
        "08/10/2026, 1:00 pm - You were added\n"  # earlier system line must not fail the check
        "10/10/2026, 1:00 pm - A: y\n"
    )
    r = android_native.parse(t)
    assert r.date_order == "DMY" and not r.needs_review


ALT_TXT = (
    '[10/9/26, 1:28:05 PM] - Asha created group "G"\n'
    "[10/9/26, 1:28:05 PM] +91 88489 91765: [Forwarded] Tv installed\n"
)


def test_alt_txt() -> None:
    r = alt_txt.parse(ALT_TXT, "G")
    assert r.date_order == "MDY" or r.needs_review is False
    assert r.envelopes[0].message_type == "system"
    assert r.envelopes[1].sent_at_precision == "second"
    assert r.envelopes[1].sender_key == "918848991765"


ALT_MD = """# WhatsApp Chat Export: G

## October 9, 2026

[1:27 PM] **Ravi:** Service Report
> _Asha: please share the report
> second quote line_

[1:28 PM] **Ravi:** [Forwarded] Tv installed
[1:29 PM] **Ravi:** [Image] IMG.jpg
[1:29 PM] **Ravi:** [album message]
[1:30 PM] **Ravi:** _This message was deleted_
[1:31 PM] __system__ Asha added you
"""


def test_alt_md() -> None:
    r = alt_md.parse(ALT_MD)
    assert r.group_name == "G"
    e = r.envelopes
    assert e[0].quoted_context == "Asha: please share the report second quote line"
    assert e[1].forwarded is True and e[1].normalized_text == "Tv installed"
    assert (
        e[2].attachments[0].kind == "image" and e[2].attachments[0].original_filename == "IMG.jpg"
    )
    assert e[3].message_type == "album_header"
    assert e[4].deleted
    assert e[5].message_type == "system"


def test_detect_and_group_name() -> None:
    assert detect_format(NATIVE) == "android_native"
    assert detect_format(ALT_TXT) == "alt_txt"
    assert detect_format(ALT_MD) == "alt_md"
    assert detect_format("hello") is None
    assert group_name_from_filename("x/WhatsApp Chat with My Group.txt") == "My Group"


def test_sender_keys_and_normalization() -> None:
    assert sender_key_for("+91 88489 91765") == "918848991765"
    assert sender_key_for("Ravi Kumar") == "ravi-kumar"
    assert sender_key_for(None) is None
    assert normalize_text("\ufeffa\u200eb") == "ab"


def test_logical_keys_occurrence_and_cross_format_identity() -> None:
    native = android_native.parse(
        "09/10/2026, 1:28 pm - Ravi: ok\n09/10/2026, 1:28 pm - Ravi: ok\n"
    )
    assign_logical_keys("g", native.envelopes)
    k = [e.logical_key for e in native.envelopes]
    assert k[0] != k[1] and k[0].endswith("|0") and k[1].endswith("|1")
    # same message from alt_txt (second precision, [Forwarded] tag) yields the same key
    alt = alt_txt.parse(
        "[10/9/26, 1:28:40 PM] Ravi: [Forwarded] ok\n[10/9/26, 1:28:41 PM] Ravi: ok\n"
    )
    assign_logical_keys("g", alt.envelopes)
    assert [e.logical_key for e in alt.envelopes] == k


def test_album_grouping() -> None:
    t = (
        "09/10/2026, 1:28 pm - Ravi: \n"
        "09/10/2026, 1:28 pm - Ravi: A.jpg (file attached)\n"
        "09/10/2026, 1:28 pm - Ravi: B.jpg (file attached)\n"
        "09/10/2026, 1:29 pm - Ravi: C.jpg (file attached)\n"
    )
    r = android_native.parse(t)
    assert r.envelopes[0].message_type == "album_header"
    assert mark_album_members(r.envelopes) == [[0, 1, 2]]
