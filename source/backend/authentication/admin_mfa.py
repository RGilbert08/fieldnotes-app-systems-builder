"""TOTP and recovery-code primitives shared by Fieldnotes accounts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import MasterAdminAuthenticator

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
RECOVERY_CODE_COUNT = 10
MFA_SETUP_TOKEN_MIN_LENGTH = 32


class MfaConfigurationError(RuntimeError):
    """Raised when an MFA encryption key is missing or unusable."""


def master_admin_mfa_enforcement_enabled() -> bool:
    """Treat the owner-configured bootstrap token as activation of MFA policy."""
    return len(os.environ.get("MASTER_ADMIN_BOOTSTRAP_TOKEN", "").strip()) >= (
        MFA_SETUP_TOKEN_MIN_LENGTH
    )


def all_account_mfa_enforcement_enabled() -> bool:
    """Require account MFA outside isolated automated test runs."""
    return os.environ.get("NODE_ENV", "").strip().lower() != "test"


def _fernet() -> Fernet:
    key = os.environ.get("MASTER_ADMIN_MFA_ENCRYPTION_KEY", "").strip()
    if not key:
        raise MfaConfigurationError("MFA encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise MfaConfigurationError("MFA encryption is not configured.") from error


def mfa_encryption_ready() -> bool:
    try:
        _fernet()
        return True
    except MfaConfigurationError:
        return False


def encrypt_totp_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as error:
        raise MfaConfigurationError("MFA authenticator data cannot be read.") from error


def create_totp_secret() -> str:
    """Create a 160-bit Base32 authenticator key without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, email: str) -> str:
    label = quote(f"Fieldnotes:{email}", safe="")
    issuer = quote("Fieldnotes", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def totp_counter_for_code(
    secret: str,
    code: str,
    *,
    timestamp: int | None = None,
    window: int = TOTP_WINDOW,
) -> int | None:
    """Return the matching 30-second TOTP counter, if the code is valid."""
    supplied = "".join(character for character in str(code) if character.isdigit())
    if len(supplied) != TOTP_DIGITS or not supplied.isdigit():
        return None
    try:
        key = base64.b32decode(
            secret + ("=" * ((8 - len(secret) % 8) % 8)),
            casefold=True,
        )
    except (ValueError, binascii.Error):
        return None
    counter_now = (int(time.time()) if timestamp is None else int(timestamp)) // TOTP_PERIOD_SECONDS
    for counter in range(counter_now - window, counter_now + window + 1):
        if counter < 0:
            continue
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        expected = f"{truncated % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"
        if hmac.compare_digest(expected, supplied):
            return counter
    return None


def _normalise_recovery_code(code: str) -> str:
    return "".join(
        character
        for character in code.upper()
        if character.isascii() and character.isalnum()
    )


def _recovery_code_digest(code: str) -> str:
    return hashlib.sha256(_normalise_recovery_code(code).encode("ascii")).hexdigest()


def create_recovery_codes() -> tuple[list[str], list[str]]:
    """Return plaintext one-time codes and their stored SHA-256 digests."""
    codes = [
        f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}"
        for _ in range(RECOVERY_CODE_COUNT)
    ]
    return codes, [_recovery_code_digest(code) for code in codes]


def verify_and_consume_admin_code(
    session: Session,
    user_id: int,
    supplied_code: str,
    *,
    timestamp: int | None = None,
) -> bool:
    """Consume a TOTP counter or single-use recovery code under a row lock."""
    authenticator = session.scalar(
        select(MasterAdminAuthenticator)
        .where(MasterAdminAuthenticator.user_id == user_id)
        .with_for_update()
    )
    if authenticator is None:
        return False

    secret = decrypt_totp_secret(authenticator.secret_ciphertext)
    counter = totp_counter_for_code(secret, supplied_code, timestamp=timestamp)
    if counter is not None:
        if (
            authenticator.last_verified_step is not None
            and counter <= authenticator.last_verified_step
        ):
            return False
        authenticator.last_verified_step = counter
        return True

    digest = _recovery_code_digest(supplied_code)
    stored_codes = list(authenticator.recovery_code_hashes or [])
    matched_index = next(
        (
            index
            for index, stored in enumerate(stored_codes)
            if hmac.compare_digest(str(stored), digest)
        ),
        None,
    )
    if matched_index is None:
        return False
    stored_codes.pop(matched_index)
    authenticator.recovery_code_hashes = stored_codes
    return True


def verify_and_consume_account_code(
    session: Session,
    user_id: int,
    supplied_code: str,
    *,
    timestamp: int | None = None,
) -> bool:
    """Consume a code from the shared per-user authenticator table."""
    return verify_and_consume_admin_code(
        session,
        user_id,
        supplied_code,
        timestamp=timestamp,
    )
