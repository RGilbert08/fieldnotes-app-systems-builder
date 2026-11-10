"""Authenticated Master Admin command endpoint."""

from __future__ import annotations

import json
import re
import subprocess
from statistics import pstdev
from typing import Annotated, Any, Literal

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.auth import (
    AuthenticationService,
    AuthorizationError,
    InvalidSession,
    can_preview_all_roles,
    extract_session_token,
)
from backend.canvas_layout import (
    CANVAS_VIEWPORTS,
    MasterControlLayoutConfiguration,
    filter_layout_for_role,
    role_key_for_user_role,
    validate_canvas_layout_for_viewport,
    viewport_audience,
)
from backend.authentication.login_gate import (
    SESSION_COOKIE_NAME,
    get_database_session,
)
from backend.dev_access import (
    allow_missing_session_for_development,
    first_active_tenant,
)
from backend.models import (
    DEFAULT_UI_LAYOUT_DECKS,
    CalendarBroadcastEvent,
    CurriculumDeck,
    CurriculumPacingLog,
    EducatorProfile,
    ScholarCognitiveProfile,
    ScholarPlaygroundTelemetryLog,
    TenantScholarTelemetryProfile,
    ScholarProfile,
    SchoolTenant,
    SchoolPersonnelRoleLink,
    SchoolTenantMembership,
    SchoolSubject,
    ScholarParentTokenVault,
    SystemIssueTicket,
    TenantCanvasBlueprint,
    User,
    UserRole,
    generate_parent_registration_token,
)
from backend.normalization import normalize_int, normalize_percentage_int
from backend.reactivation_reviews import reactivation_review_target
from backend.studio_tenant_context import resolve_studio_tenant
from backend.telemetry.curriculum_preflight import MAX_RULES, run_preflight
from backend.models import PedagogicalStandardRule

router = APIRouter(prefix="/api/admin", tags=["admin"])
support_router = APIRouter(prefix="/api/support", tags=["support"])


class CurriculumPreflightRequest(BaseModel):
    sample_text: str | None = Field(default=None, max_length=4000)


@router.post("/tenants/{tenant_id}/curriculum/preflight")
def curriculum_preflight(
    tenant_id: int,
    payload: CurriculumPreflightRequest,
    request: Request,
    session: Session = Depends(get_database_session),
) -> dict[str, Any]:
    """Check only the selected tenant's saved deck, without learning mutations."""
    _authenticated_master_admin(request, session)
    with session.no_autoflush:
        tenant = session.get(SchoolTenant, tenant_id)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=404, detail="Active school tenant not found.")
        deck = session.get(CurriculumDeck, tenant.curriculum_deck_id) if tenant.curriculum_deck_id else None
        result = {
            "tenant_id": tenant.id, "tenant_name": tenant.name,
            "deck_id": deck.id if deck else None,
            "deck_name": deck.name if deck else None,
        }
        if deck is None:
            return {
                **result, "valid": False, "matches": None,
                "errors": [{"category": None, "code": "missing_deck",
                            "message": "This school has no curriculum deck. Assign a deck before teachers record observations."}],
            }
        rows = session.scalars(
            select(PedagogicalStandardRule)
            .where(PedagogicalStandardRule.deck_id == deck.id)
            .order_by(PedagogicalStandardRule.id)
            .limit(MAX_RULES + 1)
        ).all()
        rules = [
            {"trait_category": row.trait_category, "keywords_regex_string": row.keywords_regex_string}
            for row in rows
        ]
    try:
        return {**result, **run_preflight(rules, payload.sample_text)}
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Curriculum check unavailable. Retry the check.") from error

_COMMAND_FIELD_PATTERN = re.compile(
    r"(?:^|\|)\s*(CREATE_TENANT|PLAN|CURRICULUM)\s*:\s*([^|]+)",
    re.IGNORECASE,
)
_CURRICULUM_ALIASES = {
    "caps": "CAPS",
    "ieb": "IEB",
    "cambridge": "Cambridge International",
    "cambridge international": "Cambridge International",
    "ib": "International Baccalaureate (IB)",
    "international baccalaureate": "International Baccalaureate (IB)",
    "international baccalaureate (ib)": "International Baccalaureate (IB)",
}
_SUPPORTED_PLANS = frozenset({"Trial", "Standard Growth", "Enterprise"})


class ExecuteCommandResponse(BaseModel):
    message: str
    tenant: dict[str, object]


class TenantThemePayload(BaseModel):
    primary_color: str = Field(min_length=7, max_length=7)
    secondary_color: str = Field(min_length=7, max_length=7)
    font_family: str = Field(min_length=1, max_length=80)
    logo_url: str | None = Field(default=None, max_length=2048)


class TenantThemeResponse(BaseModel):
    tenant_id: int
    primary_color: str
    secondary_color: str
    font_family: str
    logo_url: str | None


class TenantAdminResponse(BaseModel):
    id: int
    public_id: str
    name: str
    slug: str
    plan: str
    tier_status: str
    ui_layout_deck: str
    contract_terms_text: str
    billing_status: str
    school_phase: str
    is_active: bool
    curriculum_name: str | None
    ui_layout_decks: dict[str, object]


class SubjectAdminResponse(BaseModel):
    id: int
    phase: str
    name: str
    display_order: int
    is_active: bool


