"""Robustness regression for the chat WebSocket (routes/chat.py::chat_stream).

Locks the contract that a turn failure must NEVER drop the WebSocket — it
becomes an in-band error+done event and the socket stays open for the next
turn — and that client heartbeats are answered DURING a long turn (keepalive
through idle-killing proxies). Reproduces the "Connection lost" mid-tool-call
failure and proves it can't recur.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "operator/bridges/shared", "operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.routes.chat as chat_routes  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app


async def _gen_raises(sess, prompt):  # noqa: ANN001
    """A turn that emits one tool_use then blows up (engine/tool/I-O hiccup)."""
    yield {"type": "tool_use", "name": "Read", "input": {}}
    raise RuntimeError("boom inside the engine turn")


async def _gen_slow(sess, prompt):  # noqa: ANN001
    """A turn with a long tool gap (no deltas) — exercises mid-turn keepalive."""
    yield {"type": "tool_use", "name": "Edit", "input": {}}
    await asyncio.sleep(0.4)
    yield {"type": "result", "text": "ok"}
    yield {"type": "done"}


class _SessStub:
    """Serializable session stub (MagicMock breaks _project's json.send)."""
    sid = "s1"
    chat_key = "web:s1"
    title = "Test"
    created_at = 0.0
    last_active_at = 0.0
    turn_count = 0
    workdir = None


class ChatWsRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = MagicMock()
        self.rec.tenant_id = "_default"
        self.sess = _SessStub()
        # ADR-0150 added a per-turn chat_turns_per_day charge to the chat WS. These
        # tests exercise WS streaming/robustness, not quota — neutralise the gate so
        # they are not blocked (and do not write a real quota counter).
        _qp = patch(
            "corvin_console.routes._compute_license_gate.enforce_chat_turns",
            lambda *a, **k: None,
        )
        _qp.start()
        self.addCleanup(_qp.stop)

    def _client(self) -> TestClient:
        c = TestClient(_app())
        c.cookies.set("corvin_console_sid", "valid-sid")
        return c

    def test_turn_exception_does_not_drop_socket(self) -> None:
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_raises),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "do it"})
                self.assertEqual(ws.receive_json()["type"], "tool_use")
                # The engine raised — must arrive as an in-band error, NOT a drop.
                self.assertEqual(ws.receive_json()["type"], "error")
                self.assertEqual(ws.receive_json()["type"], "done")
                # Socket MUST still be open: a ping is answered with a pong.
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_ping_during_turn_gets_pong_and_turn_completes(self) -> None:
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_slow),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s2/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "long task"})
                self.assertEqual(ws.receive_json()["type"], "tool_use")
                # Heartbeat sent while the turn is mid-flight (during the gap).
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")
                # Turn still finishes normally afterwards.
                self.assertEqual(ws.receive_json()["type"], "result")
                self.assertEqual(ws.receive_json()["type"], "done")

    def test_normal_turn_streams_and_socket_survives_for_next_turn(self) -> None:
        async def _gen_ok(sess, prompt):  # noqa: ANN001
            yield {"type": "delta", "text": "hi"}
            yield {"type": "result", "text": "hi"}
            yield {"type": "done"}

        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_ok),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s3/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "one"})
                self.assertEqual(ws.receive_json()["type"], "delta")
                self.assertEqual(ws.receive_json()["type"], "result")
                self.assertEqual(ws.receive_json()["type"], "done")
                # Second turn on the SAME socket works (no reconnect needed).
                ws.send_json({"type": "user", "text": "two"})
                self.assertEqual(ws.receive_json()["type"], "delta")


if __name__ == "__main__":
    unittest.main()
