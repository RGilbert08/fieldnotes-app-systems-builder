"""Password authentication, session management, and role-scoped access.

The module deliberately has no web-framework dependency.  Route handlers can
authenticate a bearer/cookie token with :class:`AuthenticationService` and
then pass the returned ``AuthenticatedUser`` to the authorization methods.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Select, func, inspect, or_, select
from sqlalchemy.orm import Session

from backend.models import (
    AuthCredential,
    AuthSession,
    CohortEducatorMapping,
    GradeHodProfile,
    ParentRegisteredDevice,
    ParentScholarLink,
    ScholarParentTokenVault,
    ScholarCohortAssignment,
    ScholarProfile,
    SchoolTenant,
    SchoolTenantMembership,
    ScholarSignInFailure,
    User,
    UserRole,
    grade_year_for_grade_tier,
    utc_now,
)
from backend.authentication.admin_mfa import (
    all_account_mfa_enforcement_enabled,
    master_admin_mfa_enforcement_enabled,
)

logger = logging.getLogger(__name__)

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 16
SESSION_TOKEN_BYTES = 32
DEFAULT_SESSION_TTL = timedelta(days=7)
PASSWORD_ROTATION_INTERVAL = timedelta(days=90)
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = timedelta(minutes=15)

RADAR_STAFF_ROLES = frozenset(
    {
        UserRole.MASTER_ADMIN,
        UserRole.PRINCIPAL,
        UserRole.GRADE_HOD,
        UserRole.EDUCATOR,
    }
)
SUPERUSER_PROFILE_CODE = "FIELDNOTES_DEMO_ADMIN"
SESSION_END_REASON_KEY = "_auth_session_end_reason"
SESSION_END_REASONS = frozenset(
    {
        "sign_out",
        "natural_expiry",
        "session_limit",
        "account_deactivated",
        "security_policy",
        "password_change",
        "other_revocation",
        "administrator_revoked",
    }
)


def mark_session_end_reason(auth_session: AuthSession, reason: str) -> None:
    """Persist a server-generated reason without discarding existing context."""
    if reason not in SESSION_END_REASONS:
        raise ValueError("Unsupported authentication session end reason.")
    context = auth_session.action_context
    if not isinstance(context, dict):
        context = {}
    auth_session.action_context = {**context, SESSION_END_REASON_KEY: reason}


class AuthenticationError(Exception):
    """Base class for authentication failures."""


class InvalidCredentials(AuthenticationError):
    """Raised without revealing whether an account exists."""


class MfaRequired(AuthenticationError):
    """Raised after password verification when an authenticator code is needed."""


class MfaSetupRequired(AuthenticationError):
    """Raised when an account must enroll an authenticator before access."""


class PortalMismatch(AuthenticationError):
    """Raised when valid credentials are used through the wrong product portal."""


class InvalidSession(AuthenticationError):
    """Raised for missing, revoked, expired, or inactive sessions."""


class ParentDeviceLockViolation(AuthenticationError):
    """Raised when a parent session is attempted from an unregistered device."""


class InvalidRole(ValueError):
    """Raised when a role is not one of the six supported values."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated user lacks access to a resource."""


def coerce_role(role: UserRole | str) -> UserRole:
    """Convert and validate a role at the service boundary."""
    if isinstance(role, UserRole):
        return role
    if isinstance(role, str):
        try:
            return UserRole(role)
        except ValueError as error:
            raise InvalidRole(f"Unsupported user role: {role!r}") from error
    raise InvalidRole("User role must be one of the supported role values.")


def can_assume_all_roles(user: User) -> bool:
    """Return the strict, server-authoritative all-role authorization bypass."""
    return (
        user.profile_code == SUPERUSER_PROFILE_CODE
        and can_preview_all_roles(user)
    )


