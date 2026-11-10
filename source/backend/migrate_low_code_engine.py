"""Additive schema migration for low-code action context and lesson progress."""

from __future__ import annotations

from sqlalchemy import inspect, text

from backend.models import Base, LowCodeWorkspaceFolder, LowCodeWorkspaceFolderItem


def migrate_workspace_folders_schema(engine) -> None:
    """Create only the additive folder tables used by saved workspace items."""
    Base.metadata.create_all(
        engine,
        tables=[
            LowCodeWorkspaceFolder.__table__,
            LowCodeWorkspaceFolderItem.__table__,
        ],
    )


def migrate_low_code_engine_schema(engine) -> None:
    migrate_workspace_folders_schema(engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "auth_sessions" in tables:
        columns = {column["name"] for column in inspector.get_columns("auth_sessions")}
        if "action_context" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE auth_sessions ADD COLUMN action_context JSON")
                )
                connection.execute(
                    text("UPDATE auth_sessions SET action_context = '{}' "
                         "WHERE action_context IS NULL")
                )