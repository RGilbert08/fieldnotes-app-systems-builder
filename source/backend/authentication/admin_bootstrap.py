"""Token-gated, single-use enrollment for a master administrator."""

from __future__ import annotations

from datetime import timedelta, timezone
import hmac
import os
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import (
    LOGIN_LOCKOUT_DURATION,
    MAX_FAILED_LOGIN_ATTEMPTS,
    hash_password,
    hash_session_token,
    verify_password,
)
from backend.authentication.admin_mfa import (
    MfaConfigurationError,
    create_recovery_codes,
    create_totp_secret,
    encrypt_totp_secret,
    mfa_encryption_ready,
    provisioning_uri,
    totp_counter_for_code,
)
from backend.database import session_scope
from backend.models import (
    AuthCredential,
    AuthSession,
    MasterAdminAuthenticator,
    MasterAdminBootstrapState,
    MasterAdminProfile,
    User,
    UserRole,
    utc_now,
)

router = APIRouter(prefix="/bootstrap", tags=["master-admin-bootstrap"])
BOOTSTRAP_STATE_ID = 1
BOOTSTRAP_TTL = timedelta(minutes=10)
BOOTSTRAP_MAX_CODE_ATTEMPTS = 5
BOOTSTRAP_LOCKOUT = timedelta(minutes=15)
BOOTSTRAP_TOKEN_MIN_LENGTH = 32
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BootstrapStartRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=1024)
    bootstrap_token: str = Field(min_length=1, max_length=512)


class BootstrapConfirmRequest(BaseModel):
    challenge_id: str = Field(min_length=20, max_length=128)
    totp_code: str = Field(min_length=6, max_length=64)


class LegacyEnrollmentStartRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    bootstrap_token: str = Field(min_length=1, max_length=512)


class LegacyAdminRetireRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    bootstrap_token: str = Field(min_length=1, max_length=512)


def get_bootstrap_database_session(request: Request):
    session_factory = getattr(request.app.state, "session_factory", None)
    with session_scope(session_factory) as session:
        yield session


def _configured_bootstrap_token() -> str | None:
    token = os.environ.get("MASTER_ADMIN_BOOTSTRAP_TOKEN", "").strip()
    if len(token) < BOOTSTRAP_TOKEN_MIN_LENGTH:
        return None
    return token


def _schema_ready(session: Session) -> bool:
    try:
        inspector = inspect(session.connection())
        return inspector.has_table(MasterAdminBootstrapState.__tablename__) and inspector.has_table(
            MasterAdminAuthenticator.__tablename__
        )
    except Exception:
        return False


def _locked_state(session: Session, *, create: bool = False) -> MasterAdminBootstrapState | None:
    state = session.scalar(
        select(MasterAdminBootstrapState)
        .where(MasterAdminBootstrapState.id == BOOTSTRAP_STATE_ID)
        .with_for_update()
    )
    if state is None and create:
        state = MasterAdminBootstrapState(id=BOOTSTRAP_STATE_ID)
        session.add(state)
        session.flush()
    return state


def _clear_pending(state: MasterAdminBootstrapState) -> None:
    state.pending_email = None
    state.pending_display_name = None
    state.pending_password_hash = None
    state.pending_secret_ciphertext = None
    state.pending_challenge_hash = None
    state.pending_expires_at = None
    state.failed_attempts = 0
    state.locked_until = None


def _require_bootstrap_token(supplied: str) -> None:
    configured = _configured_bootstrap_token()
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Master-admin setup is not enabled for this environment.",
        )
    if not hmac.compare_digest(configured.encode("utf-8"), supplied.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup could not be authorized.",
        )


@router.get("/status")
def bootstrap_status(
    response: Response,
    session: Session = Depends(get_bootstrap_database_session),
) -> dict[str, bool]:
    """Expose guarded setup availability without account identities."""
    response.headers["Cache-Control"] = "no-store"
    owner_setup_ready = (
        _configured_bootstrap_token() is not None and _schema_ready(session)
    )
    if not owner_setup_ready:
        return {"available": False, "legacy_available": False}
    state = session.get(MasterAdminBootstrapState, BOOTSTRAP_STATE_ID)
    now = utc_now()
    available = mfa_encryption_ready() and (
        state is None
        or (
            state.claimed_at is None
            and (state.locked_until is None or state.locked_until <= now)
        )
    )
    legacy_ids = session.scalars(
        select(User.id)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .where(
            User.role == UserRole.MASTER_ADMIN,
            User.is_active.is_(True),
            AuthCredential.mfa_enabled.is_(False),
        )
    ).all()
    legacy_available = any(
        session.get(MasterAdminAuthenticator, user_id) is None
        for user_id in legacy_ids
    )
    return {
        "available": bool(available),
        "legacy_available": legacy_available,
    }


