from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ._session_schema import validate_session_schema
from ._onboarding_schema import INDEX_DDL as ONBOARDING_INDEX_DDL, TABLE_DDL as ONBOARDING_TABLE_DDL
from ._console_schema import INDEX_DDL as CONSOLE_INDEX_DDL, TABLE_DDL as CONSOLE_TABLE_DDL


SESSION_STORE_SCHEMA_VERSION = 4



class SessionStoreError(RuntimeError):
    """Content-free failure at the human-session persistence boundary."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Enrollment:
    enrollment_id: str
    issuer: str
    client_name: str
    expires_at: datetime
    organization_id: str | None = None


@dataclass(frozen=True)
class AuthorizationFlow:
    enrollment_id: str
    issuer: str
    client_name: str
    nonce: str = field(repr=False)
    pkce_verifier: str = field(repr=False)
    organization_id: str | None = None
    invitation_id: str | None = None


@dataclass(frozen=True)
class SessionCredentialPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: datetime
    session_expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": "Bearer",
            "expires_in": max(
                0,
                int((self.access_expires_at - datetime.now(timezone.utc)).total_seconds()),
            ),
            "access_expires_at": _isoformat(self.access_expires_at),
            "session_expires_at": _isoformat(self.session_expires_at),
        }


@dataclass(frozen=True)
class SessionPrincipal:
    session_id: str
    issuer: str
    subject: str
    client_name: str
    organization_id: str
    actor_id: str
    team_id: str
    clearance: str
    access_expires_at: datetime
    session_expires_at: datetime
    authorization_version: int = 1
    membership_id: str | None = None






class SQLiteSessionStore:
    """Single-node opaque-session store with atomic credential-family rotation."""

    def __init__(
        self,
        path: Path,
        *,
        master_key: bytes,
        audience: str,
        access_ttl_seconds: int,
        absolute_ttl_seconds: int,
        enrollment_ttl_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ):
        if len(master_key) != 32:
            raise SessionStoreError("session_store_invalid_master_key")
        _require_binding(audience, "session_store_invalid_audience")
        self.path = path
        binding = audience.encode("utf-8")
        self._hash_key = hmac.new(
            master_key,
            b"hormuz/session/hash/v2\x00" + binding,
            hashlib.sha256,
        ).digest()
        self._encryption_key = hmac.new(
            master_key,
            b"hormuz/session/encryption/v2\x00" + binding,
            hashlib.sha256,
        ).digest()
        self._access_ttl = timedelta(seconds=access_ttl_seconds)
        self._absolute_ttl = timedelta(seconds=absolute_ttl_seconds)
        self._enrollment_ttl = timedelta(seconds=enrollment_ttl_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        if self.path.exists() and self.path.is_symlink():
            raise SessionStoreError("session_store_symlink_refused")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._prepare_storage_file()
        self._initialize()

    def create_enrollment(
        self,
        *,
        issuer: str,
        client_name: str,
        enrollment_secret: str,
        organization_id: str | None = None,
    ) -> Enrollment:
        _require_secret(enrollment_secret, "invalid_enrollment_secret")
        if organization_id is not None:
            _require_binding(organization_id, "invalid_organization_id")
        now = self._now()
        enrollment = Enrollment(
            enrollment_id=secrets.token_urlsafe(24),
            issuer=issuer,
            client_name=client_name,
            expires_at=now + self._enrollment_ttl,
            organization_id=organization_id,
        )
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM session_enrollments WHERE expires_at <= ?", (_isoformat(now),))
            if connection.execute("SELECT COUNT(*) FROM session_enrollments").fetchone()[0] >= 1000:
                raise SessionStoreError("enrollment_capacity_reached")
            connection.execute(
                """
                INSERT INTO session_enrollments (
                    id, secret_hash, issuer, client_name, status,
                    created_at, expires_at, organization_id
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    enrollment.enrollment_id,
                    self._digest("enrollment", enrollment_secret),
                    issuer,
                    client_name,
                    _isoformat(now),
                    _isoformat(enrollment.expires_at),
                    organization_id,
                ),
            )
        return enrollment

    def begin_authorization(
        self,
        *,
        enrollment_id: str,
        state: str,
        browser_cookie: str,
        nonce: str,
        pkce_verifier: str,
    ) -> AuthorizationFlow:
        for value, code in (
            (state, "invalid_authorization_state"),
            (browser_cookie, "invalid_browser_cookie"),
            (nonce, "invalid_oidc_nonce"),
            (pkce_verifier, "invalid_pkce_verifier"),
        ):
            _require_secret(value, code)
        now = self._now()
        flow_value = json.dumps(
            {"nonce": nonce, "pkce_verifier": pkce_verifier},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = self._encrypt(flow_value, associated_data=enrollment_id.encode("utf-8"))
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT issuer, client_name, status, expires_at, organization_id "
                "FROM session_enrollments WHERE id = ?",
                (enrollment_id,),
            ).fetchone()
            if row is None or row["status"] != "pending" or _parse_time(row["expires_at"]) <= now:
                raise SessionStoreError("enrollment_unavailable")
            connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'authorizing', state_hash = ?, browser_cookie_hash = ?,
                    encrypted_flow = ?, authorization_started_at = ?
                WHERE id = ?
                """,
                (
                    self._digest("state", state),
                    self._digest("browser", browser_cookie),
                    encrypted,
                    _isoformat(now),
                    enrollment_id,
                ),
            )
            return AuthorizationFlow(
                enrollment_id=enrollment_id,
                issuer=str(row["issuer"]),
                client_name=str(row["client_name"]),
                nonce=nonce,
                pkce_verifier=pkce_verifier,
                organization_id=(
                    str(row["organization_id"])
                    if row["organization_id"] is not None
                    else None
                ),
            )

    def consume_callback(
        self,
        *,
        state: str,
        browser_cookie: str,
    ) -> AuthorizationFlow:
        _require_secret(state, "invalid_authorization_state")
        _require_secret(browser_cookie, "invalid_browser_cookie")
        now = self._now()
        state_hash = self._digest("state", state)
        browser_hash = self._digest("browser", browser_cookie)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, issuer, client_name, browser_cookie_hash, encrypted_flow,
                       expires_at, organization_id, invitation_id
                FROM session_enrollments
                WHERE state_hash = ? AND status = 'authorizing'
                """,
                (state_hash,),
            ).fetchone()
            if (
                row is None
                or not hmac.compare_digest(bytes(row["browser_cookie_hash"]), browser_hash)
                or _parse_time(row["expires_at"]) <= now
            ):
                raise SessionStoreError("invalid_callback_state")
            connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'exchanging', state_hash = NULL, browser_cookie_hash = NULL
                WHERE id = ? AND status = 'authorizing'
                """,
                (row["id"],),
            )
            plaintext = self._decrypt(
                bytes(row["encrypted_flow"]),
                associated_data=str(row["id"]).encode("utf-8"),
            )
            try:
                flow = json.loads(plaintext)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SessionStoreError("session_store_corrupt_flow") from error
            return AuthorizationFlow(
                enrollment_id=str(row["id"]),
                issuer=str(row["issuer"]),
                client_name=str(row["client_name"]),
                nonce=str(flow["nonce"]),
                pkce_verifier=str(flow["pkce_verifier"]),
                organization_id=(
                    str(row["organization_id"])
                    if row["organization_id"] is not None
                    else None
                ),
                invitation_id=row["invitation_id"],
            )

    def authorize_enrollment(
        self,
        *,
        enrollment_id: str,
        subject: str,
        organization_id: str,
        actor_id: str,
        team_id: str,
        clearance: str,
    ) -> None:
        _require_binding(subject, "invalid_subject")
        _require_binding(organization_id, "invalid_organization_id")
        _require_binding(actor_id, "invalid_actor_id")
        _require_binding(team_id, "invalid_team_id")
        _require_binding(clearance, "invalid_clearance")
        now = self._now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'authorized', subject = ?, organization_id = ?,
                    actor_id = ?, team_id = ?, clearance = ?, authorized_at = ?,
                    encrypted_flow = NULL
                WHERE id = ? AND status = 'exchanging' AND expires_at > ?
                  AND (organization_id IS NULL OR organization_id = ?)
                """,
                (
                    subject,
                    organization_id,
                    actor_id,
                    team_id,
                    clearance,
                    _isoformat(now),
                    enrollment_id,
                    _isoformat(now),
                    organization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SessionStoreError("enrollment_unavailable")

    def fail_enrollment(self, *, enrollment_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'failed', encrypted_flow = NULL, state_hash = NULL,
                    browser_cookie_hash = NULL
                WHERE id = ? AND status IN ('authorizing', 'exchanging')
                """,
                (enrollment_id,),
            )

    def redeem_enrollment(
        self,
        *,
        enrollment_id: str,
        enrollment_secret: str,
    ) -> SessionCredentialPair:
        _require_secret(enrollment_secret, "invalid_enrollment_secret")
        now = self._now()
        pair = self._new_pair(now=now, absolute_expires_at=now + self._absolute_ttl)
        session_id = "ses_" + secrets.token_urlsafe(24)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT secret_hash, issuer, subject, client_name, organization_id,
                       actor_id, team_id, clearance, status, expires_at,
                       membership_id, authorization_version
                FROM session_enrollments WHERE id = ?
                """,
                (enrollment_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "authorized"
                or _parse_time(row["expires_at"]) <= now
                or not hmac.compare_digest(
                    bytes(row["secret_hash"]),
                    self._digest("enrollment", enrollment_secret),
                )
            ):
                raise SessionStoreError("enrollment_not_redeemable")
            self._require_active_membership(connection, row)
            connection.execute(
                """
                INSERT INTO human_sessions (
                    id, issuer, subject, client_name, access_hash, refresh_hash,
                    access_expires_at, absolute_expires_at, generation,
                    created_at, refreshed_at, organization_id, actor_id, team_id,
                    clearance, membership_id, authorization_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row["issuer"],
                    row["subject"],
                    row["client_name"],
                    self._digest("access", pair.access_token),
                    self._digest("refresh", pair.refresh_token),
                    _isoformat(pair.access_expires_at),
                    _isoformat(pair.session_expires_at),
                    _isoformat(now),
                    _isoformat(now),
                    row["organization_id"],
                    row["actor_id"],
                    row["team_id"],
                    row["clearance"],
                    row["membership_id"],
                    row["authorization_version"],
                ),
            )
            connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'redeemed', secret_hash = NULL, subject = NULL,
                    redeemed_at = ?
                WHERE id = ?
                """,
                (_isoformat(now), enrollment_id),
            )
        return pair

    def authenticate_access(self, access_token: str) -> SessionPrincipal:
        _require_secret(access_token, "invalid_session_credential")
        now = self._now()
        credential_hash = self._digest("access", access_token)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, issuer, subject, client_name, organization_id,
                       actor_id, team_id, clearance, access_expires_at,
                       absolute_expires_at, revoked_at, membership_id, authorization_version
                FROM human_sessions WHERE access_hash = ?
                """,
                (credential_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise SessionStoreError("invalid_session_credential")
            access_expires_at = _parse_time(row["access_expires_at"])
            absolute_expires_at = _parse_time(row["absolute_expires_at"])
            if access_expires_at <= now or absolute_expires_at <= now:
                raise SessionStoreError("expired_session_credential")
            self._require_active_membership(connection, row)
            return SessionPrincipal(
                session_id=str(row["id"]),
                issuer=str(row["issuer"]),
                subject=str(row["subject"]),
                client_name=str(row["client_name"]),
                organization_id=str(row["organization_id"]),
                actor_id=str(row["actor_id"]),
                team_id=str(row["team_id"]),
                clearance=str(row["clearance"]),
                access_expires_at=access_expires_at,
                session_expires_at=absolute_expires_at,
                membership_id=row["membership_id"],
                authorization_version=int(row["authorization_version"]),
            )

    def refresh(self, refresh_token: str) -> SessionCredentialPair:
        _require_secret(refresh_token, "invalid_session_credential")
        now = self._now()
        old_hash = self._digest("refresh", refresh_token)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT id, absolute_expires_at, revoked_at, membership_id,
                           authorization_version, organization_id, issuer, subject,
                           actor_id, team_id, clearance, client_name
                    FROM human_sessions WHERE refresh_hash = ?
                    """,
                    (old_hash,),
                ).fetchone()
                if row is None:
                    consumed = connection.execute(
                        """
                        SELECT consumed.session_id, sessions.organization_id,
                               sessions.actor_id, sessions.team_id
                        FROM consumed_refresh_credentials AS consumed
                        JOIN human_sessions AS sessions ON sessions.id = consumed.session_id
                        WHERE consumed.credential_hash = ?
                        """,
                        (old_hash,),
                    ).fetchone()
                    if consumed is not None:
                        connection.execute(
                            "UPDATE human_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                            (_isoformat(now), consumed["session_id"]),
                        )
                        self._record_event(
                            connection,
                            session_id=str(consumed["session_id"]),
                            event_type="refresh_replay",
                            occurred_at=now,
                            organization_id=str(consumed["organization_id"]),
                            target_actor_id=str(consumed["actor_id"]),
                            target_team_id=str(consumed["team_id"]),
                        )
                        connection.commit()
                        raise SessionStoreError("refresh_replay_detected")
                    connection.rollback()
                    raise SessionStoreError("invalid_session_credential")
                absolute_expires_at = _parse_time(row["absolute_expires_at"])
                if row["revoked_at"] is not None or absolute_expires_at <= now:
                    connection.rollback()
                    raise SessionStoreError("expired_session_credential")
                self._require_active_membership(connection, row)
                pair = self._new_pair(now=now, absolute_expires_at=absolute_expires_at)
                connection.execute(
                    """
                    INSERT INTO consumed_refresh_credentials (
                        credential_hash, session_id, consumed_at, expires_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (old_hash, row["id"], _isoformat(now), _isoformat(absolute_expires_at)),
                )
                connection.execute(
                    """
                    UPDATE human_sessions
                    SET access_hash = ?, refresh_hash = ?, access_expires_at = ?,
                        generation = generation + 1, refreshed_at = ?
                    WHERE id = ?
                    """,
                    (
                        self._digest("access", pair.access_token),
                        self._digest("refresh", pair.refresh_token),
                        _isoformat(pair.access_expires_at),
                        _isoformat(now),
                        row["id"],
                    ),
                )
                connection.execute(
                    "DELETE FROM consumed_refresh_credentials WHERE expires_at <= ?",
                    (_isoformat(now),),
                )
                connection.commit()
                return pair
            except SessionStoreError:
                raise
            except sqlite3.Error as error:
                connection.rollback()
                raise SessionStoreError("session_store_unavailable") from error
            finally:
                connection.close()
                self._secure_files()

    def revoke(self, credential: str) -> bool:
        _require_secret(credential, "invalid_session_credential")
        now = self._now()
        access_hash = self._digest("access", credential)
        refresh_hash = self._digest("refresh", credential)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, organization_id, actor_id, team_id FROM human_sessions
                WHERE access_hash = ? OR refresh_hash = ?
                """,
                (access_hash, refresh_hash),
            ).fetchone()
            if row is None:
                consumed = connection.execute(
                    """
                    SELECT sessions.id, sessions.organization_id,
                           sessions.actor_id, sessions.team_id
                    FROM consumed_refresh_credentials AS consumed
                    JOIN human_sessions AS sessions ON sessions.id = consumed.session_id
                    WHERE consumed.credential_hash = ?
                    """,
                    (refresh_hash,),
                ).fetchone()
                row = consumed
            if row is None:
                return False
            connection.execute(
                "UPDATE human_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_isoformat(now), row["id"]),
            )
            self._record_event(
                connection,
                session_id=str(row["id"]),
                event_type="logout",
                occurred_at=now,
                organization_id=str(row["organization_id"]),
                target_actor_id=str(row["actor_id"]),
                target_team_id=str(row["team_id"]),
            )
            return True

    def revoke_session(
        self,
        session_id: str,
        *,
        event_type: str,
        organization_id: str | None = None,
    ) -> None:
        now = self._now()
        if organization_id is not None:
            _require_binding(organization_id, "invalid_organization_id")
        scope = " AND organization_id = ?" if organization_id is not None else ""
        parameters: tuple[object, ...] = (
            (session_id, organization_id)
            if organization_id is not None
            else (session_id,)
        )
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT organization_id, actor_id, team_id
                FROM human_sessions WHERE id = ?{scope}
                """,
                parameters,
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE human_sessions SET revoked_at = ? WHERE id = ? "
                    "AND organization_id = ? AND revoked_at IS NULL",
                    (_isoformat(now), session_id, row["organization_id"]),
                )
            self._record_event(
                connection,
                session_id=session_id,
                event_type=event_type,
                occurred_at=now,
                organization_id=str(row["organization_id"]) if row is not None else None,
                target_actor_id=str(row["actor_id"]) if row is not None else None,
                target_team_id=str(row["team_id"]) if row is not None else None,
            )




    @staticmethod
    def _require_active_membership(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        """Called inside the credential operation's transaction, never from a cache."""
        if row["membership_id"] is None:
            managed = connection.execute(
                "SELECT id FROM onboarding_organizations WHERE id = ?", (row["organization_id"],),
            ).fetchone()
            if managed is not None:
                raise SessionStoreError("session_authorization_removed")
            return
        member = connection.execute(
            """
            SELECT allowed_clients FROM onboarding_memberships
            WHERE id = ? AND organization_id = ? AND issuer = ? AND subject = ?
              AND id = ? AND team_id = ? AND clearance = ?
              AND status = 'active' AND authorization_version = ?
            """,
            (row["membership_id"], row["organization_id"], row["issuer"], row["subject"],
             row["actor_id"], row["team_id"], row["clearance"], row["authorization_version"]),
        ).fetchone()
        if member is None or row["client_name"] not in json.loads(member["allowed_clients"]):
            raise SessionStoreError("session_authorization_removed")

    def _new_pair(
        self,
        *,
        now: datetime,
        absolute_expires_at: datetime,
    ) -> SessionCredentialPair:
        access_expires_at = min(now + self._access_ttl, absolute_expires_at)
        return SessionCredentialPair(
            access_token="hox_a_" + secrets.token_urlsafe(32),
            refresh_token="hox_r_" + secrets.token_urlsafe(32),
            access_expires_at=access_expires_at,
            session_expires_at=absolute_expires_at,
        )

    def _digest(self, purpose: str, value: str) -> bytes:
        return hmac.new(
            self._hash_key,
            (purpose + "\x00" + value).encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def _encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._encryption_key).encrypt(nonce, plaintext, associated_data)

    def _decrypt(self, stored: bytes, *, associated_data: bytes) -> bytes:
        if len(stored) < 29:
            raise SessionStoreError("session_store_corrupt_flow")
        try:
            return AESGCM(self._encryption_key).decrypt(stored[:12], stored[12:], associated_data)
        except (ValueError, InvalidTag) as error:
            raise SessionStoreError("session_store_corrupt_flow") from error

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise SessionStoreError("session_store_naive_clock")
        return value.astimezone(timezone.utc)

    def _prepare_storage_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise SessionStoreError("session_store_open_failed") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SessionStoreError("session_store_regular_file_required")
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        if self.path.is_symlink():
            raise SessionStoreError("session_store_symlink_refused")

    def _connect(self) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise SessionStoreError("session_store_symlink_refused")
            connection = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA secure_delete = ON")
            return connection
        except sqlite3.Error:
            raise SessionStoreError("session_store_unavailable") from None

    def check_available(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("SELECT id FROM human_sessions LIMIT 0")

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        except sqlite3.Error as error:
            raise SessionStoreError("session_store_unavailable") from error
        finally:
            connection.close()
            self._secure_files()

    def _initialize(self) -> None:
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SESSION_STORE_SCHEMA_VERSION:
                raise SessionStoreError("session_store_schema_newer_than_binary")
            if version not in {0, 2, 3, SESSION_STORE_SCHEMA_VERSION}:
                raise SessionStoreError("session_store_schema_migration_required")
            if version == SESSION_STORE_SCHEMA_VERSION:
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_session_security_events_admin_scope
                    ON session_security_events(
                        organization_id, event_type, target_actor_id,
                        target_team_id, occurred_at, id
                    )
                    """
                )
                self._validate_content_free_schema(connection)
                return
            if version in {2, 3}:
                self._migrate_onboarding_schema(connection)
                return
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            # Another process may have initialized this file since our first
            # version read. Never reset a committed schema to the v2 baseline.
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version in {2, 3, SESSION_STORE_SCHEMA_VERSION}:
                self._migrate_onboarding_schema(connection)
                return
            if version > SESSION_STORE_SCHEMA_VERSION:
                raise SessionStoreError("session_store_schema_newer_than_binary")
            if version != 0:
                raise SessionStoreError("session_store_schema_migration_required")
            if connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone() is not None:
                raise SessionStoreError("session_store_schema_incompatible")
            # Execute this fixed DDL one statement at a time: executescript
            # would implicitly commit before the entire initialization finishes.
            for statement in """
                CREATE TABLE IF NOT EXISTS session_enrollments (
                    id TEXT PRIMARY KEY,
                    secret_hash BLOB,
                    issuer TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'authorizing', 'exchanging', 'authorized', 'redeemed', 'failed')
                    ),
                    state_hash BLOB UNIQUE,
                    browser_cookie_hash BLOB,
                    encrypted_flow BLOB,
                    subject TEXT,
                    organization_id TEXT,
                    actor_id TEXT,
                    team_id TEXT,
                    clearance TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    authorization_started_at TEXT,
                    authorized_at TEXT,
                    redeemed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_session_enrollment_expiry
                    ON session_enrollments(status, expires_at);
                CREATE TABLE IF NOT EXISTS human_sessions (
                    id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    access_hash BLOB NOT NULL UNIQUE,
                    refresh_hash BLOB NOT NULL UNIQUE,
                    access_expires_at TEXT NOT NULL,
                    absolute_expires_at TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK (generation >= 0),
                    created_at TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_human_sessions_subject
                    ON human_sessions(issuer, subject, revoked_at);
                CREATE INDEX IF NOT EXISTS idx_human_sessions_admin_scope
                    ON human_sessions(organization_id, revoked_at, actor_id, team_id, created_at, id);
                CREATE TABLE IF NOT EXISTS consumed_refresh_credentials (
                    credential_hash BLOB PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES human_sessions(id) ON DELETE CASCADE,
                    consumed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_security_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    organization_id TEXT,
                    target_actor_id TEXT,
                    target_team_id TEXT,
                    decision_actor_id TEXT,
                    decision_scope TEXT,
                    reason_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_session_security_events_admin_scope
                    ON session_security_events(
                        organization_id, event_type, target_actor_id,
                        target_team_id, occurred_at, id
                    );
                PRAGMA user_version = 2;
                """.split(";"):
                if statement.strip():
                    connection.execute(statement)
            self._migrate_onboarding_schema(connection)

    def _migrate_onboarding_schema(self, connection: sqlite3.Connection) -> None:
        # Preserve v2 hash/encryption derivation: adding membership does not rotate
        # existing credentials. DDL, validation and version change commit together.
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version in {3, SESSION_STORE_SCHEMA_VERSION}:
            self._migrate_console_schema(connection)
            return
        if version != 2 or not validate_session_schema(connection, version=2):
            raise SessionStoreError("session_store_schema_incompatible")
        for table in ("session_enrollments", "human_sessions"):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN membership_id TEXT")
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN authorization_version INTEGER NOT NULL DEFAULT 1 CHECK (authorization_version >= 1)"
            )
        connection.execute("ALTER TABLE session_enrollments ADD COLUMN invitation_id TEXT")
        for statement in ONBOARDING_TABLE_DDL.values():
            connection.execute(statement)
        for statement in ONBOARDING_INDEX_DDL:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 3")
        self._migrate_console_schema(connection)

    def _migrate_console_schema(self, connection: sqlite3.Connection) -> None:
        # Membership removal and console revocation share this database/transaction.
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == SESSION_STORE_SCHEMA_VERSION:
            self._validate_content_free_schema(connection)
            return
        if version != 3 or not validate_session_schema(connection, version=3):
            raise SessionStoreError("session_store_schema_incompatible")
        for statement in CONSOLE_TABLE_DDL.values():
            connection.execute(statement)
        for statement in CONSOLE_INDEX_DDL:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 4")
        self._validate_content_free_schema(connection)

    def _validate_content_free_schema(self, connection: sqlite3.Connection) -> None:
        if not validate_session_schema(connection):
            raise SessionStoreError("session_store_schema_incompatible")


    def _record_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        event_type: str,
        occurred_at: datetime,
        organization_id: str | None = None,
        target_actor_id: str | None = None,
        target_team_id: str | None = None,
        decision_actor_id: str | None = None,
        decision_scope: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO session_security_events (
                id, occurred_at, session_id, event_type, organization_id,
                target_actor_id, target_team_id, decision_actor_id,
                decision_scope, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sev_" + secrets.token_urlsafe(18),
                _isoformat(occurred_at),
                session_id,
                event_type,
                organization_id,
                target_actor_id,
                target_team_id,
                decision_actor_id,
                decision_scope,
                reason_code,
            ),
        )

    def _secure_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists() and not candidate.is_symlink():
                try:
                    candidate.chmod(0o600)
                except OSError:
                    pass


def _require_secret(value: str, code: str) -> None:
    if (
        not isinstance(value, str) or not 20 <= len(value.encode("utf-8")) <= 4096
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise SessionStoreError(code)


def _require_binding(value: str, code: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise SessionStoreError(code)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as error:
        raise SessionStoreError("session_store_corrupt_timestamp") from error
    if parsed.tzinfo is None:
        raise SessionStoreError("session_store_corrupt_timestamp")
    return parsed.astimezone(timezone.utc)
