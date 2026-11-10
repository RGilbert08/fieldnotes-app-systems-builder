"""Narrow development-only access helpers for local read-only dashboards."""

from __future__ import annotations

import os

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import SchoolTenant

LOCAL_DEVELOPMENT_ORIGINS = frozenset(
    {
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5000",
        "http://127.0.0.1:5173",
    }
)


def is_local_development_request(request: Request) -> bool:
    """Recognize explicit development mode, loopback, or Replit preview requests."""
    if os.getenv("PYTHON_ENV", "").strip().casefold() == "development":
        return True

    origin = request.headers.get("origin", "").rstrip("/")
    if origin in LOCAL_DEVELOPMENT_ORIGINS:
        return True

    hostname = (request.url.hostname or "").casefold()
    return (
        hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith(".replit.dev")
    )


def allow_missing_session_for_development(
    request: Request,
    *,
    cookie_name: str,
) -> bool:
    """Allow only credential-free read-only previews in a local workspace.

    A malformed or invalid credential must still go through normal
    authentication and fail. This helper is therefore intentionally stricter
    than checking whether token extraction returned a value.
    """
    authorization = request.headers.get("Authorization", "").strip()
    cookie = request.cookies.get(cookie_name, "").strip()
    return not authorization and not cookie and is_local_development_request(request)


def first_active_tenant(session: Session) -> SchoolTenant | None:
    """Return the deterministic seeded tenant used by local read-only previews."""
    return session.scalar(
        select(SchoolTenant)
        .where(SchoolTenant.is_active.is_(True))
        .order_by(SchoolTenant.id)
        .limit(1)
    )