class CanvasBlueprintPayload(BaseModel):
    editingTargetView: str = Field(min_length=1, max_length=96)
    layout: MasterControlLayoutConfiguration

    @field_validator("editingTargetView")
    @classmethod
    def normalize_viewport(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in _CANVAS_VIEWPORTS:
            raise ValueError("Unsupported dashboard viewport.")
        return normalized

    @model_validator(mode="after")
    def restrict_principal_container_to_its_viewport(self) -> "CanvasBlueprintPayload":
        validate_canvas_layout_for_viewport(self.layout, self.editingTargetView)
        return self


class CanvasBlueprintResponse(BaseModel):
    id: int
    tenant_id: int
    dashboard_viewport: str
    layout: dict[str, object]
    last_updated_at: datetime


class PrincipalPhaseAnalytics(BaseModel):
    phase: str
    grade_tier: str
    scholar_count: int
    average_analytical_logic: int
    average_fidget_index: int
    average_conative_grit: int


class PrincipalEducatorPerformance(BaseModel):
    educator_id: int
    educator_name: str
    class_grouping: str
    grade_tier: str
    phase: str
    curriculum_standard: str
    subject_name: str
    chapter_title: str
    pacing_status: str
    pacing_alert: str
    pause_reason_code: str | None
    days_delayed: int
    logged_at: datetime


class PrincipalInstitutionalEvent(BaseModel):
    id: int
    title: str
    event_details: str
    creator_id: int
    audience_tier: str
    is_reactive: bool
    tracking_status: str
    created_at: datetime


class ParentTokenRegistryResponse(BaseModel):
    scholar_id: str
    parent_a_status: Literal["ASSIGNED", "AVAILABLE", "NOT_INITIALIZED"]
    parent_b_status: Literal["ASSIGNED", "AVAILABLE", "NOT_INITIALIZED"]


class PrincipalTelemetrySummaryResponse(BaseModel):
    tenant_id: int
    fidget_index: int
    conative_grit: int
    days_delayed: int
    activity_log: list[PrincipalInstitutionalEvent]
    phase_analytics: list[PrincipalPhaseAnalytics]
    educator_performance: list[PrincipalEducatorPerformance]
    credential_registry: list[ParentTokenRegistryResponse] = Field(default_factory=list)


class PrincipalCognitiveAnalyticsRow(BaseModel):
    scholar_id: str
    display_name: str
    grade_tier: str
    grade_phase: str
    cohort_size: int
    has_recent_telemetry: bool
    intelligence_z_scores: dict[str, float | None]
    telemetry_z_scores: dict[str, float | None]
    needs_intervention_dimensions: list[str]


class PrincipalCognitiveAnalyticsResponse(BaseModel):
    cohort_scope: str
    data_window_days: int
    privacy_notice: str
    rows: list[PrincipalCognitiveAnalyticsRow]


def _cohort_z_score(value: float | None, cohort_values: list[float]) -> float | None:
    if value is None or len(cohort_values) < 2:
        return None
    standard_deviation = pstdev(cohort_values)
    if standard_deviation == 0:
        return None
    mean = sum(cohort_values) / len(cohort_values)
    return round((value - mean) / standard_deviation, 2)


class ResetParentTokenRequest(BaseModel):
    scholar_id: str | int
    target_parent_slot: Literal["PARENT_A", "PARENT_B"]

    @field_validator("scholar_id", mode="before")
    @classmethod
    def normalize_scholar_id(cls, value: object) -> str:
        if isinstance(value, bool) or value is None:
            raise ValueError("A scholar ID is required.")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("A scholar ID is required.")
        return normalized


class ResetParentTokenResponse(BaseModel):
    scholar_id: str
    target_parent_slot: Literal["PARENT_A", "PARENT_B"]
    pairing_status: Literal["RESET"]
    token_status: Literal["AVAILABLE"]


class AssistantAssignmentPayload(BaseModel):
    target_staff_user_id: int = Field(gt=0)
    action_status: bool


class AssistantAssignmentResponse(BaseModel):
    message: str
    target_staff_user_id: int
    school_tenant_id: int
    is_assistant_admin: bool


class IssueTicketPayload(BaseModel):
    user_role: str = Field(min_length=1, max_length=40)
    tenant_id: int = Field(gt=0)
    diagnostic_metric: str = Field(min_length=1, max_length=4000)
    ticket_status: str = Field(default="Review", min_length=1, max_length=32)


class IssueTicketStatusPayload(BaseModel):
    status: str = Field(min_length=1, max_length=32)


class IssueTicketResponse(BaseModel):
    id: int
    timestamp: datetime
    user_role: str
    tenant_id: int
    tenant_name: str | None
    diagnostic_metric: str
    ticket_status: str


class IssueTicketListResponse(BaseModel):
    tickets: list[IssueTicketResponse]
    count: int


_ROLE_ALIASES = {"TEACHER": UserRole.EDUCATOR.value}
_TICKET_STATUSES = frozenset({"Review", "Timeout", "Monitor", "Resolved"})
_SUPPORTED_FONT_FAMILIES = frozenset(
    {"DM Sans", "Fraunces", "Georgia", "System UI", "Ubuntu", "Open Sans"}
)
_CANVAS_VIEWPORTS = CANVAS_VIEWPORTS
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_ASSISTANT_ADMIN_TARGET_ROLES = (
    UserRole.EDUCATOR,
    UserRole.GRADE_HOD,
)
_SUBJECT_PHASE_ORDER = {
    "Foundation Phase": 1,
    "Intermediate Phase": 2,
    "Senior Phase": 3,
    "FET Phase (Matric Tiers)": 4,
}
_PHASE_BY_GRADE_NUMBER = {
    0: "Foundation Phase (R-3)",
    1: "Foundation Phase (R-3)",
    2: "Foundation Phase (R-3)",
    3: "Foundation Phase (R-3)",
    4: "Intermediate Phase (4-6)",
    5: "Intermediate Phase (4-6)",
    6: "Intermediate Phase (4-6)",
    7: "Senior Phase (7-9)",
    8: "Senior Phase (7-9)",
    9: "Senior Phase (7-9)",
    10: "FET Phase (10-12)",
    11: "FET Phase (10-12)",
    12: "FET Phase (10-12)",
}


def _principal_phase_for_grade(grade_tier: str) -> str:
    normalized = grade_tier.strip().casefold()
    if normalized in {"r", "grade r", "reception"}:
        return "Foundation Phase (R-3)"
    match = re.search(r"\d+", normalized)
    if match:
        phase = _PHASE_BY_GRADE_NUMBER.get(int(match.group()))
        if phase:
            return phase
    return "Unassigned Phase"


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_provisioning_command(command: str) -> dict[str, str]:
    """Extract the supported provisioning fields from a pipe-delimited command."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("A provisioning command is required.")
    if len(command) > 2000:
        raise ValueError("Provisioning commands must be 2000 characters or fewer.")

    fields: dict[str, str] = {}
    for raw_key, raw_value in _COMMAND_FIELD_PATTERN.findall(command):
        key = raw_key.upper()
        value = raw_value.strip()
        if not value:
            raise ValueError(f"{key} must include a value.")
        if key in fields:
            raise ValueError(f"{key} may only be provided once.")
        fields[key] = value

    missing = [
        key for key in ("CREATE_TENANT", "PLAN", "CURRICULUM") if key not in fields
    ]
    if missing:
        raise ValueError("Missing command fields: " + ", ".join(missing) + ".")
    return fields


def _safe_tenant_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ValueError("CREATE_TENANT must contain a valid school name.")
    return slug[:80]


def _authenticated_user(request: Request, session: Session) -> User:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")
    return AuthenticationService(session).authenticate(token).user


def _authenticated_master_admin(request: Request, session: Session) -> User:
    user = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(user, UserRole.MASTER_ADMIN)
    return user


def _authenticated_principal(request: Request, session: Session) -> User:
    user = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(user, UserRole.PRINCIPAL)
    return user


def _authenticated_principal_viewer(
    request: Request,
    session: Session,
) -> User | None:
    """Allow principals and active delegated assistants to view the overview."""
    user = _authenticated_user(request, session)
    auth = AuthenticationService(session)
    if user.role is UserRole.PRINCIPAL or can_preview_all_roles(user):
        return user
    tenant = auth.resolve_active_school_tenant(user)
    is_assistant = session.scalar(
        select(SchoolPersonnelRoleLink.id).where(
            SchoolPersonnelRoleLink.school_tenant_id == tenant.id,
            SchoolPersonnelRoleLink.user_id == user.id,
            SchoolPersonnelRoleLink.is_assistant_admin.is_(True),
        )
    )
    if is_assistant:
        return user
    raise AuthorizationError("The user cannot view the principal workspace.")


def _principal_viewer_tenant(
    request: Request,
    session: Session,
) -> SchoolTenant:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        if not allow_missing_session_for_development(
            request,
            cookie_name=SESSION_COOKIE_NAME,
        ):
            raise InvalidSession("Invalid session.")
        tenant = first_active_tenant(session)
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No seeded tenant is available for the local preview.",
            )
        return tenant
    principal = _authenticated_principal_viewer(request, session)
    if principal is None:
        raise InvalidSession("Invalid session.")
    return AuthenticationService(session).resolve_active_school_tenant(principal)


def _principal_credential_registry(
    tenant_id: int,
    session: Session,
) -> list[ParentTokenRegistryResponse]:
    rows = session.execute(
        select(User, ScholarParentTokenVault)
        .join(
            SchoolTenantMembership,
            SchoolTenantMembership.user_id == User.id,
        )
        .outerjoin(
            ScholarParentTokenVault,
            ScholarParentTokenVault.scholar_id == User.id,
        )
        .where(
            User.role == UserRole.SCHOLAR,
            User.is_active.is_(True),
            SchoolTenantMembership.school_tenant_id == tenant_id,
            SchoolTenantMembership.is_active.is_(True),
        )
        .order_by(User.public_id, User.id)
    ).all()
    return [
        ParentTokenRegistryResponse(
            scholar_id=scholar.public_id,
            parent_a_status=(
                "NOT_INITIALIZED"
                if vault is None
                else "ASSIGNED"
                if vault.parent_a_user_id is not None
                else "AVAILABLE"
            ),
            parent_b_status=(
                "NOT_INITIALIZED"
                if vault is None
                else "ASSIGNED"
                if vault.parent_b_user_id is not None
                else "AVAILABLE"
            ),
        )
        for scholar, vault in rows
    ]


def _validate_theme_value(payload: TenantThemePayload) -> tuple[str, str, str, str | None]:
    if not _HEX_COLOR_PATTERN.fullmatch(payload.primary_color):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="primary_color must be a six-digit hexadecimal color.",
        )
    if not _HEX_COLOR_PATTERN.fullmatch(payload.secondary_color):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="secondary_color must be a six-digit hexadecimal color.",
        )
    font_family = payload.font_family.strip()
    if font_family not in _SUPPORTED_FONT_FAMILIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="font_family is not supported by the tenant engine.",
        )

    logo_url = payload.logo_url.strip() if payload.logo_url else None
    if logo_url and not (
        logo_url.startswith("https://")
        or logo_url.startswith("http://")
        or logo_url.startswith("/")
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="logo_url must use HTTP(S) or a local asset path.",
        )
    return (
        payload.primary_color.upper(),
        payload.secondary_color.upper(),
        font_family,
        logo_url,
    )


@router.get(
    "/subjects",
    response_model=list[SubjectAdminResponse],
    status_code=status.HTTP_200_OK,
)
def list_subjects(
    request: Request,
    session: Session = Depends(get_database_session),
) -> list[SubjectAdminResponse]:
    """Return the active subject catalog to authorized school workspaces."""
    actor = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(
        actor,
        UserRole.MASTER_ADMIN,
        UserRole.PRINCIPAL,
        UserRole.GRADE_HOD,
        UserRole.EDUCATOR,
    )
    if actor.role is not UserRole.MASTER_ADMIN:
        AuthenticationService(session).resolve_active_school_tenant(actor)
    phase_order = case(
        _SUBJECT_PHASE_ORDER,
        value=SchoolSubject.phase,
        else_=99,
    )
    subjects = session.scalars(
        select(SchoolSubject)
        .where(SchoolSubject.is_active.is_(True))
        .order_by(phase_order, SchoolSubject.display_order, SchoolSubject.name)
    ).all()
    return [
        SubjectAdminResponse(
            id=subject.id,
            phase=subject.phase,
            name=subject.name,
            display_order=subject.display_order,
            is_active=subject.is_active,
        )
        for subject in subjects
    ]


def _canvas_blueprint_response(
    blueprint: TenantCanvasBlueprint,
) -> CanvasBlueprintResponse:
    try:
        layout = json.loads(blueprint.serialized_json_layout)
    except (TypeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored canvas blueprint is not valid JSON.",
        ) from error
    if not isinstance(layout, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored canvas blueprint must be a JSON object.",
        )
    return CanvasBlueprintResponse(
        id=blueprint.id,
        tenant_id=blueprint.tenant_id,
        dashboard_viewport=blueprint.dashboard_viewport,
        layout=layout,
        last_updated_at=blueprint.last_updated_at,
    )


@router.post(
    "/canvas/save-blueprint",
    response_model=CanvasBlueprintResponse,
    status_code=status.HTTP_200_OK,
)
async def save_canvas_blueprint(
    payload: CanvasBlueprintPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> CanvasBlueprintResponse:
    """Create or update a low-code canvas blueprint for the active tenant."""
    master_admin = _authenticated_master_admin(request, session)
    tenant = AuthenticationService(session).resolve_active_school_tenant(master_admin)
    blueprint = session.scalar(
        select(TenantCanvasBlueprint)
        .where(
            TenantCanvasBlueprint.tenant_id == tenant.id,
            TenantCanvasBlueprint.dashboard_viewport == payload.editingTargetView,
        )
        .with_for_update()
    )
    layout = payload.layout.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
    )
    serialized_layout = json.dumps(layout, separators=(",", ":"))
    if len(serialized_layout.encode("utf-8")) > 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The canvas layout exceeds the 1 MB size limit.",
        )
    if blueprint is None:
        blueprint = TenantCanvasBlueprint(
            tenant_id=tenant.id,
            dashboard_viewport=payload.editingTargetView,
            serialized_json_layout=serialized_layout,
        )
        session.add(blueprint)
    else:
        blueprint.serialized_json_layout = serialized_layout

    session.flush()
    session.commit()
    response = _canvas_blueprint_response(blueprint)
    stream_hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if stream_hub is not None:
        await stream_hub.broadcast(
            tenant.id,
            payload.editingTargetView,
            {
                "type": "layout.updated",
                "dashboard_viewport": payload.editingTargetView,
                "layout": response.layout,
                "last_updated_at": response.last_updated_at.isoformat(),
            },
        )
    return response


@router.get(
    "/canvas/load-blueprint",
    response_model=CanvasBlueprintResponse | None,
    status_code=status.HTTP_200_OK,
)
def load_canvas_blueprint(
    request: Request,
    dashboard_viewport: str = Query(..., min_length=1, max_length=96),
    include_hidden: bool = Query(default=False),
    session: Session = Depends(get_database_session),
) -> CanvasBlueprintResponse | None:
    """Load one persisted canvas blueprint inside the signed-in user's tenant."""
    actor = _authenticated_user(request, session)
    normalized_viewport = dashboard_viewport.strip()
    if normalized_viewport not in _CANVAS_VIEWPORTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unsupported dashboard viewport.",
        )
    requested_audience = viewport_audience(normalized_viewport)
    actor_role_key = role_key_for_user_role(actor.role)
    if requested_audience is None or actor_role_key is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This profile cannot load the requested dashboard layout.",
        )
    if include_hidden and actor_role_key != "master_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a master admin can load hidden layout components.",
        )
    effective_role_key = (
        requested_audience
        if actor_role_key == "master_admin"
        else actor_role_key
    )
    if actor_role_key != "master_admin" and actor_role_key != requested_audience:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This profile cannot load the requested dashboard layout.",
        )
    tenant = (
        resolve_studio_tenant(request, session)
        if actor_role_key == "master_admin"
        else AuthenticationService(session).resolve_active_school_tenant(actor)
    )
    blueprint = session.scalar(
        select(TenantCanvasBlueprint).where(
            TenantCanvasBlueprint.tenant_id == tenant.id,
            TenantCanvasBlueprint.dashboard_viewport == normalized_viewport,
        )
    )
    if blueprint is None:
        return None
    response = _canvas_blueprint_response(blueprint)
    try:
        validate_canvas_layout_for_viewport(response.layout, normalized_viewport)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored canvas layout is invalid.",
        ) from error
    if include_hidden:
        return response
    try:
        filtered_layout = filter_layout_for_role(
            response.layout,
            effective_role_key,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored canvas layout is invalid.",
        ) from error
    return response.model_copy(update={"layout": filtered_layout})


