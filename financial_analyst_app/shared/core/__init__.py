"""
M1 — shared.core — re-exports for `from shared.core import BaseEngine, clock, tenant_context`
"""
from .broker_vault import VaultError, create_vault, vault
from .clock import Clock, SimulatedClock, SystemClock
from .config import Settings, load_settings, settings
from .engine_interface import BaseEngine, EngineState, HealthStatus
from .environment import Environment, get_env, is_paper, is_production
from .exceptions import (
    BaseAppError,
    CapabilityNotSupportedError,
    ConfigError,
    DataGapError,
    IllegalTransitionError,
    IsolationError,
    OrderValidationError,
    PermissionDeniedError,
    RiskRejectedError,
    UnknownStateError,
)
from .feature_flags import all_flags, install_sighup_handler, is_enabled, reload as reload_flags, reload
from .health import EngineHealth, HealthSnapshot
from .lifecycle import Lifecycle
from .logging import JsonFormatter, get_logger, logger
from .permissions import Role, assert_can, can, can_user
from .retry_policy import RetryExhaustedError, retry_sync, retry_with_backoff
from .state_tracker import State
from .tenant_context import (
    TenantContext,
    get_broker_id,
    get_user_id,
    require_both,
    require_broker_id,
    require_user_id,
    tenant_context,
)

__all__ = [
    "BaseAppError",
    "BaseEngine",
    "CapabilityNotSupportedError",
    "Clock",
    "ConfigError",
    "DataGapError",
    "EngineHealth",
    "EngineState",
    "Environment",
    "HealthSnapshot",
    "HealthStatus",
    "IllegalTransitionError",
    "IsolationError",
    "JsonFormatter",
    "Lifecycle",
    "OrderValidationError",
    "PermissionDeniedError",
    "RiskRejectedError",
    "Role",
    "Settings",
    "SimulatedClock",
    "State",
    "SystemClock",
    "TenantContext",
    "UnknownStateError",
    "VaultError",
    "all_flags",
    "assert_can",
    "can",
    "can_user",
    "create_vault",
    "get_broker_id",
    "get_env",
    "get_logger",
    "get_user_id",
    "is_enabled",
    "is_paper",
    "is_production",
    "load_settings",
    "logger",
    "install_sighup_handler",
    "reload",
    "reload_flags",
    "require_both",
    "require_broker_id",
    "require_user_id",
    "retry_sync",
    "retry_with_backoff",
    "RetryExhaustedError",
    "settings",
    "tenant_context",
    "vault",
]
