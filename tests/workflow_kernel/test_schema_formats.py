from datetime import datetime, timezone

import pytest

from workflow_kernel.schema_formats import (
    is_rfc3339_datetime,
    is_workflow_event_timestamp,
    parse_rfc3339_datetime,
)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-23T05:00:00Z",
        "2026-07-23t05:00:00z",
        "2026-07-23T05:00:00.123+05:30",
        "2016-12-31T23:59:60Z",
        "2017-01-01T00:59:60+01:00",
    ],
)
def test_rfc3339_datetime_accepts_supported_valid_forms(value: str) -> None:
    assert is_rfc3339_datetime(value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-23 05:00:00+00:00",
        "2026-02-30T05:00:00Z",
        "2026-07-23T24:00:00Z",
        "2026-07-23T05:00:00",
        "2026-07-23T05:00:60Z",
        "2026-02-28T12:34:60Z",
        "9999-12-31T23:59:60Z",
        "not-a-date",
        None,
    ],
)
def test_rfc3339_datetime_rejects_invalid_forms(value: object) -> None:
    assert not is_rfc3339_datetime(value)


def test_workflow_event_timestamp_profile_matches_packaged_schema_pattern() -> None:
    assert is_workflow_event_timestamp("2026-07-23T05:00:00Z")
    assert not is_workflow_event_timestamp("2026-07-23t05:00:00z")
    assert not is_workflow_event_timestamp("2016-12-31T23:59:60Z")


def test_rfc3339_datetime_normalizes_leap_second() -> None:
    assert parse_rfc3339_datetime("2016-12-31T23:59:60Z") == datetime(
        2017, 1, 1, tzinfo=timezone.utc
    )