@router.get(
    "/principal-telemetry-summary",
    response_model=PrincipalTelemetrySummaryResponse,
    status_code=status.HTTP_200_OK,
)
@router.get(
    "/principal/telemetry-summary",
    response_model=PrincipalTelemetrySummaryResponse,
    status_code=status.HTTP_200_OK,
)
def principal_telemetry_summary(
    request: Request,
    session: Session = Depends(get_database_session),
) -> PrincipalTelemetrySummaryResponse:
    """Return live tenant telemetry, pacing, and institutional events for a principal."""
    tenant = _principal_viewer_tenant(request, session)

    fidget_index, conative_grit, total_delayed_days = session.execute(
        select(
            select(
                func.avg(TenantScholarTelemetryProfile.fidget_index),
            )
            .where(
                TenantScholarTelemetryProfile.school_tenant_id == tenant.id,
            )
            .scalar_subquery(),
            select(
                func.avg(TenantScholarTelemetryProfile.conative_grit),
            )
            .where(
                TenantScholarTelemetryProfile.school_tenant_id == tenant.id,
            )
            .scalar_subquery(),
            select(
                func.coalesce(func.sum(CurriculumPacingLog.days_delayed), 0)
            )
            .where(CurriculumPacingLog.tenant_id == tenant.id)
            .scalar_subquery(),
        )
    ).one()
    phase_analytics_rows = session.execute(
        select(
            ScholarProfile.grade_tier,
            func.count(TenantScholarTelemetryProfile.student_id),
            func.avg(TenantScholarTelemetryProfile.analytical_logic_score),
            func.avg(TenantScholarTelemetryProfile.fidget_index),
            func.avg(TenantScholarTelemetryProfile.conative_grit),
        )
        .join(
            ScholarProfile,
            ScholarProfile.user_id == TenantScholarTelemetryProfile.student_id,
        )
        .where(TenantScholarTelemetryProfile.school_tenant_id == tenant.id)
        .group_by(ScholarProfile.grade_tier)
        .order_by(ScholarProfile.grade_tier)
    ).all()
    phase_analytics = [
        PrincipalPhaseAnalytics(
            phase=_principal_phase_for_grade(row[0]),
            grade_tier=row[0],
            scholar_count=normalize_int(row[1]),
            average_analytical_logic=normalize_int(row[2]),
            average_fidget_index=normalize_percentage_int(row[3]),
            average_conative_grit=normalize_percentage_int(row[4]),
        )
        for row in phase_analytics_rows
    ]
    pacing_rows = session.execute(
        select(
            CurriculumPacingLog,
            User.display_name,
            EducatorProfile.class_identifier,
            SchoolSubject.name,
            select(CurriculumDeck.name)
            .where(CurriculumDeck.id == tenant.curriculum_deck_id)
            .scalar_subquery(),
        )
        .join(User, User.id == CurriculumPacingLog.teacher_id)
        .outerjoin(EducatorProfile, EducatorProfile.user_id == User.id)
        .join(SchoolSubject, SchoolSubject.id == CurriculumPacingLog.subject_id)
        .where(CurriculumPacingLog.tenant_id == tenant.id)
        .order_by(
            desc(CurriculumPacingLog.days_delayed),
            desc(CurriculumPacingLog.logged_at),
            desc(CurriculumPacingLog.id),
        )
        .limit(100)
    ).all()
    educator_performance = [
        PrincipalEducatorPerformance(
            educator_id=pacing.teacher_id,
            educator_name=educator_name,
            class_grouping=class_identifier or pacing.grade_tier,
            grade_tier=pacing.grade_tier,
            phase=_principal_phase_for_grade(pacing.grade_tier),
            curriculum_standard=curriculum_standard or "No curriculum deck assigned",
            subject_name=subject_name,
            chapter_title=pacing.chapter_title,
            pacing_status=pacing.pacing_status,
            pacing_alert=pacing.pause_reason_code or (
                "ON_TRACK" if pacing.pacing_status == "ACTIVE" else "PAUSED"
            ),
            pause_reason_code=pacing.pause_reason_code,
            days_delayed=normalize_int(pacing.days_delayed),
            logged_at=pacing.logged_at,
        )
        for pacing, educator_name, class_identifier, subject_name, curriculum_standard
        in pacing_rows
    ]
    events = session.scalars(
        select(CalendarBroadcastEvent)
        .where(
            CalendarBroadcastEvent.tenant_id == tenant.id,
            CalendarBroadcastEvent.is_active.is_(True),
        )
        .order_by(
            CalendarBroadcastEvent.created_at.desc(),
            CalendarBroadcastEvent.id.desc(),
        )
        .limit(50)
    ).all()

    return PrincipalTelemetrySummaryResponse(
        tenant_id=tenant.id,
        fidget_index=normalize_percentage_int(fidget_index),
        conative_grit=normalize_percentage_int(conative_grit),
        days_delayed=normalize_int(total_delayed_days),
        activity_log=[
            PrincipalInstitutionalEvent(
                id=event.id,
                title=event.title,
                event_details=event.event_details,
                creator_id=event.creator_id,
                audience_tier=event.audience_tier,
                is_reactive=event.is_reactive,
                tracking_status="ACTIVE",
                created_at=event.created_at,
            )
            for event in events
        ],
        phase_analytics=phase_analytics,
        educator_performance=educator_performance,
        credential_registry=_principal_credential_registry(tenant.id, session),
    )


