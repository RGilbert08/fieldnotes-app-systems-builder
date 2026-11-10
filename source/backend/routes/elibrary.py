"""Tenant-scoped E-Library catalog APIs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.auth import (
    AuthenticationService,
    AuthorizationError,
    InvalidSession,
    extract_session_token,
)
from backend.authentication.login_gate import (
    SESSION_COOKIE_NAME,
    get_database_session,
)
from backend.canvas_layout import (
    MasterControlLayoutConfiguration,
    iter_canvas_components,
    validate_canvas_layout_for_viewport,
)
from backend.dev_access import allow_missing_session_for_development, first_active_tenant
from backend.models import (
    DigitalAssetVault,
    ELibraryCatalog,
    ResourceAccessTier,
    SchoolTenant,
    SouthAfricanGradePhase,
    TenantCanvasBlueprint,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/library", tags=["e-library"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin", "e-library"])

_LIBRARY_ROLES = (
    UserRole.MASTER_ADMIN,
    UserRole.PRINCIPAL,
    UserRole.GRADE_HOD,
    UserRole.EDUCATOR,
    UserRole.SCHOLAR,
    UserRole.PARENT,
)


class LibraryAssetResponse(BaseModel):
    asset_id: str
    tenant_id: int
    target_phase: str
    genre_classification: str
    curriculum_term: int
    title: str
    author: str
    description: str | None
    cover_image_vector: str | None
    content_document_text: str
    access_tier: str
    is_active: bool
    license_identifier: str | None
    created_at: datetime


class ELibraryCatalogPreviewResponse(BaseModel):
    catalog_id: int
    tenant_id: int
    grade_level: str
    phase_scope: str
    title: str
    author: str
    description: str
    preview_url: str
    cover_image_url: str | None
    rights_state: str
    access_state: str
    access_state_label: str
    digital_access_token_label: str
    rating: float = Field(ge=0, le=5)
    rating_count: int = Field(ge=0)
    created_at: datetime


class LibrarySchemaFieldResponse(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool


class LibrarySchemaResponse(BaseModel):
    table_name: str
    fields: list[LibrarySchemaFieldResponse]


class CanvasLibraryBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=160)


class CanvasLibraryAssetMetadata(BaseModel):
    asset_id: str
    target_phase: str
    genre_classification: str
    curriculum_term: int
    title: str
    author: str
    description: str | None


class CanvasLibraryBindingResponse(BaseModel):
    execution_status: Literal["completed"]
    node_id: str
    grade_phase: str
    assets: list[CanvasLibraryAssetMetadata]


class UploadAssetPayload(BaseModel):
    target_phase: str = Field(min_length=1, max_length=50)
    genre_classification: str = Field(min_length=1, max_length=100)
    curriculum_term: int = Field(default=1, ge=1, le=4)
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    cover_image_vector: str | None = Field(default=None, max_length=10000)
    content_document_text: str = Field(min_length=1, max_length=200000)
    access_tier: Literal["STANDARD_UNBLOCKED"] = (
        ResourceAccessTier.STANDARD_UNBLOCKED.value
    )
    license_identifier: str | None = Field(default=None, max_length=100)

    @field_validator("target_phase")
    @classmethod
    def validate_target_phase(cls, value: str) -> str:
        normalized = value.strip().upper()
        try:
            return SouthAfricanGradePhase(normalized).value
        except ValueError as error:
            raise ValueError("target_phase must be a supported South African phase.") from error

    @field_validator("genre_classification", "title", "author", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Text fields must be strings.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Text fields cannot be blank.")
        return normalized

    @field_validator("content_document_text", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("content_document_text must be a string.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("content_document_text cannot be blank.")
        return normalized

    @field_validator("license_identifier", mode="before")
    @classmethod
    def normalize_license_identifier(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("license_identifier must be a string.")
        normalized = value.strip()
        return normalized or None


class UpdateAssetPayload(UploadAssetPayload):
    is_active: bool | None = None


def _asset_response(asset: DigitalAssetVault) -> LibraryAssetResponse:
    return LibraryAssetResponse(
        asset_id=asset.asset_id,
        tenant_id=asset.tenant_id,
        target_phase=asset.target_phase,
        genre_classification=asset.genre_classification,
        curriculum_term=asset.curriculum_term,
        title=asset.title,
        author=asset.author,
        description=asset.description,
        cover_image_vector=asset.cover_image_vector,
        content_document_text=asset.content_document_text,
        access_tier=asset.access_tier,
        is_active=asset.is_active,
        license_identifier=asset.license_identifier,
        created_at=asset.created_at,
    )


def _authenticated_user(request: Request, session: Session) -> User:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")
    return AuthenticationService(session).authenticate(token).user


def _active_library_tenant(request: Request, session: Session) -> tuple[User | None, SchoolTenant]:
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
        return None, tenant

    actor = _authenticated_user(request, session)
    auth = AuthenticationService(session)
    auth.require_roles(actor, *_LIBRARY_ROLES)
    if actor.role is UserRole.MASTER_ADMIN:
        tenant = first_active_tenant(session)
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No active school tenant is available.",
            )
        return actor, tenant
    return actor, auth.resolve_active_school_tenant(actor)


@router.get(
    "/browse-catalog",
    response_model=list[LibraryAssetResponse],
    status_code=status.HTTP_200_OK,
)
def browse_catalog(
    request: Request,
    grade_phase: str | None = Query(default=None, max_length=50),
    session: Session = Depends(get_database_session),
) -> list[LibraryAssetResponse]:
    """Return only the active tenant's catalog for the requested learning phase."""
    actor, tenant = _active_library_tenant(request, session)
    requested_phase = grade_phase.strip().upper() if grade_phase else None
    if requested_phase is None and actor is not None:
        scholar_phase = (
            actor.scholar_profile.current_grade_phase
            if actor.role is UserRole.SCHOLAR and actor.scholar_profile is not None
            else None
        )
        requested_phase = (
            scholar_phase.value
            if isinstance(scholar_phase, SouthAfricanGradePhase)
            else str(scholar_phase).upper()
            if scholar_phase
            else SouthAfricanGradePhase.FOUNDATION_PHASE.value
        )
    requested_phase = requested_phase or SouthAfricanGradePhase.FOUNDATION_PHASE.value
    try:
        normalized_phase = SouthAfricanGradePhase(requested_phase).value
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="grade_phase must be a supported South African phase.",
        ) from error

    assets = session.scalars(
        select(DigitalAssetVault)
        .where(
            DigitalAssetVault.tenant_id == tenant.id,
            DigitalAssetVault.target_phase == normalized_phase,
            DigitalAssetVault.is_active.is_(True),
        )
        .order_by(DigitalAssetVault.title, DigitalAssetVault.asset_id)
    ).all()
    return [_asset_response(asset) for asset in assets]


