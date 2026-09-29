"""
M1.4 â€” Money Precision. The single authority for every monetary and numeric value.

WHY this file exists: AGENTS.md 3.1 cites `shared/core/money.py` as the one place
ROUND_HALF_UP lives. That file did not exist (defect D33), so every module improvised
its own arithmetic â€” and 20+ money fields across the frozen M1.1 contracts were typed
`float`. This module makes the correct path the only path.

Spec: docs/specs/m1.4_money_precision.spec.md

WHY Decimal and not float â€” measured on this stack, not asserted:
  float("2000000075959.342") stores 2000000075959.342041015625 (error +4.1e-5) yet
  PRINTS identically. 100,000 additions of 0.0000001 give 0.009999999999994874 and
  `== 0.01` is False. IEEE-754 addition is not associative, so a ledger summed in a
  different order does not foot. The damage is in accumulation, not magnitude.

WHY prec=34 and not the default 28: worst-case domain requirement is 16 significant
digits (BTC at max supply, 8dp = 21000000.00000000). 34 is roughly double the domain.
The default 28 silently truncates a 41-digit value, so we never rely on it.

WHY the context is set on every entry point: a stray `localcontext(prec=6)` from any
library silently turned 2000000075959.352 into 2.00000E+12. Every arithmetic operation
below therefore runs inside its own localcontext, so a hostile ambient context cannot
change a money result.

WHY floats are REJECTED rather than coerced: `Decimal(0.1)` is 0.10000000000000000555...
Converting a float would import the very error Decimal was chosen to remove. And
`Decimal * float` raises TypeError, so a Decimal contract field makes the type
discipline self-enforcing: a money/float mix crashes at the offending line instead of
silently producing a wrong number.

Sign convention (stated once, never inferred): long qty > 0, short qty < 0,
PnL > 0 = profit, fees > 0 = cost, all money in the quote currency.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext
from typing import Any, Final

# WHY the literal guard is imported from the CONTRACTS layer, not defined here:
# shared/contracts/base.py needs the identical primitive to validate every MoneyDecimal
# field, and `shared.contracts must never import shared.core` is a machine-checked
# architectural law. So the primitive lives in the root layer
# (shared/contracts/_money_literal.py) and BOTH sides import it â€” one implementation,
# correct direction (core may depend on contracts, never the reverse). Duplicating the
# parser here would let the contract guard and the money authority drift apart, which is
# the exact failure this module exists to prevent.
from ..contracts._money_literal import MAX_ADJUSTED as _MAX_ADJUSTED
from ..contracts._money_literal import MoneyLiteralError
from ..contracts._money_literal import parse_money_literal as _parse_money_literal

# WHY 34: see module docstring. Exposed because a test asserts it is NOT the default.
MONEY_PREC: Final[int] = 34

# WHY this ceiling: a literal beyond this is not a money value, it is a bug or an
# attack. Set well above the 16-digit domain so legitimate magnitudes pass and
# absurd ones (1e400) are rejected as a type problem rather than silently accepted.

# WHY 1e15 / 1e9 / 1e18: sized for the largest ACCUMULATOR a field will ever hold,
# not a single trade (industry guidance, and AGENTS.md 3.13 â€” refuse absurd values).
# max_balance 1e15 is ~500x our largest realistic account, so it is a guard rail and
# not a business limit; max_qty 1e9 lots is already far beyond any real position.
DOMAIN_BOUNDS: Final[dict[str, Decimal]] = {
    "max_balance": Decimal("1e15"),
    "max_notional": Decimal("1e18"),
    "max_qty": Decimal("1e9"),
    "max_price": Decimal("1e9"),
}

# WHY a table and not hardcoded 2: currency minor units are defined by ISO 4217, and
# they are NOT all 2. JPY has zero minor units, KWD has three. Assuming 2 would
# silently round JPY up and truncate KWD. NOTE: no ISO 4217 package (babel/pycountry)
# is installed, so the values are carried here and PINNED BY TEST against the
# well-known notable cases rather than trusted from memory. If a library is adopted
# later, this mapping is the single place to delete.
MONEY_SCALES: Final[dict[str, int]] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "AUD": 2,
    "CAD": 2,
    "NZD": 2,
    "SGD": 2,
    "HKD": 2,
    "CNY": 2,
    "INR": 2,
    "BRL": 2,
    "ZAR": 2,
    "MXN": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "PLN": 2,
    "TRY": 2,
    "AED": 2,
    "SAR": 2,
    "THB": 2,
    "ILS": 2,
    "JPY": 0,  # ISO 4217: zero minor units. Must not be rounded up to 2.
    "KRW": 0,
    "ISK": 0,
    "VND": 0,
    "CLF": 4,  # ISO 4217: four minor units. Must not be truncated to 2.
    "UYW": 4,
    "BHD": 3,  # ISO 4217: three minor units.
    "KWD": 3,
    "JOD": 3,
    "OMR": 3,
    "TND": 3,
}

# WHY there is deliberately NO price-scale table here: an instrument's price quantum
# is the BROKER's `pip_size`, read live from `active_symbols` and verified against our
# config by scripts/verify_m2b.5_broker_reference.py. Hardcoding one is the exact
# defect this project already found once â€” BTC was 0.01 in our config while the broker
# said 0.001, and XAG 0.01 vs 0.0001 (a 100x error that silently discarded two orders
# of magnitude of price). A hand-written table would reintroduce that class of bug, so
# price scale is DERIVED from the authoritative pip_size via scale_from_pip_size(), and
# settlement scale for crypto comes from the asset-class standard (M2b.5, config-driven).

# WHY a regex and not bare Decimal(str): Python's Decimal ACCEPTS underscore digit
# separators ("1_000.50" -> 1000.50) and exponent forms. A money literal must be
# explicit, so the accepted grammar is written down rather than inherited.
_LITERAL: Final[re.Pattern[str]] = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


# ------------------------------------------------------------------ errors
class MoneyError(ValueError):
    """Base for every money rejection. A ValueError so existing handlers still work."""


class MoneyTypeError(MoneyError):
    """The value is not a money literal at all: float, int, bytes, None, NaN, Inf."""


class ScaleError(MoneyError):
    """Unknown currency/instrument, or a value finer than the declared scale."""


class DomainBoundError(MoneyError):
    """A real number, but beyond the declared domain. Never silently rounded."""


# ------------------------------------------------------------------ internals
def _context() -> Context:
    """A fresh Context pinned to OUR precision and rounding.

    WHY a new Context rather than mutating the ambient one: `localcontext()` with no
    argument hands back a context MANAGER, and mutating a copy of the caller's context
    is fragile. Constructing a Context outright is explicit, immutable in practice, and
    is what makes a hostile ambient localcontext provably unable to change a result.
    """
    return Context(prec=MONEY_PREC, rounding=ROUND_HALF_UP)


def _decimal_places(amount: Decimal) -> int:
    """Decimal places carried by a value. Rejects non-finite, which have no scale."""
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int):
        raise MoneyTypeError(f"{amount} is non-finite and has no scale")
    return -exponent


def _as_decimal(value: Decimal | str) -> Decimal:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MoneyTypeError(f"{value} is non-finite â€” NaN and Infinity are not money")
        return value
    return assert_decimal(value)


# ------------------------------------------------------------------ public API
def assert_decimal(value: Any) -> Decimal:
    """Coerce a money literal to Decimal, or raise. The only sanctioned entry point.

    Accepts `Decimal` and `str` ONLY. Rejects float, int and bool: an int is a count
    and a float has already lost the value. Rejecting rather than coercing is the
    whole point — `Decimal(0.1)` would import 0.10000000000000000555...

    WHY this delegates instead of re-implementing: the identical primitive validates
    every MoneyDecimal field in shared/contracts. One implementation means the contract
    guard and the money authority can never drift apart. The leaf primitive raises
    MoneyLiteralError; it is re-raised here as MoneyTypeError so callers of the money
    authority keep catching money errors without importing the contracts layer.
    """
    try:
        return _parse_money_literal(value)
    except MoneyLiteralError as exc:
        raise MoneyTypeError(str(exc)) from exc


def quantize_money(
    value: Decimal | str,
    scale: int,
    *,
    reject_excess_scale: bool = False,
) -> Decimal:
    """Round ONCE to `scale` decimals, HALF_UP, on both signs. The only rounding path.

    WHY once, at the boundary: rounding per step is how an institution loses a cent
    per fill. WHY HALF_UP: float's round() is banker's rounding on top of binary
    error, so it gives 0.01 for 0.015 and 2.67 for 2.675 â€” both wrong for money.

    `reject_excess_scale` turns "value carries more precision than this column can
    hold" into an error. Silent truncation is how money goes missing, so a caller
    persisting to a fixed scale must opt in to that refusal.
    """
    amount = _as_decimal(value)
    if not isinstance(scale, int) or isinstance(scale, bool) or scale < 0:
        raise ScaleError(f"scale must be a non-negative int, got {scale!r}")

    if reject_excess_scale and _decimal_places(amount) > scale:
        raise ScaleError(
            f"{amount} carries {_decimal_places(amount)} decimals, "
            f"more than the declared scale of {scale} â€” refusing to truncate"
        )

    with localcontext(_context()):
        try:
            rounded = amount.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise DomainBoundError(
                f"{amount} cannot be represented at scale {scale} within prec={MONEY_PREC}"
            ) from exc

    # WHY: a ledger must never carry negative zero â€” it breaks equality and printing.
    return abs(rounded) if rounded.is_zero() else rounded


def assert_in_domain(value: Decimal | str, bound_name: str) -> Decimal:
    """Reject a value beyond a declared bound. Sizes are guard rails, not business limits.

    WHY reject rather than clamp: clamping a balance silently invents money, and
    clamping a qty silently hides a runaway strategy.
    """
    amount = _as_decimal(value)
    bound = DOMAIN_BOUNDS.get(bound_name)
    if bound is None:
        raise DomainBoundError(
            f"unknown bound {bound_name!r}; known: {sorted(DOMAIN_BOUNDS)}"
        )
    with localcontext(_context()):
        if abs(amount) > bound:
            raise DomainBoundError(f"{amount} exceeds {bound_name}={bound}")
    return amount


def scale_of(kind: str, key: str) -> int:
    """Decimal places for a currency's money (ISO 4217 minor units).

    WHY raise for an unknown key rather than defaulting to 2: a wrong default is
    silent, and a silent scale corrupts every figure derived from it. An instrument
    is NOT a currency, so `scale_of("money", "BTC")` raises â€” BTC has no ISO 4217
    minor units and its settlement scale comes from the asset-class standard.
    """
    registry = {"money": MONEY_SCALES}.get(kind)
    if registry is None:
        raise ScaleError(
            f"unknown scale kind {kind!r}; money minor units are the only standard here. "
            "For an instrument price, use scale_from_pip_size(broker pip_size)."
        )
    if key not in registry:
        raise ScaleError(f"{key!r} is not a known ISO 4217 currency â€” refusing to assume a scale")
    return registry[key]


def scale_from_pip_size(pip_size: Decimal | str) -> int:
    """Decimal places implied by the BROKER's authoritative `pip_size`.

    WHY this exists instead of a price table: the venue is the authority on its own
    tick quantum. Deriv reports pip_size per symbol (BTC 0.001, XAG 0.0001, EURUSD
    0.00001), and our job is to obey it, not to remember it. Deriving scale from that
    value is what makes a config drift loud â€” a mismatch fails
    scripts/verify_m2b.5_broker_reference.py at boot instead of silently truncating
    prices for months.
    """
    quantum = assert_decimal(pip_size)
    if quantum <= 0:
        raise ScaleError(f"pip_size must be positive, got {quantum}")
    return _decimal_places(quantum)


def sum_money(values: Iterable[Decimal | str], *, scale: int | None = None) -> Decimal:
    """Exact total at OUR precision, immune to the ambient decimal context.

    WHY sum here and not with Python's `sum`: a plain sum runs in whatever context
    the caller happens to be in, and its intermediate rounding is order-dependent.
    Summing inside a pinned context makes the total order-independent, which is what
    "the ledger foots" actually requires.

    `scale` is the OUTPUT scale, not a filter on the inputs. Inputs are deliberately
    NOT rejected for carrying more precision than `scale`: that is the normal state of
    a calculation being rounded once at the end (a 3-dp balance rounding to a 2-dp
    currency is precisely the case this exists for). Refusing excess scale is a
    PERSISTENCE decision and lives in `quantize_money(..., reject_excess_scale=True)`,
    which is where a value is actually being written into a fixed-scale column.
    """
    total = Decimal(0)
    with localcontext(_context()):
        for raw in values:
            total = total + _as_decimal(raw)
    total = assert_in_domain(total, "max_balance")
    return quantize_money(total, scale) if scale is not None else total