@router.post("/start", status_code=status.HTTP_200_OK)
def start_bootstrap(
    payload: BootstrapStartRequest,
    response: Response,
    session: Session = Depends(get_bootstrap_database_session),
) -> dict[str, object]:
    """Create a short-lived pending enrollment; no account exists until TOTP verifies."""
    _require_bootstrap_token(payload.bootstrap_token)
    if not _schema_ready(session):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Master-admin setup is not ready in this environment.",
        )

    email = payload.email.strip().lower()
    display_name = payload.display_name.strip()
    if not _EMAIL_RE.fullmatch(email) or not display_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a valid email and name.")
    if session.scalar(
        select(User.id).where(func.lower(User.email) == email)
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That email cannot be used for this setup.")

    now = utc_now()
    state = _locked_state(session, create=True)
    assert state is not None
    if state.claimed_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Master-admin setup has already been used.")
    if state.locked_until is not None and state.locked_until > now:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Setup is temporarily locked.")

    try:
        totp_secret = create_totp_secret()
        secret_ciphertext = encrypt_totp_secret(totp_secret)
    except MfaConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        ) from error

    challenge_id = secrets.token_urlsafe(32)
    state.pending_email = email
    state.pending_display_name = display_name
    state.pending_password_hash = hash_password(payload.password)
    state.pending_secret_ciphertext = secret_ciphertext
    state.pending_challenge_hash = hash_session_token(challenge_id)
    state.pending_expires_at = now + BOOTSTRAP_TTL
    state.failed_attempts = 0
    state.locked_until = None
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge_id": challenge_id,
        "authenticator_secret": totp_secret,
        "authenticator_uri": provisioning_uri(totp_secret, email),
        "expires_in_seconds": int(BOOTSTRAP_TTL.total_seconds()),
    }


@router.post("/confirm", status_code=status.HTTP_200_OK)
def confirm_bootstrap(
    payload: BootstrapConfirmRequest,
    response: Response,
    session: Session = Depends(get_bootstrap_database_session),
) -> dict[str, object]:
    """Validate TOTP and create the first admin or enroll a legacy admin."""
    if _configured_bootstrap_token() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Master-admin setup is not enabled for this environment.",
        )
    if not _schema_ready(session):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Master-admin setup is not ready in this environment.",
        )

    now = utc_now()
    state = _locked_state(session)
    if (
        state is None
        or not state.pending_challenge_hash
        or not state.pending_secret_ciphertext
        or not state.pending_email
        or not state.pending_display_name
        or state.pending_expires_at is None
        or state.pending_expires_at <= now
        or (state.locked_until is not None and state.locked_until > now)
        or not hmac.compare_digest(
            state.pending_challenge_hash,
            hash_session_token(payload.challenge_id),
        )
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup could not be completed.")

    try:
        from backend.authentication.admin_mfa import decrypt_totp_secret

        secret = decrypt_totp_secret(state.pending_secret_ciphertext)
    except MfaConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        ) from error

    counter = totp_counter_for_code(
        secret,
        payload.totp_code,
        timestamp=int(now.replace(tzinfo=timezone.utc).timestamp()),
    )
    if counter is None:
        state.failed_attempts += 1
        if state.failed_attempts >= BOOTSTRAP_MAX_CODE_ATTEMPTS:
            state.locked_until = now + BOOTSTRAP_LOCKOUT
        session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup could not be completed.")

    if state.pending_password_hash is None and state.pending_display_name.startswith(
        "legacy-admin:"
    ):
        raw_user_id = state.pending_display_name.removeprefix("legacy-admin:")
        if not raw_user_id.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup could not be completed.")
        user = session.get(User, int(raw_user_id))
        credential = user.auth_credential if user is not None else None
        if (
            user is None
            or not user.is_active
            or user.role is not UserRole.MASTER_ADMIN
            or credential is None
            or credential.mfa_enabled
            or session.get(MasterAdminAuthenticator, user.id) is not None
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup could not be completed.")

        recovery_codes, recovery_code_hashes = create_recovery_codes()
        session.execute(
            AuthSession.__table__.update()
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        credential.mfa_enabled = True
        session.add(
            MasterAdminAuthenticator(
                user_id=user.id,
                secret_ciphertext=state.pending_secret_ciphertext,
                last_verified_step=counter,
                recovery_code_hashes=recovery_code_hashes,
                enabled_at=now,
            )
        )
        _clear_pending(state)
        response.headers["Cache-Control"] = "no-store"
        try:
            session.flush()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Authenticator setup could not be completed.",
            ) from error
        return {
            "enrolled": True,
            "recovery_codes": recovery_codes,
            "message": "Save these recovery codes now. Use a new authenticator code to sign in.",
        }

    if (
        state.claimed_at is not None
        or not state.pending_password_hash
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Setup could not be completed.")

    email = state.pending_email
    if session.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That email cannot be used for this setup.")

    recovery_codes, recovery_code_hashes = create_recovery_codes()
    user = User(
        profile_code=f"MA-{secrets.token_hex(12).upper()}",
        email=email,
        display_name=state.pending_display_name,
        role=UserRole.MASTER_ADMIN,
        is_active=True,
    )
    user.auth_credential = AuthCredential(
        password_hash=state.pending_password_hash,
        mfa_enabled=True,
    )
    user.master_admin_profile = MasterAdminProfile(
        backend_access=True,
        system_log_access=True,
        override_enabled=True,
    )
    session.add(user)
    session.flush()
    session.add(
        MasterAdminAuthenticator(
            user_id=user.id,
            secret_ciphertext=state.pending_secret_ciphertext,
            last_verified_step=counter,
            recovery_code_hashes=recovery_code_hashes,
            enabled_at=now,
        )
    )
    state.claimed_at = now
    state.claimed_user_id = user.id
    _clear_pending(state)
    response.headers["Cache-Control"] = "no-store"
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Master-admin setup could not be completed.",
        ) from error
    return {
        "registered": True,
        "email": email,
        "recovery_codes": recovery_codes,
        "message": "Save these recovery codes now. Use a new authenticator code to sign in.",
    }


@router.post("/legacy/start", status_code=status.HTTP_200_OK)
def start_legacy_enrollment(
    payload: LegacyEnrollmentStartRequest,
    response: Response,
    session: Session = Depends(get_bootstrap_database_session),
) -> dict[str, object]:
    """Create a short-lived enrollment challenge for an existing master admin."""
    _require_bootstrap_token(payload.bootstrap_token)
    if not _schema_ready(session):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not ready in this environment.",
        )
    if not mfa_encryption_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        )

    normalized_identifier = payload.identifier.strip().lower()
    result = session.execute(
        select(User, AuthCredential)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .where(
            (func.lower(User.email) == normalized_identifier)
            | (func.lower(User.profile_code) == normalized_identifier)
        )
    ).one_or_none()
    now = utc_now()
    if result is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account could not be verified.")
    user, credential = result
    if (
        not user.is_active
        or user.role is not UserRole.MASTER_ADMIN
        or (credential.locked_until is not None and credential.locked_until > now)
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account could not be verified.")
    if not verify_password(payload.password, credential.password_hash):
        credential.failed_login_attempts += 1
        if credential.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            credential.locked_until = now + LOGIN_LOCKOUT_DURATION
        session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account could not be verified.")
    if (
        credential.mfa_enabled
        or session.get(MasterAdminAuthenticator, user.id) is not None
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account could not be verified.")

    state = _locked_state(session, create=True)
    assert state is not None
    if state.locked_until is not None and state.locked_until > now:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Authenticator setup is temporarily locked.")
    try:
        secret = create_totp_secret()
        ciphertext = encrypt_totp_secret(secret)
    except MfaConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        ) from error

    challenge_id = secrets.token_urlsafe(32)
    state.pending_email = user.email or user.profile_code
    state.pending_display_name = f"legacy-admin:{user.id}"
    state.pending_password_hash = None
    state.pending_secret_ciphertext = ciphertext
    state.pending_challenge_hash = hash_session_token(challenge_id)
    state.pending_expires_at = now + BOOTSTRAP_TTL
    state.failed_attempts = 0
    state.locked_until = None
    credential.failed_login_attempts = 0
    credential.locked_until = None
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge_id": challenge_id,
        "authenticator_secret": secret,
        "authenticator_uri": provisioning_uri(secret, str(state.pending_email)),
        "expires_in_seconds": int(BOOTSTRAP_TTL.total_seconds()),
    }


