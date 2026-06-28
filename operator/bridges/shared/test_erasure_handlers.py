"""Tests for the per-layer ErasureHandler implementations."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from erasure_handlers import (  # noqa: E402
    IdentityMappingHandlerBase,
    L7SkillForgeHandler,
    L24DataSnapshotHandler,
    L28RecallHandler,
    L33ArtifactHandler,
    real_handler_chain,
)
from erasure_orchestrator import LayerStatus  # noqa: E402


# ── L28 ──────────────────────────────────────────────────────────────


class TestL28RecallHandler(unittest.TestCase):

    def _build_db(self, path: Path, rows: list[tuple[str, str]]) -> None:
        """rows: [(chat_key, user_text), ...] — simplified turns table."""
        with sqlite3.connect(str(path)) as conn:
            conn.execute("""
                CREATE TABLE turns (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL NOT NULL,
                    channel   TEXT NOT NULL,
                    chat_key  TEXT NOT NULL,
                    msg_id    TEXT,
                    run_id    TEXT,
                    persona   TEXT,
                    user_chars INTEGER NOT NULL,
                    asst_chars INTEGER NOT NULL,
                    redacted_classes TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    asst_text TEXT NOT NULL
                )
            """)
            for i, (chat_key, text) in enumerate(rows):
                conn.execute(
                    "INSERT INTO turns(ts, channel, chat_key, msg_id, run_id, persona, "
                    "user_chars, asst_chars, redacted_classes, user_text, asst_text) "
                    "VALUES(?, 'discord', ?, ?, '', '', ?, 0, '[]', ?, '')",
                    (1.0 + i, chat_key, f"m{i}", len(text), text),
                )
            conn.commit()

    def test_purge_deletes_matching_rows(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "recall.db"
            self._build_db(db, [
                ("user_42", "hello"),
                ("user_42", "world"),
                ("user_99", "other"),
            ])
            handler = L28RecallHandler(db_path=db)
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.APPLIED)
            self.assertEqual(result.count, 2)
            # Verify the other user's rows are still there
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
            self.assertEqual(row[0], 1)

    def test_purge_no_match_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "recall.db"
            self._build_db(db, [("user_99", "x")])
            handler = L28RecallHandler(db_path=db)
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.SKIPPED)
            self.assertEqual(result.count, 0)

    def test_missing_db_returns_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            handler = L28RecallHandler(
                db_path=Path(td) / "nonexistent.db",
            )
            result = handler.purge("user_42", "er-test")
            self.assertEqual(result.status, LayerStatus.SKIPPED)
            self.assertIn("not present", result.reason)

    def test_layer_id_default(self):
        h = L28RecallHandler()
        self.assertEqual(h.layer_id, "L28-recall")


# ── L33 ──────────────────────────────────────────────────────────────


class TestL33ArtifactHandler(unittest.TestCase):

    def _corvin_home(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory(prefix="erasure-l33-")

    def test_purge_removes_session_files(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                session_key = "discord:user_42"
                art_dir = (Path(td) / "tenants" / "_default" / "sessions"
                           / session_key / "artifacts")
                art_dir.mkdir(parents=True)
                # Two files + a manifest entry
                (art_dir / "report.pdf").write_bytes(b"x" * 100)
                (art_dir / ".manifest.jsonl").write_text(
                    '{"name":"report.pdf"}\n'
                )

                handler = L33ArtifactHandler()
                result = handler.purge(session_key, "er-test")

                self.assertEqual(result.status, LayerStatus.APPLIED)
                self.assertEqual(result.count, 2)
                self.assertFalse((art_dir / "report.pdf").exists())
                self.assertFalse((art_dir / ".manifest.jsonl").exists())
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_missing_session_dir_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                handler = L33ArtifactHandler()
                result = handler.purge("discord:never_existed",
                                       "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertIn("no session artifacts", result.reason)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_empty_artifacts_dir_returns_skipped(self):
        with self._corvin_home() as td:
            os.environ["CORVIN_HOME"] = td
            try:
                session_key = "discord:user_42"
                art_dir = (Path(td) / "tenants" / "_default" / "sessions"
                           / session_key / "artifacts")
                art_dir.mkdir(parents=True)
                handler = L33ArtifactHandler()
                result = handler.purge(session_key, "er-test")
                self.assertEqual(result.status, LayerStatus.SKIPPED)
                self.assertIn("empty", result.reason)
            finally:
                os.environ.pop("CORVIN_HOME", None)

    def test_layer_id_default(self):
        h = L33ArtifactHandler()
        self.assertEqual(h.layer_id, "L33-artifacts")


# ── L7 + L24 stubs ───────────────────────────────────────────────────


class TestStubHandlers(unittest.TestCase):

    def test_l7_returns_skipped_with_documented_reason(self):
        h = L7SkillForgeHandler()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("not yet implemented", r.reason)
        self.assertEqual(r.layer_id, "L7-skill-forge")

    def test_l24_returns_skipped_with_documented_reason(self):
        h = L24DataSnapshotHandler()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("not yet implemented", r.reason)
        self.assertEqual(r.layer_id, "L24-data-snapshot")

    def test_identity_mapping_base_warns_when_unconfigured(self):
        h = IdentityMappingHandlerBase()
        r = h.purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.SKIPPED)
        self.assertIn("no concrete", r.reason)

    def test_identity_mapping_subclassable(self):
        from dataclasses import dataclass
        from erasure_orchestrator import ErasureLayerResult, LayerStatus as LS

        @dataclass
        class _RealMapping(IdentityMappingHandlerBase):
            def purge(self, subject_id, request_id):
                return ErasureLayerResult(
                    layer_id=self.layer_id,
                    status=LS.APPLIED,
                    count=1,
                    reason="mapping deleted",
                )

        r = _RealMapping().purge("user_42", "er-test")
        self.assertEqual(r.status, LayerStatus.APPLIED)


# ── default chain factory ────────────────────────────────────────────


class TestRealHandlerChain(unittest.TestCase):

    def test_chain_includes_core_handlers(self):
        chain = real_handler_chain()
        layer_ids = [h.layer_id for h in chain]
        # Core per-layer handlers that must always be present. ACS-traces
        # was added (ADR-0127 review) to purge plaintext WDAT worker traces
        # under GDPR Art. 17 — assert it is wired into the default chain.
        for required in ("L28-recall", "L33-artifacts", "ACS-traces",
                         "L7-skill-forge", "L24-data-snapshot"):
            self.assertIn(required, layer_ids, f"missing handler: {required}")
        # No duplicate layer ids in the default chain.
        self.assertEqual(len(layer_ids), len(set(layer_ids)))


if __name__ == "__main__":
    unittest.main()