@router.post(
    "/canvas-binding/execute",
    response_model=CanvasLibraryBindingResponse,
    status_code=status.HTTP_200_OK,
)
def execute_canvas_library_binding(
    payload: CanvasLibraryBindingRequest,
    request: Request,
    session: Session = Depends(get_database_session),
) -> CanvasLibraryBindingResponse:
    """Execute one saved E-Library node within the signed-in scholar's scope."""
    actor = _authenticated_user(request, session)
    auth = AuthenticationService(session)
    auth.require_roles(actor, UserRole.SCHOLAR)
    if actor.role is not UserRole.SCHOLAR or actor.scholar_profile is None:
        raise AuthorizationError("An active scholar profile is required.")

    tenant = auth.resolve_active_school_tenant(actor)
    phase_value = actor.scholar_profile.current_grade_phase
    try:
        phase = SouthAfricanGradePhase(phase_value).value
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The scholar's grade phase is unavailable.",
        ) from error

    blueprint = session.scalar(
        select(TenantCanvasBlueprint).where(
            TenantCanvasBlueprint.tenant_id == tenant.id,
            TenantCanvasBlueprint.dashboard_viewport == "Scholar Simulation Portal",
        )
    )
    if blueprint is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No saved scholar canvas is available to execute.",
        )
    try:
        configuration = MasterControlLayoutConfiguration.model_validate(
            json.loads(blueprint.serialized_json_layout)
        )
        validate_canvas_layout_for_viewport(
            configuration,
            "Scholar Simulation Portal",
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The saved scholar canvas cannot be executed.",
        ) from error

    matching_nodes = [
        component
        for component in iter_canvas_components(configuration)
        if component.id == payload.node_id
    ]
    if len(matching_nodes) != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested E-Library node is not in the saved scholar canvas.",
        )
    node = matching_nodes[0]
    if (
        node.type != "ELibraryDataNode"
        or node.nodeDefinition != "ELibraryDataNode"
        or node.tenantFilterBinding != "ACTIVE_TENANT_ID"
        or node.metadataStreamBinding != "ELIBRARY_ASSET_METADATA_V1"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The E-Library node is not fully configured for execution.",
        )

    try:
        assets = session.scalars(
            select(DigitalAssetVault)
            .where(
                DigitalAssetVault.tenant_id == tenant.id,
                DigitalAssetVault.target_phase == phase,
                DigitalAssetVault.is_active.is_(True),
                DigitalAssetVault.access_tier
                == ResourceAccessTier.STANDARD_UNBLOCKED.value,
            )
            .order_by(DigitalAssetVault.title, DigitalAssetVault.asset_id)
        ).all()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The E-Library metadata stream could not be executed.",
        ) from error

    return CanvasLibraryBindingResponse(
        execution_status="completed",
        node_id=node.id,
        grade_phase=phase,
        assets=[
            CanvasLibraryAssetMetadata(
                asset_id=asset.asset_id,
                target_phase=asset.target_phase,
                genre_classification=asset.genre_classification,
                curriculum_term=asset.curriculum_term,
                title=asset.title,
                author=asset.author,
                description=asset.description,
            )
            for asset in assets
        ],
    )