@router.post("/legacy/retire", status_code=status.HTTP_200_OK)
def retire_legacy_admin(
    payload: LegacyAdminRetireRequest,
    response: Response,
    session: Session = Depends(get_bootstrap_database_session),
) -> dict[str, bool]:
    """Disable one password-only admin after another MFA admin is available."""
    _require_bootstrap_token(payload.bootstrap_token)
    if not _schema_ready(session):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not ready in this environment.",
        )
    normalized_identifier = payload.identifier.strip().lower()
    user = session.scalar(
        select(User)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .where(
            (func.lower(User.email) == normalized_identifier)
            | (func.lower(User.profile_code) == normalized_identifier),
            User.role == UserRole.MASTER_ADMIN,
            User.is_active.is_(True),
            AuthCredential.mfa_enabled.is_(False),
        )
    )
    if user is None:
        response.headers["Cache-Control"] = "no-store"
        return {"retired": False}

    protected_admin_id = session.scalar(
        select(User.id)
        .join(AuthCredential, AuthCredential.user_id == User.id)
        .join(MasterAdminAuthenticator, MasterAdminAuthenticator.user_id == User.id)
        .where(
            User.role == UserRole.MASTER_ADMIN,
            User.is_active.is_(True),
            User.id != user.id,
            AuthCredential.mfa_enabled.is_(True),
        )
        .limit(1)
    )
    if protected_admin_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Keep at least one active authenticator-protected master admin.",
        )
    user.is_active = False
    session.execute(
        AuthSession.__table__.update()
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )
    response.headers["Cache-Control"] = "no-store"
    return {"retired": True}
