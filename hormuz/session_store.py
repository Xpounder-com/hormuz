from __future__ import annotations

import base64
import binascii
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


SESSION_STORE_SCHEMA_VERSION = 2
_ADMIN_REASON_CODES = {
    "access_change",
    "employment_change",
    "security_incident",
    "administrative",
}
SESSION_SECURITY_EVENT_TYPES = frozenset(
    {
        "refresh_replay",
        "logout",
        "authorization_mapping_removed",
        "admin_revocation",
    }
)


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
    organization_id: str
    actor_id: str
    team_id: str
    clearance: str
    access_expires_at: datetime
    session_expires_at: datetime


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    organization_id: str
    actor_id: str
    team_id: str
    client_name: str
    created_at: datetime
    refreshed_at: datetime
    access_expires_at: datetime
    session_expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "organization_id": self.organization_id,
            "actor_id": self.actor_id,
            "team_id": self.team_id,
            "client_name": self.client_name,
            "created_at": _isoformat(self.created_at),
            "refreshed_at": _isoformat(self.refreshed_at),
            "access_expires_at": _isoformat(self.access_expires_at),
            "session_expires_at": _isoformat(self.session_expires_at),
        }


@dataclass(frozen=True)
class SessionSecurityEvent:
    event_id: str
    occurred_at: datetime
    session_id: str
    event_type: str
    organization_id: str
    target_actor_id: str
    target_team_id: str
    decision_actor_id: str | None
    decision_scope: str | None
    reason_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "occurred_at": _isoformat(self.occurred_at),
            "session_id": self.session_id,
            "event_type": self.event_type,
            "organization_id": self.organization_id,
            "target_actor_id": self.target_actor_id,
            "target_team_id": self.target_team_id,
            "decision_actor_id": self.decision_actor_id,
            "decision_scope": self.decision_scope,
            "reason_code": self.reason_code,
        }


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
                       actor_id, team_id, clearance, status, expires_at
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
                    created_at, refreshed_at, organization_id, actor_id, team_id,
                    clearance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
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
                organization_id=str(row["organization_id"]),
                actor_id=str(row["actor_id"]),
                team_id=str(row["team_id"]),
                clearance=str(row["clearance"]),
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

    def revoke_session(self, session_id: str, *, event_type: str) -> None:
        now = self._now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT organization_id, actor_id, team_id
                FROM human_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            connection.execute(
                "UPDATE human_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_isoformat(now), session_id),
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

    def list_active_sessions(
        self,
        *,
        organization_id: str,
        limit: int,
        cursor: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[tuple[SessionSummary, ...], str | None]:
        _require_binding(organization_id, "invalid_organization_id")
        if actor_id is not None:
            _require_binding(actor_id, "invalid_actor_id")
        if team_id is not None:
            _require_binding(team_id, "invalid_team_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SessionStoreError("invalid_session_page_limit")
        before = _decode_cursor(cursor) if cursor is not None else None
        now = self._now()
        conditions = [
            "organization_id = ?",
            "revoked_at IS NULL",
            "absolute_expires_at > ?",
        ]
        parameters: list[object] = [organization_id, _isoformat(now)]
        if actor_id is not None:
            conditions.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            conditions.append("team_id = ?")
            parameters.append(team_id)
        if before is not None:
            conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
            parameters.extend((before[0], before[0], before[1]))
        parameters.append(limit + 1)
        query = (
            "SELECT id, organization_id, actor_id, team_id, client_name, created_at, "
            "refreshed_at, access_expires_at, absolute_expires_at FROM human_sessions WHERE "
            + " AND ".join(conditions)
            + " ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(
            SessionSummary(
                session_id=str(row["id"]),
                organization_id=str(row["organization_id"]),
                actor_id=str(row["actor_id"]),
                team_id=str(row["team_id"]),
                client_name=str(row["client_name"]),
                created_at=_parse_time(row["created_at"]),
                refreshed_at=_parse_time(row["refreshed_at"]),
                access_expires_at=_parse_time(row["access_expires_at"]),
                session_expires_at=_parse_time(row["absolute_expires_at"]),
            )
            for row in selected
        )
        next_cursor = (
            _encode_cursor(str(selected[-1]["created_at"]), str(selected[-1]["id"]))
            if has_more and selected
            else None
        )
        return items, next_cursor

    def revoke_administratively(
        self,
        *,
        organization_id: str,
        decision_actor_id: str,
        reason_code: str,
        session_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> int:
        _require_binding(organization_id, "invalid_organization_id")
        _require_binding(decision_actor_id, "invalid_decision_actor_id")
        if reason_code not in _ADMIN_REASON_CODES:
            raise SessionStoreError("invalid_admin_revocation_reason")
        selectors = [session_id is not None, actor_id is not None, team_id is not None]
        if sum(selectors) > 1:
            raise SessionStoreError("invalid_admin_revocation_selector")
        conditions = [
            "organization_id = ?",
            "revoked_at IS NULL",
            "absolute_expires_at > ?",
        ]
        now = self._now()
        parameters: list[object] = [organization_id, _isoformat(now)]
        scope = "organization"
        if session_id is not None:
            _require_binding(session_id, "invalid_session_id")
            conditions.append("id = ?")
            parameters.append(session_id)
            scope = "session"
        elif actor_id is not None:
            _require_binding(actor_id, "invalid_actor_id")
            conditions.append("actor_id = ?")
            parameters.append(actor_id)
            scope = "actor"
        elif team_id is not None:
            _require_binding(team_id, "invalid_team_id")
            conditions.append("team_id = ?")
            parameters.append(team_id)
            scope = "team"
        query = (
            "SELECT id, organization_id, actor_id, team_id FROM human_sessions WHERE "
            + " AND ".join(conditions)
            + " ORDER BY id"
        )
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(query, tuple(parameters)).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE human_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                    (_isoformat(now), row["id"]),
                )
                self._record_event(
                    connection,
                    session_id=str(row["id"]),
                    event_type="admin_revocation",
                    occurred_at=now,
                    organization_id=str(row["organization_id"]),
                    target_actor_id=str(row["actor_id"]),
                    target_team_id=str(row["team_id"]),
                    decision_actor_id=decision_actor_id,
                    decision_scope=scope,
                    reason_code=reason_code,
                )
            return len(rows)

    def list_security_events(
        self,
        *,
        organization_id: str,
        limit: int,
        cursor: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
    ) -> tuple[tuple[SessionSecurityEvent, ...], str | None]:
        _require_binding(organization_id, "invalid_organization_id")
        if actor_id is not None:
            _require_binding(actor_id, "invalid_actor_id")
        if team_id is not None:
            _require_binding(team_id, "invalid_team_id")
        if event_type is not None and event_type not in SESSION_SECURITY_EVENT_TYPES:
            raise SessionStoreError("invalid_session_event_type")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SessionStoreError("invalid_session_page_limit")
        if since is not None:
            if not isinstance(since, datetime) or since.tzinfo is None:
                raise SessionStoreError("invalid_session_event_since")
            since = since.astimezone(timezone.utc)
        before = _decode_cursor(cursor) if cursor is not None else None
        conditions = ["organization_id = ?"]
        parameters: list[object] = [organization_id]
        if actor_id is not None:
            conditions.append("target_actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            conditions.append("target_team_id = ?")
            parameters.append(team_id)
        if event_type is not None:
            conditions.append("event_type = ?")
            parameters.append(event_type)
        if since is not None:
            conditions.append("occurred_at >= ?")
            parameters.append(_isoformat(since))
        if before is not None:
            conditions.append("(occurred_at < ? OR (occurred_at = ? AND id < ?))")
            parameters.extend((before[0], before[0], before[1]))
        parameters.append(limit + 1)
        query = (
            "SELECT id, occurred_at, session_id, event_type, organization_id, "
            "target_actor_id, target_team_id, decision_actor_id, decision_scope, "
            "reason_code FROM session_security_events WHERE "
            + " AND ".join(conditions)
            + " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(
            SessionSecurityEvent(
                event_id=str(row["id"]),
                occurred_at=_parse_time(row["occurred_at"]),
                session_id=str(row["session_id"]),
                event_type=str(row["event_type"]),
                organization_id=str(row["organization_id"]),
                target_actor_id=str(row["target_actor_id"]),
                target_team_id=str(row["target_team_id"]),
                decision_actor_id=(
                    str(row["decision_actor_id"])
                    if row["decision_actor_id"] is not None
                    else None
                ),
                decision_scope=(
                    str(row["decision_scope"])
                    if row["decision_scope"] is not None
                    else None
                ),
                reason_code=(
                    str(row["reason_code"]) if row["reason_code"] is not None else None
                ),
            )
            for row in selected
        )
        next_cursor = (
            _encode_cursor(str(selected[-1]["occurred_at"]), str(selected[-1]["id"]))
            if has_more and selected
            else None
        )
        return items, next_cursor

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
            if version not in {0, 1, SESSION_STORE_SCHEMA_VERSION}:
                raise SessionStoreError("session_store_schema_migration_required")
            if version == 1:
                self._migrate_v1_to_v2(connection)
                return
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
                return
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
                COMMIT;
                """
            )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        now = _isoformat(self._now())
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table, definition in (
                ("session_enrollments", "organization_id TEXT"),
                ("session_enrollments", "actor_id TEXT"),
                ("session_enrollments", "team_id TEXT"),
                ("session_enrollments", "clearance TEXT"),
                (
                    "human_sessions",
                    "organization_id TEXT NOT NULL DEFAULT '__legacy_unbound__'",
                ),
                (
                    "human_sessions",
                    "actor_id TEXT NOT NULL DEFAULT '__legacy_unbound__'",
                ),
                (
                    "human_sessions",
                    "team_id TEXT NOT NULL DEFAULT '__legacy_unbound__'",
                ),
                (
                    "human_sessions",
                    "clearance TEXT NOT NULL DEFAULT '__legacy_unbound__'",
                ),
                ("session_security_events", "organization_id TEXT"),
                ("session_security_events", "target_actor_id TEXT"),
                ("session_security_events", "target_team_id TEXT"),
                ("session_security_events", "decision_actor_id TEXT"),
                ("session_security_events", "decision_scope TEXT"),
                ("session_security_events", "reason_code TEXT"),
            ):
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
            legacy = connection.execute(
                "SELECT id FROM human_sessions WHERE revoked_at IS NULL ORDER BY id"
            ).fetchall()
            for row in legacy:
                connection.execute(
                    "UPDATE human_sessions SET revoked_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                self._record_event(
                    connection,
                    session_id=str(row["id"]),
                    event_type="migration_identity_binding_required",
                    occurred_at=self._now(),
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_human_sessions_admin_scope
                ON human_sessions(organization_id, revoked_at, actor_id, team_id, created_at, id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_security_events_admin_scope
                ON session_security_events(
                    organization_id, event_type, target_actor_id,
                    target_team_id, occurred_at, id
                )
                """
            )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise SessionStoreError("session_store_migration_failed") from error

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
    if not isinstance(value, str) or not 20 <= len(value.encode("utf-8")) <= 4096:
        raise SessionStoreError(code)


def _require_binding(value: str, code: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise SessionStoreError(code)


def _encode_cursor(created_at: str, session_id: str) -> str:
    value = json.dumps([created_at, session_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in value)
    ):
        raise SessionStoreError("invalid_session_cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError, binascii.Error, RecursionError) as error:
        raise SessionStoreError("invalid_session_cursor") from error
    if (
        not isinstance(parsed, list)
        or len(parsed) != 2
        or not all(isinstance(item, str) for item in parsed)
    ):
        raise SessionStoreError("invalid_session_cursor")
    try:
        _parse_time(parsed[0])
        _require_binding(parsed[1], "invalid_session_cursor")
    except SessionStoreError as error:
        raise SessionStoreError("invalid_session_cursor") from error
    return parsed[0], parsed[1]
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
