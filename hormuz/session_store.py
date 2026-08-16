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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SESSION_STORE_SCHEMA_VERSION = 1


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


@dataclass(frozen=True)
class AuthorizationFlow:
    enrollment_id: str
    issuer: str
    client_name: str
    nonce: str
    pkce_verifier: str


@dataclass(frozen=True)
class SessionCredentialPair:
    access_token: str
    refresh_token: str
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
    access_expires_at: datetime
    session_expires_at: datetime


class SQLiteSessionStore:
    """Single-node opaque-session store with atomic credential-family rotation."""

    def __init__(
        self,
        path: Path,
        *,
        master_key: bytes,
        access_ttl_seconds: int,
        absolute_ttl_seconds: int,
        enrollment_ttl_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ):
        if len(master_key) != 32:
            raise SessionStoreError("session_store_invalid_master_key")
        self.path = path
        self._hash_key = hmac.new(
            master_key,
            b"hormuz/session/hash/v1",
            hashlib.sha256,
        ).digest()
        self._encryption_key = hmac.new(
            master_key,
            b"hormuz/session/encryption/v1",
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
    ) -> Enrollment:
        _require_secret(enrollment_secret, "invalid_enrollment_secret")
        now = self._now()
        enrollment = Enrollment(
            enrollment_id=secrets.token_urlsafe(24),
            issuer=issuer,
            client_name=client_name,
            expires_at=now + self._enrollment_ttl,
        )
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO session_enrollments (
                    id, secret_hash, issuer, client_name, status,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    enrollment.enrollment_id,
                    self._digest("enrollment", enrollment_secret),
                    issuer,
                    client_name,
                    _isoformat(now),
                    _isoformat(enrollment.expires_at),
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
                "SELECT issuer, client_name, status, expires_at FROM session_enrollments WHERE id = ?",
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
                SELECT id, issuer, client_name, browser_cookie_hash, encrypted_flow, expires_at
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
            )

    def authorize_enrollment(self, *, enrollment_id: str, subject: str) -> None:
        if not subject or len(subject.encode("utf-8")) > 1024:
            raise SessionStoreError("invalid_subject")
        now = self._now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE session_enrollments
                SET status = 'authorized', subject = ?, authorized_at = ?, encrypted_flow = NULL
                WHERE id = ? AND status = 'exchanging' AND expires_at > ?
                """,
                (subject, _isoformat(now), enrollment_id, _isoformat(now)),
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
        session_id = secrets.token_urlsafe(24)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT secret_hash, issuer, subject, client_name, status, expires_at
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
            connection.execute(
                """
                INSERT INTO human_sessions (
                    id, issuer, subject, client_name, access_hash, refresh_hash,
                    access_expires_at, absolute_expires_at, generation,
                    created_at, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
                SELECT id, issuer, subject, client_name, access_expires_at,
                       absolute_expires_at, revoked_at
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
            return SessionPrincipal(
                session_id=str(row["id"]),
                issuer=str(row["issuer"]),
                subject=str(row["subject"]),
                client_name=str(row["client_name"]),
                access_expires_at=access_expires_at,
                session_expires_at=absolute_expires_at,
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
                    SELECT id, absolute_expires_at, revoked_at
                    FROM human_sessions WHERE refresh_hash = ?
                    """,
                    (old_hash,),
                ).fetchone()
                if row is None:
                    consumed = connection.execute(
                        "SELECT session_id FROM consumed_refresh_credentials WHERE credential_hash = ?",
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
                        )
                        connection.commit()
                        raise SessionStoreError("refresh_replay_detected")
                    connection.rollback()
                    raise SessionStoreError("invalid_session_credential")
                absolute_expires_at = _parse_time(row["absolute_expires_at"])
                if row["revoked_at"] is not None or absolute_expires_at <= now:
                    connection.rollback()
                    raise SessionStoreError("expired_session_credential")
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
                SELECT id FROM human_sessions
                WHERE access_hash = ? OR refresh_hash = ?
                """,
                (access_hash, refresh_hash),
            ).fetchone()
            if row is None:
                consumed = connection.execute(
                    "SELECT session_id AS id FROM consumed_refresh_credentials WHERE credential_hash = ?",
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
            )
            return True

    def revoke_session(self, session_id: str, *, event_type: str) -> None:
        now = self._now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE human_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_isoformat(now), session_id),
            )
            self._record_event(
                connection,
                session_id=session_id,
                event_type=event_type,
                occurred_at=now,
            )

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
        except ValueError as error:
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
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA secure_delete = ON")
        return connection

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
            if version not in {0, SESSION_STORE_SCHEMA_VERSION}:
                raise SessionStoreError("session_store_schema_migration_required")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
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
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_human_sessions_subject
                    ON human_sessions(issuer, subject, revoked_at);
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
                    event_type TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                COMMIT;
                """
            )

    def _record_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO session_security_events (id, occurred_at, session_id, event_type)
            VALUES (?, ?, ?, ?)
            """,
            (secrets.token_urlsafe(18), _isoformat(occurred_at), session_id, event_type),
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
    if not isinstance(value, str) or not 20 <= len(value.encode("utf-8")) <= 4096:
        raise SessionStoreError(code)
    if any(character in value for character in ("\n", "\r", "\x00")):
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