@router.get(
    "/principal-cognitive-analytics",
    response_model=PrincipalCognitiveAnalyticsResponse,
    status_code=status.HTTP_200_OK,
)
def principal_cognitive_analytics(
    request: Request,
    session: Session = Depends(get_database_session),
) -> PrincipalCognitiveAnalyticsResponse:
    """Return grade-cohort Z-score summaries for the authenticated principal."""
    actor = _authenticated_user(request, session)
    auth = AuthenticationService(session)
    auth.require_roles(actor, UserRole.PRINCIPAL)
    tenant = auth.resolve_active_school_tenant(actor)
    tenant_key = str(tenant.id)
    data_window_days = 30
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=data_window_days
    )

    active_scholars = session.execute(
        select(User, ScholarProfile, ScholarCognitiveProfile)
        .join(
            SchoolTenantMembership,
            and_(
                SchoolTenantMembership.user_id == User.id,
                SchoolTenantMembership.school_tenant_id == tenant.id,
                SchoolTenantMembership.is_active.is_(True),
            ),
        )
        .join(ScholarProfile, ScholarProfile.user_id == User.id)
        .outerjoin(
            ScholarCognitiveProfile,
            and_(
                ScholarCognitiveProfile.tenant_id == tenant_key,
                ScholarCognitiveProfile.scholar_id == User.public_id,
            ),
        )
        .where(
            User.role == UserRole.SCHOLAR,
            User.is_active.is_(True),
            User.public_id.is_not(None),
        )
        .order_by(User.display_name, User.id)
    ).all()

    scholar_ids = [user.public_id for user, _, _ in active_scholars]
    telemetry_by_scholar: dict[str, dict[str, Any]] = {}
    if scholar_ids:
        telemetry_rows = session.execute(
            select(
                ScholarPlaygroundTelemetryLog.scholar_id,
                func.count(ScholarPlaygroundTelemetryLog.id),
                func.coalesce(
                    func.sum(ScholarPlaygroundTelemetryLog.errors_encountered_count),
                    0,
                ),
                func.coalesce(
                    func.sum(ScholarPlaygroundTelemetryLog.successful_moves_count),
                    0,
                ),
                func.avg(ScholarPlaygroundTelemetryLog.time_spent_seconds),
            )
            .where(
                ScholarPlaygroundTelemetryLog.tenant_id == tenant_key,
                ScholarPlaygroundTelemetryLog.scholar_id.in_(scholar_ids),
                ScholarPlaygroundTelemetryLog.timestamp >= cutoff,
            )
            .group_by(ScholarPlaygroundTelemetryLog.scholar_id)
        ).all()
        telemetry_by_scholar = {
            scholar_id: {
                "session_count": int(session_count or 0),
                "errors": int(error_count or 0),
                "successes": int(success_count or 0),
                "average_time": (
                    float(average_time) if average_time is not None else None
                ),
            }
            for (
                scholar_id,
                session_count,
                error_count,
                success_count,
                average_time,
            ) in telemetry_rows
        }

    dimensions = {
        "verbal_linguistic": ("verbal_linguistic_score", "Verbal-Linguistic"),
        "logical_mathematical": (
            "logical_mathematical_score",
            "Logical-Mathematical",
        ),
        "visual_spatial": ("visual_spatial_score", "Visual-Spatial"),
    }
    records: list[dict[str, Any]] = []
    for user, scholar_profile, cognitive_profile in active_scholars:
        grade_tier = str(scholar_profile.grade_tier or "Unassigned")
        telemetry = telemetry_by_scholar.get(user.public_id, {})
        attempts = int(telemetry.get("errors", 0)) + int(
            telemetry.get("successes", 0)
        )
        grade_phase = scholar_profile.current_grade_phase
        records.append(
            {
                "scholar_id": user.public_id,
                "display_name": user.display_name or "Scholar",
                "grade_tier": grade_tier,
                "grade_phase": (
                    grade_phase.value if hasattr(grade_phase, "value") else str(grade_phase)
                ),
                "scores": {
                    key: (
                        float(getattr(cognitive_profile, field))
                        if cognitive_profile is not None
                        else None
                    )
                    for key, (field, _) in dimensions.items()
                },
                "velocity": (
                    float(cognitive_profile.processing_velocity_score)
                    if cognitive_profile is not None
                    else None
                ),
                "telemetry": {
                    "error_burden": (
                        int(telemetry.get("errors", 0)) / attempts
                        if attempts
                        else None
                    ),
                    "time_on_task": telemetry.get("average_time"),
                },
                "has_recent_telemetry": bool(telemetry.get("session_count")),
            }
        )

    cohorts: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        cohorts.setdefault(record["grade_tier"], []).append(record)

    result_rows: list[PrincipalCognitiveAnalyticsRow] = []
    for record in records:
        cohort = cohorts[record["grade_tier"]]
        intelligence_z_scores = {
            key: _cohort_z_score(
                record["scores"][key],
                [
                    peer["scores"][key]
                    for peer in cohort
                    if peer["scores"][key] is not None
                ],
            )
            for key in dimensions
        }

        def metric_cohort_values(metric: str) -> list[float]:
            values = []
            for peer in cohort:
                value = (
                    peer[metric]
                    if metric == "velocity"
                    else peer["telemetry"][metric]
                )
                if value is not None:
                    values.append(float(value))
            return values

        telemetry_z_scores = {
            "error_burden": _cohort_z_score(
                record["telemetry"]["error_burden"],
                metric_cohort_values("error_burden"),
            ),
            "time_on_task": _cohort_z_score(
                record["telemetry"]["time_on_task"],
                metric_cohort_values("time_on_task"),
            ),
            "velocity": _cohort_z_score(
                record["velocity"],
                metric_cohort_values("velocity"),
            ),
        }
        needs_intervention = [
            label
            for key, (_, label) in dimensions.items()
            if intelligence_z_scores[key] is not None
            and intelligence_z_scores[key] < -1.0
        ]
        result_rows.append(
            PrincipalCognitiveAnalyticsRow(
                scholar_id=record["scholar_id"],
                display_name=record["display_name"],
                grade_tier=record["grade_tier"],
                grade_phase=record["grade_phase"],
                cohort_size=len(cohort),
                has_recent_telemetry=record["has_recent_telemetry"],
                intelligence_z_scores=intelligence_z_scores,
                telemetry_z_scores=telemetry_z_scores,
                needs_intervention_dimensions=needs_intervention,
            )
        )

    return PrincipalCognitiveAnalyticsResponse(
        cohort_scope="grade_tier",
        data_window_days=data_window_days,
        privacy_notice=(
            "Only grade-cohort Z-score summaries are returned. Individual "
            "error counts and time-on-task durations are not included. "
            "These learning signals are non-clinical and require educator judgment."
        ),
        rows=result_rows,
    )


