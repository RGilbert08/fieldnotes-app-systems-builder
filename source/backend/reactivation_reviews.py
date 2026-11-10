"""Server-owned identities for account reviews stored in the existing support queue.

The ticket role column is a string, not a database enum. Reserve a role tag with
the target ID for this workflow rather than trusting user-authored diagnostics.
General support submissions accept only UserRole values and cannot set this tag.
Public support responses still present the requesting role as PRINCIPAL.
"""

from __future__ import annotations


REVIEW_ROLE_PREFIX = "PRINCIPAL_REVIEW:"


def reactivation_review_role(user_id: int) -> str:
    return f"{REVIEW_ROLE_PREFIX}{user_id}"


def reactivation_review_target(role_tag: str) -> int | None:
    if not role_tag.startswith(REVIEW_ROLE_PREFIX):
        return None
    identifier = role_tag.removeprefix(REVIEW_ROLE_PREFIX)
    if not identifier.isascii() or not identifier.isdecimal():
        return None
    return int(identifier)
