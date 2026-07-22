from __future__ import annotations

import pytest

from gateway.platforms.qqbot.adapter import _parse_token_expires_in


def test_parse_token_expires_in_defaults_when_missing() -> None:
    assert _parse_token_expires_in({}) == 7200


@pytest.mark.parametrize("value", [1, 7200])
def test_parse_token_expires_in_accepts_positive_integers(value: int) -> None:
    assert _parse_token_expires_in({"expires_in": value}) == value


@pytest.mark.parametrize("value", [1.5, 0, -1, True, False, "3600", None])
def test_parse_token_expires_in_rejects_non_positive_or_non_integer_values(value: object) -> None:
    with pytest.raises(RuntimeError, match="expires_in must be a positive integer"):
        _parse_token_expires_in({"expires_in": value})
