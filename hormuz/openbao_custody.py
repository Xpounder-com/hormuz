"""OpenBao Transit implementation of Hormuz's key-custody contract.

OpenBao's Transit engine remains the key authority.  Hormuz receives a
short-lived plaintext *data key* only when it must perform local AES-GCM
envelope work; it never receives or stores a Transit master key.  The adapter
uses the standard HTTP API directly so the core package needs no OpenBao SDK.
"""

from __future__ import annotations

import base64
import hmac
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .custody import CustodyError, GeneratedDataKey, RewrappedDataKey, encryption_context


_MAX_RESPONSE_BYTES = 1024 * 1024
_DATA_KEY_BYTES = 32


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so a configured Transit token stays at its origin."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_OPENER = build_opener(_NoRedirectHandler())


def _default_transport(request: Request, timeout_seconds: float) -> Any:
    return _OPENER.open(request, timeout=timeout_seconds)


class OpenBaoTransitDataKeyProvider:
    """Use an OpenBao Transit mount for tenant- and purpose-bound data keys."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        token: str,
        mount: str = "transit",
        timeout_seconds: float = 5,
        transport: Callable[[Request, float], Any] | None = None,
    ) -> None:
        if not _service_origin(endpoint_url):
            raise CustodyError("openbao_custody_endpoint_invalid")
        if not isinstance(token, str) or not token:
            raise CustodyError("openbao_custody_token_unavailable")
        if not isinstance(mount, str) or not mount or "/" in mount:
            raise CustodyError("openbao_custody_mount_invalid")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise CustodyError("openbao_custody_timeout_invalid")
        self._endpoint_url = endpoint_url.rstrip("/")
        self._token = token
        self._mount = mount
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport or _default_transport

    def generate_data_key(
        self,
        *,
        key_reference: str,
        encryption_context: Mapping[str, str],
    ) -> GeneratedDataKey:
        data = self._post(
            "datakey/plaintext",
            key_reference,
            {"context": _encoded_context(encryption_context)},
        )
        plaintext = _decode_base64(data.get("plaintext"))
        encrypted = _ciphertext_bytes(data.get("ciphertext"))
        if len(plaintext) != _DATA_KEY_BYTES:
            raise CustodyError("openbao_custody_response_invalid")
        return GeneratedDataKey(
            key_reference=key_reference,
            plaintext=plaintext,
            encrypted=encrypted,
        )

    def decrypt_data_key(
        self,
        *,
        key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> bytes:
        data = self._post(
            "decrypt",
            key_reference,
            {
                "ciphertext": _ciphertext_string(encrypted),
                "context": _encoded_context(encryption_context),
            },
        )
        plaintext = _decode_base64(data.get("plaintext"))
        if len(plaintext) != _DATA_KEY_BYTES:
            raise CustodyError("openbao_custody_response_invalid")
        return plaintext

    def rewrap_data_key(
        self,
        *,
        source_key_reference: str,
        destination_key_reference: str,
        encrypted: bytes,
        encryption_context: Mapping[str, str],
    ) -> RewrappedDataKey:
        """Rotate a data key without handling the protected application secret.

        Transit can rewrap a ciphertext under a newer version of the *same*
        named key server-side.  Moving between distinct purpose keys requires
        a bounded decrypt/encrypt of the random data key inside Hormuz.  The
        protected provider credential or audit artifact is never decrypted in
        this operation.
        """

        payload = {
            "ciphertext": _ciphertext_string(encrypted),
            "context": _encoded_context(encryption_context),
        }
        if source_key_reference == destination_key_reference:
            data = self._post("rewrap", destination_key_reference, payload)
            return RewrappedDataKey(
                key_reference=destination_key_reference,
                encrypted=_ciphertext_bytes(data.get("ciphertext")),
            )

        plaintext = self.decrypt_data_key(
            key_reference=source_key_reference,
            encrypted=encrypted,
            encryption_context=encryption_context,
        )
        data = self._post(
            "encrypt",
            destination_key_reference,
            {
                "plaintext": base64.b64encode(plaintext).decode("ascii"),
                "context": _encoded_context(encryption_context),
            },
        )
        return RewrappedDataKey(
            key_reference=destination_key_reference,
            encrypted=_ciphertext_bytes(data.get("ciphertext")),
        )

    def _post(self, operation: str, key_reference: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        if not isinstance(key_reference, str) or not key_reference:
            raise CustodyError("openbao_custody_key_reference_invalid")
        return self._post_path(
            f"{quote(self._mount, safe='')}/{quote(operation, safe='/')}/{quote(key_reference, safe='')}",
            payload,
        )

    def _post_path(self, api_path: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        """Send one bounded authenticated OpenBao request within this origin."""

        if not isinstance(api_path, str) or not api_path or api_path.startswith("/"):
            raise CustodyError("openbao_custody_request_rejected")
        encoded_payload = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self._endpoint_url}/v1/{api_path}",
            data=encoded_payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Vault-Token": self._token,
            },
        )
        try:
            response = self._transport(request, self._timeout_seconds)
        except HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            raise _http_error(error.code) from None
        except (OSError, TimeoutError, URLError):
            raise CustodyError("openbao_custody_unavailable") from None
        except Exception:
            raise CustodyError("openbao_custody_unavailable") from None
        try:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except Exception:
            raise CustodyError("openbao_custody_unavailable") from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise CustodyError("openbao_custody_response_invalid")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CustodyError("openbao_custody_response_invalid") from None
        if not isinstance(parsed, Mapping):
            raise CustodyError("openbao_custody_response_invalid")
        data = parsed.get("data")
        if not isinstance(data, Mapping):
            raise CustodyError("openbao_custody_response_invalid")
        return data


class OpenBaoTransitKeyRotationControl:
    """A separately credentialed, explicit control for Transit key-version rotation.

    This class is deliberately outside :class:`DataKeyProvider`: a normal
    Hormuz runtime credential needs data-key operations, never permission to
    rotate a named Transit key. Recovery or conformance tooling constructs this
    control only from a separately supplied administrative credential.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        token: str,
        mount: str = "transit",
        timeout_seconds: float = 5,
        transport: Callable[[Request, float], Any] | None = None,
    ) -> None:
        self._client = OpenBaoTransitDataKeyProvider(
            endpoint_url=endpoint_url,
            token=token,
            mount=mount,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._mount = mount

    def assert_rotation_denied(self, *, key_reference: str) -> None:
        """Require that this token has no ability to rotate one named key."""

        if self._capabilities(f"{self._mount}/keys/{_validate_key_reference(key_reference)}/rotate") != {"deny"}:
            raise CustodyError("openbao_custody_runtime_rotation_authorized")

    def assert_rotation_only_administrator(self, *, key_reference: str) -> None:
        """Require exactly the one rotation capability and no data-key access."""

        key = _validate_key_reference(key_reference)
        if self._capabilities(f"{self._mount}/keys/{key}/rotate") != {"update"}:
            raise CustodyError("openbao_custody_rotation_administrator_scope_invalid")
        for operation in ("datakey/plaintext", "decrypt", "rewrap", "encrypt"):
            if self._capabilities(f"{self._mount}/{operation}/{key}") != {"deny"}:
                raise CustodyError("openbao_custody_rotation_administrator_data_plane_authorized")

    def rotate_key_version(self, *, key_reference: str) -> None:
        """Rotate one named Transit key with the separately scoped admin token."""

        _validate_key_reference(key_reference)
        self._client._post_path(  # noqa: SLF001 - same-module bounded control path.
            f"{quote(self._mount, safe='')}/keys/{quote(key_reference, safe='')}/rotate",
            {},
        )

    def _capabilities(self, path: str) -> set[str]:
        data = self._client._post_path(  # noqa: SLF001 - same-module bounded control path.
            "sys/capabilities-self",
            {"paths": [path]},
        )
        capabilities = data.get("capabilities")
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise CustodyError("openbao_custody_rotation_capability_invalid")
        return set(capabilities)


def verify_openbao_transit_profile(
    provider: OpenBaoTransitDataKeyProvider,
    key_references: Mapping[str, str],
    *,
    organization_id: str,
) -> int:
    """Prove that the configured caller can use every declared Transit key."""

    if not key_references:
        raise CustodyError("openbao_custody_keys_unconfigured")
    if len(set(key_references.values())) != len(key_references):
        raise CustodyError("openbao_custody_key_purposes_not_separated")
    verified = 0
    for purpose, key_reference in sorted(key_references.items()):
        context = encryption_context(organization_id=organization_id, purpose=purpose)
        generated = provider.generate_data_key(
            key_reference=key_reference,
            encryption_context=context,
        )
        recovered = provider.decrypt_data_key(
            key_reference=generated.key_reference,
            encrypted=generated.encrypted,
            encryption_context=context,
        )
        if not hmac.compare_digest(generated.plaintext, recovered):
            raise CustodyError("openbao_custody_response_invalid")
        verified += 1
    return verified


def _encoded_context(value: Mapping[str, str]) -> str:
    if not isinstance(value, Mapping) or not value:
        raise CustodyError("openbao_custody_context_invalid")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise CustodyError("openbao_custody_context_invalid")
        normalized[key] = item
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(serialized).decode("ascii")


def _validate_key_reference(value: object) -> str:
    if not isinstance(value, str) or not value or "/" in value:
        raise CustodyError("openbao_custody_key_reference_invalid")
    return value


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_RESPONSE_BYTES:
        raise CustodyError("openbao_custody_response_invalid")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise CustodyError("openbao_custody_response_invalid") from None


def _ciphertext_bytes(value: object) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_RESPONSE_BYTES:
        raise CustodyError("openbao_custody_response_invalid")
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise CustodyError("openbao_custody_response_invalid") from None


def _ciphertext_string(value: bytes) -> str:
    if not isinstance(value, bytes) or not value or len(value) > _MAX_RESPONSE_BYTES:
        raise CustodyError("openbao_custody_ciphertext_invalid")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        raise CustodyError("openbao_custody_ciphertext_invalid") from None


def _http_error(status: int) -> CustodyError:
    if status in {401, 403}:
        return CustodyError("openbao_custody_access_denied")
    if status == 404:
        return CustodyError("openbao_custody_key_unavailable")
    if status in {400, 405, 413, 422}:
        return CustodyError("openbao_custody_request_rejected")
    return CustodyError("openbao_custody_unavailable")


def _service_origin(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    if not (
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return False
    return parsed.scheme != "http" or parsed.hostname in {"127.0.0.1", "::1", "localhost"}
