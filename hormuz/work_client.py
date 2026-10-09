"""Small authenticated client for attaching existing API agents to AI work."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .work_learning import REQUEST_KINDS

_UNSET = object()


class WorkClientError(ValueError):
    def __init__(self, reason, status=0):
        self.reason, self.status = reason, status
        super().__init__(reason)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise WorkClientError("gateway_redirect_refused", code)


class WorkClient:
    def __init__(self, endpoint, credential, *, allow_loopback_http=False, timeout=30):
        parsed = urlsplit(endpoint or "")
        local = parsed.hostname == "localhost"
        try:
            local = local or ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            pass
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or (parsed.scheme != "https" and not (allow_loopback_http and local and parsed.scheme == "http")):
            raise WorkClientError("invalid_gateway_url")
        if not isinstance(credential, str) or not credential or len(credential) > 65536 or any(ord(c) < 33 or ord(c) == 127 for c in credential):
            raise WorkClientError("credential_required")
        self.endpoint, self._credential, self.timeout = endpoint.rstrip("/"), credential, timeout
        self._opener = build_opener(_NoRedirect())

    @classmethod
    def from_environment(cls, *, environ=None):
        environ = os.environ if environ is None else environ
        return cls(environ.get("HORMUZ_GATEWAY_URL", ""), environ.get("HORMUZ_TOKEN", ""), allow_loopback_http=environ.get("HORMUZ_ALLOW_LOOPBACK_HTTP") == "1")

    def _request(self, method, path, value=None, *, headers=None):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).query or urlsplit(path).fragment:
            raise WorkClientError("invalid_gateway_path")
        request_headers = {"Authorization": "Bearer " + self._credential, "Accept": "application/json"}
        for name, item in (headers or {}).items():
            if name.lower() in {"authorization", "host", "cookie", "origin", "content-length", "transfer-encoding"}:
                raise WorkClientError("invalid_request_header")
            request_headers[name] = item
        body = None if value is None else json.dumps(value, separators=(",", ":")).encode("utf-8")
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, body, request_headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise WorkClientError("gateway_response_too_large")
                return json.loads(raw)
        except HTTPError as failure:
            reason = "gateway_request_failed"
            try:
                response = json.loads(failure.read(65536))
                code = response.get("error", {}).get("code")
                if isinstance(code, str) and len(code) < 128 and all(c.isalnum() or c == "_" for c in code):
                    reason = code
            except (ValueError, TypeError, AttributeError):
                pass
            raise WorkClientError(reason, failure.code) from None
        except (URLError, OSError):
            raise WorkClientError("gateway_unavailable") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise WorkClientError("invalid_gateway_response") from None

    def state(self):
        return self._request("GET", "/v1/work/state")

    def connect(self):
        return self._request("GET", "/v1/work/connect")

    def create_job(self, repository, *, title=None, task_type=None, context_revision=None, completion_condition="workflow.completed.v1"):
        values = {"repository": repository}
        if task_type is not None:
            values["task_type"] = task_type
        if completion_condition != "workflow.completed.v1":
            values["completion_condition"] = completion_condition
        if title is not None:
            values["title"] = title
        if context_revision is not None:
            values["context_revision"] = context_revision
        return self._request("POST", "/v1/work/jobs", values)

    def job(self, work_id):
        return WorkJob(self, work_id)

    def set_plan(self, scope_type, scope_id, *, budget_microusd, objective="cost", expected_version=None, exploration_enabled=_UNSET):
        values = {"scope_type": scope_type, "scope_id": scope_id, "budget_microusd": budget_microusd, "objective": objective}
        if expected_version is not None:
            values["expected_version"] = expected_version
        if exploration_enabled is not _UNSET:
            if exploration_enabled is not None and type(exploration_enabled) is not bool:
                raise WorkClientError("invalid_exploration_enabled")
            values["exploration_enabled"] = exploration_enabled
        return self._request("POST", "/v1/work/policies", values)

    def activation(self, operation=None, **values):
        if operation is not None and (not isinstance(operation, str) or operation not in {"request", "review", "reset", "reverify"}):
            raise WorkClientError("invalid_activation_action")
        return self._request("POST", "/v1/work/activation/" + operation, values) if operation in {"request", "review", "reset", "reverify"} else self._request("GET", "/v1/work/activation")

    def billing(self, action):
        if action not in {"checkout", "portal"}:
            raise WorkClientError("invalid_billing_action")
        return self._request("POST", "/v1/work/billing/" + action, {})

    def support_receipt(self):
        return self._request("GET", "/v1/work/support/receipt")


@dataclass(frozen=True)
class WorkJob:
    client: WorkClient
    work_id: str

    def __post_init__(self):
        if not isinstance(self.work_id, str) or not self.work_id or not self.work_id.isascii() or len(self.work_id) > 128 or any(not (c.isalnum() or c in "-_.") for c in self.work_id):
            raise WorkClientError("invalid_work_id")

    @property
    def headers(self):
        """Work attachment only: safe to pass alongside an agent's credentials."""
        return {"X-Hormuz-Work-Id": self.work_id}

    @property
    def _path(self):
        return "/v1/work/jobs/" + quote(self.work_id, safe="")

    def state(self):
        return self.client._request("GET", self._path)

    def bindings(self, **values):
        return self.client._request("POST" if values else "GET", self._path + "/bindings", values if values else None)

    def request(self, path, payload, *, request_kind=None):
        """Attach every request/retry; a successful response never closes the job."""
        if path not in {"/v1/responses", "/v1/chat/completions", "/v1/messages"}:
            raise WorkClientError("unsupported_inference_path")
        if not isinstance(payload, dict) or payload.get("stream") is True:
            raise WorkClientError("use_agent_headers_for_streaming")
        headers = self.headers
        if request_kind is not None:
            if not isinstance(request_kind, str) or request_kind not in REQUEST_KINDS:
                raise WorkClientError("invalid_request_kind")
            headers["X-Hormuz-Request-Kind"] = request_kind
        return self.client._request("POST", path, payload, headers=headers)

    def act(self, action, *, budget_microusd=None, expected_version=None):
        values = {"action": action}
        if budget_microusd is not None:
            values["budget_microusd"] = budget_microusd
        if expected_version is not None:
            values["expected_version"] = expected_version
        return self.client._request("POST", self._path + "/actions", values)

    def observe(self, status, *, source="workflow", reference=None):
        values = {"status": status, "source": source}
        if reference is not None:
            values["reference"] = reference
        return self.client._request("POST", self._path + "/observations", values)

    def observe_check(self, returncode, *, reference, completes_work=False):
        """Report a real check; completion must be an explicitly declared criterion."""
        if type(returncode) is not int or not isinstance(completes_work, bool):
            raise WorkClientError("invalid_check_result")
        if returncode == 0 and not completes_work:
            # An intermediate passing check cannot establish task completion.
            return self.state()
        return self.observe("completed" if returncode == 0 else "corrected", source="workflow", reference=reference)