@router.post(
    "/reset-parent-token",
    response_model=ResetParentTokenResponse,
    status_code=status.HTTP_200_OK,
)
def reset_parent_token(
    payload: ResetParentTokenRequest,
    request: Request,
    session: Session = Depends(get_database_session),
) -> ResetParentTokenResponse:
    """Clear one parent pairing and rotate its one-time registration token."""
    principal = _authenticated_principal(request, session)
    tenant = AuthenticationService(session).resolve_active_school_tenant(principal)

    scholar_identity = payload.scholar_id
    identity_filter = (
        or_(
            User.id == int(scholar_identity),
            User.public_id == scholar_identity,
        )
        if scholar_identity.isdigit()
        else User.public_id == scholar_identity
    )
    scholar = session.scalar(
        select(User)
        .join(
            SchoolTenantMembership,
            SchoolTenantMembership.user_id == User.id,
        )
        .where(
            identity_filter,
            User.role == UserRole.SCHOLAR,
            User.is_active.is_(True),
            SchoolTenantMembership.school_tenant_id == tenant.id,
            SchoolTenantMembership.is_active.is_(True),
        )
        .with_for_update()
    )
    if scholar is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The scholar is not active in the principal's school tenant.",
        )

    vault = session.scalar(
        select(ScholarParentTokenVault)
        .where(ScholarParentTokenVault.scholar_id == scholar.id)
        .with_for_update()
    )
    if vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The scholar does not have an initialized parent token vault.",
        )

    if payload.target_parent_slot == "PARENT_A":
        vault.parent_a_user_id = None
        vault.parent_token_a = generate_parent_registration_token()
    else:
        vault.parent_b_user_id = None
        vault.parent_token_b = generate_parent_registration_token()

    session.flush()
    return ResetParentTokenResponse(
        scholar_id=scholar.public_id,
        target_parent_slot=payload.target_parent_slot,
        pairing_status="RESET",
        token_status="AVAILABLE",
    )


