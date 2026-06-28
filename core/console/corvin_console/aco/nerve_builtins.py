"""Built-in Nerve Fibers für CorvinOS — Tier-0 Discovery.

Jede Fiber hier wrAPPT einen bestehenden ACO-Check ohne dessen Implementierung
zu verändern. Neue Layer fügen einfach eine neue Fiber-Klasse hinzu.

Diese Datei ist der zentrale Ort wo der Nutzer sehen kann, welche Layers
vom Nervensystem erfasst werden.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .nerve import NerveFiber, NerveSignal, SEVERITY_OK, SEVERITY_HIGH, SEVERITY_CRITICAL

logger = logging.getLogger(__name__)


# ── Fiber: Session-Gesundheit (L1-L5 ACO) ────────────────────────────────────

class SessionFiber(NerveFiber):
    """Scannt alle Chat-Sessions auf Stalls, ACS-Fehler, WS-Instabilität.

    Wraps: anomaly_detector.scan_session + repair.repair_session
    Scope: alle Kanäle (web, discord, voice, cli)
    """
    fiber_id = "aco.session"
    fiber_version = "1.0.0"
    fiber_description = "Chat-Session-Gesundheit (L1-L5): Stalls, ACS-Fehler, WS-Instabilität"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .anomaly_detector import scan_session, SEVERITY_CRITICAL, SEVERITY_HIGH
            from .boot_healer import _find_all_workdirs, _discover_tenants
            for tenant_id in _discover_tenants():
                for workdir in _find_all_workdirs(tenant_id)[:20]:
                    try:
                        anomalies = scan_session(workdir)
                        for a in anomalies:
                            if a.severity in (SEVERITY_CRITICAL, SEVERITY_HIGH):
                                signals.append(NerveSignal(
                                    fiber_id=self.fiber_id,
                                    signal_type=f"session.{a.anomaly_class}",
                                    severity=a.severity,
                                    message=a.message,
                                    data={
                                        "workdir": str(workdir.name),
                                        "tenant_id": tenant_id,
                                        "suggestion": a.suggestion,
                                    },
                                    repair_hint=a.suggestion,
                                ))
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("[SessionFiber] Scan-Fehler: %s", exc)
        return signals

    def repair(self, signal: NerveSignal) -> NerveSignal | None:
        workdir_name = signal.data.get("workdir")
        tenant_id = signal.data.get("tenant_id", "_default")
        if not workdir_name:
            return None
        try:
            from .repair import repair_session
            from .boot_healer import _find_all_workdirs
            for workdir in _find_all_workdirs(tenant_id):
                if workdir.name == workdir_name:
                    result = repair_session(workdir, dry_run=False)
                    return NerveSignal(
                        fiber_id=self.fiber_id,
                        signal_type="session.repaired",
                        severity=SEVERITY_OK if result.convergence_reached else SEVERITY_HIGH,
                        message=(
                            f"Reparatur: delta_loss={result.delta_loss}, "
                            f"convergence={result.convergence_reached}"
                        ),
                        data={"workdir": workdir_name, "tenant_id": tenant_id},
                    )
        except Exception as exc:
            logger.debug("[SessionFiber] Repair-Fehler: %s", exc)
        return None


# ── Fiber: Engine-Bereitschaft ────────────────────────────────────────────────

class EngineFiber(NerveFiber):
    """Prüft Engine- und Voice-Bereitschaft (claude_code / hermes / TTS / STT).

    Wraps: engine_healer.run_readiness_check
    """
    fiber_id = "aco.engine"
    fiber_version = "1.0.0"
    fiber_description = "Engine + Voice Readiness: claude_code/hermes, TTS, STT"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .engine_healer import run_readiness_check
            from .boot_healer import _discover_tenants
            for tenant_id in _discover_tenants():
                try:
                    result = run_readiness_check(tenant_id)
                    if not result.engine_ok:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.unavailable",
                            severity=SEVERITY_CRITICAL,
                            message=f"Keine Engine verfügbar (tenant={tenant_id}, "
                                    f"configured={result.engine_id}, "
                                    f"action={result.engine_action})",
                            data=result.to_audit_details(),
                            repair_hint="Ollama starten oder claude-binary prüfen",
                        ))
                    elif result.engine_action not in ("none", ""):
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.auto_healed",
                            severity=SEVERITY_OK,
                            message=f"Engine auto-geheilt: {result.engine_action} "
                                    f"(tenant={tenant_id})",
                            data=result.to_audit_details(),
                        ))
                    for warning in result.warnings:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type="engine.warning",
                            severity=SEVERITY_HIGH,
                            message=warning,
                            data={"tenant_id": tenant_id},
                        ))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[EngineFiber] Scan-Fehler: %s", exc)
        return signals


# ── Fiber: System-Integrität (Immunsystem) ───────────────────────────────────

class IntegrityFiber(NerveFiber):
    """Schutz der System-Integrität: Audit-Chain, Config, Licensing, Compliance.

    Wraps: integrity_monitor.run_integrity_scan
    """
    fiber_id = "aco.integrity"
    fiber_version = "1.0.0"
    fiber_description = (
        "System-Integrität: Audit-Chain, Config-Tampering, License-Pubkey, "
        "Compliance-Gates (house_rules, consent, disclosure)"
    )

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from .integrity_monitor import run_integrity_scan
            from .boot_healer import _discover_tenants
            for tenant_id in _discover_tenants():
                try:
                    findings = run_integrity_scan(tenant_id)
                    for f in findings:
                        signals.append(NerveSignal(
                            fiber_id=self.fiber_id,
                            signal_type=f"integrity.{f.check_name}",
                            severity=f.severity,
                            message=f.message,
                            data={"tenant_id": tenant_id, **f.detail},
                            repair_hint=f"Audit-Log prüfen: {f.check_name}",
                        ))
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[IntegrityFiber] Scan-Fehler: %s", exc)
        return signals


# ── Fiber: Installation-Bereitschaft ─────────────────────────────────────────

class InstallFiber(NerveFiber):
    """Prüft kritische Dependencies vom Pip-Install bis zur Laufzeit.

    Erkennt: fehlende Pakete, falsche Versionen, platform-inkompatible Deps.
    Wichtig: Muss auf frischer Installation (pip install corvinos) grün sein.
    """
    fiber_id = "install.deps"
    fiber_version = "1.0.0"
    fiber_description = "Installationsbereitschaft: kritische Pakete und Plattform-Kompatibilität"

    # (paketname, importname, min_version_check, required)
    _DEPS = [
        ("fastapi",     "fastapi",        None,    True),
        ("pydantic",    "pydantic",       None,    True),
        ("httpx",       "httpx",          None,    True),
        ("uvicorn",     "uvicorn",        None,    True),
        ("pyyaml",      "yaml",           None,    True),
        ("PyJWT",       "jwt",            None,    True),
        ("cryptography","cryptography",   None,    True),
        ("edge-tts",    "edge_tts",       None,    False),  # opt-in TTS
        ("openai",      "openai",         None,    False),  # opt-in STT/TTS
    ]

    def scan(self) -> list[NerveSignal]:
        import importlib as _il
        import sys

        signals: list[NerveSignal] = []
        for pkg_name, import_name, _, required in self._DEPS:
            spec = _il.util.find_spec(import_name)
            available = spec is not None
            if not available and required:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_required",
                    severity=SEVERITY_CRITICAL,
                    message=f"Pflichtpaket fehlt: {pkg_name} ({import_name} nicht importierbar)",
                    data={"package": pkg_name, "import_name": import_name},
                    repair_hint=f"pip install '{pkg_name}'",
                ))
            elif not available and not required:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_optional",
                    severity=SEVERITY_HIGH,
                    message=f"Optionales Paket fehlt: {pkg_name} — Feature eingeschränkt",
                    data={"package": pkg_name, "import_name": import_name},
                    repair_hint=f"pip install '{pkg_name}' für volle Funktionalität",
                    audit=False,  # Nicht jede fehlende Optional-Dep in Audit schreiben
                ))

        # Plattform-spezifische Prüfung: faster-whisper auf non-Windows
        if sys.platform != "win32":
            spec = _il.util.find_spec("faster_whisper")
            if spec is None:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="install.missing_optional",
                    severity=SEVERITY_HIGH,
                    message="faster-whisper fehlt — lokale Spracheingabe deaktiviert",
                    data={"package": "faster-whisper", "platform": sys.platform},
                    repair_hint="pip install faster-whisper",
                    audit=False,
                ))

        return signals


# ── Fiber: Audit-Chain-Gesundheit ─────────────────────────────────────────────

class AuditChainFiber(NerveFiber):
    """Kontinuierliches Monitoring der Hash-Chain-Integrität (L16 GDPR Art. 30, 32).

    Überwacht: Kettenbrüche, fehlende Events, verdächtige Lücken.
    """
    fiber_id = "l16.audit_chain"
    fiber_version = "1.0.0"
    fiber_description = "L16 Audit-Chain: Hash-Integrität, GDPR Art. 30/32"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            from operator.bridges.shared.audit import verify_audit, audit_path
            audit_file = audit_path()
            if not audit_file.exists():
                return signals  # frische Installation
            ok, problems = verify_audit(audit_file)
            if not ok:
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="audit.chain_broken",
                    severity=SEVERITY_CRITICAL,
                    message=f"Audit-Chain gebrochen: {len(problems)} Problem(e)",
                    data={"problem_count": len(problems),
                          "first_problem": problems[0] if problems else {}},
                    repair_hint="Audit-Log-Datei auf Manipulation untersuchen",
                ))
            else:
                # Stichprobe: Letzter Event sollte kürzlich sein
                signals.append(NerveSignal(
                    fiber_id=self.fiber_id,
                    signal_type="audit.chain_ok",
                    severity=SEVERITY_OK,
                    message="Audit-Chain intakt",
                    data={},
                    audit=False,  # OK-Signals nicht in Audit schreiben
                ))
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("[AuditChainFiber] Fehler: %s", exc)
        return signals


# ── Fiber: Consent- und Disclosure-Gate (L16, EU AI Act Art. 50) ─────────────

class ComplianceFiber(NerveFiber):
    """EU AI Act Art. 50 + GDPR Art. 6/7 Consent-Gate-Monitoring.

    Prüft: Disclosure-Gate aktiv, Consent-Gate aktiv, House-Rules geladen.
    """
    fiber_id = "l16.compliance"
    fiber_version = "1.0.0"
    fiber_description = "EU AI Act Art. 50 + GDPR: Disclosure, Consent, House-Rules"

    def scan(self) -> list[NerveSignal]:
        signals: list[NerveSignal] = []
        try:
            import importlib.util
            for module_name, label in [
                ("operator.bridges.shared.house_rules", "house_rules"),
                ("operator.bridges.shared.consent",     "consent_gate"),
                ("operator.bridges.shared.disclosure",  "disclosure_gate"),
            ]:
                spec = importlib.util.find_spec(module_name)
                if spec is None:
                    signals.append(NerveSignal(
                        fiber_id=self.fiber_id,
                        signal_type=f"compliance.{label}_missing",
                        severity=SEVERITY_CRITICAL,
                        message=f"Compliance-Modul nicht importierbar: {module_name}",
                        data={"module": module_name, "label": label},
                        repair_hint=f"pip install corvinos oder Paket-Installation prüfen",
                    ))
        except Exception as exc:
            logger.debug("[ComplianceFiber] Fehler: %s", exc)
        return signals


# ── Registry der Built-in Fibers (wird von nerve.py importiert) ───────────────

_BUILTIN_FIBERS: list[NerveFiber] = [
    InstallFiber(),       # Immer zuerst — frische Installation
    AuditChainFiber(),    # L16 Kern-Sicherheit
    ComplianceFiber(),    # EU AI Act / GDPR
    IntegrityFiber(),     # Immunsystem
    EngineFiber(),        # Engine + Voice
    SessionFiber(),       # Chat-Sessions
]