def can_preview_all_roles(user: User) -> bool:
    """Return the persisted, read-only dashboard preview capability."""
    return (
        coerce_role(user.role) is UserRole.MASTER_ADMIN
        and user.master_admin_profile is not None
        and user.master_admin_profile.override_enabled
    )


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    # Avoid accepting an input that can be silently truncated by downstream
    # request handling or turn password hashing into an unbounded operation.
    if len(password) > 1024:
        raise ValueError("Password is too long.")


def hash_password(password: str) -> str:
    """Hash a password using a salted, deliberately slow KDF."""
    _validate_password(password)
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    return (
        f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${encoded_salt}"
        f"${digest.hex()}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password safely, returning False for malformed hashes."""
    if (
        not isinstance(password, str)
        or not isinstance(encoded_hash, str)
        or len(password) > 1024
    ):
        return False

    try:
        scheme, iterations_text, encoded_salt, expected_hex = encoded_hash.split(
            "$", 3
        )
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 10_000_000:
            return False
        padding = "=" * (-len(encoded_salt) % 4)
        salt = base64.urlsafe_b64decode((encoded_salt + padding).encode("ascii"))
        expected_digest = bytes.fromhex(expected_hex)
    except (TypeError, ValueError, UnicodeError):
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def hash_session_token(token: str) -> str:
    """Return the only value that should be persisted for a session token."""
    if not isinstance(token, str) or not token:
        raise ValueError("Session token must be a non-empty string.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_parent_device_token(token: str) -> str:
    """Persist only a one-way digest of the HttpOnly parent-device cookie."""
    if not isinstance(token, str) or not token:
        raise ValueError("Parent device token must be a non-empty string.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_session_token(
    authorization_header: str | None,
    cookie_token: str | None,
) -> str | None:
    """Resolve a bearer session token, falling back to the HttpOnly cookie."""
    if isinstance(authorization_header, str):
        scheme, separator, credentials = authorization_header.partition(" ")
        if scheme.casefold() == "bearer" and separator and credentials.strip():
            return credentials.strip()
    return cookie_token


@dataclass(frozen=True)
class SessionCredentials:
    """The one-time raw token returned after a successful sign-in."""

    token: str
    expires_at: datetime
    user: User
    parent_device_token: str | None = None

    @property
    def must_change_password(self) -> bool:
        return bool(self.user.auth_credential and (
            self.user.auth_credential.must_change_password
            or self.user.auth_credential.password_updated_at
            + PASSWORD_ROTATION_INTERVAL
            <= utc_now()
        ))

    @property
    def session_token(self) -> str:
        """Compatibility alias for callers that use an explicit token name."""
        return self.token


@dataclass(frozen=True)
class AuthenticatedUser:
    """A user resolved from a currently valid persisted session."""

    user: User
    session: AuthSession

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def public_id(self) -> str:
        return self.user.public_id

    @property
    def role(self) -> UserRole:
        return coerce_role(self.user.role)


Actor = User | AuthenticatedUser
UserReference = User | int | str


class AuthenticationService:
    """Application service for authentication and server-side authorization."""

    def __init__(
        self,
        session: Session,
        *,
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
        now: Callable[[], datetime] = utc_now,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("Session TTL must be positive.")
        self.session = session
        self.session_ttl = session_ttl
        self._now = now
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        )

    def _record_failed_scholar_sign_in(self, user: User, failed_at: datetime) -> bool:
        """Queue a minimal failure event when its separately approved table exists."""
        if coerce_role(user.role) is not UserRole.SCHOLAR:
            return False
        bind = self.session.get_bind()
        if not inspect(bind).has_table(ScholarSignInFailure.__tablename__):
            return False
        self.session.add(
            ScholarSignInFailure(
                scholar_user_id=user.id,
                failed_at=failed_at,
            )
        )
        return True

    def create_user(
        self,
        *,
        profile_code: str,
        display_name: str,
        role: UserRole | str,
        password: str,
        email: str | None = None,
        is_active: bool = True,
    ) -> User:
        """Create a user and store only a password hash."""
        validated_role = coerce_role(role)
        if not profile_code or not display_name:
            raise ValueError("Profile code and display name are required.")
        normalized_email = email.strip().lower() if email is not None else None
        if normalized_email == "":
            normalized_email = None

        user = User(
            profile_code=profile_code,
            email=normalized_email,
            display_name=display_name,
            role=validated_role,
            is_active=is_active,
        )
        user.auth_credential = AuthCredential(password_hash=hash_password(password))
        self.session.add(user)
        self.session.commit()
        return user

    def set_password(self, user: User, password: str) -> None:
        """Create or replace a user's password credential."""
        credential = user.auth_credential
        if credential is None:
            credential = AuthCredential(user_id=user.id)
            self.session.add(credential)
            user.auth_credential = credential
        credential.password_hash = hash_password(password)
        credential.password_updated_at = self._now()
        credential.must_change_password = False
        credential.failed_login_attempts = 0
        credential.locked_until = None
        if coerce_role(user.role) is UserRole.SCHOLAR:
            self.session.query(ParentScholarLink).filter(
                ParentScholarLink.scholar_user_id == user.id
            ).update({"re_signature_required": True}, synchronize_session=False)
        self.session.commit()

    def sign_in(
        self,
        identifier: str,
        password: str,
        *,
        mfa_code: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        parent_device_token: str | None = None,
        portal: str | None = None,
    ) -> SessionCredentials:
        """Verify credentials and issue a raw token whose hash is persisted."""
        if not isinstance(identifier, str) or not identifier.strip():
            raise InvalidCredentials("Invalid credentials.")

        normalized_identifier = identifier.strip().lower()
        result = self.session.execute(
            select(User, AuthCredential)
            .join(AuthCredential, AuthCredential.user_id == User.id)
            .where(
                or_(
                    func.lower(User.email) == normalized_identifier,
                    func.lower(User.profile_code) == normalized_identifier,
                )
            )
        ).one_or_none()
        now = self._now()
        if result is None:
            raise InvalidCredentials("Invalid credentials.")

        user, credential = result
        # Defensively validate data loaded from the database as well as data
        # entering through create_user.
        try:
            coerce_role(user.role)
        except InvalidRole as error:
            raise InvalidCredentials("Invalid credentials.") from error

        if not user.is_active or (
            credential.locked_until is not None and credential.locked_until > now
        ):
            if self._record_failed_scholar_sign_in(user, now):
                self.session.commit()
            raise InvalidCredentials("Invalid credentials.")

        if not verify_password(password, credential.password_hash):
            credential.failed_login_attempts += 1
            if credential.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                credential.locked_until = now + LOGIN_LOCKOUT_DURATION
            self._record_failed_scholar_sign_in(user, now)
            self.session.commit()
            raise InvalidCredentials("Invalid credentials.")

        portal_roles = {
            "admin": {UserRole.MASTER_ADMIN},
            "compass": {UserRole.SCHOLAR},
            "canopy-staff": {
                UserRole.PRINCIPAL,
                UserRole.GRADE_HOD,
                UserRole.EDUCATOR,
            },
            "canopy-parent": {UserRole.PARENT},
        }
        allowed_roles = portal_roles.get(portal)
        if allowed_roles is not None and coerce_role(user.role) not in allowed_roles:
            raise PortalMismatch("This account is assigned to another Fieldnotes portal.")

        if (
            not credential.mfa_enabled
            and (
                all_account_mfa_enforcement_enabled()
                or (
                    coerce_role(user.role) is UserRole.MASTER_ADMIN
                    and master_admin_mfa_enforcement_enabled()
                )
            )
        ):
            raise MfaSetupRequired("Authenticator setup is required.")

        if credential.mfa_enabled:
            if not mfa_code:
                raise MfaRequired("An authenticator code is required.")
            try:
                from backend.authentication.admin_mfa import (
                    MfaConfigurationError,
                    verify_and_consume_account_code,
                )

                verified = verify_and_consume_account_code(
                    self.session,
                    user.id,
                    mfa_code,
                    timestamp=int(now.replace(tzinfo=timezone.utc).timestamp()),
                )
            except MfaConfigurationError:
                logger.error("Account MFA encryption is unavailable.")
                raise InvalidCredentials("Invalid credentials.") from None
            if not verified:
                credential.failed_login_attempts += 1
                if credential.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                    credential.locked_until = now + LOGIN_LOCKOUT_DURATION
                self._record_failed_scholar_sign_in(user, now)
                self.session.commit()
                raise InvalidCredentials("Invalid credentials.")

        # Keep the cap in the database.  FOR UPDATE serializes this operation
        # on databases that support row locks; SQLite remains transaction-safe.
        try:
            locked_user = self.session.execute(
                select(User).where(User.id == user.id).with_for_update()
            ).scalar_one()
            user = locked_user
        except Exception:
            pass
        issued_parent_device_token: str | None = None
        if coerce_role(user.role) is UserRole.PARENT:
            registered_device = self.session.scalar(
                select(ParentRegisteredDevice)
                .where(ParentRegisteredDevice.parent_user_id == user.id)
                .with_for_update()
            )
            if registered_device is not None:
                presented_hash = (
                    hash_parent_device_token(parent_device_token)
                    if parent_device_token
                    else ""
                )
                if not hmac.compare_digest(
                    registered_device.token_hash, presented_hash
                ):
                    logger.critical(
                        "Parent device lock violation for parent_user_id=%s",
                        user.id,
                    )
                    raise ParentDeviceLockViolation("403 Device Lock Violation")
                registered_device.last_seen_at = now
            else:
                issued_parent_device_token = secrets.token_urlsafe(32)
                self.session.add(
                    ParentRegisteredDevice(
                        parent_user_id=user.id,
                        token_hash=hash_parent_device_token(
                            issued_parent_device_token
                        ),
                        enrolled_at=now,
                        last_seen_at=now,
                    )
                )
        live_sessions = self.session.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.created_at.asc(), AuthSession.id.asc())
        ).all()
        max_sessions = 2 if coerce_role(user.role) is UserRole.SCHOLAR else 1
        sessions_to_revoke = max(0, len(live_sessions) - max_sessions + 1)
        for old_session in live_sessions[:sessions_to_revoke]:
            old_session.revoked_at = now
            mark_session_end_reason(old_session, "session_limit")

        raw_token = self._token_factory()
        if not isinstance(raw_token, str) or not raw_token:
            raise RuntimeError("The session token generator returned an invalid token.")
        expires_at = now + self.session_ttl
        credential.failed_login_attempts = 0
        credential.locked_until = None
        user.last_login_at = now
        auth_session = AuthSession(
            user=user,
            token_hash=hash_session_token(raw_token),
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(auth_session)
        self.session.commit()
        return SessionCredentials(
            raw_token,
            expires_at,
            user,
            parent_device_token=issued_parent_device_token,
        )

    def verify_mfa_enrollment_credentials(
        self,
        identifier: str,
        password: str,
    ) -> tuple[User, AuthCredential]:
        """Recheck an account password before starting or confirming TOTP setup."""
        if not isinstance(identifier, str) or not identifier.strip():
            raise InvalidCredentials("Invalid credentials.")

        normalized_identifier = identifier.strip().lower()
        result = self.session.execute(
            select(User, AuthCredential)
            .join(AuthCredential, AuthCredential.user_id == User.id)
            .where(
                or_(
                    func.lower(User.email) == normalized_identifier,
                    func.lower(User.profile_code) == normalized_identifier,
                )
            )
            .with_for_update()
        ).one_or_none()
        now = self._now()
        if result is None:
            raise InvalidCredentials("Invalid credentials.")

        user, credential = result
        try:
            coerce_role(user.role)
        except InvalidRole as error:
            raise InvalidCredentials("Invalid credentials.") from error
        if not user.is_active or (
            credential.locked_until is not None and credential.locked_until > now
        ):
            raise InvalidCredentials("Invalid credentials.")
        if not verify_password(password, credential.password_hash):
            credential.failed_login_attempts += 1
            if credential.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                credential.locked_until = now + LOGIN_LOCKOUT_DURATION
            self.session.commit()
            raise InvalidCredentials("Invalid credentials.")
        if credential.mfa_enabled:
            raise MfaRequired("This account already has an authenticator.")
        return user, credential

    def authenticate(
        self, token: str, *, allow_temporary: bool = False
    ) -> AuthenticatedUser:
        """Resolve a raw token, updating activity only for a valid session."""
        try:
            token_hash = hash_session_token(token)
        except ValueError as error:
            raise InvalidSession("Invalid session.") from error

        result = self.session.execute(
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
        ).one_or_none()
        now = self._now()
        if result is None:
            raise InvalidSession("Invalid session.")

        auth_session, user = result
        if auth_session.expires_at <= now or not user.is_active:
            if auth_session.revoked_at is None:
                auth_session.revoked_at = now
                mark_session_end_reason(
                    auth_session,
                    "natural_expiry"
                    if auth_session.expires_at <= now
                    else "account_deactivated",
                )
                self.session.commit()
            raise InvalidSession("Invalid session.")

        try:
            coerce_role(user.role)
        except InvalidRole as error:
            raise InvalidSession("Invalid session.") from error

        credential = user.auth_credential
        if credential is None or not credential.mfa_enabled:
            mfa_enforced = (
                all_account_mfa_enforcement_enabled()
                or (
                    coerce_role(user.role) is UserRole.MASTER_ADMIN
                    and master_admin_mfa_enforcement_enabled()
                )
            )
        else:
            mfa_enforced = False
        if mfa_enforced:
            auth_session.revoked_at = now
            mark_session_end_reason(auth_session, "security_policy")
            self.session.commit()
            raise InvalidSession("Authenticator setup required.")
        if not allow_temporary and credential is not None:
            if credential.must_change_password or (
                credential.password_updated_at + PASSWORD_ROTATION_INTERVAL <= now
            ):
                raise InvalidSession("Password change required.")

        auth_session.last_seen_at = now
        self.session.commit()
        return AuthenticatedUser(user=user, session=auth_session)

    def change_password(self, authenticated: AuthenticatedUser, password: str) -> None:
        """Replace a password and invalidate every pre-change session."""
        user = authenticated.user
        credential = user.auth_credential
        if credential is None:
            raise InvalidCredentials("A password credential is required.")
        _validate_password(password)
        if verify_password(password, credential.password_hash):
            raise InvalidCredentials(
                "Choose a password different from the current password."
            )
        credential.password_hash = hash_password(password)
        credential.password_updated_at = self._now()
        credential.must_change_password = False
        credential.failed_login_attempts = 0
        credential.locked_until = None
        now = self._now()
        for auth_session in self.session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            auth_session.revoked_at = now
            mark_session_end_reason(auth_session, "password_change")
        if coerce_role(user.role) is UserRole.SCHOLAR:
            self.session.query(ParentScholarLink).filter(
                ParentScholarLink.scholar_user_id == user.id
            ).update({"re_signature_required": True}, synchronize_session=False)
            # Older parent registrations may only be represented in the
            # scholar's token vault. Materialize the link now so the
            # re-signature lock also applies to those accounts.
            vaults = self.session.scalars(
                select(ScholarParentTokenVault).where(
                    ScholarParentTokenVault.scholar_id == user.id
                )
            ).all()
            for vault in vaults:
                for parent_id in (vault.parent_a_user_id, vault.parent_b_user_id):
                    if parent_id is None:
                        continue
                    link = self.session.get(
                        ParentScholarLink, (parent_id, user.id)
                    )
                    if link is None:
                        self.session.add(
                            ParentScholarLink(
                                parent_user_id=parent_id,
                                scholar_user_id=user.id,
                                relationship_label="parent",
                                re_signature_required=True,
                            )
                        )
                    else:
                        link.re_signature_required = True
        self.session.commit()

    def sign_out(self, token: str) -> None:
        """Revoke one session; repeated sign-out is intentionally idempotent."""
        try:
            token_hash = hash_session_token(token)
        except ValueError:
            return
        auth_session = self.session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
        )
        if auth_session is not None:
            auth_session.revoked_at = self._now()
            mark_session_end_reason(auth_session, "sign_out")
            self.session.commit()

    def revoke_session(self, auth_session: AuthSession, reason: str) -> bool:
        """Revoke one still-active session and record its server-selected reason."""
        now = self._now()
        if auth_session.revoked_at is not None or auth_session.expires_at <= now:
            return False
        auth_session.revoked_at = now
        mark_session_end_reason(auth_session, reason)
        self.session.commit()
        return True

    def revoke_all_sessions(self, user: User) -> None:
        """Revoke every active session for a user."""
        now = self._now()
        for auth_session in self.session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            auth_session.revoked_at = now
            mark_session_end_reason(auth_session, "other_revocation")
        self.session.commit()

    def require_roles(self, actor: Actor, *roles: UserRole | str) -> User:
        """Require that the actor has one of the explicitly allowed roles."""
        user = self._actor_user(actor)
        if not user.is_active:
            raise AuthorizationError("Inactive users cannot access resources.")
        actor_role = coerce_role(user.role)
        allowed_roles = {coerce_role(role) for role in roles}
        if actor_role not in allowed_roles and not can_assume_all_roles(user):
            raise AuthorizationError("The user's role cannot access this resource.")
        return user

    def resolve_active_school_tenant(self, actor: Actor) -> SchoolTenant:
        """Resolve one active tenant from persisted membership, never client input."""
        user = self._actor_user(actor)
        if not user.is_active:
            raise AuthorizationError("Inactive users cannot access school resources.")

        tenants = self.session.scalars(
            select(SchoolTenant)
            .join(
                SchoolTenantMembership,
                SchoolTenantMembership.school_tenant_id == SchoolTenant.id,
            )
            .where(
                SchoolTenantMembership.user_id == user.id,
                SchoolTenantMembership.is_active.is_(True),
                SchoolTenant.is_active.is_(True),
            )
            .order_by(SchoolTenant.id)
        ).all()
        if len(tenants) != 1:
            raise AuthorizationError(
                "An active school tenant is required for this resource."
            )
        return tenants[0]

    def assigned_scholar_scope(
        self, actor: Actor, *, allow_preview: bool = False
    ) -> tuple[SchoolTenant, Select[tuple[int]]]:
        """Return a server-owned scholar scope for registers and observations.

        Educators need a finalized mapping to an academically active cohort.
        HODs need a visible, valid assigned grade. Browser rosters and educator
        profile labels alone never grant class access.
        """
        user = self._actor_user(actor)
        if not user.is_active:
            raise AuthorizationError("Inactive users cannot access scholar scope.")
        # Preserve read-only previews without granting observation writes.
        role = coerce_role(user.role)
        is_preview = allow_preview and can_preview_all_roles(user)
        if role not in {UserRole.EDUCATOR, UserRole.GRADE_HOD} and not is_preview:
            raise AuthorizationError("This account has no teaching assignment.")
        tenant = self.resolve_active_school_tenant(user)
        scope = (
            select(User.id)
            .join(SchoolTenantMembership, SchoolTenantMembership.user_id == User.id)
            .where(
                User.role == UserRole.SCHOLAR,
                User.is_active.is_(True),
                SchoolTenantMembership.school_tenant_id == tenant.id,
                SchoolTenantMembership.is_active.is_(True),
            )
        )
        if is_preview:
            return tenant, scope
        if role is UserRole.EDUCATOR:
            mapping = (
                select(CohortEducatorMapping.id)
                .where(
                    CohortEducatorMapping.tenant_account_number
                    == ScholarCohortAssignment.tenant_account_number,
                    CohortEducatorMapping.grade_level
                    == ScholarCohortAssignment.grade_level,
                    CohortEducatorMapping.cohort_name_string
                    == ScholarCohortAssignment.cohort_name_string,
                    CohortEducatorMapping.educator_user_id == user.id,
                    CohortEducatorMapping.is_finalized.is_(True),
                )
                .exists()
            )
            scope = scope.join(
                ScholarCohortAssignment,
                ScholarCohortAssignment.scholar_id == User.public_id,
            ).where(
                ScholarCohortAssignment.tenant_account_number
                == tenant.account_identifier,
                ScholarCohortAssignment.allocation_status == "ACADEMICALLY_ACTIVE",
                mapping,
            )
        else:
            profile = self.session.get(GradeHodProfile, user.id)
            grade = (
                grade_year_for_grade_tier(profile.grade_tier)
                if profile is not None and profile.grade_data_visibility
                else None
            )
            if grade is None:
                raise AuthorizationError("This account has no active grade scope.")
            # Use the shared grade parser rather than dialect-specific regex SQL.
            # Distinct labels are drawn only from active scholars in this tenant.
            grade_labels = self.session.scalars(
                select(ScholarProfile.grade_tier)
                .where(ScholarProfile.user_id.in_(scope))
                .distinct()
            ).all()
            matching_labels = [
                label for label in grade_labels
                if grade_year_for_grade_tier(label) == grade
            ]
            scope = scope.join(
                ScholarProfile, ScholarProfile.user_id == User.id
            ).where(ScholarProfile.grade_tier.in_(matching_labels))
        return tenant, scope

    def can_read_radar(
        self,
        actor: Actor,
        scholar: UserReference,
    ) -> bool:
        """Check radar access, including the parent-to-scholar link boundary."""
        actor_user = self._actor_user(actor)
        scholar_user = self._resolve_user(scholar)
        if (
            not actor_user.is_active
            or scholar_user is None
            or not scholar_user.is_active
        ):
            return False

        try:
            actor_role = coerce_role(actor_user.role)
            scholar_role = coerce_role(scholar_user.role)
        except InvalidRole:
            return False
        if scholar_role is not UserRole.SCHOLAR:
            return False
        if actor_role is UserRole.PARENT:
            return (
                self.session.scalar(
                    select(ParentScholarLink.parent_user_id).where(
                        ParentScholarLink.parent_user_id == actor_user.id,
                        ParentScholarLink.scholar_user_id == scholar_user.id,
                        ParentScholarLink.radar_strength_map_access.is_(True),
                        ParentScholarLink.re_signature_required.is_(False),
                    )
                )
                is not None
            )
        if actor_role is UserRole.SCHOLAR:
            return actor_user.id == scholar_user.id
        return actor_role in RADAR_STAFF_ROLES

    def authorize_radar_read(self, actor: Actor, scholar: UserReference) -> User:
        """Return the radar subject only when the actor is authorized."""
        if not self.can_read_radar(actor, scholar):
            raise AuthorizationError("The user cannot read this scholar's radar.")
        resolved = self._resolve_user(scholar)
        assert resolved is not None
        return resolved

    def _actor_user(self, actor: Actor) -> User:
        if isinstance(actor, AuthenticatedUser):
            return actor.user
        if isinstance(actor, User):
            return actor
        raise TypeError("Actor must be a User or an AuthenticatedUser.")

    def _resolve_user(self, reference: UserReference) -> User | None:
        if isinstance(reference, User):
            return reference
        if isinstance(reference, int):
            return self.session.get(User, reference)
        if isinstance(reference, str):
            return self.session.scalar(
                select(User).where(User.public_id == reference)
            )
        raise TypeError("User reference must be a User, id, or public id.")


# A concise alias for route modules that prefer the shorter service name.
AuthService = AuthenticationService