@router.post(
    "/assign-assistant",
    response_model=AssistantAssignmentResponse,
    status_code=status.HTTP_200_OK,
)
def assign_assistant_admin(
    payload: AssistantAssignmentPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> AssistantAssignmentResponse:
    """Assign or revoke one of two tenant-scoped assistant administrator slots."""
    principal = _authenticated_principal(request, session)
    auth = AuthenticationService(session)
    tenant = auth.resolve_active_school_tenant(principal)

    # Serialize all assistant assignments for this tenant on the tenant row.
    locked_tenant = session.scalar(
        select(SchoolTenant)
        .where(SchoolTenant.id == tenant.id)
        .with_for_update()
    )
    if locked_tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The principal's active school tenant is unavailable.",
        )

    target = session.scalar(
        select(User)
        .join(
            SchoolTenantMembership,
            SchoolTenantMembership.user_id == User.id,
        )
        .where(
            User.id == payload.target_staff_user_id,
            User.is_active.is_(True),
            User.role.in_(_ASSISTANT_ADMIN_TARGET_ROLES),
            SchoolTenantMembership.school_tenant_id == tenant.id,
            SchoolTenantMembership.is_active.is_(True),
        )
        .with_for_update()
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The target staff member is not available in this school tenant.",
        )

    link = session.scalar(
        select(SchoolPersonnelRoleLink)
        .where(
            SchoolPersonnelRoleLink.school_tenant_id == tenant.id,
            SchoolPersonnelRoleLink.user_id == target.id,
        )
        .with_for_update()
    )
    current_status = bool(link and link.is_assistant_admin)

    if payload.action_status and not current_status:
        assistant_count = session.scalar(
            select(func.count())
            .select_from(SchoolPersonnelRoleLink)
            .where(
                SchoolPersonnelRoleLink.school_tenant_id == tenant.id,
                SchoolPersonnelRoleLink.is_assistant_admin.is_(True),
            )
        ) or 0
        if assistant_count >= 2:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assistant administrator capacity is limited to two active staff members per school tenant.",
            )

    if link is None:
        if not payload.action_status:
            return AssistantAssignmentResponse(
                message="Assistant administrator status is already inactive.",
                target_staff_user_id=target.id,
                school_tenant_id=tenant.id,
                is_assistant_admin=False,
            )
        link = SchoolPersonnelRoleLink(
            school_tenant_id=tenant.id,
            user_id=target.id,
            is_assistant_admin=True,
            assigned_by_principal_id=principal.id,
        )
        session.add(link)
    else:
        link.is_assistant_admin = payload.action_status
        if payload.action_status:
            link.assigned_by_principal_id = principal.id

    session.flush()
    return AssistantAssignmentResponse(
        message=(
            "Assistant administrator assigned."
            if payload.action_status
            else "Assistant administrator assignment revoked."
        ),
        target_staff_user_id=target.id,
        school_tenant_id=tenant.id,
        is_assistant_admin=payload.action_status,
    )