def _normalize_preview_grade(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = " ".join(value.strip().split())
    lowered = normalized.casefold()
    if lowered in {"r", "grade r", "gr r", "reception"}:
        return "Grade R"
    if lowered.startswith("grade ") and lowered[6:].isdigit():
        grade = int(lowered[6:])
    elif lowered.startswith("gr ") and lowered[3:].isdigit():
        grade = int(lowered[3:])
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="grade_level must be Grade R or Grade 1 through Grade 12.",
        )
    if not 1 <= grade <= 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="grade_level must be Grade R or Grade 1 through Grade 12.",
        )
    return f"Grade {grade}"


def _catalog_preview_response(
    entry: ELibraryCatalog,
) -> ELibraryCatalogPreviewResponse:
    return ELibraryCatalogPreviewResponse(
        catalog_id=entry.id,
        tenant_id=entry.tenant_id,
        grade_level=entry.grade_level,
        phase_scope=entry.phase_scope,
        title=entry.title,
        author=entry.author,
        description=entry.description,
        preview_url=entry.preview_url,
        cover_image_url=entry.cover_image_url,
        rights_state=entry.rights_state,
        access_state=entry.access_state,
        access_state_label=entry.access_state_label,
        digital_access_token_label=entry.digital_access_token_label.replace(
            "SEED_PREVIEW_", "Digital access · "
        ),
        rating=float(entry.rating),
        rating_count=entry.rating_count,
        created_at=entry.created_at,
    )


@router.get(
    "/catalog-preview",
    response_model=list[ELibraryCatalogPreviewResponse],
    status_code=status.HTTP_200_OK,
)
def catalog_preview(
    request: Request,
    grade_level: str | None = Query(default=None, max_length=48),
    session: Session = Depends(get_database_session),
) -> list[ELibraryCatalogPreviewResponse]:
    """Return active preview metadata matched to a requested grade level."""
    actor, tenant = _active_library_tenant(request, session)
    normalized_grade = _normalize_preview_grade(grade_level)
    if normalized_grade is None and actor is not None:
        profile = (
            actor.scholar_profile
            if actor.role is UserRole.SCHOLAR
            else actor.educator_profile
            if actor.role is UserRole.EDUCATOR
            else actor.grade_hod_profile
            if actor.role is UserRole.GRADE_HOD
            else None
        )
        if profile is not None and getattr(profile, "grade_tier", None):
            try:
                normalized_grade = _normalize_preview_grade(profile.grade_tier)
            except HTTPException:
                normalized_grade = None

    query = select(ELibraryCatalog).where(
        ELibraryCatalog.tenant_id == tenant.id,
        ELibraryCatalog.is_active.is_(True),
    )
    if normalized_grade is not None:
        query = query.where(ELibraryCatalog.grade_level == normalized_grade)
    entries = session.scalars(
        query.order_by(ELibraryCatalog.grade_level, ELibraryCatalog.title)
    ).all()
    return [_catalog_preview_response(entry) for entry in entries]


