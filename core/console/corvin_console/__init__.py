"""corvin-console — owner-self-service web UI for Corvin.

Owner-tier (single-tenant, semantic / "what is my machine doing").

Mounted onto the gateway's ASGI app under:
  * /v1/console/* (REST API)
  * /console/*    (React SPA — web-next/dist)

See README.md for the architecture summary and the
``outputs/corvin-console-konzept.md`` document for the full
design rationale.
"""
from __future__ import annotations


# Windows compatibility shim — MUST be the very first import so the no-op
# fcntl/resource stand-ins land in sys.modules before any submodule (or vendored
# operator subtree) does a module-level ``import fcntl``. No-op on POSIX.
from . import _wincompat  # noqa: F401  (import side-effect installs the shim)

# Wheel-install operator-dependency bootstrap (must run before any submodule
# import).  In a source-tree checkout this is a no-op; in a wheel install it
# makes the vendored ``operator/`` subtrees importable so the per-module
# ``from forge import paths`` / ``import engine_switch`` / ``import
# license.validator`` injections resolve.  See _operator_bootstrap.py.
from ._operator_bootstrap import ensure_operator_on_path as _ensure_operator_on_path

# MUST run before ``from . import _bootstrap``: _bootstrap eagerly executes a
# module-level ``from forge import paths``, and on a WHEEL install the vendored
# ``operator/`` subtrees are only put on sys.path by ensure_operator_on_path().
# Importing _bootstrap first left _bootstrap.forge_paths permanently None, so
# every route module that captured ``_forge_paths = _bootstrap.forge_paths`` at
# import time crashed with 500 on a fresh ``pip install`` (round-7 #5).
_ensure_operator_on_path()

from . import _bootstrap  # noqa: F401,E402  (source-tree sys.path injection)

__version__ = "0.1.4"
