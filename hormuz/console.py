"""OIDC console login and bounded, organization-scoped reporting."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone

from .auth import AuthenticationError
from .console_store import ConsoleError, ConsolePrincipal, ConsoleStore
from .onboarding import _identifier
from .session import SessionBroker, SessionBrokerError, _build_authorization_url, _exchange_code
from .session_store import SessionStoreError
from .store import UsageRepository


class ConsoleService:
    def __init__(self, broker: SessionBroker, usage: UsageRepository):
        self.broker = broker
        self.sessions = ConsoleStore(broker.store, broker.directory)
        self.usage = usage

    @property
    def callback_url(self) -> str:
        return self.broker.config.session_broker.public_base_url + "/v1/admin/auth/callback"

    def begin_login(self, organization_id: str) -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        cookie = "hox_cf_" + secrets.token_urlsafe(32)
        flow = self.sessions.begin_login(organization_id=organization_id, state=state, browser_cookie=cookie,
                                         nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
        try:
            issuer = self.broker.config.oidc_issuers[flow.issuer]
            metadata = self.broker.authenticator.login_metadata(flow.issuer)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.pkce_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
            url = _build_authorization_url(metadata.authorization_endpoint, {
                "response_type": "code", "response_mode": "form_post", "client_id": issuer.login.client_id,
                "redirect_uri": self.callback_url, "scope": "openid", "state": state, "nonce": flow.nonce,
                "code_challenge": challenge, "code_challenge_method": "S256",
            })
            return url, cookie
        except (AuthenticationError, SessionBrokerError, KeyError):
            self.sessions.fail_login(flow.flow_id)
            raise ConsoleError("admin_login_unavailable") from None

    def complete_login(self, *, state: str, browser_cookie: str, code: str | None,
                       provider_error: str | None = None, response_issuer: str | None = None) -> str:
        flow = self.sessions.consume_callback(state=state, browser_cookie=browser_cookie)
        try:
            if provider_error is not None or response_issuer is not None and response_issuer != flow.issuer:
                raise ConsoleError("admin_login_invalid")
            if not isinstance(code, str) or not 1 <= len(code.encode("utf-8")) <= 4096:
                raise ConsoleError("admin_login_invalid")
            issuer = self.broker.config.oidc_issuers.get(flow.issuer)
            if issuer is None or issuer.login is None:
                raise ConsoleError("admin_login_unavailable")
            metadata = self.broker.authenticator.login_metadata(flow.issuer)
            tokens = _exchange_code(metadata.token_endpoint, allow_insecure_http=issuer.allow_insecure_http,
                                    client_id=issuer.login.client_id, client_secret=issuer.login.client_secret,
                                    auth_method=issuer.login.token_endpoint_auth_method, redirect_uri=self.callback_url,
                                    code=code, pkce_verifier=flow.pkce_verifier)
            if not isinstance(tokens.get("id_token"), str):
                raise ConsoleError("admin_login_invalid")
            claims = self.broker.authenticator.validate_login_claims(tokens["id_token"], issuer_name=flow.issuer, nonce=flow.nonce)
            return self.sessions.complete_login(flow, claims)
        except (AuthenticationError, SessionBrokerError) as error:
            self.sessions.fail_login(flow.flow_id)
            code = "admin_login_unavailable" if error.code in {"oidc_metadata_unavailable", "oidc_token_exchange_failed"} else "admin_login_invalid"
            raise ConsoleError(code) from None
        except SessionStoreError:
            self.sessions.fail_login(flow.flow_id)
            raise

    def report(self, principal: ConsolePrincipal, *, from_date: str = "", through_date: str = "",
               team_id: str = "") -> dict[str, object]:
        today = self.broker.store._now().date()
        if not from_date and not through_date:
            start, end = today.replace(day=1), today
        else:
            try:
                if not all(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) for value in (from_date, through_date)):
                    raise ValueError
                start, end = date.fromisoformat(from_date), date.fromisoformat(through_date)
            except (ValueError, TypeError):
                raise ConsoleError("admin_invalid_window") from None
        if start > end or end > today or (end - start).days >= 31:
            raise ConsoleError("admin_invalid_window")
        team_name = None
        if team_id:
            _identifier(team_id)
            with self.broker.store._connection() as connection:
                team = connection.execute("SELECT name FROM onboarding_teams WHERE id = ? AND organization_id = ?",
                                          (team_id, principal.organization_id)).fetchone()
                if team is None:
                    raise ConsoleError("admin_team_unavailable")
                team_name = team["name"]
        totals = self.usage.monthly_totals(
            organization_id=principal.organization_id, team_id=team_id or None,
            starts_at=datetime.combine(start, time.min, timezone.utc),
            ends_before=datetime.combine(end + timedelta(days=1), time.min, timezone.utc),
        )
        fields = ("requests", "denied_requests", "rate_limited_requests", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "cost_microusd", "redaction_count")
        return {
            "schema_id": "hormuz.admin-usage", "schema_version": 1,
            "scope": {"organization_id": principal.organization_id, "team_id": team_id or None, "team_name": team_name},
            "window": {"from_date": start.isoformat(), "through_date": end.isoformat(), "timezone": "UTC"},
            "totals": {**{key: getattr(totals, key) for key in fields}, "total_tokens": totals.total_tokens},
            "cost_basis": "configured_rate_card_estimate", "coverage": "gateway_captured_requests_only",
        }

    def list_records(self, principal: ConsolePrincipal, kind: str, *, after: str = "", limit: int = 20) -> dict[str, object]:
        if kind not in {"teams", "memberships"}:
            raise ConsoleError("admin_invalid_request")
        if kind == "memberships" and principal.role != "member_admin":
            raise ConsoleError("admin_access_denied")
        page = self.broker.directory.list_records(kind, organization_id=principal.organization_id, after=after, limit=limit)
        return {"schema_id": "hormuz.admin-list", "schema_version": 1, "organization_id": principal.organization_id,
                "kind": "members" if kind == "memberships" else kind, **page}