@router.get(
    "/tenants",
    response_model=list[TenantAdminResponse],
    status_code=status.HTTP_200_OK,
)
def list_tenants(
    request: Request,
    session: Session = Depends(get_database_session),
) -> list[TenantAdminResponse]:
    """Return live tenant rows for the Master Admin culture console."""
    _authenticated_master_admin(request, session)
    tenants = session.scalars(
        select(SchoolTenant)
        .options(joinedload(SchoolTenant.curriculum_deck))
        .where(SchoolTenant.is_active.is_(True))
        .order_by(SchoolTenant.id)
    ).all()
    return [
        TenantAdminResponse(
            id=tenant.id,
            public_id=tenant.public_id,
            name=tenant.name,
            slug=tenant.slug,
            plan=tenant.plan,
            tier_status=tenant.tier_status,
            ui_layout_deck=tenant.ui_layout_deck,
            contract_terms_text=tenant.contract_terms_text,
            billing_status=tenant.billing_status,
            school_phase=tenant.school_phase,
            is_active=tenant.is_active,
            curriculum_name=(
                tenant.curriculum_deck.name
                if tenant.curriculum_deck is not None
                else None
            ),
            ui_layout_decks=dict(tenant.ui_layout_decks or {}),
        )
        for tenant in tenants
    ]


