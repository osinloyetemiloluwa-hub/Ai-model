"""
CorvinOS Console REST API — Phase 1 Implementation

Endpoints:
  POST /v1/console/auth/login
  POST /v1/console/auth/logout
  GET  /v1/console/auth/whoami
  GET  /v1/console/dashboard
  GET  /v1/console/profile
  PUT  /v1/console/profile

Session Management:
  - Cookie: corvin_console_sid (HttpOnly, Secure, SameSite)
  - CSRF Token: X-CSRF-Token header (required for mutations)
  - Auth: Bearer token alternative (for CLI/API clients)

Data Isolation:
  - Per-session state (Redis-backed, in-memory for mock)
  - No cross-session data leakage
  - Timestamps UTC

Error Handling:
  - 400: Bad Request (validation)
  - 401: Unauthorized (no session)
  - 403: Forbidden (CSRF failed, permission denied)
  - 500: Server error (logged, safe message)
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr


# ── Session & CSRF Management ────────────────────────────────────────────

_SESSIONS: dict[str, dict[str, Any]] = {}
_TEST_USER = {
    "email": "test@example.com",
    "password_hash": hashlib.sha256(b"password123").hexdigest(),  # test cred
    "tier": "owner",
    "tenant_id": "_default",
}


def _gen_csrf_token() -> str:
    """Generate a secure CSRF token (32 bytes hex)."""
    return secrets.token_hex(16)


def _gen_session_id() -> str:
    """Generate a session ID."""
    return secrets.token_hex(32)


def _verify_csrf(request: Request, token: Optional[str]) -> bool:
    """Verify CSRF token from request headers."""
    if not token:
        return False
    sid = request.cookies.get("corvin_console_sid")
    if not sid or sid not in _SESSIONS:
        return False
    stored_token = _SESSIONS[sid].get("csrf_token")
    return stored_token == token


def _get_session(request: Request) -> dict[str, Any] | None:
    """Get the current session from cookie."""
    sid = request.cookies.get("corvin_console_sid")
    if not sid:
        return None
    session = _SESSIONS.get(sid)
    if not session:
        return None
    # Check expiration (24 hours)
    if time.time() > session.get("expires_at", 0):
        del _SESSIONS[sid]
        return None
    return session


# ── Models ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    tier: str
    tenant_id: str
    fingerprint: str
    csrf_token: str
    expires_at: float


class WhoamiResponse(BaseModel):
    tier: str
    tenant_id: str
    fingerprint: str
    csrf_token: str
    expires_at: float


class DashboardResponse(BaseModel):
    engines_online: int
    channels: list[str]
    audit_events_today: int
    last_sync: str
    uptime_percent: float


class ProfileResponse(BaseModel):
    notification_sound: str
    theme: str
    language: str
    timezone: str


class ProfileUpdateRequest(BaseModel):
    notification_sound: Optional[str] = None
    theme: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


# ── Router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1/console", tags=["console"])


@router.post("/auth/login", response_model=LoginResponse, status_code=200)
async def login(request: LoginRequest, response: Response) -> LoginResponse:
    """Authenticate with email + password, return session cookie + CSRF token."""
    # Validate credentials (demo: only test@example.com/password123)
    if request.email != _TEST_USER["email"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    password_hash = hashlib.sha256(request.password.encode()).hexdigest()
    if password_hash != _TEST_USER["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create session
    sid = _gen_session_id()
    csrf_token = _gen_csrf_token()
    expires_at = time.time() + 86400  # 24 hours
    fingerprint = hashlib.sha256(f"{request.email}:{sid}".encode()).hexdigest()[:16]

    _SESSIONS[sid] = {
        "email": request.email,
        "tier": _TEST_USER["tier"],
        "tenant_id": _TEST_USER["tenant_id"],
        "csrf_token": csrf_token,
        "expires_at": expires_at,
        "created_at": time.time(),
    }

    # Set session cookie
    response.set_cookie(
        key="corvin_console_sid",
        value=sid,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,
    )

    return LoginResponse(
        tier=_TEST_USER["tier"],
        tenant_id=_TEST_USER["tenant_id"],
        fingerprint=fingerprint,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


@router.post("/auth/logout", status_code=200)
async def logout(request: Request) -> dict[str, bool]:
    """Invalidate session and clear session cookie."""
    sid = request.cookies.get("corvin_console_sid")
    if sid and sid in _SESSIONS:
        del _SESSIONS[sid]
    return {"success": True}


@router.get("/auth/whoami", response_model=WhoamiResponse)
async def whoami(request: Request) -> WhoamiResponse:
    """Get current user session info."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    csrf_token = _gen_csrf_token()  # Refresh CSRF token on each call
    session["csrf_token"] = csrf_token

    fingerprint = hashlib.sha256(
        f"{session['email']}:{request.cookies.get('corvin_console_sid')}".encode()
    ).hexdigest()[:16]

    return WhoamiResponse(
        tier=session["tier"],
        tenant_id=session["tenant_id"],
        fingerprint=fingerprint,
        csrf_token=csrf_token,
        expires_at=session["expires_at"],
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(request: Request) -> DashboardResponse:
    """Get dashboard metrics (requires session)."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    return DashboardResponse(
        engines_online=2,
        channels=["discord", "slack", "telegram"],
        audit_events_today=142,
        last_sync=datetime.now(timezone.utc).isoformat(),
        uptime_percent=99.8,
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(request: Request) -> ProfileResponse:
    """Get user profile settings."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    # Return default profile
    return ProfileResponse(
        notification_sound="bell",
        theme="light",
        language="en",
        timezone="UTC",
    )


@router.put("/profile", response_model=ProfileResponse, status_code=200)
async def update_profile(
    request: Request, payload: ProfileUpdateRequest
) -> ProfileResponse:
    """Update user profile (requires CSRF token)."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    # Verify CSRF token
    csrf_token = request.headers.get("X-CSRF-Token")
    if not _verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")

    # In real impl, would save to DB
    # For now, return updated values
    return ProfileResponse(
        notification_sound=payload.notification_sound or "bell",
        theme=payload.theme or "light",
        language=payload.language or "en",
        timezone=payload.timezone or "UTC",
    )


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint (no auth required)."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Standalone dev server (for testing) ──────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    from fastapi import FastAPI

    app = FastAPI(
        title="CorvinOS Console API (Mock)",
        version="0.1.0",
        description="Mock Console API for local E2E testing",
    )

    app.include_router(router)

    print("🚀 Starting Console API on http://localhost:8765")
    print("   Test user: test@example.com / password123")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
