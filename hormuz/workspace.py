"""Customer OIDC login uses the existing validated broker, without API access."""

import base64
import hashlib

from .auth import AuthenticationError
from .session import SessionBrokerError, _build_authorization_url, _complete_onboarding_claims, _exchange_code
from .session_store import SessionStoreError
from .workspace_store import WorkspaceError, WorkspaceStore
from .workspace_domains import WorkspaceDomains


class WorkspaceService:
    def __init__(self, broker):
        if not broker.config.session_broker.public_base_url.startswith("https://"):
            raise SessionBrokerError("workspace_https_required")
        self.broker = broker
        self.sessions = WorkspaceStore(broker)
        self.domains = WorkspaceDomains(self.sessions)

    @property
    def callback_url(self):
        return self.sessions.origin + "/v1/workspaces/auth/callback"

    def begin_login(self, *, workspace_id=None, handoff_id=None):
        flow, state, cookie = self.sessions.begin_login(workspace_id=workspace_id, handoff_id=handoff_id)
        try:
            issuer = self.broker.config.oidc_issuers[flow.issuer]
            metadata = self.broker.authenticator.login_metadata(flow.issuer)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).rstrip(b"=").decode()
            url = _build_authorization_url(metadata.authorization_endpoint, {
                "response_type": "code", "response_mode": "form_post", "client_id": issuer.login.client_id,
                "redirect_uri": self.callback_url, "scope": " ".join(issuer.login.scopes),
                "state": state, "nonce": flow.nonce, "code_challenge": challenge, "code_challenge_method": "S256",
            })
            return url, cookie
        except (AuthenticationError, SessionBrokerError, KeyError):
            self.sessions.fail_login(flow.id)
            raise WorkspaceError("workspace_login_unavailable") from None

    def complete_login(self, *, state, cookie, code=None, error=None, response_issuer=None):
        flow = self.sessions.consume_callback(state, cookie)
        try:
            if error or not code or len(code) > 4096 or response_issuer is not None and response_issuer != flow.issuer:
                raise WorkspaceError("workspace_login_invalid")
            issuer = self.broker.config.oidc_issuers.get(flow.issuer)
            if issuer is None or issuer.login is None:
                raise WorkspaceError("workspace_login_unavailable")
            metadata = self.broker.authenticator.login_metadata(flow.issuer)
            tokens = _exchange_code(metadata.token_endpoint, allow_insecure_http=issuer.allow_insecure_http,
                                    client_id=issuer.login.client_id, client_secret=issuer.login.client_secret,
                                    auth_method=issuer.login.token_endpoint_auth_method, redirect_uri=self.callback_url,
                                    code=code, pkce_verifier=flow.verifier)
            if not isinstance(tokens.get("id_token"), str):
                raise WorkspaceError("workspace_login_invalid")
            claims = self.broker.authenticator.validate_login_claims(tokens["id_token"], issuer_name=flow.issuer, nonce=flow.nonce)
            claims = _complete_onboarding_claims(claims, token_response=tokens, userinfo_endpoint=metadata.userinfo_endpoint, allow_insecure_http=issuer.allow_insecure_http)
            return self.sessions.complete_login(flow, claims)
        except (AuthenticationError, SessionBrokerError) as failure:
            self.sessions.fail_login(flow.id)
            code = "workspace_login_unavailable" if failure.code in {"oidc_metadata_unavailable", "oidc_token_exchange_failed", "oidc_userinfo_failed"} else "workspace_login_invalid"
            raise WorkspaceError(code) from None
        except SessionStoreError:
            self.sessions.fail_login(flow.id)
            raise
