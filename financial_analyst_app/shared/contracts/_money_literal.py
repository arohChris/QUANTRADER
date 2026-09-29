"""
Money literal parsing — a LEAF module. WHY it exists in its own file:

`shared/contracts/base.py` needs to reject a float before it becomes a money value, and
`shared/core/money.py` needs exactly the same primitive. The obvious shortcut is for
contracts to import money.py — and that BREAKS a machine-checked architectural law:

    shared.contracts must never import shared.core, modules, or app

That law is real and worth keeping: contracts are the ROOT vocabulary and must not depend
on infrastructure, or every consumer of a contract inherits that dependency. The layering
is `contracts (root) <- core`, never the reverse.

So the primitive lives HERE, in the root layer, with ZERO dependencies. Both sides import it:
  - contracts/base.py  -> _money_literal   (same layer)
  - core/money.py      -> contracts._money_literal   (core may depend on contracts)

One implementation, correct layering, no drift. The `core` -> `contracts` direction is
permitted; the `contracts` -> `core` direction is not.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final

# WHY a regex and not bare Decimal(str): Python's Decimal ACCEPTS underscore digit
# separators ("1_000.50" -> 1000.50) and exponent forms. A money literal must be explicit,
# so the accepted grammar is written down rather than inherited from the stdlib.
LITERAL: Final[re.Pattern[str]] = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

# WHY this ceiling: a literal beyond it is not a money value, it is a bug or an attack.
# Set well above the 16-digit domain so legitimate magnitudes pass and absurd ones
# (1e400) are rejected as a type problem rather than silently accepted.
MAX_ADJUSTED: Final[int] = 100


class MoneyLiteralError(ValueError):
    """The value is not a money literal: float, int, bytes, None, NaN, Inf, or absurd."""


def parse_money_literal(value: Any) -> Decimal:
    """Coerce a money literal to Decimal, or raise. Accepts `Decimal` and `str` ONLY.

    WHY float is rejected rather than coerced: `Decimal(0.1)` is
    0.10000000000000000555... — converting a float would import the very error Decimal was
    chosen to remove. WHY int is rejected: an int is a COUNT, not an amount, and letting
    them share a field is how a lot size becomes a price. WHY bool is rejected: it is an
    int subclass, so `True` would silently become the amount 1.
    """
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise MoneyLiteralError(
            f"money must be Decimal or str, got {type(value).__name__} ({value!r}). "
            "A float has already lost precision and an int is a count, not an amount."
        )
    if isinstance(value, bytes):
        raise MoneyLiteralError("money literal must be str, not bytes")
    if value is None:
        raise MoneyLiteralError("money value is None — absent is not zero")
    if isinstance(value, Decimal):
        return _validate(value)
    if not isinstance(value, str):
        raise MoneyLiteralError(f"money must be Decimal or str, got {type(value).__name__}")

    text = value.strip()
    if not LITERAL.match(text):
        raise MoneyLiteralError(f"{value!r} is not a valid money literal")
    try:
        return _validate(Decimal(text))
    except InvalidOperation as exc:  # pragma: no cover - the regex gates this already
        raise MoneyLiteralError(f"{value!r} is not a valid money literal") from exc


def _validate(result: Decimal) -> Decimal:
    if not result.is_finite():
        raise MoneyLiteralError(f"{result} is not finite — NaN and Infinity are not money")
    if abs(result.adjusted()) > MAX_ADJUSTED:
        raise MoneyLiteralError(
            f"{result} has magnitude 1e{result.adjusted()}, beyond any money domain"
        )
    return result
