"""Skeleton of golden acceptance tests (build brief 8.6). xfail until M2 supplies the pipeline
and the real fixtures. Numbers are exact."""

import pytest

pytestmark = [
    pytest.mark.golden,
    pytest.mark.xfail(reason="needs M2 + real fixtures", strict=False),
]

GOLDEN = {
    "v1_to_v2": {
        "added": 99,
        "removed": 15,
        "common": 415,
        "changed_rows": 18,
        "changed_timeline": 15,
        "changed_dispatch": 3,
        "format_only": 1,
        "conflict": 1,
    },
    "master_ready_vs_v1": {"newly_ready": 160, "dropped": 8},
}


def test_v1_to_v2_diff_numbers() -> None:
    from app.workbooks.diff import diff_summary_for_fixtures  # type: ignore[import-not-found]

    assert diff_summary_for_fixtures("v1", "v2") == GOLDEN["v1_to_v2"]


def test_master_ready_vs_v1() -> None:
    from app.workbooks.diff import ready_delta_for_fixtures  # type: ignore[import-not-found]

    assert ready_delta_for_fixtures() == GOLDEN["master_ready_vs_v1"]
