"""Unit tests for the output parsers (``teller.replay.parsers``)."""

from __future__ import annotations

import pytest

from teller.replay.parsers import (
    ParseError,
    as_decimal,
    as_int,
    as_string,
    currency_usd,
    parse,
    with_regex,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$2,431.17", "2431.17"),
        ("-$12.00", "-12.00"),
        ("($12.00)", "-12.00"),
        ("  $1,000  ", "1000.00"),
        ("0.5", "0.50"),
        ("$0.00", "0.00"),
        ("1234567.891", "1234567.89"),
    ],
)
def test_currency_usd_normalises(text: str, expected: str) -> None:
    assert currency_usd(text) == expected


@pytest.mark.parametrize("text", ["N/A", "", "$", "1.2.3", "--"])
def test_currency_usd_rejects_non_amounts(text: str) -> None:
    with pytest.raises(ParseError, match="not a USD amount"):
        currency_usd(text)


def test_parse_error_is_a_value_error() -> None:
    assert issubclass(ParseError, ValueError)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1,234", "1234"), ("  42 ", "42"), ("-7", "-7"), ("1 234 567", "1234567"), ("007", "7")],
)
def test_as_int_normalises(text: str, expected: str) -> None:
    assert as_int(text) == expected


@pytest.mark.parametrize("text", ["12a", "1.5", "", "$12", "one"])
def test_as_int_rejects_non_integers(text: str) -> None:
    with pytest.raises(ParseError, match="not an integer"):
        as_int(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("$1,234.5", "1234.5"), ("12", "12"), ("-0.25", "-0.25"), (" 3.10 ", "3.10")],
)
def test_as_decimal_normalises(text: str, expected: str) -> None:
    assert as_decimal(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "(1.00)", "1.2.3"])
def test_as_decimal_rejects_garbage(text: str) -> None:
    with pytest.raises(ParseError, match="not a decimal"):
        as_decimal(text)


def test_as_string_strips() -> None:
    assert as_string("  Dana Whitfield \n") == "Dana Whitfield"


def test_with_regex_returns_first_group_when_present() -> None:
    assert with_regex("Acct 1234567890 (Share Savings)", r"Acct (\d+)") == "1234567890"


def test_with_regex_returns_whole_match_without_groups() -> None:
    assert with_regex("Acct 1234567890 (Share Savings)", r"\d{10}") == "1234567890"


def test_with_regex_no_match_raises() -> None:
    with pytest.raises(ParseError, match="did not match"):
        with_regex("no digits here", r"\d+")


def test_parse_dispatches_to_each_parser() -> None:
    assert parse("$2,431.17", "currency_usd") == "2431.17"
    assert parse("1,234", "int") == "1234"
    assert parse("$1,234.5", "decimal") == "1234.5"
    assert parse("Acct 1234567890", "regex", regex=r"\d+") == "1234567890"


def test_parse_defaults_to_stripped_string() -> None:
    assert parse("  Share Savings  ", "string") == "Share Savings"
    assert parse("  Share Savings  ", "something_unknown") == "Share Savings"


def test_parse_regex_without_regex_raises() -> None:
    with pytest.raises(ParseError, match="without a regex"):
        parse("abc", "regex")


def test_parse_pattern_mismatch_raises() -> None:
    with pytest.raises(ParseError, match="does not match pattern"):
        parse("1234", "int", pattern=r"\d{5}")


def test_parse_pattern_is_checked_against_the_parsed_value() -> None:
    # the raw text has a '$' and a comma; the pattern applies to the normalised value
    assert parse("$2,431.17", "currency_usd", pattern=r"\d+\.\d{2}") == "2431.17"
    assert parse("1234567890", "string", pattern=r"^[0-9]{10}$") == "1234567890"
