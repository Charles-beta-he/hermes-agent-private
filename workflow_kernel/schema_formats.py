"""Deterministic JSON Schema format validation used by the workflow kernel."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from jsonschema import FormatChecker

SUPPORTED_JSON_SCHEMA_FORMATS = frozenset({"date-time"})
_RFC3339_DATE_TIME = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"[Tt](?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d|60)"
    r"(?:\.\d+)?(?P<zone>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
_WORKFLOW_EVENT_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
_KNOWN_UTC_LEAP_SECOND_DATES = frozenset({
    "1972-06-30",
    "1972-12-31",
    "1973-12-31",
    "1974-12-31",
    "1975-12-31",
    "1976-12-31",
    "1977-12-31",
    "1978-12-31",
    "1979-12-31",
    "1981-06-30",
    "1982-06-30",
    "1983-06-30",
    "1985-06-30",
    "1987-12-31",
    "1989-12-31",
    "1990-12-31",
    "1992-06-30",
    "1993-06-30",
    "1994-06-30",
    "1995-12-31",
    "1997-06-30",
    "1998-12-31",
    "2005-12-31",
    "2008-12-31",
    "2012-06-30",
    "2015-06-30",
    "2016-12-31",
})


def parse_rfc3339_datetime(value: str) -> datetime:
    """Parse an RFC 3339 date-time, including leap-second syntax."""
    match = _RFC3339_DATE_TIME.fullmatch(value)
    if match is None:
        raise ValueError("not an RFC 3339 date-time")
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    leap_second = match.group("second") == "60"
    if leap_second:
        start, end = match.span("second")
        normalized = normalized[:start] + "59" + normalized[end:]
    parsed = datetime.fromisoformat(normalized)
    if not leap_second:
        return parsed
    utc_predecessor = parsed.astimezone(timezone.utc)
    if utc_predecessor.strftime("%Y-%m-%d") not in _KNOWN_UTC_LEAP_SECOND_DATES or (
        utc_predecessor.hour,
        utc_predecessor.minute,
        utc_predecessor.second,
    ) != (23, 59, 59):
        raise ValueError("date-time does not identify a known UTC leap second")
    return parsed + timedelta(seconds=1)


def is_rfc3339_datetime(value: object) -> bool:
    """Return whether value is an RFC 3339 date-time string."""
    if not isinstance(value, str):
        return False
    try:
        parse_rfc3339_datetime(value)
    except (OverflowError, ValueError):
        return False
    return True


def is_workflow_event_timestamp(value: object) -> bool:
    """Apply the event schemas' uppercase, non-leap RFC 3339 profile."""
    return (
        isinstance(value, str)
        and _WORKFLOW_EVENT_TIMESTAMP.fullmatch(value) is not None
        and is_rfc3339_datetime(value)
    )


@lru_cache(maxsize=1)
def workflow_format_checker() -> FormatChecker:
    """Build the format checker without relying on optional jsonschema extras."""
    checker = FormatChecker()
    checker.checks("date-time")(is_rfc3339_datetime)
    return checker
