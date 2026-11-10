"""FastAPI router for the universal cookie-backed authentication entry gate."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.auth import (
    AuthenticationService,
    AuthorizationError,
    can_assume_all_roles,
    can_preview_all_roles,
    DEFAULT_SESSION_TTL,
    InvalidCredentials,
    MfaRequired,
    MfaSetupRequired,
    PortalMismatch,
    PASSWORD_ROTATION_INTERVAL,
    LOGIN_LOCKOUT_DURATION,
    MAX_FAILED_LOGIN_ATTEMPTS,
    extract_session_token,
    InvalidSession,
    ParentDeviceLockViolation,
    mark_session_end_reason,
)
from backend.authentication.admin_mfa import (
    MfaConfigurationError,
    create_recovery_codes,
    create_totp_secret,
    decrypt_totp_secret,
    encrypt_totp_secret,
    provisioning_uri,
    totp_counter_for_code,
)
from backend.database import session_scope
from backend.models import (
    AuthSession,
    MasterAdminAuthenticator,
    SouthAfricanGradePhase,
    User,
    UserRole,
    utc_now,
)

SESSION_COOKIE_NAME = "session"
PARENT_DEVICE_COOKIE_NAME = "parent_registered_hardware_signature"
SESSION_COOKIE_MAX_AGE = int(DEFAULT_SESSION_TTL.total_seconds())
PARENT_DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
SESSION_COOKIE_SAMESITE = "strict"

router = APIRouter(prefix="/auth", tags=["authentication"])


class SignInRequest(BaseModel):
    """Credentials accepted by the universal login gate."""

    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = Field(default=None, max_length=64)
    portal: Literal["admin", "compass", "canopy-staff", "canopy-parent"] | None = None


class MfaEnrollmentStartRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class MfaEnrollmentConfirmRequest(MfaEnrollmentStartRequest):
    totp_code: str = Field(min_length=6, max_length=64)


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=1024)


class PublicUser(BaseModel):
    """The non-sensitive identity returned to the frontend router."""

    id: int
    public_id: str
    display_name: str
    role: UserRole
    can_access_global_project_hub: bool = False
    can_assume_all_roles: bool = False
    can_preview_all_roles: bool = False
    grade_phase: SouthAfricanGradePhase | None = None


class TenantProfileResponse(BaseModel):
    tenant_id: int
    account_identifier: str
    name: str
    school_phase: Literal["PRIMARY", "HIGH_SCHOOL"] = "PRIMARY"
    metadata: dict[str, object] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """A validated session and its public account identity."""

    user: PublicUser
    expires_at: datetime
    tenant_profile: TenantProfileResponse | None = None
    must_change_password: bool = False
    password_expires_at: datetime | None = None


def get_database_session(request: Request) -> Iterator[Session]:
    """Provide one transaction-scoped database session per request."""
    session_factory = getattr(request.app.state, "session_factory", None)
    with session_scope(session_factory) as session:
        yield session


def _public_user(user: User) -> PublicUser:
    return PublicUser(
        id=user.id,
        public_id=user.public_id,
        display_name=user.display_name,
        role=user.role,
        can_access_global_project_hub=user.role is UserRole.MASTER_ADMIN,
        can_assume_all_roles=can_assume_all_roles(user),
        can_preview_all_roles=can_preview_all_roles(user),
        grade_phase=(
            user.scholar_profile.current_grade_phase
            if user.role is UserRole.SCHOLAR and user.scholar_profile is not None
            else None
        ),
    )


def _tenant_profile(session: Session, user: User) -> TenantProfileResponse | None:
    try:
        tenant = AuthenticationService(session).resolve_active_school_tenant(user)
    except AuthorizationError:
        return None
    return TenantProfileResponse(
        tenant_id=tenant.id,
        account_identifier=tenant.account_identifier,
        name=tenant.name,
        school_phase=tenant.school_phase,
        metadata=tenant.ui_layout_decks or {},
    )


def _request_metadata(request: Request) -> tuple[str | None, str | None]:
    client_ip = request.client.host if request.client is not None else None
    return client_ip, request.headers.get("user-agent")


def _set_session_cookie(response: Response, token: str) -> None:
    """Store the opaque token only in a secure browser-managed cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )


