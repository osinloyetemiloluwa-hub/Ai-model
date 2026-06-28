"""Tests for instance_identity.py — Layer 38 stable instance UUID."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import instance_identity  # type: ignore[import-not-found]


class _TempHomeMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.id_path = self.tmpdir / "instance_id.json"
        self._prev_env = os.environ.get("CORVIN_INSTANCE_ID_PATH")
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(self.id_path)

    def tearDown(self) -> None:  # type: ignore[override]
        if self._prev_env is not None:
            os.environ["CORVIN_INSTANCE_ID_PATH"] = self._prev_env
        else:
            os.environ.pop("CORVIN_INSTANCE_ID_PATH", None)
        self._tmp.cleanup()


class TestFirstCall(_TempHomeMixin, unittest.TestCase):
    def test_first_call_creates_file(self) -> None:
        self.assertFalse(self.id_path.exists())
        iid = instance_identity.get_instance_id()
        self.assertTrue(self.id_path.exists())
        # Validate UUID4 shape
        uuid.UUID(iid, version=4)

    def test_file_mode_is_0600(self) -> None:
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_file_not_group_or_world_readable(self) -> None:
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode
        self.assertFalse(mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_metadata_includes_created_at(self) -> None:
        meta = instance_identity.instance_id_metadata()
        self.assertIn("created_at", meta)
        # ISO 8601 with timezone
        self.assertIn("T", meta["created_at"])

    def test_metadata_label_defaults_empty(self) -> None:
        meta = instance_identity.instance_id_metadata()
        self.assertEqual(meta.get("label"), "")


class TestStability(_TempHomeMixin, unittest.TestCase):
    def test_subsequent_call_returns_same_id(self) -> None:
        iid1 = instance_identity.get_instance_id()
        iid2 = instance_identity.get_instance_id()
        self.assertEqual(iid1, iid2)

    def test_id_survives_module_reload(self) -> None:
        iid1 = instance_identity.get_instance_id()
        # Simulate restart by reloading via fresh metadata read
        with self.id_path.open() as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["instance_id"], iid1)


class TestThreadSafety(_TempHomeMixin, unittest.TestCase):
    def test_concurrent_first_call_yields_single_uuid(self) -> None:
        results: list[str] = []
        ev = threading.Event()

        def worker() -> None:
            ev.wait()
            results.append(instance_identity.get_instance_id())

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        ev.set()
        for t in threads:
            t.join()
        self.assertEqual(len(set(results)), 1)


class TestLabel(_TempHomeMixin, unittest.TestCase):
    def test_set_label_creates_and_updates(self) -> None:
        meta = instance_identity.set_label("test-instance")
        self.assertEqual(meta["label"], "test-instance")
        # And the file reflects it
        with self.id_path.open() as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["label"], "test-instance")

    def test_set_label_preserves_uuid(self) -> None:
        iid = instance_identity.get_instance_id()
        instance_identity.set_label("renamed")
        self.assertEqual(instance_identity.get_instance_id(), iid)

    def test_set_label_rejects_control_chars(self) -> None:
        with self.assertRaises(ValueError):
            instance_identity.set_label("bad\x00label")
        with self.assertRaises(ValueError):
            instance_identity.set_label("with\nnewline")

    def test_set_label_rejects_too_long(self) -> None:
        with self.assertRaises(ValueError):
            instance_identity.set_label("x" * 65)

    def test_set_label_accepts_max_length(self) -> None:
        meta = instance_identity.set_label("x" * 64)
        self.assertEqual(len(meta["label"]), 64)

    def test_set_label_requires_str(self) -> None:
        with self.assertRaises(TypeError):
            instance_identity.set_label(123)  # type: ignore[arg-type]


class TestSelfHeal(_TempHomeMixin, unittest.TestCase):
    def test_world_readable_file_gets_tightened(self) -> None:
        instance_identity.get_instance_id()
        os.chmod(self.id_path, 0o644)
        # Re-read; the loader should silently tighten back to 0600.
        instance_identity.get_instance_id()
        mode = self.id_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_corrupt_file_regenerates(self) -> None:
        with self.id_path.open("w") as fh:
            fh.write("not valid json {{{")
        os.chmod(self.id_path, 0o600)
        iid = instance_identity.get_instance_id()
        uuid.UUID(iid, version=4)

    def test_missing_uuid_field_regenerates(self) -> None:
        with self.id_path.open("w") as fh:
            json.dump({"created_at": "2026-01-01T00:00:00+00:00"}, fh)
        os.chmod(self.id_path, 0o600)
        iid = instance_identity.get_instance_id()
        uuid.UUID(iid, version=4)


class TestEnvLabel(_TempHomeMixin, unittest.TestCase):
    def test_env_label_used_on_first_create(self) -> None:
        os.environ["CORVIN_INSTANCE_LABEL"] = "from-env"
        try:
            meta = instance_identity.instance_id_metadata()
            self.assertEqual(meta["label"], "from-env")
        finally:
            os.environ.pop("CORVIN_INSTANCE_LABEL", None)


class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self) -> None:
        import ast
        src = (_here / "instance_identity.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
