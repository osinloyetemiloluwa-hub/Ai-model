"""Standalone FastAPI application for native (non-Docker) deployments.

Usage
-----
Start directly::

    uvicorn corvin_console.standalone:create_app --factory \
        --host 0.0.0.0 --port 8000 \
        --ws-ping-interval 20 --ws-ping-timeout 30

Or simply::

    python -m corvin_console.standalone

The ``--ws-ping-interval`` flag enables protocol-level WebSocket pings (RFC 6455
opcode 0x9) every 20 s. These keep connections alive through proxies that drop idle
sockets after 60 s, and work even during long tool calls when no data frames flow.

Or via ``corvin serve`` / ``corvin start`` (pip-install path).

The app exposes:
  /v1/console/...   Console REST API (all existing routes)
  /console/         React SPA (served from web-next/dist/)
  /                 Redirect → /v1/console/auth/local-login

local-login creates a session automatically for localhost operators and
redirects to /console/. The SetupGate component then guides first-time
configuration (engine key, optional bridge channel).
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .app import mount_static, router

log = logging.getLogger(__name__)

# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return the standalone CorvinOS console application.

    Callable as a uvicorn ``--factory`` target:
    ``corvin_console.standalone:create_app``
    """
    app = FastAPI(
        title="CorvinOS Console",
        version="1.0",
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,
    )

    # Allow the same-origin SPA to call the API in development.
    # In production (serving SPA from the same origin) this is a no-op.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount all console API routes at /v1/console
    app.include_router(router, prefix="/v1/console")

    # Mount the pre-built React SPA at /console
    mount_static(app, url_prefix="/console")

    # Root redirect → local-login → session cookie → /console/
    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/v1/console/auth/local-login", status_code=302)

    log.info("CorvinOS standalone app ready — local-login enabled by default")
    return app


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=20,
        ws_ping_timeout=30,
    )