@admin_router.get(
    "/library-schema",
    response_model=LibrarySchemaResponse,
    status_code=status.HTTP_200_OK,
)
def library_schema(
    request: Request,
    session: Session = Depends(get_database_session),
) -> LibrarySchemaResponse:
    """Inspect the actual E-Library asset table for the master-admin audit."""
    actor = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.MASTER_ADMIN)
    table_name = DigitalAssetVault.__tablename__
    try:
        columns = inspect(session.get_bind()).get_columns(table_name)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The E-Library table schema is unavailable.",
        ) from error
    if not columns:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The E-Library table schema is unavailable.",
        )
    return LibrarySchemaResponse(
        table_name=table_name,
        fields=[
            LibrarySchemaFieldResponse(
                name=column["name"],
                type=str(column["type"]),
                nullable=column["nullable"],
                primary_key=column["primary_key"],
            )
            for column in columns
        ],
    )


def _master_admin_asset(
    asset_id: str,
    request: Request,
    session: Session,
) -> tuple[User, SchoolTenant, DigitalAssetVault]:
    actor = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.MASTER_ADMIN)
    tenant = first_active_tenant(session)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active school tenant is available.",
        )
    asset = session.scalar(
        select(DigitalAssetVault).where(
            DigitalAssetVault.asset_id == asset_id,
            DigitalAssetVault.tenant_id == tenant.id,
        )
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The E-Library asset is not available in the active tenant.",
        )
    return actor, tenant, asset


@admin_router.get(
    "/library-assets",
    response_model=list[LibraryAssetResponse],
    status_code=status.HTTP_200_OK,
)
def list_library_assets(
    request: Request,
    session: Session = Depends(get_database_session),
) -> list[LibraryAssetResponse]:
    """Return active and retired assets for the master-admin registry."""
    actor = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.MASTER_ADMIN)
    tenant = first_active_tenant(session)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active school tenant is available.",
        )
    assets = session.scalars(
        select(DigitalAssetVault)
        .where(DigitalAssetVault.tenant_id == tenant.id)
        .order_by(DigitalAssetVault.is_active.desc(), DigitalAssetVault.title)
    ).all()
    return [_asset_response(asset) for asset in assets]


@admin_router.patch(
    "/library-assets/{asset_id}",
    response_model=LibraryAssetResponse,
    status_code=status.HTTP_200_OK,
)
def update_library_asset(
    asset_id: str,
    payload: UpdateAssetPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> LibraryAssetResponse:
    """Edit a live catalog asset or explicitly change its lifecycle state."""
    _, _, asset = _master_admin_asset(asset_id, request, session)
    asset.target_phase = payload.target_phase
    asset.genre_classification = payload.genre_classification
    asset.curriculum_term = payload.curriculum_term
    asset.title = payload.title
    asset.author = payload.author
    asset.description = payload.description
    asset.cover_image_vector = payload.cover_image_vector
    asset.content_document_text = payload.content_document_text
    asset.access_tier = payload.access_tier
    asset.license_identifier = payload.license_identifier
    if payload.is_active is not None:
        asset.is_active = payload.is_active
    try:
        session.flush()
    except Exception as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An asset with this title already exists in the active tenant.",
        ) from error
    return _asset_response(asset)


@admin_router.post(
    "/library-assets/{asset_id}/retire",
    response_model=LibraryAssetResponse,
    status_code=status.HTTP_200_OK,
)
def retire_library_asset(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_database_session),
) -> LibraryAssetResponse:
    """Soft-retire an asset while preserving its audit record."""
    _, _, asset = _master_admin_asset(asset_id, request, session)
    asset.is_active = False
    session.flush()
    return _asset_response(asset)


@admin_router.post(
    "/upload-asset",
    response_model=LibraryAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_asset(
    payload: UploadAssetPayload,
    request: Request,
    session: Session = Depends(get_database_session),
) -> LibraryAssetResponse:
    """Create a catalog asset for the authenticated master-admin tenant."""
    actor = _authenticated_user(request, session)
    AuthenticationService(session).require_roles(actor, UserRole.MASTER_ADMIN)
    tenant = first_active_tenant(session)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active school tenant is available.",
        )

    asset = DigitalAssetVault(
        tenant_id=tenant.id,
        target_phase=payload.target_phase,
        genre_classification=payload.genre_classification,
        curriculum_term=payload.curriculum_term,
        title=payload.title,
        author=payload.author,
        description=payload.description,
        cover_image_vector=payload.cover_image_vector,
        content_document_text=payload.content_document_text,
        access_tier=payload.access_tier,
        is_active=True,
        license_identifier=payload.license_identifier,
    )
    session.add(asset)
    try:
        session.flush()
    except Exception as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An asset with this title already exists in the active tenant.",
        ) from error
    return _asset_response(asset)