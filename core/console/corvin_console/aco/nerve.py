"""ACO Nervous System — selbsterweiterbare Signal-Infrastruktur (ADR-0177).

Das Nervensystem von CorvinOS: ein einheitliches Protokoll, das alle Layer
und Konnektoren automatisch erfasst und es dem Nutzer ermöglicht, neue
Schichten anzuschließen ohne Kerncode zu ändern.

Drei-Tier-Discovery:
  Tier 0  Built-in Fibers       — immer verfügbar, frische Installation
  Tier 1  Entry-Point-Fibers    — installierte Pakete (corvinOS.nerve_fibers)
  Tier 2  Lokale Plugin-Fibers  — ~/.corvin/nerve_fibers/*.py (kein Packaging nötig)

Erweiterung durch den Nutzer:
  Option A — Paket installieren:
      In setup.py/pyproject.toml:
          [project.entry-points."corvinOS.nerve_fibers"]
          my_layer = "my_package.nerve:MyLayerFiber"

  Option B — Lokale Datei ablegen (kein Packaging):
      ~/.corvin/nerve_fibers/my_connector.py
          from corvin_console.aco.nerve import NerveFiber, NerveSignal, register_fiber

          @register_fiber
          class MyConnectorFiber:
              fiber_id = "my.connector"
              fiber_version = "1.0.0"

              def scan(self) -> list[NerveSignal]: ...

  Option C — Runtime-Registrierung:
          from corvin_console.aco.nerve import register_fiber
          register_fiber(MyFiber())

Jede Fiber emittiert `NerveSignal`-Objekte — das einheitliche Schema für
alle Gesundheitssignale im System, unabhängig von Layer oder Konnektor.

Contract:
  * scan() NEVER blockiert > 10 s.
  * scan() NEVER raises — degradiert still.
  * Alle Signale fließen in die Audit-Chain und den Boot-Healer.
  * Fiber-Registrierung ist idempotent (same fiber_id = überschreiben).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Einheitliches Signal-Schema ───────────────────────────────────────────────

SEVERITY_OK       = "OK"
SEVERITY_LOW      = "LOW"
SEVERITY_MEDIUM   = "MEDIUM"
SEVERITY_HIGH     = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

_SEVERITY_ORDER = {
    SEVERITY_OK:       5,
    SEVERITY_LOW:      4,
    SEVERITY_MEDIUM:   3,
    SEVERITY_HIGH:     2,
    SEVERITY_CRITICAL: 1,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class NerveSignal:
    """Einheitliches Gesundheits-Signal von einer Nerve Fiber.

    Das ist das zentrale Datenformat des Nervensystems.
    Jeder Layer, jeder Konnektor, jedes Plugin emittiert NerveSignals.
    """
    fiber_id: str          # "aco.session" | "l16.audit" | "l10.path_gate" | …
    signal_type: str       # "health.ok" | "anomaly.stall" | "integrity.tampered" | …
    severity: str          # SEVERITY_* Konstanten oben
    message: str           # Menschenlesbare Beschreibung
    data: dict = field(default_factory=dict)   # Domänen-spezifische Details
    ts: str = field(default_factory=_now_iso)
    repair_hint: str = ""  # Für den Boot-Healer: was tun?
    # Ob das Signal einen Audit-Event erzeugen soll
    audit: bool = True

    def to_dict(self) -> dict:
        return {
            "fiber_id": self.fiber_id,
            "signal_type": self.signal_type,
            "severity": self.severity,
            "message": self.message,
            "data": self.data,
            "ts": self.ts,
            "repair_hint": self.repair_hint,
        }

    @property
    def is_healthy(self) -> bool:
        return self.severity in (SEVERITY_OK, SEVERITY_LOW)

    @property
    def needs_repair(self) -> bool:
        return self.severity in (SEVERITY_HIGH, SEVERITY_CRITICAL)


# ── NerveFiber-Protokoll ──────────────────────────────────────────────────────

class NerveFiber:
    """Basis-Klasse für alle Nerve Fibers.

    Jeder Layer und Konnektor implementiert diese Klasse.
    Der Nutzer kann eigene Fibers erstellen und über drei Wege anschließen:
      - Entry Points (Paket, Option A)
      - Lokale .py-Datei (Option B)
      - register_fiber() Decorator/Funktion (Option C)
    """
    fiber_id: str = "unnamed"
    fiber_version: str = "1.0.0"
    fiber_description: str = ""

    def scan(self) -> list[NerveSignal]:
        """Produziert Gesundheits-Signale. Wird bei jedem Healer-Zyklus aufgerufen.

        Muss innerhalb von ~10 s abschließen. Darf niemals werfen.
        Gibt eine leere Liste zurück wenn alles OK ist (implizites OK-Signal).
        """
        return []

    def repair(self, signal: NerveSignal) -> NerveSignal | None:
        """Versucht Selbstheilung für ein gegebenes Signal.

        Gibt None zurück wenn keine Reparatur möglich ist.
        Gibt ein neues NerveSignal zurück das den Reparatur-Ausgang beschreibt.
        """
        return None

    def describe(self) -> dict:
        return {
            "fiber_id": self.fiber_id,
            "fiber_version": self.fiber_version,
            "fiber_description": self.fiber_description or self.__class__.__doc__ or "",
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class NerveRegistry:
    """Zentrale Registratur aller Nerve Fibers.

    Thread-safe durch GIL (keine atomaren Operationen nötig für dict-Reads).
    Discovery ist einmalig beim ersten `scan_all()` Aufruf (lazy).
    """
    _fibers: dict[str, NerveFiber] = {}
    _discovered: bool = False

    @classmethod
    def register(cls, fiber: NerveFiber) -> None:
        """Registriert eine Fiber. Idempotent bei gleicher fiber_id."""
        cls._fibers[fiber.fiber_id] = fiber
        logger.debug("[Nerve] Fiber registriert: %s v%s", fiber.fiber_id, fiber.fiber_version)

    @classmethod
    def unregister(cls, fiber_id: str) -> None:
        cls._fibers.pop(fiber_id, None)

    @classmethod
    def list_fibers(cls) -> list[dict]:
        return [f.describe() for f in cls._fibers.values()]

    @classmethod
    def scan_all(cls) -> list[NerveSignal]:
        """Führt scan() auf allen registrierten Fibers aus.

        Fail-safe: ein fehlerhafter Fiber erzeugt ein HIGH-Signal,
        blockiert aber keine anderen Fibers.
        """
        if not cls._discovered:
            cls.discover()

        signals: list[NerveSignal] = []
        for fiber in list(cls._fibers.values()):
            try:
                result = fiber.scan()
                signals.extend(result)
            except Exception as exc:
                signals.append(NerveSignal(
                    fiber_id=fiber.fiber_id,
                    signal_type="nerve.fiber_error",
                    severity=SEVERITY_HIGH,
                    message=f"Fiber-Scan fehlgeschlagen: {exc}",
                    data={"error": str(exc)},
                    repair_hint="Fiber-Implementierung prüfen",
                ))
        return signals

    @classmethod
    def repair_all(cls, signals: list[NerveSignal]) -> list[NerveSignal]:
        """Führt repair() auf allen Fibers für Signale aus, die Reparatur benötigen.

        Gibt eine Liste der Reparatur-Ergebnis-Signale zurück.
        """
        repair_results: list[NerveSignal] = []
        for signal in signals:
            if not signal.needs_repair:
                continue
            fiber = cls._fibers.get(signal.fiber_id)
            if fiber is None:
                continue
            try:
                result = fiber.repair(signal)
                if result is not None:
                    repair_results.append(result)
            except Exception as exc:
                logger.debug("[Nerve] Repair-Fehler für %s: %s", signal.fiber_id, exc)
        return repair_results

    @classmethod
    def discover(cls) -> None:
        """Drei-Tier-Discovery: Built-ins → Entry-Points → Lokale Plugins."""
        cls._register_builtins()
        cls._discover_entry_points()
        cls._discover_local_plugins()
        cls._discovered = True
        logger.info("[Nerve] Discovery abgeschlossen — %d Fibers registriert", len(cls._fibers))

    @classmethod
    def reset(cls) -> None:
        """Für Tests: Registry zurücksetzen."""
        cls._fibers.clear()
        cls._discovered = False

    # ── Tier 0: Built-in Fibers ───────────────────────────────────────────────

    @classmethod
    def _register_builtins(cls) -> None:
        """Registriert alle eingebauten Fibers. Immer verfügbar, kein Packaging nötig."""
        try:
            from .nerve_builtins import _BUILTIN_FIBERS
            for fiber in _BUILTIN_FIBERS:
                cls.register(fiber)
        except Exception as exc:
            logger.debug("[Nerve] Built-in-Registrierung fehlgeschlagen: %s", exc)

    # ── Tier 1: Entry-Point-Discovery ─────────────────────────────────────────

    @classmethod
    def _discover_entry_points(cls) -> None:
        """Lädt Fibers von installierten Paketen via corvinOS.nerve_fibers Entry-Points."""
        try:
            eps = importlib.metadata.entry_points(group="corvinOS.nerve_fibers")
            for ep in eps:
                try:
                    fiber_cls = ep.load()
                    instance = fiber_cls() if isinstance(fiber_cls, type) else fiber_cls
                    cls.register(instance)
                    logger.info("[Nerve] Entry-Point-Fiber geladen: %s → %s",
                                ep.name, instance.fiber_id)
                except Exception as exc:
                    logger.warning("[Nerve] Entry-Point %s konnte nicht geladen werden: %s",
                                   ep.name, exc)
        except Exception as exc:
            logger.debug("[Nerve] Entry-Point-Discovery übersprungen: %s", exc)

    # ── Tier 2: Lokale Plugin-Discovery ──────────────────────────────────────

    @classmethod
    def _discover_local_plugins(cls) -> None:
        """Lädt Fibers aus ~/.corvin/nerve_fibers/*.py.

        Kein Packaging, kein pip-Install nötig. Nutzer legt einfach eine
        Python-Datei ab und die Fiber wird beim nächsten Healer-Zyklus aktiv.
        """
        try:
            from forge import paths as _fp
            plugins_dir = Path(_fp.corvin_home()) / "nerve_fibers"
        except Exception:
            home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
            plugins_dir = home / "nerve_fibers"

        if not plugins_dir.exists():
            return

        for plugin_file in sorted(plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"corvin_nerve_plugin_{plugin_file.stem}", plugin_file
                )
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
                # Alle NerveFiber-Subklassen in diesem Modul registrieren
                for name in dir(mod):
                    obj = getattr(mod, name)
                    if (isinstance(obj, type) and issubclass(obj, NerveFiber)
                            and obj is not NerveFiber):
                        try:
                            instance = obj()
                            cls.register(instance)
                            logger.info("[Nerve] Lokales Plugin geladen: %s → %s",
                                        plugin_file.name, instance.fiber_id)
                        except Exception as exc:
                            logger.warning("[Nerve] Plugin-Instanz %s/%s fehlgeschlagen: %s",
                                           plugin_file.name, name, exc)
            except Exception as exc:
                logger.warning("[Nerve] Plugin-Datei %s konnte nicht geladen werden: %s",
                               plugin_file.name, exc)


# ── Decorator / Funktions-API für einfache Registrierung ─────────────────────

def register_fiber(fiber_or_cls=None, *, fiber_id: str | None = None):
    """Decorator oder Funktion zur Fiber-Registrierung.

    Kann auf drei Arten verwendet werden:

    Als Klassen-Decorator:
        @register_fiber
        class MyFiber(NerveFiber):
            fiber_id = "my.fiber"
            ...

    Als Decorator mit expliziter ID:
        @register_fiber(fiber_id="my.fiber")
        class MyFiber(NerveFiber):
            ...

    Als Funktion mit einer Instanz:
        register_fiber(MyFiber())
    """
    if fiber_or_cls is None:
        # @register_fiber(fiber_id=...) Variante
        def decorator(cls_or_instance):
            if isinstance(cls_or_instance, type):
                instance = cls_or_instance()
            else:
                instance = cls_or_instance
            if fiber_id is not None:
                instance.fiber_id = fiber_id
            NerveRegistry.register(instance)
            return cls_or_instance
        return decorator

    # Direkte Instanz: register_fiber(MyFiber())
    if isinstance(fiber_or_cls, NerveFiber):
        NerveRegistry.register(fiber_or_cls)
        return fiber_or_cls

    # Klassen-Decorator ohne Argumente: @register_fiber
    if isinstance(fiber_or_cls, type) and issubclass(fiber_or_cls, NerveFiber):
        NerveRegistry.register(fiber_or_cls())
        return fiber_or_cls

    return fiber_or_cls


# ── Signal-Aggregation + Audit-Output ────────────────────────────────────────

def write_signals_to_audit(signals: list[NerveSignal], tenant_id: str = "_default") -> None:
    """Schreibt NerveSignals in die Audit-Chain (hash-chained, GDPR-konform)."""
    critical_or_high = [s for s in signals if s.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH)]
    if not critical_or_high:
        return
    try:
        from corvin_console import audit as console_audit
        for sig in critical_or_high[:20]:  # Sicherheitsobergrenze pro Zyklus
            if not sig.audit:
                continue
            console_audit.action_performed(
                action=f"nerve.signal.{sig.severity.lower()}",
                details={
                    "fiber_id": sig.fiber_id,
                    "signal_type": sig.signal_type,
                    "message": sig.message[:500],
                    "tenant_id": tenant_id,
                },
            )
    except Exception as exc:
        logger.debug("[Nerve] Audit-Schreiben fehlgeschlagen: %s", exc)


def summarize_signals(signals: list[NerveSignal]) -> dict:
    """Liefert eine strukturierte Zusammenfassung aller Signale."""
    return {
        "total": len(signals),
        "critical": sum(1 for s in signals if s.severity == SEVERITY_CRITICAL),
        "high": sum(1 for s in signals if s.severity == SEVERITY_HIGH),
        "medium": sum(1 for s in signals if s.severity == SEVERITY_MEDIUM),
        "low": sum(1 for s in signals if s.severity == SEVERITY_LOW),
        "ok": sum(1 for s in signals if s.severity == SEVERITY_OK),
        "fibers": sorted({s.fiber_id for s in signals}),
        "needs_repair": [s.to_dict() for s in signals if s.needs_repair],
    }
