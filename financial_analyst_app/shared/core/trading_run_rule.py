"""
M1.5 — trading_run_rule — THE single auditable run gate (pure bool logic)

WHY: one function encodes the human law (2026-09-30) so every consumer agrees:
    run <=> global admin approval AND user clicked auto-trade AND user has not
           turned this strategy off AND at least one asset is selected.
Fail-closed by construction: any missing input -> False. "Instrument selected"
means "at least one configured instrument" — validating that the instrument is
tradable belongs to the runner / M5 risk gate, NOT here (review note §8: keep
this gate simple; configuration validation lives elsewhere).
"""

from __future__ import annotations

from collections.abc import Sequence


def run_decision(
    *,
    admin_approved: bool,
    auto_trade: bool,
    user_disabled: bool,
    instruments: Sequence[str],
) -> bool:
    return (
        bool(admin_approved)
        and bool(auto_trade)
        and not bool(user_disabled)
        and len(instruments) > 0
    )
