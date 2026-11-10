"""Tenant-scoped folder operations for saved low-code workspace items."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    LowCodeWorkspaceFolder,
    LowCodeWorkspaceFolderItem,
    utc_now,
)

CANVAS_PROJECTS = "canvas-projects"
SEMANTIC_WEBSITES = "semantic-websites"
WorkspaceCollection = Literal["canvas-projects", "semantic-websites"]


class WorkspaceFolderCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folderName: str = Field(min_length=1, max_length=80)

    @field_validator("folderName")
    @classmethod
    def normalize_folder_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Folder name cannot be blank.")
        if normalized.casefold() == "unfiled":
            raise ValueError("'Unfiled' is reserved for items without a folder.")
        return normalized


class WorkspaceFolderMovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folderId: int | None = Field(default=None, gt=0)


def workspace_folder_response(folder: LowCodeWorkspaceFolder) -> dict[str, object]:
    return {
        "folderId": folder.folder_id,
        "folderName": folder.folder_name,
        "createdTimestamp": folder.created_timestamp.isoformat(),
    }


def create_workspace_folder(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
    folder_name: str,
) -> LowCodeWorkspaceFolder:
    normalized_name = folder_name.strip()
    if not normalized_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Folder name cannot be blank.",
        )
    folder_key = normalized_name.casefold()
    existing = session.scalar(
        select(LowCodeWorkspaceFolder.folder_id).where(
            LowCodeWorkspaceFolder.tenant_id == tenant_id,
            LowCodeWorkspaceFolder.collection == collection,
            LowCodeWorkspaceFolder.folder_key == folder_key,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists in this collection.",
        )

    folder = LowCodeWorkspaceFolder(
        tenant_id=tenant_id,
        collection=collection,
        folder_name=normalized_name,
        folder_key=folder_key,
        created_timestamp=utc_now(),
    )
    session.add(folder)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists in this collection.",
        ) from error
    return folder


def list_workspace_folders(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
) -> list[dict[str, object]]:
    folders = session.scalars(
        select(LowCodeWorkspaceFolder)
        .where(
            LowCodeWorkspaceFolder.tenant_id == tenant_id,
            LowCodeWorkspaceFolder.collection == collection,
        )
        .order_by(
            LowCodeWorkspaceFolder.folder_key,
            LowCodeWorkspaceFolder.folder_id,
        )
    ).all()
    return [workspace_folder_response(folder) for folder in folders]


def list_workspace_item_folders(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
    item_ids: list[str],
) -> dict[str, dict[str, object]]:
    if not item_ids:
        return {}
    rows = session.execute(
        select(
            LowCodeWorkspaceFolderItem.item_id,
            LowCodeWorkspaceFolder.folder_id,
            LowCodeWorkspaceFolder.folder_name,
        )
        .join(
            LowCodeWorkspaceFolder,
            LowCodeWorkspaceFolder.folder_id
            == LowCodeWorkspaceFolderItem.folder_id,
        )
        .where(
            LowCodeWorkspaceFolderItem.tenant_id == tenant_id,
            LowCodeWorkspaceFolderItem.collection == collection,
            LowCodeWorkspaceFolderItem.item_id.in_(item_ids),
            LowCodeWorkspaceFolder.tenant_id == tenant_id,
            LowCodeWorkspaceFolder.collection == collection,
        )
    ).all()
    return {
        item_id: {"folderId": folder_id, "folderName": folder_name}
        for item_id, folder_id, folder_name in rows
    }


def get_workspace_item_folder(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
    item_id: str,
) -> dict[str, object]:
    return list_workspace_item_folders(
        session,
        tenant_id,
        collection,
        [item_id],
    ).get(item_id, {"folderId": None, "folderName": None})


def move_workspace_item(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
    item_id: str,
    folder_id: int | None,
) -> dict[str, object]:
    if folder_id is None:
        session.execute(
            delete(LowCodeWorkspaceFolderItem).where(
                LowCodeWorkspaceFolderItem.tenant_id == tenant_id,
                LowCodeWorkspaceFolderItem.collection == collection,
                LowCodeWorkspaceFolderItem.item_id == item_id,
            )
        )
        return {"folderId": None, "folderName": None}

    folder = session.scalar(
        select(LowCodeWorkspaceFolder)
        .where(
            LowCodeWorkspaceFolder.folder_id == folder_id,
            LowCodeWorkspaceFolder.tenant_id == tenant_id,
            LowCodeWorkspaceFolder.collection == collection,
        )
        .with_for_update()
    )
    if folder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The folder was not found in this workspace collection.",
        )

    assignment = session.scalar(
        select(LowCodeWorkspaceFolderItem)
        .where(
            LowCodeWorkspaceFolderItem.tenant_id == tenant_id,
            LowCodeWorkspaceFolderItem.collection == collection,
            LowCodeWorkspaceFolderItem.item_id == item_id,
        )
        .with_for_update()
    )
    if assignment is None:
        assignment = LowCodeWorkspaceFolderItem(
            tenant_id=tenant_id,
            collection=collection,
            item_id=item_id,
            folder_id=folder.folder_id,
        )
        session.add(assignment)
    else:
        assignment.folder_id = folder.folder_id
    session.flush()
    return {
        "folderId": folder.folder_id,
        "folderName": folder.folder_name,
    }


def remove_workspace_item_folder(
    session: Session,
    tenant_id: int,
    collection: WorkspaceCollection,
    item_id: str,
) -> None:
    session.execute(
        delete(LowCodeWorkspaceFolderItem).where(
            LowCodeWorkspaceFolderItem.tenant_id == tenant_id,
            LowCodeWorkspaceFolderItem.collection == collection,
            LowCodeWorkspaceFolderItem.item_id == item_id,
        )
    )
