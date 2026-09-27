"""
M4.1 — shadow_mode: fail-closed prod gating (A3.10 dry-run-first).
WHY: prod entry is opt-in per strategy@version — an unlisted strategy in prod
  is ALWAYS shadowed, never dropped (returned with shadow=True so the run stays
  observable while M4.3 refuses to forward it to M5). Paper never shadows.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from shared.contracts.signal import Signal

_ENVS = frozenset({"prod", "paper"})  # live goes through M5/M6, never here


def apply_shadow(signal: Signal, *, env: str, allowlist: frozenset[str]) -> Signal:
    if env not in _ENVS:
        raise ValueError(f"env must be one of {sorted(_ENVS)}, got {env!r}")
    shadowed = env == "prod" and f"{signal.strategy_id}@{signal.version}" not in allowlist
    return signal.model_copy(update={"shadow": shadowed})


def load_shadow_allowlist(config_dir: Path) -> frozenset[str]:
    """Missing file -> empty set (fail closed); malformed -> loud, never silent."""
    path = Path(config_dir) / "shadow_allowlist.yaml"
    if not path.exists():
        return frozenset()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return frozenset()
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: root must be a mapping")  # noqa: TRY004 — config-shape errors are ValueError (3.13 family)
    prod = raw.get("prod", [])
    if prod is None:
        prod = []
    if not isinstance(prod, list) or not all(isinstance(k, str) for k in prod):
        raise ValueError(f"{path.name}: prod must be a list of strategy@version keys")
    return frozenset(prod)  # `paper:` key is read-ignored (paper never shadows)