def _set_parent_device_cookie(response: Response, token: str | None) -> None:
    """Set the opaque device token only as a Secure, HttpOnly browser cookie."""
    if not token:
        return
    response.set_cookie(
        key=PARENT_DEVICE_COOKIE_NAME,
        value=token,
        max_age=PARENT_DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )


def _credential_status(user: User) -> tuple[bool, datetime | None]:
    credential = user.auth_credential
    if credential is None:
        return False, None
    expires_at = credential.password_updated_at + PASSWORD_ROTATION_INTERVAL
    return bool(credential.must_change_password or expires_at <= datetime.utcnow()), expires_at


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite=SESSION_COOKIE_SAMESITE,
        path="/",
    )


@router.post(
    "/sign-in",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
)
def sign_in(
    credentials: SignInRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> SessionResponse | JSONResponse:
    """Verify credentials and establish a hashed server-side session."""
    client_ip, user_agent = _request_metadata(request)
    try:
        issued_session = AuthenticationService(session).sign_in(
            credentials.identifier,
            credentials.password,
            mfa_code=credentials.totp_code,
            ip_address=client_ip,
            user_agent=user_agent,
            parent_device_token=request.cookies.get(PARENT_DEVICE_COOKIE_NAME),
            portal=credentials.portal,
        )
    except MfaRequired:
        response.headers["Cache-Control"] = "no-store"
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"mfa_required": True},
            headers={"Cache-Control": "no-store"},
        )
    except MfaSetupRequired:
        response.headers["Cache-Control"] = "no-store"
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"mfa_setup_required": True},
            headers={"Cache-Control": "no-store"},
        )
    except PortalMismatch as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is assigned to another Fieldnotes portal.",
        ) from error
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        ) from error
    except ParentDeviceLockViolation as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="403 Device Lock Violation",
        ) from error
    _set_session_cookie(response, issued_session.token)
    _set_parent_device_cookie(response, issued_session.parent_device_token)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        user=_public_user(issued_session.user),
        expires_at=issued_session.expires_at,
        tenant_profile=_tenant_profile(session, issued_session.user),
        must_change_password=_credential_status(issued_session.user)[0],
        password_expires_at=_credential_status(issued_session.user)[1],
    )


@router.post("/mfa/enrollment/start", status_code=status.HTTP_200_OK)
def start_mfa_enrollment(
    payload: MfaEnrollmentStartRequest,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    """Verify a user's password and issue a QR setup secret for their account."""
    response.headers["Cache-Control"] = "no-store"
    service = AuthenticationService(session)
    try:
        user, credential = service.verify_mfa_enrollment_credentials(
            payload.identifier,
            payload.password,
        )
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The account could not be verified.",
        ) from error
    except MfaRequired as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An authenticator is already enabled for this account.",
        ) from error
    if user.role is UserRole.MASTER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Master Admin setup requires the owner-authorized setup flow.",
        )

    try:
        secret = create_totp_secret()
        ciphertext = encrypt_totp_secret(secret)
        now = utc_now()
        authenticator = session.get(MasterAdminAuthenticator, user.id)
        if authenticator is None:
            authenticator = MasterAdminAuthenticator(
                user_id=user.id,
                secret_ciphertext=ciphertext,
                last_verified_step=None,
                recovery_code_hashes=[],
                enabled_at=now,
            )
            session.add(authenticator)
        else:
            authenticator.secret_ciphertext = ciphertext
            authenticator.last_verified_step = None
            authenticator.recovery_code_hashes = []
            authenticator.enabled_at = now
        session.commit()
    except MfaConfigurationError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        ) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Authenticator setup could not be started.",
        ) from error
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not available in this environment.",
        ) from error

    response.headers["Cache-Control"] = "no-store"
    return {
        "authenticator_secret": secret,
        "authenticator_uri": provisioning_uri(
            secret,
            user.email or user.profile_code or f"account-{user.id}",
        ),
    }


