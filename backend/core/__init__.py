"""Core layer: config, paths, errors, logging, fx, units, and io primitives.

This package depends on nothing internal (see docs/ENGINEERING_GUIDE.md §2.1) —
it is the bottom of the dependency rule (api/ai → services → db → core). The rest
of the tree should reach for these via ``from backend.core import …`` so there is
one obvious import root.
"""

from backend.core.config import Settings, get_settings
from backend.core.errors import (
    ConfigurationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    OpenNetWorthError,
    ValidationError,
    to_http_status,
)
from backend.core.fx import (
    base_currency,
    set_base_currency_loader,
    set_fx_rates_loader,
    to_base,
    txn_base_amount,
)
from backend.core.io import (
    atomic_write_text,
    fire_db_write_hook,
    fire_write_hook,
    register_db_write_hook,
    register_write_hook,
)
from backend.core.logging import attach_uvicorn_handlers, configure_logging, get_logger

__all__ = [
    # config
    "Settings",
    "get_settings",
    # errors
    "OpenNetWorthError",
    "NotFoundError",
    "ValidationError",
    "ConflictError",
    "ExternalServiceError",
    "ConfigurationError",
    "to_http_status",
    # logging
    "get_logger",
    "configure_logging",
    "attach_uvicorn_handlers",
    # fx
    "base_currency",
    "to_base",
    "txn_base_amount",
    "set_fx_rates_loader",
    "set_base_currency_loader",
    # io
    "atomic_write_text",
    "register_write_hook",
    "fire_write_hook",
    "register_db_write_hook",
    "fire_db_write_hook",
]
