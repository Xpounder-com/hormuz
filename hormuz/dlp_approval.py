from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


class DLPApprovalError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def payload_fingerprint(value: Any, *, key: bytes) -> str:
    """Return a domain-separated keyed fingerprint of a canonical JSON value."""
    if len(key) != 32:
        raise DLPApprovalError("approval_fingerprint_key_unavailable")
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise DLPApprovalError("approval_payload_not_canonicalizable") from error
    digest = hmac.new(
        key,
        b"hormuz-dlp-approval-payload-v1\x00" + canonical,
        hashlib.sha256,
    ).hexdigest()
    return "hdf_v1_" + digest
