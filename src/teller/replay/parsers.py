"""Output parsers: turn extracted text into the declared output shape, or fail loudly."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


class ParseError(ValueError):
    pass


def currency_usd(text: str) -> str:
    """'$2,431.17' -> '2431.17'; '($12.00)' and '-$12.00' -> '-12.00'."""
    t = text.strip()
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    digits = re.sub(r"[^\d.]", "", t)
    if not digits or digits.count(".") > 1:
        raise ParseError(f"not a USD amount: {text!r}")
    try:
        value = Decimal(digits)
    except InvalidOperation as e:
        raise ParseError(f"not a USD amount: {text!r}") from e
    value = value.quantize(Decimal("0.01"))
    return str(-value if neg else value)


def as_int(text: str) -> str:
    t = re.sub(r"[,\s]", "", text.strip())
    if not re.fullmatch(r"-?\d+", t):
        raise ParseError(f"not an integer: {text!r}")
    return str(int(t))


def as_decimal(text: str) -> str:
    t = re.sub(r"[,\s$]", "", text.strip())
    try:
        return str(Decimal(t))
    except InvalidOperation as e:
        raise ParseError(f"not a decimal: {text!r}") from e


def as_string(text: str) -> str:
    return text.strip()


def with_regex(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    if not m:
        raise ParseError(f"{pattern!r} did not match {text!r}")
    return m.group(1) if m.groups() else m.group(0)


def parse(text: str, parser: str, *, regex: str | None = None, pattern: str | None = None) -> str:
    if parser == "currency_usd":
        out = currency_usd(text)
    elif parser == "int":
        out = as_int(text)
    elif parser == "decimal":
        out = as_decimal(text)
    elif parser == "regex":
        if not regex:
            raise ParseError("regex parser without a regex")
        out = with_regex(text, regex)
    else:
        out = as_string(text)
    if pattern and not re.fullmatch(pattern, out):
        raise ParseError(f"parsed value {out!r} does not match pattern {pattern!r}")
    return out
