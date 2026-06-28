"""PluginRegistry — discovery, lifecycle, health aggregation (ADR-0030)."""
from __future__ import annotations

import logging
import threading

from .protocol import (
    CorvinPlugin,
    HealthStatus,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
)

log = logging.getLogger(__name__)


class PluginRegistry:
    """Thread-safe registry for CorvinPlugin instances.

    Handles registration, lifecycle calls, health aggregation, and
    typed lookups.  One global instance is provided as ``_registry``
    at module level; convenience functions wrap it.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, CorvinPlugin] = {}
        self._contexts: dict[str, PluginContext] = {}
        self._lock = threading.Lock()

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, plugin: CorvinPlugin, ctx: PluginContext) -> None:
        """Call plugin.on_load(ctx) and store the plugin.

        Raises PluginAlreadyRegistered if plugin.plugin_id is already registered.
        """
        with self._lock:
            if plugin.plugin_id in self._plugins:
                raise PluginAlreadyRegistered(
                    f"plugin_id {plugin.plugin_id!r} is already registered"
                )
            # Optimistically reserve the slot before on_load so a re-entrant
            # call from on_load() (e.g. in tests) also gets the collision guard.
            self._plugins[plugin.plugin_id] = plugin
            self._contexts[plugin.plugin_id] = ctx

        try:
            plugin.on_load(ctx)
        except Exception:
            # on_load failed — roll back the slot reservation.
            with self._lock:
                self._plugins.pop(plugin.plugin_id, None)
                self._contexts.pop(plugin.plugin_id, None)
            raise

        log.info(
            "plugin loaded: id=%r type=%r version=%r tenant=%r",
            plugin.plugin_id, plugin.plugin_type, plugin.version, ctx.tenant_id,
        )
        ctx.audit_emit("plugin.loaded", {
            "plugin_id": plugin.plugin_id,
            "plugin_type": plugin.plugin_type,
            "version": plugin.version,
            "tenant_id": ctx.tenant_id,
        })

    def unregister(self, plugin_id: str) -> None:
        """Call plugin.on_unload() and remove it from the registry.

        Raises PluginNotFound if plugin_id is not registered.
        """
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            ctx = self._contexts.get(plugin_id)
            if plugin is None:
                raise PluginNotFound(plugin_id)
            del self._plugins[plugin_id]
            del self._contexts[plugin_id]

        try:
            plugin.on_unload()
        except Exception:
            log.exception("plugin %r raised during on_unload", plugin_id)

        log.info("plugin unloaded: id=%r", plugin_id)
        if ctx is not None:
            ctx.audit_emit("plugin.unloaded", {
                "plugin_id": plugin_id,
                "tenant_id": ctx.tenant_id,
            })

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, plugin_id: str) -> CorvinPlugin:
        """Return the plugin for plugin_id.

        Raises PluginNotFound if not registered.
        """
        with self._lock:
            plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise PluginNotFound(plugin_id)
        return plugin

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check_all(self) -> dict[str, HealthStatus]:
        """Call health_check() on every plugin.

        Catches per-plugin exceptions and returns HealthStatus(ok=False, ...)
        so one broken plugin never blocks the rest.
        """
        with self._lock:
            snapshot = list(self._plugins.items())

        results: dict[str, HealthStatus] = {}
        for pid, plugin in snapshot:
            try:
                results[pid] = plugin.health_check()
            except Exception as exc:  # noqa: BLE001
                log.warning("health_check failed for plugin %r: %s", pid, type(exc).__name__)
                ctx = self._contexts.get(pid)
                if ctx is not None:
                    ctx.audit_emit("plugin.health_check_failed", {
                        "plugin_id": pid,
                        "error_type": type(exc).__name__,  # class name only — no PII
                    })
                results[pid] = HealthStatus(ok=False, message=str(exc))
        return results

    # ── Filtered lookup ───────────────────────────────────────────────────────

    def plugins_by_type(self, plugin_type: str) -> list[CorvinPlugin]:
        """Return all registered plugins of the given type."""
        with self._lock:
            return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> list[str]:
        """Return a sorted list of all registered plugin_ids."""
        with self._lock:
            return sorted(self._plugins.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._plugins)


# ── Module-level convenience functions ────────────────────────────────────────

_registry: PluginRegistry = PluginRegistry()


def register(plugin: CorvinPlugin, ctx: PluginContext) -> None:
    _registry.register(plugin, ctx)


def unregister(plugin_id: str) -> None:
    _registry.unregister(plugin_id)


def get(plugin_id: str) -> CorvinPlugin:
    return _registry.get(plugin_id)


def health_check_all() -> dict[str, HealthStatus]:
    return _registry.health_check_all()


def discover() -> list[str]:
    return _registry.discover()


def get_registry() -> PluginRegistry:
    """Return the module-level PluginRegistry instance."""
    return _registry
