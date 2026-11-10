"""Schema readiness is the explicit activation gate for curriculum evidence."""

from sqlalchemy import inspect
from sqlalchemy.engine import Connection


def curriculum_context_is_active(connection: Connection) -> bool:
    """Read-only check on the request's connection, never run a migration."""
    return inspect(connection).has_table("observation_curriculum_contexts")
