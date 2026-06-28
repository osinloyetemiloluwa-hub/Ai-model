"""corvin_plugins — unified plugin system for Corvin (ADR-0030)."""
from __future__ import annotations

from .protocol import (
    CorvinPlugin,
    HealthStatus,
    KNOWN_PLUGIN_TYPES,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
)
from .registry import (
    PluginRegistry,
    discover,
    get,
    get_registry,
    health_check_all,
    register,
    unregister,
)

__all__ = [
    "CorvinPlugin",
    "HealthStatus",
    "KNOWN_PLUGIN_TYPES",
    "PluginAlreadyRegistered",
    "PluginContext",
    "PluginNotFound",
    "PluginRegistry",
    "register",
    "unregister",
    "get",
    "health_check_all",
    "discover",
    "get_registry",
]
