"""Multi-instance PostgreSQL human-session repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
from typing import Callable, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .postgres import (
    DEFAULT_POSTGRES_RUNTIME_ROLE,
    DEFAULT_POSTGRES_SCHEMA,
    PostgresStorageError,
    TenantContext,
    _open_connection,
    tenant_transaction,
    validate_postgres_identifier,
    validate_tenant_id,
)
from .session_store import (
    SESSION_SECURITY_EVENT_TYPES,
    AuthorizationFlow,
    Enrollment,
    SessionCredentialPair,
    SessionPrincipal,
    SessionSecurityEvent,
    SessionStoreError,
    SessionSummary,
    _ADMIN_REASON_CODES,
    _decode_cursor,
    _encode_cursor,
    _require_binding,
    _require_secret,
)


def _time(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SessionStoreError("session_store_corrupt_timestamp")
    return value.astimezone(timezone.utc)


class PostgresSessionStore:
    """Tenant-routed sessions with transaction-local RLS and atomic rotation."""

    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        organization_ids: tuple[str, ...],
        master_key: bytes,
        access_ttl_seconds: int,
        absolute_ttl_seconds: int,
        enrollment_ttl_seconds: int,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
        connect: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise SessionStoreError("postgres_dsn_unavailable")
        if len(master_key) != 32:
            raise SessionStoreError("session_store_invalid_master_key")
        normalized = tuple(sorted(set(organization_ids)))
        if not normalized:
            raise SessionStoreError("session_store_tenant_set_empty")
        for organization_id in normalized:
            validate_tenant_id(organization_id)
        self._dsn = dsn
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
        self.organization_ids = normalized
        self._connect = connect
        self._qualified = '"' + self.schema + '"'
        self._hash_key = hmac.new(master_key, b"hormuz/session/hash/v1", hashlib.sha256).digest()
        self._encryption_key = hmac.new(
            master_key, b"hormuz/session/encryption/v1", hashlib.sha256
        ).digest()
        self._routing_key = hmac.new(
            master_key, b"hormuz/session/tenant-routing/v1", hashlib.sha256
        ).digest()
        self._access_ttl = timedelta(seconds=access_ttl_seconds)
        self._absolute_ttl = timedelta(seconds=absolute_ttl_seconds)
        self._enrollment_ttl = timedelta(seconds=enrollment_ttl_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        routes = {self._routing_tag(value): value for value in normalized}
        if len(routes) != len(normalized):
            raise SessionStoreError("session_store_tenant_route_collision")
        self._routes = routes

    def _routing_tag(self, organization_id: str) -> str:
        digest = hmac.new(
            self._routing_key,
            organization_id.encode("utf-8"),
            hashlib.sha256,
        ).digest()[:12]
        return digest.hex()

    def _routed_value(self, prefix: str, organization_id: str, size: int) -> str:
        return prefix + self._routing_tag(organization_id) + "_" + secrets.token_urlsafe(size)

    def _route(self, value: str, prefix: str, code: str) -> str:
        _require_secret(value, code)
        if not value.startswith(prefix):
            raise SessionStoreError(code)
        tag, separator, _secret = value[len(prefix):].partition("_")
        organization_id = self._routes.get(tag) if separator else None
        if organization_id is None:
            raise SessionStoreError(code)
        return organization_id

    def new_authorization_state(self, enrollment_id: str) -> str:
        organization_id = self._route(enrollment_id, "hox_e_", "enrollment_unavailable")
        return self._routed_value("hox_s_", organization_id, 32)

    @contextmanager
    def _transaction(
        self,
        organization_id: str,
        *,
        principal_id: str = "session-broker",
        client_id: str = "hormuz-session",
        authorization_version: int = 1,
    ) -> Iterator[object]:
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            context = TenantContext(
                organization_id,
                principal_id,
                client_id,
                authorization_version,
            )
            with tenant_transaction(
                connection,
                context,
                runtime_role=self.runtime_role,
                schema=self.schema,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except SessionStoreError:
            raise
        except PostgresStorageError as error:
            raise SessionStoreError(error.code) from None
        finally:
            connection.close()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise SessionStoreError("session_store_naive_clock")
        return value.astimezone(timezone.utc)

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
        except ValueError:
            raise SessionStoreError("session_store_corrupt_flow") from None

    def _new_pair(
        self,
        organization_id: str,
        *,
        now: datetime,
        absolute_expires_at: datetime,
    ) -> SessionCredentialPair:
        return SessionCredentialPair(
            access_token=self._routed_value("hox_a_", organization_id, 32),
            refresh_token=self._routed_value("hox_r_", organization_id, 32),
            access_expires_at=min(now + self._access_ttl, absolute_expires_at),
            session_expires_at=absolute_expires_at,
        )

    def create_enrollment(
        self,
        *,
        issuer: str,
        client_name: str,
        enrollment_secret: str,
        organization_id: str | None = None,
    ) -> Enrollment:
        _require_secret(enrollment_secret, "invalid_enrollment_secret")
        if organization_id is None or organization_id not in self.organization_ids:
            raise SessionStoreError("invalid_organization_id")
        now = self._now()
        enrollment = Enrollment(
            enrollment_id=self._routed_value("hox_e_", organization_id, 24),
            issuer=issuer,
            client_name=client_name,
            expires_at=now + self._enrollment_ttl,
            organization_id=organization_id,
        )
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "INSERT INTO gateway_session_enrollments ("
                    "tenant_id, id, secret_hash, issuer, client_name, status, created_at, expires_at"
                    ") VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)",
                    (
                        organization_id,
                        enrollment.enrollment_id,
                        self._digest("enrollment", enrollment_secret),
                        issuer,
                        client_name,
                        now,
                        enrollment.expires_at,
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
        organization_id = self._route(enrollment_id, "hox_e_", "enrollment_unavailable")
        if self._route(state, "hox_s_", "invalid_authorization_state") != organization_id:
            raise SessionStoreError("invalid_authorization_state")
        for value, code in (
            (browser_cookie, "invalid_browser_cookie"),
            (nonce, "invalid_oidc_nonce"),
            (pkce_verifier, "invalid_pkce_verifier"),
        ):
            _require_secret(value, code)
        now = self._now()
        encrypted = self._encrypt(
            json.dumps(
                {"nonce": nonce, "pkce_verifier": pkce_verifier},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            associated_data=enrollment_id.encode("utf-8"),
        )
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT issuer, client_name, status, expires_at "
                    "FROM gateway_session_enrollments WHERE tenant_id = %s AND id = %s FOR UPDATE",
                    (organization_id, enrollment_id),
                )
                row = cursor.fetchone()
                if row is None or row[2] != "pending" or _time(row[3]) <= now:
                    raise PostgresStorageError("enrollment_unavailable")
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'authorizing', "
                    "state_hash = %s, browser_cookie_hash = %s, encrypted_flow = %s, "
                    "authorization_started_at = %s WHERE tenant_id = %s AND id = %s",
                    (
                        self._digest("state", state),
                        self._digest("browser", browser_cookie),
                        encrypted,
                        now,
                        organization_id,
                        enrollment_id,
                    ),
                )
        return AuthorizationFlow(
            enrollment_id=enrollment_id,
            issuer=str(row[0]),
            client_name=str(row[1]),
            nonce=nonce,
            pkce_verifier=pkce_verifier,
            organization_id=organization_id,
        )

    def consume_callback(self, *, state: str, browser_cookie: str) -> AuthorizationFlow:
        organization_id = self._route(state, "hox_s_", "invalid_callback_state")
        _require_secret(browser_cookie, "invalid_browser_cookie")
        now = self._now()
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT id, issuer, client_name, browser_cookie_hash, encrypted_flow, expires_at "
                    "FROM gateway_session_enrollments WHERE tenant_id = %s AND state_hash = %s "
                    "AND status = 'authorizing' FOR UPDATE",
                    (organization_id, self._digest("state", state)),
                )
                row = cursor.fetchone()
                if (
                    row is None
                    or not hmac.compare_digest(bytes(row[3]), self._digest("browser", browser_cookie))
                    or _time(row[5]) <= now
                ):
                    raise PostgresStorageError("invalid_callback_state")
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'exchanging', "
                    "state_hash = NULL, browser_cookie_hash = NULL "
                    "WHERE tenant_id = %s AND id = %s AND status = 'authorizing'",
                    (organization_id, row[0]),
                )
                try:
                    plaintext = self._decrypt(
                        bytes(row[4]), associated_data=str(row[0]).encode("utf-8")
                    )
                except SessionStoreError as error:
                    raise PostgresStorageError(error.code) from None
        try:
            flow = json.loads(plaintext)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            raise SessionStoreError("session_store_corrupt_flow") from None
        return AuthorizationFlow(
            enrollment_id=str(row[0]),
            issuer=str(row[1]),
            client_name=str(row[2]),
            nonce=str(flow["nonce"]),
            pkce_verifier=str(flow["pkce_verifier"]),
            organization_id=organization_id,
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
        routed = self._route(enrollment_id, "hox_e_", "enrollment_unavailable")
        if routed != organization_id:
            raise SessionStoreError("enrollment_unavailable")
        for value, code in (
            (subject, "invalid_subject"),
            (actor_id, "invalid_actor_id"),
            (team_id, "invalid_team_id"),
            (clearance, "invalid_clearance"),
        ):
            _require_binding(value, code)
        now = self._now()
        with self._transaction(organization_id, principal_id=actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT principal.authorization_version, principal.disabled_at, "
                    "projection.team_id, projection.clearance "
                    "FROM external_identities AS external "
                    "JOIN principals AS principal USING (tenant_id, principal_id) "
                    "JOIN gateway_principal_projections AS projection USING (tenant_id, principal_id) "
                    "WHERE external.tenant_id = %s AND external.issuer = ("
                    "SELECT issuer FROM gateway_session_enrollments "
                    "WHERE tenant_id = %s AND id = %s) "
                    "AND external.subject = %s AND external.principal_id = %s",
                    (organization_id, organization_id, enrollment_id, subject, actor_id),
                )
                binding = cursor.fetchone()
                if (
                    binding is None
                    or binding[1] is not None
                    or str(binding[2]) != team_id
                    or str(binding[3]) != clearance
                ):
                    raise PostgresStorageError("identity_projection_mismatch")
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'authorized', subject = %s, "
                    "actor_id = %s, team_id = %s, clearance = %s, authorization_version = %s, "
                    "authorized_at = %s, encrypted_flow = NULL WHERE tenant_id = %s AND id = %s "
                    "AND status = 'exchanging' AND expires_at > %s",
                    (
                        subject,
                        actor_id,
                        team_id,
                        clearance,
                        int(binding[0]),
                        now,
                        organization_id,
                        enrollment_id,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PostgresStorageError("enrollment_unavailable")

    def fail_enrollment(self, *, enrollment_id: str) -> None:
        organization_id = self._route(enrollment_id, "hox_e_", "enrollment_unavailable")
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'failed', "
                    "encrypted_flow = NULL, state_hash = NULL, browser_cookie_hash = NULL "
                    "WHERE tenant_id = %s AND id = %s AND status IN ('authorizing', 'exchanging')",
                    (organization_id, enrollment_id),
                )

    def redeem_enrollment(
        self, *, enrollment_id: str, enrollment_secret: str
    ) -> SessionCredentialPair:
        organization_id = self._route(enrollment_id, "hox_e_", "enrollment_not_redeemable")
        _require_secret(enrollment_secret, "invalid_enrollment_secret")
        now = self._now()
        pair = self._new_pair(
            organization_id, now=now, absolute_expires_at=now + self._absolute_ttl
        )
        session_id = "ses_" + secrets.token_urlsafe(24)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT secret_hash, issuer, subject, client_name, actor_id, team_id, clearance, "
                    "authorization_version, status, expires_at FROM gateway_session_enrollments "
                    "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                    (organization_id, enrollment_id),
                )
                row = cursor.fetchone()
                if (
                    row is None
                    or row[8] != "authorized"
                    or _time(row[9]) <= now
                    or row[0] is None
                    or not hmac.compare_digest(
                        bytes(row[0]), self._digest("enrollment", enrollment_secret)
                    )
                ):
                    raise PostgresStorageError("enrollment_not_redeemable")
                cursor.execute(
                    "INSERT INTO gateway_human_sessions (tenant_id, id, issuer, subject, "
                    "client_name, access_hash, refresh_hash, access_expires_at, absolute_expires_at, "
                    "generation, created_at, refreshed_at, actor_id, team_id, clearance, "
                    "authorization_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, "
                    "%s, %s, %s, %s, %s, %s)",
                    (
                        organization_id,
                        session_id,
                        row[1],
                        row[2],
                        row[3],
                        self._digest("access", pair.access_token),
                        self._digest("refresh", pair.refresh_token),
                        pair.access_expires_at,
                        pair.session_expires_at,
                        now,
                        now,
                        row[4],
                        row[5],
                        row[6],
                        int(row[7]),
                    ),
                )
                cursor.execute(
                    "UPDATE gateway_session_enrollments SET status = 'redeemed', secret_hash = NULL, "
                    "subject = NULL, redeemed_at = %s WHERE tenant_id = %s AND id = %s",
                    (now, organization_id, enrollment_id),
                )
        return pair

    def authenticate_access(self, access_token: str) -> SessionPrincipal:
        organization_id = self._route(access_token, "hox_a_", "invalid_session_credential")
        now = self._now()
        stale: tuple[str, str, str] | None = None
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT session.id, session.issuer, session.subject, session.client_name, "
                    "session.actor_id, session.team_id, session.clearance, session.access_expires_at, "
                    "session.absolute_expires_at, session.revoked_at, session.authorization_version, "
                    "principal.authorization_version, principal.disabled_at, projection.team_id, "
                    "projection.clearance FROM gateway_human_sessions AS session "
                    "JOIN principals AS principal ON principal.tenant_id = session.tenant_id "
                    "AND principal.principal_id = session.actor_id "
                    "LEFT JOIN gateway_principal_projections AS projection ON "
                    "projection.tenant_id = session.tenant_id AND projection.principal_id = session.actor_id "
                    "WHERE session.tenant_id = %s AND session.access_hash = %s FOR UPDATE OF session",
                    (organization_id, self._digest("access", access_token)),
                )
                row = cursor.fetchone()
                if row is None or row[9] is not None:
                    raise PostgresStorageError("invalid_session_credential")
                if _time(row[7]) <= now or _time(row[8]) <= now:
                    raise PostgresStorageError("expired_session_credential")
                if (
                    int(row[10]) != int(row[11])
                    or row[12] is not None
                    or row[13] is None
                    or str(row[5]) != str(row[13])
                    or str(row[6]) != str(row[14])
                ):
                    cursor.execute(
                        "UPDATE gateway_human_sessions SET revoked_at = %s "
                        "WHERE tenant_id = %s AND id = %s AND revoked_at IS NULL",
                        (now, organization_id, row[0]),
                    )
                    self._record_event(
                        cursor,
                        organization_id=organization_id,
                        session_id=str(row[0]),
                        event_type="authorization_mapping_removed",
                        target_actor_id=str(row[4]),
                        target_team_id=str(row[5]),
                        occurred_at=now,
                    )
                    stale = (str(row[0]), str(row[4]), str(row[5]))
                else:
                    principal = SessionPrincipal(
                        session_id=str(row[0]),
                        issuer=str(row[1]),
                        subject=str(row[2]),
                        client_name=str(row[3]),
                        organization_id=organization_id,
                        actor_id=str(row[4]),
                        team_id=str(row[5]),
                        clearance=str(row[6]),
                        access_expires_at=_time(row[7]),
                        session_expires_at=_time(row[8]),
                        authorization_version=int(row[10]),
                    )
        if stale is not None:
            raise SessionStoreError("invalid_session_credential")
        return principal

    def refresh(self, refresh_token: str) -> SessionCredentialPair:
        organization_id = self._route(refresh_token, "hox_r_", "invalid_session_credential")
        now = self._now()
        old_hash = self._digest("refresh", refresh_token)
        outcome: SessionCredentialPair | str | None = None
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("hormuz:refresh:" + organization_id + ":" + old_hash.hex(),),
                )
                cursor.execute(
                    "SELECT session.id, session.absolute_expires_at, session.revoked_at, "
                    "session.actor_id, session.team_id, session.authorization_version, "
                    "principal.authorization_version, principal.disabled_at "
                    "FROM gateway_human_sessions AS session JOIN principals AS principal "
                    "ON principal.tenant_id = session.tenant_id AND principal.principal_id = session.actor_id "
                    "WHERE session.tenant_id = %s AND session.refresh_hash = %s FOR UPDATE OF session",
                    (organization_id, old_hash),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "SELECT consumed.session_id, session.actor_id, session.team_id "
                        "FROM gateway_consumed_refresh_credentials AS consumed "
                        "JOIN gateway_human_sessions AS session ON "
                        "session.tenant_id = consumed.tenant_id AND session.id = consumed.session_id "
                        "WHERE consumed.tenant_id = %s AND consumed.credential_hash = %s "
                        "FOR UPDATE OF session",
                        (organization_id, old_hash),
                    )
                    consumed = cursor.fetchone()
                    if consumed is None:
                        outcome = "invalid_session_credential"
                    else:
                        cursor.execute(
                            "UPDATE gateway_human_sessions SET revoked_at = COALESCE(revoked_at, %s) "
                            "WHERE tenant_id = %s AND id = %s",
                            (now, organization_id, consumed[0]),
                        )
                        self._record_event(
                            cursor,
                            organization_id=organization_id,
                            session_id=str(consumed[0]),
                            event_type="refresh_replay",
                            target_actor_id=str(consumed[1]),
                            target_team_id=str(consumed[2]),
                            occurred_at=now,
                        )
                        outcome = "refresh_replay_detected"
                elif (
                    row[2] is not None
                    or _time(row[1]) <= now
                    or int(row[5]) != int(row[6])
                    or row[7] is not None
                ):
                    outcome = "expired_session_credential"
                else:
                    pair = self._new_pair(
                        organization_id,
                        now=now,
                        absolute_expires_at=_time(row[1]),
                    )
                    cursor.execute(
                        "INSERT INTO gateway_consumed_refresh_credentials "
                        "(tenant_id, credential_hash, session_id, consumed_at, expires_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (organization_id, old_hash, row[0], now, _time(row[1])),
                    )
                    cursor.execute(
                        "UPDATE gateway_human_sessions SET access_hash = %s, refresh_hash = %s, "
                        "access_expires_at = %s, generation = generation + 1, refreshed_at = %s "
                        "WHERE tenant_id = %s AND id = %s",
                        (
                            self._digest("access", pair.access_token),
                            self._digest("refresh", pair.refresh_token),
                            pair.access_expires_at,
                            now,
                            organization_id,
                            row[0],
                        ),
                    )
                    cursor.execute(
                        "DELETE FROM gateway_consumed_refresh_credentials "
                        "WHERE tenant_id = %s AND expires_at <= %s",
                        (organization_id, now),
                    )
                    outcome = pair
        if isinstance(outcome, SessionCredentialPair):
            return outcome
        raise SessionStoreError(str(outcome or "invalid_session_credential"))

    def revoke(self, credential: str) -> bool:
        prefix = "hox_a_" if credential.startswith("hox_a_") else "hox_r_"
        organization_id = self._route(credential, prefix, "invalid_session_credential")
        now = self._now()
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT id, actor_id, team_id FROM gateway_human_sessions "
                    "WHERE tenant_id = %s AND (access_hash = %s OR refresh_hash = %s) FOR UPDATE",
                    (
                        organization_id,
                        self._digest("access", credential),
                        self._digest("refresh", credential),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "SELECT session.id, session.actor_id, session.team_id "
                        "FROM gateway_consumed_refresh_credentials AS consumed "
                        "JOIN gateway_human_sessions AS session ON "
                        "session.tenant_id = consumed.tenant_id AND session.id = consumed.session_id "
                        "WHERE consumed.tenant_id = %s AND consumed.credential_hash = %s",
                        (organization_id, self._digest("refresh", credential)),
                    )
                    row = cursor.fetchone()
                if row is None:
                    return False
                cursor.execute(
                    "UPDATE gateway_human_sessions SET revoked_at = COALESCE(revoked_at, %s) "
                    "WHERE tenant_id = %s AND id = %s",
                    (now, organization_id, row[0]),
                )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    session_id=str(row[0]),
                    event_type="logout",
                    target_actor_id=str(row[1]),
                    target_team_id=str(row[2]),
                    occurred_at=now,
                )
        return True

    def revoke_session(
        self,
        session_id: str,
        *,
        event_type: str,
        organization_id: str | None = None,
    ) -> None:
        _require_binding(session_id, "invalid_session_id")
        if event_type not in SESSION_SECURITY_EVENT_TYPES:
            raise SessionStoreError("invalid_session_event_type")
        if organization_id is not None and organization_id not in self.organization_ids:
            raise SessionStoreError("invalid_organization_id")
        found = False
        now = self._now()
        organizations = (
            (organization_id,) if organization_id is not None else self.organization_ids
        )
        for scoped_organization in organizations:
            with self._transaction(scoped_organization) as connection:
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        "SELECT actor_id, team_id FROM gateway_human_sessions "
                        "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                        (scoped_organization, session_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        continue
                    cursor.execute(
                        "UPDATE gateway_human_sessions SET revoked_at = COALESCE(revoked_at, %s) "
                        "WHERE tenant_id = %s AND id = %s",
                        (now, scoped_organization, session_id),
                    )
                    self._record_event(
                        cursor,
                        organization_id=scoped_organization,
                        session_id=session_id,
                        event_type=event_type,
                        target_actor_id=str(row[0]),
                        target_team_id=str(row[1]),
                        occurred_at=now,
                    )
                    found = True
                    break
        if not found:
            return

    def list_active_sessions(
        self,
        *,
        organization_id: str,
        limit: int,
        cursor: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[tuple[SessionSummary, ...], str | None]:
        self._validate_admin_query(organization_id, limit, actor_id, team_id)
        before = _decode_cursor(cursor) if cursor is not None else None
        conditions = ["tenant_id = %s", "revoked_at IS NULL", "absolute_expires_at > %s"]
        parameters: list[object] = [organization_id, self._now()]
        if actor_id is not None:
            conditions.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            conditions.append("team_id = %s")
            parameters.append(team_id)
        if before is not None:
            conditions.append("(created_at < %s OR (created_at = %s AND id < %s))")
            parameters.extend((_parse_cursor_time(before[0]), _parse_cursor_time(before[0]), before[1]))
        parameters.append(limit + 1)
        query = (
            "SELECT id, actor_id, team_id, client_name, created_at, refreshed_at, "
            "access_expires_at, absolute_expires_at FROM gateway_human_sessions WHERE "
            + " AND ".join(conditions)
            + " ORDER BY created_at DESC, id DESC LIMIT %s"
        )
        with self._transaction(organization_id) as connection:
            with connection.cursor() as db_cursor:  # type: ignore[attr-defined]
                db_cursor.execute(query, tuple(parameters))
                rows = db_cursor.fetchall()
        selected = rows[:limit]
        items = tuple(
            SessionSummary(
                session_id=str(row[0]),
                organization_id=organization_id,
                actor_id=str(row[1]),
                team_id=str(row[2]),
                client_name=str(row[3]),
                created_at=_time(row[4]),
                refreshed_at=_time(row[5]),
                access_expires_at=_time(row[6]),
                session_expires_at=_time(row[7]),
            )
            for row in selected
        )
        next_cursor = (
            _encode_cursor(_time(selected[-1][4]).isoformat(), str(selected[-1][0]))
            if len(rows) > limit and selected
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
        self._validate_admin_query(organization_id, 1, actor_id, team_id)
        _require_binding(decision_actor_id, "invalid_decision_actor_id")
        if reason_code not in _ADMIN_REASON_CODES:
            raise SessionStoreError("invalid_admin_revocation_reason")
        if sum(value is not None for value in (session_id, actor_id, team_id)) > 1:
            raise SessionStoreError("invalid_admin_revocation_selector")
        conditions = ["tenant_id = %s", "revoked_at IS NULL", "absolute_expires_at > %s"]
        params: list[object] = [organization_id, self._now()]
        scope = "organization"
        for name, value in (("id", session_id), ("actor_id", actor_id), ("team_id", team_id)):
            if value is not None:
                _require_binding(value, "invalid_admin_revocation_selector")
                conditions.append(name + " = %s")
                params.append(value)
                scope = {"id": "session", "actor_id": "actor", "team_id": "team"}[name]
        now = self._now()
        with self._transaction(organization_id, principal_id=decision_actor_id) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT id, actor_id, team_id FROM gateway_human_sessions WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY id FOR UPDATE",
                    tuple(params),
                )
                rows = cursor.fetchall()
                for row in rows:
                    cursor.execute(
                        "UPDATE gateway_human_sessions SET revoked_at = %s "
                        "WHERE tenant_id = %s AND id = %s AND revoked_at IS NULL",
                        (now, organization_id, row[0]),
                    )
                    self._record_event(
                        cursor,
                        organization_id=organization_id,
                        session_id=str(row[0]),
                        event_type="admin_revocation",
                        target_actor_id=str(row[1]),
                        target_team_id=str(row[2]),
                        occurred_at=now,
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
        self._validate_admin_query(organization_id, limit, actor_id, team_id)
        if event_type is not None and event_type not in SESSION_SECURITY_EVENT_TYPES:
            raise SessionStoreError("invalid_session_event_type")
        if since is not None and (not isinstance(since, datetime) or since.tzinfo is None):
            raise SessionStoreError("invalid_session_event_since")
        before = _decode_cursor(cursor) if cursor is not None else None
        conditions = ["tenant_id = %s"]
        params: list[object] = [organization_id]
        for name, value in (("target_actor_id", actor_id), ("target_team_id", team_id), ("event_type", event_type)):
            if value is not None:
                conditions.append(name + " = %s")
                params.append(value)
        if since is not None:
            conditions.append("occurred_at >= %s")
            params.append(since.astimezone(timezone.utc))
        if before is not None:
            before_time = _parse_cursor_time(before[0])
            conditions.append("(occurred_at < %s OR (occurred_at = %s AND id < %s))")
            params.extend((before_time, before_time, before[1]))
        params.append(limit + 1)
        query = (
            "SELECT id, occurred_at, session_id, event_type, target_actor_id, target_team_id, "
            "decision_actor_id, decision_scope, reason_code FROM gateway_session_security_events WHERE "
            + " AND ".join(conditions)
            + " ORDER BY occurred_at DESC, id DESC LIMIT %s"
        )
        with self._transaction(organization_id) as connection:
            with connection.cursor() as db_cursor:  # type: ignore[attr-defined]
                db_cursor.execute(query, tuple(params))
                rows = db_cursor.fetchall()
        selected = rows[:limit]
        items = tuple(
            SessionSecurityEvent(
                event_id=str(row[0]),
                occurred_at=_time(row[1]),
                session_id=str(row[2]),
                event_type=str(row[3]),
                organization_id=organization_id,
                target_actor_id=str(row[4]),
                target_team_id=str(row[5]),
                decision_actor_id=str(row[6]) if row[6] is not None else None,
                decision_scope=str(row[7]) if row[7] is not None else None,
                reason_code=str(row[8]) if row[8] is not None else None,
            )
            for row in selected
        )
        next_cursor = (
            _encode_cursor(_time(selected[-1][1]).isoformat(), str(selected[-1][0]))
            if len(rows) > limit and selected
            else None
        )
        return items, next_cursor

    def _validate_admin_query(
        self,
        organization_id: str,
        limit: int,
        actor_id: str | None,
        team_id: str | None,
    ) -> None:
        if organization_id not in self.organization_ids:
            raise SessionStoreError("invalid_organization_id")
        if actor_id is not None:
            _require_binding(actor_id, "invalid_actor_id")
        if team_id is not None:
            _require_binding(team_id, "invalid_team_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SessionStoreError("invalid_session_page_limit")

    def _record_event(
        self,
        cursor: object,
        *,
        organization_id: str,
        session_id: str,
        event_type: str,
        target_actor_id: str,
        target_team_id: str,
        occurred_at: datetime,
        decision_actor_id: str | None = None,
        decision_scope: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "INSERT INTO gateway_session_security_events (tenant_id, id, occurred_at, "
            "session_id, event_type, target_actor_id, target_team_id, decision_actor_id, "
            "decision_scope, reason_code) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                organization_id,
                "sev_" + secrets.token_urlsafe(18),
                occurred_at,
                session_id,
                event_type,
                target_actor_id,
                target_team_id,
                decision_actor_id,
                decision_scope,
                reason_code,
            ),
        )


def _parse_cursor_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise SessionStoreError("invalid_session_cursor") from None
    if parsed.tzinfo is None:
        raise SessionStoreError("invalid_session_cursor")
    return parsed.astimezone(timezone.utc)
