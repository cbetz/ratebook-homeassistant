"""Decimal helpers and JSON scalar codecs.

Money and energy quantities are ``Decimal`` end to end (never float): the 2% bill-match
promise plus a TypeScript port sharing JSON test vectors makes exact, language-agnostic
arithmetic worth more than matching PySAM's C doubles byte-for-byte. On the JSON wire a
``Decimal`` is a string, so the Python and TS engines round-trip identical vectors.

Intermediate arithmetic is never rounded (PySAM does not round intermediates either);
rounding, if any, is a presentation concern left to callers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

ZERO = Decimal(0)


def to_decimal(value: Decimal | int | str | float) -> Decimal:
    """Coerce to ``Decimal`` without introducing binary-float artifacts.

    Floats are routed through ``str`` so ``0.1`` becomes ``Decimal("0.1")`` rather than the
    full binary expansion. Prefer passing strings or Decimals; floats are tolerated for
    ergonomics (synthetic load profiles, pasted bill numbers).
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def opt_decimal(value: Decimal | int | str | float | None) -> Decimal | None:
    return None if value is None else to_decimal(value)


def decimal_to_json(value: Decimal | None) -> str | None:
    """Canonical string form of a Decimal for the JSON wire.

    ``str(Decimal)`` preserves scale, so ``Decimal("0.10")`` and ``Decimal("0.1")`` — equal in
    value — would serialize to different bytes, breaking the "byte-identical across languages"
    contract for the shared test vectors and any content-hash change-detection. We canonicalize
    to fixed-point (never exponent form — ``Decimal.normalize()`` would emit ``"7E+2"``) with
    trailing fractional zeros stripped, so value-equal Decimals always serialize identically.
    The TypeScript port must apply the same rule.
    """
    if value is None:
        return None
    text = format(value, "f")  # fixed-point, no exponent
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text


def date_to_json(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def date_from_json(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)