@router.post(
    "/execute-command",
    response_model=ExecuteCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
def execute_command(
    command: Annotated[str, Body(media_type="text/plain")],
    request: Request,
    session: Session = Depends(get_database_session),
) -> ExecuteCommandResponse:
    """Provision one tenant from a validated Master Admin command."""
    _authenticated_master_admin(request, session)
    try:
        fields = parse_provisioning_command(command)
        school_name = fields["CREATE_TENANT"]
        plan = fields["PLAN"]
        curriculum_key = fields["CURRICULUM"].casefold()
        curriculum_name = _CURRICULUM_ALIASES.get(
            curriculum_key,
            fields["CURRICULUM"],
        )
        if plan not in _SUPPORTED_PLANS:
            raise ValueError(
                "PLAN must be Trial, Standard Growth, or Enterprise."
            )
        if len(school_name) > 160 or len(plan) > 48:
            raise ValueError("Tenant name or plan exceeds the supported length.")
        if any(ord(character) < 32 for character in school_name + plan):
            raise ValueError("Tenant name and plan contain unsupported characters.")
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    curriculum_deck = session.scalar(
        select(CurriculumDeck).where(CurriculumDeck.name == curriculum_name)
    )
    if curriculum_deck is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Requested curriculum deck is not configured.",
        )

    try:
        tenant = SchoolTenant(
            name=school_name,
            slug=_safe_tenant_slug(school_name),
            plan=plan,
            ui_layout_decks=dict(DEFAULT_UI_LAYOUT_DECKS),
            curriculum_deck=curriculum_deck,
        )
        session.add(tenant)
        session.flush()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with that school name already exists.",
        ) from error

    return ExecuteCommandResponse(
        message=(
            f"SUCCESS: Tenant '{tenant.name}' provisioned with "
            f"{tenant.plan} and {curriculum_deck.name} curriculum."
        ),
        tenant={
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "curriculum_deck_id": curriculum_deck.id,
            "curriculum_name": curriculum_deck.name,
            "is_active": tenant.is_active,
            "ui_layout_decks": tenant.ui_layout_decks,
        },
    )


@router.put(
    "/tenants/{tenant_id}/theme",
    response_model=TenantThemeResponse,
    status_code=status.HTTP_200_OK,
)
def update_tenant_theme(
    tenant_id: int,
    payload: TenantThemePayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> TenantThemeResponse:
    """Persist one tenant's visual design tokens for the Master Admin."""
    _authenticated_master_admin(request, session)
    primary_color, secondary_color, font_family, logo_url = _validate_theme_value(
        payload
    )
    tenant = session.get(SchoolTenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested school tenant was not found.",
        )

    layout = dict(tenant.ui_layout_decks or {})
    layout.update(
        {
            "primary_color": primary_color,
            "secondary_color": secondary_color,
            "font_family": font_family,
            "logo_url": logo_url,
            # Keep the existing visual-engine key in sync for older readers.
            "crest_url": logo_url,
        }
    )
    tenant.ui_layout_decks = layout
    session.flush()
    return TenantThemeResponse(
        tenant_id=tenant.id,
        primary_color=primary_color,
        secondary_color=secondary_color,
        font_family=font_family,
        logo_url=logo_url,
    )


def _normalized_ticket_role(value: str) -> str:
    normalized = value.strip().upper()
    normalized = _ROLE_ALIASES.get(normalized, normalized)
    try:
        return UserRole(normalized).value
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="user_role must be one of the supported application roles.",
        ) from error


def _issue_ticket_response(ticket: SystemIssueTicket) -> IssueTicketResponse:
    return IssueTicketResponse(
        id=ticket.id,
        timestamp=ticket.timestamp,
        user_role=(
            UserRole.PRINCIPAL.value
            if reactivation_review_target(ticket.user_role) is not None
            else ticket.user_role
        ),
        tenant_id=ticket.tenant_id,
        tenant_name=ticket.school_tenant.name,
        diagnostic_metric=ticket.diagnostic_metric,
        ticket_status=ticket.ticket_status,
    )


@support_router.post(
    "/log-issue",
    response_model=IssueTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def log_issue(
    payload: IssueTicketPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> IssueTicketResponse:
    """Persist an authenticated, tenant-linked support issue."""
    actor = _authenticated_user(request, session)
    auth = AuthenticationService(session)
    actor_role = actor.role.value
    requested_role = _normalized_ticket_role(payload.user_role)

    if actor_role != UserRole.MASTER_ADMIN.value and requested_role != actor_role:
        raise AuthorizationError(
            "A user may only log an issue for their own application role."
        )

    tenant = session.get(SchoolTenant, payload.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested school tenant was not found.",
        )
    if actor_role != UserRole.MASTER_ADMIN.value:
        active_tenant = auth.resolve_active_school_tenant(actor)
        if active_tenant.id != tenant.id:
            raise AuthorizationError(
                "A user may only log an issue for their active school tenant."
            )

    ticket_status = payload.ticket_status.strip()
    if ticket_status not in _TICKET_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="ticket_status must be Review, Timeout, Monitor, or Resolved.",
        )

    ticket = SystemIssueTicket(
        user_role=requested_role,
        tenant_id=tenant.id,
        diagnostic_metric=payload.diagnostic_metric.strip(),
        ticket_status=ticket_status,
    )
    session.add(ticket)
    session.flush()
    return _issue_ticket_response(ticket)


@support_router.patch(
    "/tickets/{ticket_id}/status",
    response_model=IssueTicketResponse,
    status_code=status.HTTP_200_OK,
)
def update_ticket_status(
    ticket_id: int,
    payload: IssueTicketStatusPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> IssueTicketResponse:
    """Update one ticket status from the Master Admin troubleshooting grid."""
    _authenticated_master_admin(request, session)
    next_status = payload.status.strip()
    if next_status not in _TICKET_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="status must be Review, Monitor, Timeout, or Resolved.",
        )

    ticket = session.get(SystemIssueTicket, ticket_id)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Support ticket not found.",
        )
    ticket.ticket_status = next_status
    session.flush()
    return _issue_ticket_response(ticket)


@support_router.get(
    "/tickets",
    response_model=IssueTicketListResponse,
    status_code=status.HTTP_200_OK,
)
def list_issue_tickets(
    request: Request,
    session: Session = Depends(get_database_session),
) -> IssueTicketListResponse:
    """Return the newest support issues for the Master Admin troubleshooting hub."""
    _authenticated_master_admin(request, session)
    tickets = session.scalars(
        select(SystemIssueTicket)
        .options(joinedload(SystemIssueTicket.school_tenant))
        .order_by(desc(SystemIssueTicket.timestamp), desc(SystemIssueTicket.id))
        .limit(200)
    ).all()
    result = [
        _issue_ticket_response(ticket)
        for ticket in tickets
    ]
    return IssueTicketListResponse(tickets=result, count=len(result))