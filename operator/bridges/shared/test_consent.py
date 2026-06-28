"""test_consent.py — V-016: consent store corruption handling for ADR-0072.

Tests that is_granted() handles a corrupted (invalid JSON) consent store
gracefully: returns (False, "store-corrupted"), backs up the corrupted file,
and returns (False, "no-entry") for a fresh store with no entry.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the shared package importable.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import consent  # noqa: E402


class ConsentStoreCorruptionTests(unittest.TestCase):
    """V-016: corrupted consent store must be handled without an exception."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="consent-test-")
        os.environ["CORVIN_HOME"] = self._tmp

    def tearDown(self) -> None:
        os.environ.pop("CORVIN_HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _store_path(self, channel: str, chat_key: str) -> Path:
        """Resolve the consent store path using the same logic as the module."""
        return consent._store_path(channel, chat_key)

    # ------------------------------------------------------------------
    # Corruption handling
    # ------------------------------------------------------------------

    def test_consent_store_corrupted(self):
        """is_granted returns (False, 'store-corrupted') when the JSON is invalid
        and backs up the corrupted file next to the original."""
        channel = "discord"
        chat_key = "chat1"
        uid = "user123"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{ this is not valid JSON !!!")

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertFalse(granted,
                         "is_granted must return False on store corruption")
        self.assertEqual(reason, "store-corrupted",
                         f"reason must be 'store-corrupted', got {reason!r}")

        # The corrupted file must have been backed up.
        parent = store_path.parent
        backups = list(parent.glob(store_path.name + ".corrupt.*"))
        self.assertGreater(len(backups), 0,
                           "a .corrupt.TIMESTAMP backup file must exist after corruption handling")

    def test_consent_store_corrupted_original_removed(self):
        """After corruption handling the original corrupted file must be gone
        (so the next call starts with a clean state)."""
        channel = "discord"
        chat_key = "chat2"
        uid = "user-clean"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("NOT JSON AT ALL")

        consent.is_granted(channel, chat_key, uid)

        self.assertFalse(store_path.exists(),
                         "corrupted store file must be removed after backup")

    def test_consent_store_corrupted_subsequent_call_is_no_entry(self):
        """After the corrupted file is removed, the next call must return
        (False, 'no-entry') — not another 'store-corrupted'."""
        channel = "discord"
        chat_key = "chat3"
        uid = "user-subsequent"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{invalid}")

        # First call: handles corruption.
        consent.is_granted(channel, chat_key, uid)

        # Second call: file is gone, behaves like a fresh store.
        granted2, reason2 = consent.is_granted(channel, chat_key, uid)
        self.assertFalse(granted2)
        self.assertEqual(reason2, "no-entry",
                         "after corruption cleanup subsequent call must return 'no-entry'")

    # ------------------------------------------------------------------
    # Fresh / no-entry cases
    # ------------------------------------------------------------------

    def test_consent_fresh_store_is_no_entry(self):
        """A brand-new CORVIN_HOME with no consent file must yield
        (False, 'no-entry') — never an exception."""
        channel = "discord"
        chat_key = "chat1"
        uid = "user-new"

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertFalse(granted,
                         "is_granted must return False for a user with no consent entry")
        self.assertEqual(reason, "no-entry",
                         f"reason must be 'no-entry' for a fresh store, got {reason!r}")

    def test_consent_no_entry_for_unknown_uid(self):
        """A store that exists but has no entry for the queried UID must return
        (False, 'no-entry')."""
        channel = "discord"
        chat_key = "chat4"
        uid_present = "user-A"
        uid_absent  = "user-B"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid_present: {
                "mode": "durable",
                "granted_at": 1778204770.0,
                "expires_at": None,
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        granted, reason = consent.is_granted(channel, chat_key, uid_absent)

        self.assertFalse(granted,
                         "unknown UID must yield is_granted=False")
        self.assertEqual(reason, "no-entry",
                         f"reason must be 'no-entry' for absent UID, got {reason!r}")

    def test_consent_no_uid_returns_no_uid(self):
        """Empty-string UID short-circuits with 'no-uid', not 'no-entry'."""
        granted, reason = consent.is_granted("discord", "chat1", "")
        self.assertFalse(granted)
        self.assertEqual(reason, "no-uid",
                         f"empty UID must return 'no-uid', got {reason!r}")

    # ------------------------------------------------------------------
    # Happy-path sanity
    # ------------------------------------------------------------------

    def test_consent_durable_entry_is_granted(self):
        """A valid durable consent entry must cause is_granted to return True."""
        channel = "discord"
        chat_key = "chat5"
        uid = "user-durable"

        store_path = self._store_path(channel, chat_key)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({
            uid: {
                "mode": "durable",
                "granted_at": 1778204770.0,
                "expires_at": None,
                "channel": channel,
                "granted_via": "slash",
            }
        }))

        granted, reason = consent.is_granted(channel, chat_key, uid)

        self.assertTrue(granted,
                        f"durable consent must be granted, got ({granted}, {reason!r})")
        self.assertEqual(reason, "durable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
