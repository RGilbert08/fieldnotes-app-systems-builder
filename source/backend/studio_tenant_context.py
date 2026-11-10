"""Master Admin tenant selection for tenant-scoped Studio records."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import AuthenticationService, InvalidSession, extract_session_token
from backend.authentication.login_gate import SESSION_COOKIE_NAME
from backend.models import SchoolTenant, User, UserRole

STUDIO_TENANT_HEADER = "X-Fieldnotes-Studio-Tenant"
STUDIO_WEBSITE_OWNER_HEADER = "X-Fieldnotes-Studio-Website-Owner"


def authenticate_studio_master_admin(request: Request, session: Session) -> User:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")
    authentication = AuthenticationService(session)
    actor = authentication.authenticate(token)
    authentication.require_roles(actor.user, UserRole.MASTER_ADMIN)
    return actor.user


def list_active_studio_tenants(session: Session) -> list[SchoolTenant]:
    return list(
        session.scalars(
            select(SchoolTenant)
            .where(SchoolTenant.is_active.is_(True))
            .order_by(SchoolTenant.name, SchoolTenant.id)
        ).all()
    )


def studio_tenant_options(
    tenants: list[SchoolTenant],
) -> list[dict[str, int | str]]:
    return [
        {"tenantId": tenant.id, "tenantName": tenant.name}
        for tenant in tenants
    ]


def build_studio_tenant_context(
    request: Request,
    tenants: list[SchoolTenant],
) -> dict[str, object]:
    selected_id: int | None = None
    raw_selected_id = request.headers.get(STUDIO_TENANT_HEADER, "").strip()
    if raw_selected_id:
        try:
            requested_id = int(raw_selected_id)
        except ValueError:
            requested_id = None
        if requested_id is not None and any(
            tenant.id == requested_id for tenant in tenants
        ):
            selected_id = requested_id
    elif len(tenants) == 1:
        selected_id = tenants[0].id

    return {
        "tenants": studio_tenant_options(tenants),
        "selectedTenantId": selected_id,
        "selectionRequired": len(tenants) > 1 and selected_id is None,
    }


def resolve_studio_tenant(request: Request, session: Session) -> SchoolTenant:
    tenants = list_active_studio_tenants(session)
    options = studio_tenant_options(tenants)
    raw_selected_id = request.headers.get(STUDIO_TENANT_HEADER, "").strip()
    if raw_selected_id:
        try:
            requested_id = int(raw_selected_id)
        except ValueError:
            requested_id = None
        selected = next(
            (tenant for tenant in tenants if tenant.id == requested_id),
            None,
        )
        if selected is not None:
            return selected

    if not raw_selected_id and len(tenants) == 1:
        return tenants[0]

    if not tenants:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "studio_no_active_tenants",
                "message": "No active schools are available for Studio data.",
                "tenants": options,
            },
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "studio_tenant_selection_required",
            "message": "Choose an active school to load Studio data.",
            "tenants": options,
        },
    )


def resolve_studio_website_owner(
    request: Request,
    session: Session,
    *,
    authenticated_user: User | None = None,
) -> int | None:
    """Return the tenant ID, or None for the explicitly selected platform scope."""
    if authenticated_user is None:
        authenticate_studio_master_admin(request, session)
    raw_scope = request.headers.get(STUDIO_WEBSITE_OWNER_HEADER, "").strip()
    if raw_scope:
        if raw_scope.lower() != "platform":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported website owner scope.",
            )
        return None
    return resolve_studio_tenant(request, session).id
