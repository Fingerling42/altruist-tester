import pytest

from altruist_tester.duration import DurationParseError, parse_duration_seconds


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30s", 30),
        ("10m", 600),
        ("2h", 7200),
        ("24h", 86400),
        ("15", 15),
        (" 5M ", 300),
    ],
)
def test_parse_duration_seconds(value, expected):
    assert parse_duration_seconds(value) == expected


@pytest.mark.parametrize("value", ["", "0s", "-1s", "ten minutes", "1d", "1.5h"])
def test_parse_duration_seconds_rejects_invalid_values(value):
    with pytest.raises(DurationParseError):
        parse_duration_seconds(value)
