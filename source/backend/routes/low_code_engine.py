"""Authenticated server-side targets and progress for low-code canvas actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from backend.auth import AuthenticationService, InvalidSession, extract_session_token
from backend.authentication.login_gate import SESSION_COOKIE_NAME, get_database_session
from backend.low_code_folders import (
    CANVAS_PROJECTS,
    WorkspaceFolderCreatePayload,
    WorkspaceFolderMovePayload,
    create_workspace_folder,
    get_workspace_item_folder,
    list_workspace_folders,
    list_workspace_item_folders,
    move_workspace_item,
    remove_workspace_item_folder,
    workspace_folder_response,
)
from backend.canvas_layout import (
    MasterControlLayoutConfiguration,
    validate_canvas_layout_for_viewport,
)
from backend.models import (
    AcademicYearPlanner,
    ClassStreamLedger,
    ClassroomHandshake,
    EducatorProfile,
    GradeHodProfile,
    LessonProgress,
    LowCodeUserProjectSnapshot,
    LowCodeWebsiteSnapshot,
    SchoolTenantMembership,
    StudentAcademicEnrollment,
    TenantCanvasBlueprint,
    User,
    UserRole,
    utc_now,
)
from backend.routes.classroom_periods import local_clock
from backend.standalone_bundle import (
    CORE_WORKSPACE_EXPORT_IDS,
    build_canvas_bundle,
    build_core_workspace_bundle,
    build_website_bundle,
)
from backend.studio_tenant_context import (
    authenticate_studio_master_admin,
    build_studio_tenant_context,
    list_active_studio_tenants,
    resolve_studio_tenant,
)

router = APIRouter(prefix="/api/low-code", tags=["low-code engine"])
studio_projects_router = APIRouter(
    prefix="/api/studio/projects",
    tags=["studio project snapshots"],
)
studio_export_router = APIRouter(
    prefix="/api/studio/export",
    tags=["standalone studio exports"],
)
JHB = ZoneInfo("Africa/Johannesburg")
OTP_TTL = timedelta(minutes=5)
MAX_CONTENT_BYTES = 100_000
MAX_PROJECT_SNAPSHOT_BYTES = 1_000_000
PROJECT_HUB_VIEWPORT = "Global Project Hub"
PROJECT_STUDIO_VIEWPORT = "Canvas Project Studio"
CORE_WORKSPACE_FOLDER_ITEM_PREFIX = "core-workspace:"


def _core_workspace_folder_item_id(workspace_id: str) -> str:
    return f"{CORE_WORKSPACE_FOLDER_ITEM_PREFIX}{workspace_id}"


class ActionContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classStreamId: int | None = Field(default=None, gt=0)
    plannerId: int | None = Field(default=None, gt=0)


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actionBinding: str = Field(min_length=1, max_length=48)


class LessonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesson_key: str = Field(min_length=1, max_length=180)
    class_identifier: str = Field(min_length=1, max_length=96)
    scholar_public_ids: list[str] = Field(min_length=1, max_length=100)
    active_topic_key: str = Field(min_length=1, max_length=180)
    subject: str = Field(default="", max_length=160)
    subject_label: str = Field(default="", max_length=160)
    title: str = Field(min_length=1, max_length=180)
    content_json: dict[str, object] = Field(default_factory=dict)
    status: str
    period_date: date
    initiated_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ProgressPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lessons: list[LessonItem] = Field(min_length=1, max_length=40)


class ProjectSnapshotCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectTitle: str = Field(min_length=1, max_length=120)
    selectedBaseLayoutType: Literal[
        "balanced-dual-column",
        "three-rail-command",
        "asymmetric-analytics",
        "custom",
    ]
    canvasBlueprintData: dict[str, object]

    @field_validator("projectTitle")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        # Titles are persisted and rendered as launcher text; normalize only
        # surrounding whitespace while rejecting an empty result.
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project title cannot be blank.")
        return normalized


def _auth(request: Request, session: Session):
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")
    authenticated = AuthenticationService(session).authenticate(token)
    tenant = AuthenticationService(session).resolve_active_school_tenant(authenticated)
    return authenticated, tenant


def _studio_auth(request: Request, session: Session):
    actor = authenticate_studio_master_admin(request, session)
    tenant = resolve_studio_tenant(request, session)
    return actor, tenant


@studio_projects_router.get("/tenant-context")
def studio_tenant_context(
    request: Request,
    session: Session = Depends(get_database_session),
):
    authenticate_studio_master_admin(request, session)
    return build_studio_tenant_context(
        request,
        list_active_studio_tenants(session),
    )


def _validated_snapshot_data(raw_data: dict[str, object]) -> dict[str, object]:
    raw_layout = raw_data.get("layout")
    if not isinstance(raw_layout, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A canvas layout object is required.",
        )
    try:
        layout_model = validate_canvas_layout_for_viewport(
            MasterControlLayoutConfiguration.model_validate(raw_layout),
            "Canvas Project Studio",
        )
    except (ValidationError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The canvas layout contains unsupported fields, bindings, or viewport components.",
        ) from error

    layout = layout_model.model_dump(mode="json")
    components_by_tab: list[tuple[str | None, dict[str, object]]] = []
    tabs = layout.get("subTabs")
    if isinstance(tabs, list) and tabs:
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tab_id = tab.get("tabId")
            tab_label = tab.get("tabLabelDisplayName")
            for component in tab.get("canvasLayout", []):
                if isinstance(component, dict):
                    components_by_tab.append(
                        (str(tab_id or tab_label or ""), component)
                    )
    else:
        for component in layout.get("canvasLayout", []):
            if isinstance(component, dict):
                components_by_tab.append((None, component))

    def non_empty_values(key: str) -> list[str]:
        return sorted(
            {
                str(component[key])
                for _, component in components_by_tab
                if component.get(key)
            }
        )

    design_variables = [
        {
            "tabId": tab_id,
            "componentId": component.get("id"),
            "dataBinding": component.get("dataBinding", ""),
            "variableBinding": component.get("variableBinding", ""),
            "functionBinding": component.get("functionBinding", ""),
            "actionTrigger": component.get("actionTrigger", ""),
        }
        for tab_id, component in components_by_tab
    ]
    coordinate_matrix = [
        {
            "tabId": tab_id,
            "componentId": component.get("id"),
            "gridColumn": component.get("gridColumn"),
            "gridRow": component.get("gridRow"),
            "gridOrderIndex": component.get("gridOrderIndex"),
            "gridColumnSpan": component.get("gridColumnSpan"),
            "gridRowSpan": component.get("gridRowSpan"),
            "x": component.get("x"),
            "y": component.get("y"),
            "width": component.get("width"),
            "height": component.get("height"),
        }
        for tab_id, component in components_by_tab
    ]
    color_wheel_values = [
        {
            "tabId": tab_id,
            "componentId": component.get("id"),
            "foreground": (component.get("styles") or {}).get("color"),
            "background": (component.get("styles") or {}).get("backgroundColor"),
        }
        for tab_id, component in components_by_tab
    ]
    allowlist_tokens = {
        "componentTypes": non_empty_values("type"),
        "actionBindings": non_empty_values("functionBinding"),
        "variableBindings": non_empty_values("variableBinding"),
        "actionTriggers": non_empty_values("actionTrigger"),
        "visibilityRoles": sorted(
            {
                role
                for _, component in components_by_tab
                for role in (component.get("visibility") or {})
            }
        ),
    }
    return {
        "schemaVersion": 1,
        "layout": layout,
        "studioTabId": layout.get("activeSubTabId"),
        "designVariables": design_variables,
        "coordinateMatrix": coordinate_matrix,
        "colorWheelValues": color_wheel_values,
        "allowlistTokens": allowlist_tokens,
    }


def _project_snapshot_response(
    snapshot: LowCodeUserProjectSnapshot,
    folder: dict[str, object] | None = None,
) -> dict[str, object]:
    folder = folder or {"folderId": None, "folderName": None}
    return {
        "projectId": snapshot.project_id,
        "projectTitle": snapshot.project_title,
        "selectedBaseLayoutType": snapshot.selected_base_layout_type,
        "createdTimestamp": snapshot.created_timestamp.isoformat(),
        "folderId": folder["folderId"],
        "folderName": folder["folderName"],
    }


def _snapshot_studio_tab_id(snapshot_data: object) -> str | None:
    if not isinstance(snapshot_data, dict):
        return None
    direct_value = snapshot_data.get("studioTabId")
    if isinstance(direct_value, str) and direct_value:
        return direct_value
    layout = snapshot_data.get("layout")
    if not isinstance(layout, dict):
        return None
    tab_id = layout.get("activeSubTabId")
    return tab_id if isinstance(tab_id, str) and tab_id else None


def _remove_unreferenced_studio_tab(
    session: Session,
    tenant_id: int,
    tab_id: str,
    deleting_project_id: str,
) -> dict[str, object] | None:
    blueprint = session.scalar(
        select(TenantCanvasBlueprint)
        .where(
            TenantCanvasBlueprint.tenant_id == tenant_id,
            TenantCanvasBlueprint.dashboard_viewport == PROJECT_STUDIO_VIEWPORT,
        )
        .with_for_update()
    )
    if blueprint is None:
        return None
    other_snapshots = session.scalars(
        select(LowCodeUserProjectSnapshot).where(
            LowCodeUserProjectSnapshot.tenant_id == tenant_id,
            LowCodeUserProjectSnapshot.project_id != deleting_project_id,
        )
    ).all()
    if any(
        _snapshot_studio_tab_id(snapshot.canvas_blueprint_data) == tab_id
        for snapshot in other_snapshots
    ):
        return None
    try:
        raw_layout = json.loads(blueprint.serialized_json_layout)
        configuration = MasterControlLayoutConfiguration.model_validate(raw_layout)
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored canvas studio layout is invalid; the snapshot was not deleted.",
        ) from error

    layout = configuration.model_dump(mode="json")
    tabs = layout.get("subTabs", [])
    remaining_tabs = [
        tab for tab in tabs if isinstance(tab, dict) and tab.get("tabId") != tab_id
    ]
    if len(remaining_tabs) == len(tabs):
        return None

    remaining_tabs.sort(key=lambda tab: tab.get("displayOrderWeight", 0))
    layout["subTabs"] = remaining_tabs
    if remaining_tabs:
        active_tab = next(
            (
                tab
                for tab in remaining_tabs
                if tab.get("tabId") == layout.get("activeSubTabId")
            ),
            remaining_tabs[0],
        )
        layout.update(
            {
                "activeSubTabId": active_tab["tabId"],
                "canvasLayout": active_tab.get("canvasLayout", []),
                "layoutMode": active_tab.get("layoutMode") or "grid",
                "gridColumns": active_tab.get("gridColumns")
                if active_tab.get("gridColumns") is not None
                else layout.get("gridColumns", 3),
                "gridRows": active_tab.get("gridRows")
                if active_tab.get("gridRows") is not None
                else layout.get("gridRows", 1),
                "gap": active_tab.get("gap")
                if active_tab.get("gap") is not None
                else layout.get("gap", 12),
                "canvasHeight": active_tab.get("canvasHeight")
                if active_tab.get("canvasHeight") is not None
                else layout.get("canvasHeight", 480),
            }
        )
    else:
        layout["activeSubTabId"] = None
        layout["canvasLayout"] = []

    try:
        canonical_layout = MasterControlLayoutConfiguration.model_validate(
            layout
        ).model_dump(mode="json")
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The remaining canvas studio layout is invalid; the snapshot was not deleted.",
        ) from error
    updated_at = utc_now()
    blueprint.serialized_json_layout = json.dumps(
        canonical_layout,
        separators=(",", ":"),
    )
    blueprint.last_updated_at = updated_at
    return {
        "layout": canonical_layout,
        "last_updated_at": updated_at.isoformat(),
    }


@studio_projects_router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_project_snapshot(
    payload: ProjectSnapshotCreatePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    try:
        incoming_size = len(
            json.dumps(payload.canvasBlueprintData, separators=(",", ":")).encode()
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The project snapshot must contain valid JSON data.",
        ) from error
    if incoming_size > MAX_PROJECT_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The project snapshot exceeds the 1 MB size limit.",
        )

    safe_data = _validated_snapshot_data(payload.canvasBlueprintData)
    if len(json.dumps(safe_data, separators=(",", ":")).encode()) > MAX_PROJECT_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The validated project snapshot exceeds the 1 MB size limit.",
        )

    # Serialize snapshot creation with deletion around the shared studio blueprint,
    # so the last delete cannot remove a tab while a new snapshot is linking to it.
    session.scalar(
        select(TenantCanvasBlueprint)
        .where(
            TenantCanvasBlueprint.tenant_id == tenant.id,
            TenantCanvasBlueprint.dashboard_viewport == PROJECT_STUDIO_VIEWPORT,
        )
        .with_for_update()
    )
    snapshot = LowCodeUserProjectSnapshot(
        project_id=str(uuid4()),
        project_title=payload.projectTitle,
        selected_base_layout_type=payload.selectedBaseLayoutType,
        canvas_blueprint_data=safe_data,
        created_timestamp=utc_now(),
        tenant_id=tenant.id,
    )
    session.add(snapshot)
    session.commit()
    result = _project_snapshot_response(
        snapshot,
        get_workspace_item_folder(
            session,
            tenant.id,
            CANVAS_PROJECTS,
            snapshot.project_id,
        ),
    )
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "created",
                "projectId": snapshot.project_id,
                "refreshTenantProjectCache": True,
            },
        )
    return result


@studio_projects_router.put("/update/{project_id}")
async def update_project_snapshot(
    project_id: str,
    payload: ProjectSnapshotCreatePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    try:
        incoming_size = len(
            json.dumps(payload.canvasBlueprintData, separators=(",", ":")).encode()
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The project snapshot must contain valid JSON data.",
        ) from error
    if incoming_size > MAX_PROJECT_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The project snapshot exceeds the 1 MB size limit.",
        )
    safe_data = _validated_snapshot_data(payload.canvasBlueprintData)
    if len(json.dumps(safe_data, separators=(",", ":")).encode()) > MAX_PROJECT_SNAPSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The validated project snapshot exceeds the 1 MB size limit.",
        )

    snapshot = session.scalar(
        select(LowCodeUserProjectSnapshot)
        .where(
            LowCodeUserProjectSnapshot.project_id == project_id,
            LowCodeUserProjectSnapshot.tenant_id == tenant.id,
        )
        .with_for_update()
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The project snapshot was not found in the active tenant.",
        )
    snapshot.project_title = payload.projectTitle
    snapshot.selected_base_layout_type = payload.selectedBaseLayoutType
    snapshot.canvas_blueprint_data = safe_data
    session.commit()
    result = _project_snapshot_response(
        snapshot,
        get_workspace_item_folder(
            session,
            tenant.id,
            CANVAS_PROJECTS,
            snapshot.project_id,
        ),
    )
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "updated",
                "projectId": snapshot.project_id,
                "refreshTenantProjectCache": True,
            },
        )
    return result


@studio_projects_router.get("/list")
def list_project_snapshots(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    snapshots = session.scalars(
        select(LowCodeUserProjectSnapshot)
        .where(LowCodeUserProjectSnapshot.tenant_id == tenant.id)
        .order_by(
            LowCodeUserProjectSnapshot.created_timestamp.desc(),
            LowCodeUserProjectSnapshot.project_id,
        )
    ).all()
    folders_by_project = list_workspace_item_folders(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        [snapshot.project_id for snapshot in snapshots],
    )
    return [
        _project_snapshot_response(
            snapshot,
            folders_by_project.get(snapshot.project_id),
        )
        for snapshot in snapshots
    ]


@studio_projects_router.get("/folders")
def list_project_folders(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    folders = list_workspace_folders(session, tenant.id, CANVAS_PROJECTS)
    workspace_ids = sorted(CORE_WORKSPACE_EXPORT_IDS)
    folder_assignments = list_workspace_item_folders(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        [
            _core_workspace_folder_item_id(workspace_id)
            for workspace_id in workspace_ids
        ],
    )
    return [
        {
            **folder,
            "coreWorkspaceIds": [
                workspace_id
                for workspace_id in workspace_ids
                if folder_assignments.get(
                    _core_workspace_folder_item_id(workspace_id), {}
                ).get("folderId") == folder["folderId"]
            ],
        }
        for folder in folders
    ]


@studio_projects_router.put("/core-workspaces/{workspace_id}/folder")
async def move_core_workspace_to_folder(
    workspace_id: str,
    payload: WorkspaceFolderMovePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    if workspace_id not in CORE_WORKSPACE_EXPORT_IDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The protected core workspace was not found.",
        )
    folder_assignment = move_workspace_item(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        _core_workspace_folder_item_id(workspace_id),
        payload.folderId,
    )
    session.commit()
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "core_workspace_moved",
                "workspaceId": workspace_id,
                "refreshTenantProjectCache": True,
            },
        )
    return {"workspaceId": workspace_id, **folder_assignment}


@studio_projects_router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_project_folder(
    payload: WorkspaceFolderCreatePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    folder = create_workspace_folder(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        payload.folderName,
    )
    session.commit()
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "folder_created",
                "refreshTenantProjectCache": True,
            },
        )
    return workspace_folder_response(folder)


@studio_projects_router.put("/folder/{project_id}")
async def move_project_to_folder(
    project_id: str,
    payload: WorkspaceFolderMovePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    snapshot = session.scalar(
        select(LowCodeUserProjectSnapshot)
        .where(
            LowCodeUserProjectSnapshot.project_id == project_id,
            LowCodeUserProjectSnapshot.tenant_id == tenant.id,
        )
        .with_for_update()
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The project snapshot was not found in the active tenant.",
        )
    folder_assignment = move_workspace_item(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        snapshot.project_id,
        payload.folderId,
    )
    session.commit()
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "moved",
                "projectId": snapshot.project_id,
                "refreshTenantProjectCache": True,
            },
        )
    return {"projectId": snapshot.project_id, **folder_assignment}


def _archive_chunks(archive: bytes, chunk_size: int = 64 * 1024):
    for offset in range(0, len(archive), chunk_size):
        yield archive[offset : offset + chunk_size]


@studio_export_router.get("/standalone/{item_id}")
def export_standalone_app(
    item_id: str,
    collection: Literal[
        "canvas-projects",
        "semantic-websites",
        "core-workspaces",
    ],
    request: Request,
    session: Session = Depends(get_database_session),
) -> StreamingResponse:
    _, tenant = _studio_auth(request, session)
    if collection == "core-workspaces":
        if item_id not in CORE_WORKSPACE_EXPORT_IDS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The core workspace export was not found.",
            )
        build_bundle = lambda: build_core_workspace_bundle(item_id)
    elif collection == "canvas-projects":
        snapshot = session.scalar(
            select(LowCodeUserProjectSnapshot).where(
                LowCodeUserProjectSnapshot.project_id == item_id,
                LowCodeUserProjectSnapshot.tenant_id == tenant.id,
            )
        )
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The project snapshot was not found in the active tenant.",
            )
        build_bundle = lambda: build_canvas_bundle(
            snapshot.canvas_blueprint_data,
            snapshot.project_title,
        )
    else:
        snapshot = session.scalar(
            select(LowCodeWebsiteSnapshot).where(
                LowCodeWebsiteSnapshot.site_id == item_id,
                LowCodeWebsiteSnapshot.tenant_id == tenant.id,
            )
        )
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The website snapshot was not found in the active tenant.",
            )
        build_bundle = lambda: build_website_bundle(
            snapshot.semantic_blueprint_data,
            snapshot.site_title,
        )

    try:
        archive = build_bundle()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The saved layout could not be compiled.",
        ) from error
    return StreamingResponse(
        _archive_chunks(archive),
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="fieldnotes_exported_app.zip"',
            "Content-Length": str(len(archive)),
            "X-Content-Type-Options": "nosniff",
        },
    )


@studio_projects_router.delete("/delete/{project_id}")
async def delete_project_snapshot(
    project_id: str,
    request: Request,
    session: Session = Depends(get_database_session),
):
    _, tenant = _studio_auth(request, session)
    snapshot = session.scalar(
        select(LowCodeUserProjectSnapshot)
        .where(
            LowCodeUserProjectSnapshot.project_id == project_id,
            LowCodeUserProjectSnapshot.tenant_id == tenant.id,
        )
        .with_for_update()
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The project snapshot was not found in the active tenant.",
        )
    deleted_project_id = snapshot.project_id
    studio_tab_id = _snapshot_studio_tab_id(snapshot.canvas_blueprint_data)
    studio_update = (
        _remove_unreferenced_studio_tab(
            session,
            tenant.id,
            studio_tab_id,
            deleted_project_id,
        )
        if studio_tab_id
        else None
    )
    remove_workspace_item_folder(
        session,
        tenant.id,
        CANVAS_PROJECTS,
        deleted_project_id,
    )
    session.delete(snapshot)
    session.commit()
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        if studio_update is not None:
            await hub.broadcast(
                tenant.id,
                PROJECT_STUDIO_VIEWPORT,
                {
                    "type": "layout.updated",
                    "dashboard_viewport": PROJECT_STUDIO_VIEWPORT,
                    "layout": studio_update["layout"],
                    "last_updated_at": studio_update["last_updated_at"],
                },
            )
        await hub.broadcast(
            tenant.id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "project.snapshots.updated",
                "action": "deleted",
                "projectId": deleted_project_id,
                "refreshTenantProjectCache": True,
            },
        )
    return {"projectId": deleted_project_id, "deleted": True}


def _allowed_streams(actor: User, tenant_id: int, session: Session):
    query = select(ClassStreamLedger).where(
        ClassStreamLedger.tenant_id == tenant_id,
        ClassStreamLedger.is_active.is_(True),
    )
    if actor.role is UserRole.EDUCATOR:
        query = query.where(ClassStreamLedger.educator_id == actor.id)
    elif actor.role is UserRole.GRADE_HOD:
        profile = session.get(GradeHodProfile, actor.id)
        if profile is None:
            return []
        query = query.where(ClassStreamLedger.grade_year == profile.grade_tier)
    return session.scalars(query.order_by(ClassStreamLedger.display_name, ClassStreamLedger.id)).all()


def _allowed_planners(actor: User, tenant_id: int, session: Session):
    query = select(AcademicYearPlanner).where(AcademicYearPlanner.tenant_id == tenant_id)
    if actor.role is UserRole.GRADE_HOD:
        profile = session.get(GradeHodProfile, actor.id)
        if profile is None:
            return []
        query = query.where(AcademicYearPlanner.grade_level == profile.grade_tier)
    elif actor.role is UserRole.EDUCATOR:
        profile = session.get(EducatorProfile, actor.id)
        if profile is None:
            return []
        query = query.where(AcademicYearPlanner.grade_level == profile.grade_tier)
    return session.scalars(query.order_by(AcademicYearPlanner.created_at.desc(), AcademicYearPlanner.id.desc())).all()


@router.get("/action-context")
def read_action_context(request: Request, session: Session = Depends(get_database_session)):
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    if actor.role not in {UserRole.PRINCIPAL, UserRole.GRADE_HOD, UserRole.EDUCATOR}:
        raise HTTPException(status_code=403, detail="This profile cannot use low-code actions.")
    streams = _allowed_streams(actor, tenant.id, session)
    planners = _allowed_planners(actor, tenant.id, session)
    context = authenticated.session.action_context or {}
    stream_ids = {item.id for item in streams}
    planner_ids = {item.id for item in planners}
    active_stream = context.get("classStreamId") if context.get("classStreamId") in stream_ids else None
    active_planner = context.get("plannerId") if context.get("plannerId") in planner_ids else None
    return {
        "classOptions": [{"id": item.id, "label": item.display_name} for item in streams],
        "plannerOptions": [{"id": item.id, "label": item.grade_level} for item in planners],
        "activeClassStreamId": active_stream,
        "activePlannerId": active_planner,
    }


@router.put("/action-context")
def write_action_context(payload: ActionContextPayload, request: Request, session: Session = Depends(get_database_session)):
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    if actor.role not in {UserRole.PRINCIPAL, UserRole.GRADE_HOD, UserRole.EDUCATOR}:
        raise HTTPException(status_code=403, detail="This profile cannot select low-code targets.")
    streams = {item.id: item for item in _allowed_streams(actor, tenant.id, session)}
    planners = {item.id: item for item in _allowed_planners(actor, tenant.id, session)}
    if payload.classStreamId is not None and payload.classStreamId not in streams:
        raise HTTPException(status_code=403, detail="The class stream is outside the active scope.")
    if payload.plannerId is not None and payload.plannerId not in planners:
        raise HTTPException(status_code=403, detail="The planner is outside the active scope.")
    authenticated.session.action_context = {
        "classStreamId": payload.classStreamId,
        "plannerId": payload.plannerId,
    }
    session.commit()
    return read_action_context(request, session)


def _digest(code: str) -> str:
    key = os.getenv("HANDSHAKE_DIGEST_KEY") or os.getenv("SESSION_SECRET")
    if not key:
        raise RuntimeError("A handshake digest key is required.")
    return hmac.new(key.encode(), code.encode(), hashlib.sha256).hexdigest()


@router.post("/actions/execute")
async def execute_action(payload: ActionPayload, request: Request, response: Response, session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    context = authenticated.session.action_context or {}
    stream_id = context.get("classStreamId")
    planner_id = context.get("plannerId")
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if payload.actionBinding == "TRIGGER_OTP_GATE":
        if actor.role not in {UserRole.PRINCIPAL, UserRole.EDUCATOR}:
            raise HTTPException(status_code=403, detail="Only principals and educators can trigger a gate.")
        stream = session.scalar(select(ClassStreamLedger).where(
            ClassStreamLedger.id == stream_id, ClassStreamLedger.tenant_id == tenant.id,
            ClassStreamLedger.is_active.is_(True),
        ))
        if stream is None or (actor.role is UserRole.EDUCATOR and stream.educator_id != actor.id):
            raise HTTPException(status_code=403, detail="The active class stream is not authorized.")
        scholars = session.scalars(select(User).join(StudentAcademicEnrollment, StudentAcademicEnrollment.scholar_id == User.id).join(SchoolTenantMembership, SchoolTenantMembership.user_id == User.id).where(
            User.role == UserRole.SCHOLAR, User.is_active.is_(True),
            SchoolTenantMembership.school_tenant_id == tenant.id, SchoolTenantMembership.is_active.is_(True),
            StudentAcademicEnrollment.tenant_id == tenant.id, StudentAcademicEnrollment.class_stream_id == stream.id,
            StudentAcademicEnrollment.enrollment_status == "ACTIVE",
        )).all()
        if not scholars:
            raise HTTPException(status_code=409, detail="The active class has no enrolled scholars.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = utc_now()
        expires = now + OTP_TTL
        for scholar in scholars:
            session.add(ClassroomHandshake(
                tenant_id=tenant.id, educator_id=actor.id, scholar_id=scholar.id,
                phase="OTP_GATE", response_token_digest=_digest(code),
                expires_at=expires, created_at=now,
            ))
        session.commit()
        if hub is not None:
            await hub.broadcast(tenant.id, "Scholar Simulation Portal", {
                "type": "classroom.otp_gate.updated",
                "action": "TRIGGER_OTP_GATE",
                "expiresAt": expires.isoformat(),
            }, recipient_user_ids={scholar.id for scholar in scholars})
        return {
            "actionBinding": payload.actionBinding,
            "targetLabel": stream.display_name,
            "expiresAt": expires,
            "code": code,
        }
    if payload.actionBinding == "ROLLOVER_PAST_PACING":
        if actor.role not in {UserRole.PRINCIPAL, UserRole.GRADE_HOD}:
            raise HTTPException(status_code=403, detail="Only principals and grade HODs can roll over pacing.")
        planner = session.scalar(select(AcademicYearPlanner).where(
            AcademicYearPlanner.id == planner_id, AcademicYearPlanner.tenant_id == tenant.id,
        ))
        if planner is None:
            raise HTTPException(status_code=409, detail="No active planner is selected.")
        if actor.role is UserRole.GRADE_HOD:
            profile = session.get(GradeHodProfile, actor.id)
            if profile is None or profile.grade_tier != planner.grade_level:
                raise HTTPException(status_code=403, detail="The planner is outside the HOD grade scope.")
        planner_data = planner.syllabus_map_json
        chapters = planner_data.get("chapters") if isinstance(planner_data, dict) else None
        if not isinstance(chapters, list):
            raise HTTPException(status_code=409, detail="The planner has no valid chapter schedule.")
        created_at = planner.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        anchor_date = created_at.astimezone(JHB).date()
        local_today = datetime.now(JHB).date()
        current_week = max(1, ((local_today - anchor_date).days // 7) + 1)
        active = []
        for item in chapters:
            if not isinstance(item, dict):
                continue
            try:
                start_week = int(item.get("planned_start_week", 0))
                end_week = int(item.get("planned_end_week", 0))
            except (TypeError, ValueError):
                continue
            if start_week <= current_week <= end_week:
                active.append(item)
        if len(active) != 1:
            raise HTTPException(status_code=409, detail="The planner has no unique active chapter.")
        chapter = dict(active[0])
        try:
            start_week = int(chapter["planned_start_week"])
            end_week = int(chapter["planned_end_week"])
            old_start = date.fromisoformat(
                str(chapter.get("target_start_date")
                    or (anchor_date + timedelta(days=(start_week - 1) * 7)).isoformat())
            )
            old_end = date.fromisoformat(
                str(chapter.get("target_end_date")
                    or (anchor_date + timedelta(days=end_week * 7 - 1)).isoformat())
            )
            target_shift_days = int(chapter.get("target_shift_days", 0)) + 1
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=409, detail="The active chapter has invalid pacing dates.") from None
        chapter["target_start_date"] = (old_start + timedelta(days=1)).isoformat()
        chapter["target_end_date"] = (old_end + timedelta(days=1)).isoformat()
        chapter["target_shift_days"] = target_shift_days
        chapter_identifier = chapter.get("chapter_identifier")
        chapter_title = str(chapter.get("chapter_title") or chapter_identifier or "Current chapter")
        updated_chapters = list(chapters)
        selected_index = next(index for index, item in enumerate(chapters) if item is active[0])
        updated_chapters[selected_index] = chapter

        now = utc_now()
        variance_note = (
            f"{actor.display_name} shifted {chapter_title} forward by one day "
            "because pacing fell behind."
        )
        variance_log = planner_data.get("pacing_variance_log", [])
        if not isinstance(variance_log, list):
            variance_log = []
        audit_entry = {
            "action": "ROLLOVER_PAST_PACING",
            "actor_user_id": actor.id,
            "actor_display_name": actor.display_name,
            "chapter_identifier": chapter_identifier,
            "chapter_title": chapter_title,
            "recorded_at": now.isoformat(),
            "days_shifted": 1,
            "target_start_date_before": old_start.isoformat(),
            "target_end_date_before": old_end.isoformat(),
            "target_start_date_after": chapter["target_start_date"],
            "target_end_date_after": chapter["target_end_date"],
            "note": variance_note,
        }
        planner.syllabus_map_json = {
            **planner_data,
            "chapters": updated_chapters,
            "pacing_variance_log": [*variance_log, audit_entry][-200:],
        }

        previous_day = local_clock().date() - timedelta(days=1)
        scholar_ids = set(session.scalars(
            select(LessonProgress.scholar_id)
            .join(ClassStreamLedger, ClassStreamLedger.id == LessonProgress.class_stream_id)
            .join(
                StudentAcademicEnrollment,
                (StudentAcademicEnrollment.scholar_id == LessonProgress.scholar_id)
                & (StudentAcademicEnrollment.class_stream_id == LessonProgress.class_stream_id),
            )
            .join(SchoolTenantMembership, SchoolTenantMembership.user_id == LessonProgress.scholar_id)
            .join(User, User.id == LessonProgress.scholar_id)
            .where(
                LessonProgress.tenant_id == tenant.id,
                LessonProgress.period_date == previous_day,
                LessonProgress.status.in_({"INITIATED", "PAUSED"}),
                LessonProgress.completed_at.is_(None),
                ClassStreamLedger.tenant_id == tenant.id,
                ClassStreamLedger.grade_year == planner.grade_level,
                ClassStreamLedger.is_active.is_(True),
                StudentAcademicEnrollment.tenant_id == tenant.id,
                StudentAcademicEnrollment.enrollment_status == "ACTIVE",
                SchoolTenantMembership.school_tenant_id == tenant.id,
                SchoolTenantMembership.is_active.is_(True),
                User.role == UserRole.SCHOLAR,
                User.is_active.is_(True),
            )
            .distinct()
        ).all())
        session.commit()
        if hub is not None and scholar_ids:
            await hub.broadcast(
                tenant.id,
                "Scholar Simulation Portal",
                {"type": "lesson.progress.refresh", "reason": "server_rollover"},
                recipient_user_ids=scholar_ids,
            )
        return {
            "actionBinding": payload.actionBinding,
            "chapterIdentifier": chapter_identifier,
            "chapterTitle": chapter_title,
            "targetStartDate": chapter["target_start_date"],
            "targetEndDate": chapter["target_end_date"],
            "targetShiftDays": chapter["target_shift_days"],
            "actorName": actor.display_name,
            "auditNote": variance_note,
        }
    raise HTTPException(status_code=422, detail="Unsupported low-code action.")


@router.post("/lesson-progress/sync")
def sync_progress(payload: ProgressPayload, request: Request, session: Session = Depends(get_database_session)):
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    AuthenticationService(session).require_roles(actor, UserRole.EDUCATOR)
    for lesson in payload.lessons:
        if lesson.status not in {"INITIATED", "PAUSED", "COMPLETED"}:
            raise HTTPException(status_code=422, detail="Unsupported lesson status.")
        if len(json.dumps(lesson.content_json, separators=(",", ":")).encode()) > MAX_CONTENT_BYTES:
            raise HTTPException(status_code=413, detail="Lesson content exceeds the size limit.")
        stream = session.scalar(select(ClassStreamLedger).where(ClassStreamLedger.tenant_id == tenant.id, ClassStreamLedger.stream_package == lesson.class_identifier, ClassStreamLedger.educator_id == actor.id, ClassStreamLedger.is_active.is_(True)))
        if stream is None:
            raise HTTPException(status_code=403, detail="The lesson class is not assigned to this educator.")
        scholars = session.scalars(
            select(User)
            .join(StudentAcademicEnrollment, StudentAcademicEnrollment.scholar_id == User.id)
            .join(SchoolTenantMembership, SchoolTenantMembership.user_id == User.id)
            .where(
                User.public_id.in_(lesson.scholar_public_ids),
                User.role == UserRole.SCHOLAR,
                User.is_active.is_(True),
                StudentAcademicEnrollment.tenant_id == tenant.id,
                StudentAcademicEnrollment.class_stream_id == stream.id,
                StudentAcademicEnrollment.enrollment_status == "ACTIVE",
                SchoolTenantMembership.school_tenant_id == tenant.id,
                SchoolTenantMembership.is_active.is_(True),
            )
        ).all()
        if len(scholars) != len(set(lesson.scholar_public_ids)):
            raise HTTPException(status_code=403, detail="A lesson scholar is outside the assigned class.")
        for scholar in scholars:
            row = session.scalar(select(LessonProgress).where(LessonProgress.tenant_id == tenant.id, LessonProgress.scholar_id == scholar.id, LessonProgress.lesson_key == lesson.lesson_key).with_for_update())
            values = dict(class_stream_id=stream.id, active_topic_key=lesson.active_topic_key, subject=lesson.subject, subject_label=lesson.subject_label, title=lesson.title, content_json=lesson.content_json, status=lesson.status, period_date=lesson.period_date, initiated_at=lesson.initiated_at, updated_at=lesson.updated_at, completed_at=lesson.completed_at)
            if row is None:
                session.add(LessonProgress(tenant_id=tenant.id, scholar_id=scholar.id, lesson_key=lesson.lesson_key, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
    session.commit()
    return {"synced": len(payload.lessons)}


@router.get("/scholar/lesson-progress/previous-day")
def previous_day_progress(request: Request, response: Response, session: Session = Depends(get_database_session)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    AuthenticationService(session).require_roles(actor, UserRole.SCHOLAR)
    previous = local_clock().date() - timedelta(days=1)
    rows = session.scalars(select(LessonProgress).where(LessonProgress.tenant_id == tenant.id, LessonProgress.scholar_id == actor.id, LessonProgress.period_date == previous, LessonProgress.status.in_({"INITIATED", "PAUSED"}), LessonProgress.completed_at.is_(None)).order_by(LessonProgress.updated_at.desc())).all()
    return {
        "previous_day": previous.isoformat(),
        "lessons": [
            {
                "lesson_key": row.lesson_key,
                "active_topic_key": row.active_topic_key,
                "subject": row.subject,
                "subject_label": row.subject_label,
                "title": row.title,
                "status": row.status,
                "period_date": row.period_date.isoformat(),
                "initiated_at": row.initiated_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "content_json": row.content_json,
            }
            for row in rows
        ],
    }


@router.get("/scholar/lesson-progress/active")
def active_lesson_progress(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    authenticated, tenant = _auth(request, session)
    actor = authenticated.user
    AuthenticationService(session).require_roles(actor, UserRole.SCHOLAR)
    today = local_clock().date()
    rows = session.execute(
        select(LessonProgress, ClassStreamLedger.stream_package)
        .join(
            StudentAcademicEnrollment,
            and_(
                StudentAcademicEnrollment.scholar_id == LessonProgress.scholar_id,
                StudentAcademicEnrollment.tenant_id == LessonProgress.tenant_id,
                StudentAcademicEnrollment.class_stream_id == LessonProgress.class_stream_id,
                StudentAcademicEnrollment.enrollment_status == "ACTIVE",
            ),
        )
        .join(
            ClassStreamLedger,
            and_(
                ClassStreamLedger.id == LessonProgress.class_stream_id,
                ClassStreamLedger.tenant_id == LessonProgress.tenant_id,
                ClassStreamLedger.is_active.is_(True),
            ),
        )
        .where(
            LessonProgress.tenant_id == tenant.id,
            LessonProgress.scholar_id == actor.id,
            LessonProgress.period_date == today,
            LessonProgress.status == "INITIATED",
            LessonProgress.completed_at.is_(None),
        )
        .order_by(LessonProgress.updated_at.desc())
    ).all()
    return {
        "current_day": today.isoformat(),
        "lessons": [
            {
                "lesson_key": row.lesson_key,
                "active_topic_key": row.active_topic_key,
                "subject": row.subject,
                "subject_label": row.subject_label,
                "title": row.title,
                "status": row.status,
                "class_identifier": class_identifier,
                "period_date": row.period_date.isoformat(),
                "initiated_at": row.initiated_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "content_json": row.content_json,
            }
            for row, class_identifier in rows
        ],
    }