@router.post("/mfa/enrollment/confirm", status_code=status.HTTP_200_OK)
def confirm_mfa_enrollment(
    payload: MfaEnrollmentConfirmRequest,
    response: Response,
    session: Session = Depends(get_database_session),
) -> dict[str, object]:
    """Enable a verified authenticator; the next sign-in must use a fresh code."""
    response.headers["Cache-Control"] = "no-store"
    service = AuthenticationService(session)
    try:
        user, credential = service.verify_mfa_enrollment_credentials(
            payload.identifier,
            payload.password,
        )
    except InvalidCredentials as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The account could not be verified.",
        ) from error
    except MfaRequired as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An authenticator is already enabled for this account.",
        ) from error
    if user.role is UserRole.MASTER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Master Admin setup requires the owner-authorized setup flow.",
        )

    now = utc_now()
    try:
        authenticator = session.scalar(
            select(MasterAdminAuthenticator)
            .where(MasterAdminAuthenticator.user_id == user.id)
            .with_for_update()
        )
        if authenticator is None or credential.mfa_enabled:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authenticator setup could not be completed.",
            )
        secret = decrypt_totp_secret(authenticator.secret_ciphertext)
        counter = totp_counter_for_code(
            secret,
            payload.totp_code,
            timestamp=int(now.replace(tzinfo=timezone.utc).timestamp()),
        )
        if counter is None or (
            authenticator.last_verified_step is not None
            and counter <= authenticator.last_verified_step
        ):
            credential.failed_login_attempts += 1
            if credential.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                credential.locked_until = now + LOGIN_LOCKOUT_DURATION
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That authenticator code was not accepted.",
            )

        recovery_codes, recovery_code_hashes = create_recovery_codes()
        authenticator.last_verified_step = counter
        authenticator.recovery_code_hashes = recovery_code_hashes
        authenticator.enabled_at = now
        credential.mfa_enabled = True
        credential.failed_login_attempts = 0
        credential.locked_until = None
        for auth_session in session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            auth_session.revoked_at = now
            mark_session_end_reason(auth_session, "security_policy")
        session.commit()
    except HTTPException:
        raise
    except MfaConfigurationError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not configured for this environment.",
        ) from error
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authenticator setup is not available in this environment.",
        ) from error

    response.headers["Cache-Control"] = "no-store"
    return {
        "enrolled": True,
        "recovery_codes": recovery_codes,
        "message": (
            "Save these recovery codes privately. Use a fresh authenticator code "
            "to sign in."
        ),
    }


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> None:
    """Revoke the presented session and remove its browser cookie."""
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if token:
        AuthenticationService(session).sign_out(token)
    _clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"


@router.get(
    "/session",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
)
def current_session(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> SessionResponse:
    """Resolve a bearer or cookie token against its stored session hash."""
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")

    authenticated = AuthenticationService(session).authenticate(token, allow_temporary=True)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        user=_public_user(authenticated.user),
        expires_at=authenticated.session.expires_at,
        tenant_profile=_tenant_profile(session, authenticated.user),
        must_change_password=_credential_status(authenticated.user)[0],
        password_expires_at=_credential_status(authenticated.user)[1],
    )


@router.post("/change-password", response_model=SessionResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
) -> SessionResponse:
    token = extract_session_token(
        request.headers.get("Authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if not token:
        raise InvalidSession("Invalid session.")
    authenticated = AuthenticationService(session).authenticate(token, allow_temporary=True)
    AuthenticationService(session).change_password(authenticated, payload.new_password)
    _clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        user=_public_user(authenticated.user),
        expires_at=authenticated.session.expires_at,
        tenant_profile=_tenant_profile(session, authenticated.user),
        must_change_password=False,
        password_expires_at=authenticated.user.auth_credential.password_updated_at
        + PASSWORD_ROTATION_INTERVAL,
    )


from backend.authentication.admin_bootstrap import router as admin_bootstrap_router

router.include_router(admin_bootstrap_router)