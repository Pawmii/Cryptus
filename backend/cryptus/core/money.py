"""Exact amount handling. All on-chain values are integers in atomic units.

Floats are never used for money anywhere in CRYPTUS.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation, localcontext


class AmountError(ValueError):
    pass


def to_atomic(value: str | Decimal | int, decimals: int, *, round_up: bool = False) -> int:
    """Convert a human decimal amount to integer atomic units.

    Raises if the value has more precision than the asset supports, unless
    ``round_up`` is set (used for quotes, where the customer must never be
    asked for less than the price).
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AmountError(f"invalid amount: {value!r}") from exc
    if not d.is_finite() or d < 0:
        raise AmountError("amount must be a finite, non-negative number")
    with localcontext() as ctx:
        ctx.prec = 100
        scaled = d.scaleb(decimals)
        if scaled != scaled.to_integral_value():
            if not round_up:
                raise AmountError(f"amount has more than {decimals} decimal places")
            scaled = scaled.to_integral_value(rounding=ROUND_CEILING)
        return int(scaled)


def from_atomic(value: int | str, decimals: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 100
        return Decimal(int(value)).scaleb(-decimals)


def fmt_atomic(value: int | str, decimals: int) -> str:
    """Canonical string form without exponent and without trailing zeros."""
    d = from_atomic(value, decimals)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def quantize_down(d: Decimal, places: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
