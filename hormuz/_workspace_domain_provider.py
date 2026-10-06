"""Bounded Render/DNS/TLS checks; customer hosts never receive operator secrets."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .workspace_store import WorkspaceError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _json_request(url, *, method="GET", headers=None, value=None, missing_ok=False):
    request = Request(url, data=json.dumps(value).encode() if value is not None else None,
                      headers={"Accept": "application/json", **(headers or {}), **({"Content-Type": "application/json"} if value is not None else {})}, method=method)
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise WorkspaceError("workspace_domain_unavailable")
            return json.loads(body) if body else {}
    except HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise WorkspaceError("workspace_domain_unavailable") from None
    except (OSError, ValueError, RecursionError):
        raise WorkspaceError("workspace_domain_unavailable") from None


class RenderDomainProvider:
    def __init__(self, settings):
        self.target = settings.workspace_domain_target
        self._base = "https://api.render.com/v1/services/" + quote(settings.workspace_domain_service_id, safe="") + "/custom-domains"
        self._headers = {"Authorization": "Bearer " + settings.workspace_domain_api_key}

    def records(self, name, kind, *, exact=True):
        value = _json_request("https://dns.google/resolve?" + urlencode({"name": name, "type": kind}))
        if not isinstance(value, dict) or value.get("Status") != 0:
            return []
        expected_type = {"TXT": 16, "CNAME": 5, "A": 1, "AAAA": 28}[kind]
        answers = value.get("Answer", [])
        if not isinstance(answers, list) or len(answers) > 100:
            raise WorkspaceError("workspace_domain_unavailable")
        return [row["data"] for row in answers if isinstance(row, dict) and row.get("type") == expected_type and (not exact or row.get("name", "").rstrip(".").lower() == name.lower()) and isinstance(row.get("data"), str)]

    def owned(self, hostname, challenge):
        # Short single-string TXT values avoid ambiguous record concatenation.
        return '"hormuz-verification=' + challenge + '"' in self.records("_hormuz." + hostname, "TXT")

    def routed(self, hostname):
        return self.target in [value.rstrip(".").lower() for value in self.records(hostname, "CNAME")]

    def ensure(self, hostname):
        path = self._base + "/" + quote(hostname, safe="")
        value = _json_request(path, headers=self._headers, missing_ok=True)
        if value is None:
            _json_request(self._base, method="POST", headers=self._headers, value={"name": hostname})
            value = _json_request(path, headers=self._headers)
        if not isinstance(value, dict) or value.get("name") != hostname or value.get("domainType") != "subdomain" or not isinstance(value.get("id"), str) or value.get("redirectForName"):
            raise WorkspaceError("workspace_domain_unavailable")
        if value.get("verificationStatus") != "verified":
            _json_request(path + "/verify", method="POST", headers=self._headers)
        return value["id"], value.get("verificationStatus") == "verified"

    def remove(self, hostname, provider_id=None):
        _json_request(self._base + "/" + quote(provider_id or hostname, safe=""), method="DELETE", headers=self._headers, missing_ok=True)

    def tls_probe(self, hostname, domain_id, nonce):
        # Resolve via a fixed public resolver, reject every non-public address,
        # and pin the TCP destination. TLS still verifies the customer's name.
        addresses = self.records(hostname, "A", exact=False) + self.records(hostname, "AAAA", exact=False)
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            return None
        connection = http.client.HTTPSConnection(hostname, timeout=5, context=ssl.create_default_context())
        deadline = None
        try:
            raw = socket.create_connection((addresses[0], 443), timeout=5)
            try:
                connection.sock = connection._context.wrap_socket(raw, server_hostname=hostname)
            except BaseException:
                raw.close()
                raise
            wrapped = connection.sock
            def expire():
                try:
                    wrapped.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # An attacker-controlled server must not extend a check forever
            # by trickling bytes within the per-read socket timeout.
            deadline = threading.Timer(5, expire)
            deadline.daemon = True
            deadline.start()
            connection.request("GET", "/.well-known/hormuz-domain/" + quote(domain_id, safe="") + "?" + urlencode({"nonce": nonce}))
            response = connection.getresponse()
            body = response.read(1025)
            if response.status != 200 or len(body) > 1024:
                return None
            value = json.loads(body)
            return value.get("proof") if isinstance(value, dict) else None
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            if deadline:
                deadline.cancel()
            connection.close()
