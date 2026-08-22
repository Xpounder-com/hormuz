"""Runtime construction and safe local handling for custody artifacts.

The helpers here are the only bridge from validated configuration to the AWS
adapters.  They deliberately keep plaintext provider credentials in memory
only and reject unsafe encrypted-envelope files before AWS is contacted.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Mapping

from .aws_custody import create_aws_kms_key_custodian, create_s3_object_lock_anchor_sink
from .config import GatewayConfig
from .custody import (
    KEY_PURPOSE_DATA_ENCRYPTION,
    KEY_PURPOSE_PROVIDER_CREDENTIAL,
    AuditAnchorSink,
    CustodyError,
    DataKeyProvider,
    EncryptedEnvelope,
    EnvelopeCipher,
    parse_envelope,
    serialize_envelope,
)


_MAX_ENVELOPE_FILE_BYTES = 32 * 1024 * 1024


def create_data_key_provider(config: GatewayConfig) -> DataKeyProvider:
    """Construct the configured key provider without accepting raw credentials."""

    key_custody = config.key_custody
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    if key_custody.backend == "aws-kms":
        return create_aws_kms_key_custodian(region=key_custody.region)
    raise CustodyError("key_custody_backend_unsupported")


def create_audit_anchor_sink(config: GatewayConfig) -> AuditAnchorSink:
    """Construct the explicit immutable-audit destination from configuration."""

    anchor = config.audit_anchor
    key_custody = config.key_custody
    if anchor is None:
        raise CustodyError("audit_anchor_unconfigured")
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    encryption_key_reference = key_custody.key_reference_for(KEY_PURPOSE_DATA_ENCRYPTION)
    if anchor.backend == "aws-s3-object-lock":
        return create_s3_object_lock_anchor_sink(
            region=anchor.region,
            bucket=anchor.bucket,
            prefix=anchor.prefix,
            encryption_key_reference=encryption_key_reference,
        )
    raise CustodyError("audit_anchor_backend_unsupported")


def resolve_upstream_credentials(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve server-only upstream credentials from env or sealed envelopes.

    The function intentionally returns no origin path or provider error detail.
    A missing environment source remains an empty string so the existing server
    behavior can return the stable provider-shaped unavailable response.
    Encrypted sources fail before listener startup instead of silently falling
    back to a different source.
    """

    environment = os.environ if environ is None else environ
    result: dict[str, str] = {}
    cipher: EnvelopeCipher | None = None
    for protocol, upstream in config.upstreams.items():
        if upstream.api_key_env is not None:
            result[protocol] = environment.get(upstream.api_key_env, "")
            continue
        if upstream.api_key_envelope_path is None:
            raise CustodyError("upstream_credential_source_invalid")
        if cipher is None:
            cipher = EnvelopeCipher(create_data_key_provider(config))
        envelope = read_envelope_file(upstream.api_key_envelope_path)
        expected_organization_id = _single_organization_id(config)
        if (
            envelope.organization_id != expected_organization_id
            or envelope.purpose != KEY_PURPOSE_PROVIDER_CREDENTIAL
        ):
            raise CustodyError("upstream_credential_envelope_mismatch")
        plaintext = cipher.unseal(envelope)
        try:
            credential = plaintext.decode("utf-8")
        except UnicodeDecodeError:
            raise CustodyError("upstream_credential_envelope_invalid") from None
        if not credential or "\x00" in credential or "\r" in credential or "\n" in credential:
            raise CustodyError("upstream_credential_envelope_invalid")
        result[protocol] = credential
    return result


def read_envelope_file(path: Path) -> EncryptedEnvelope:
    """Read one owner-only regular envelope file without following symlinks."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise CustodyError("encrypted_envelope_file_unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CustodyError("encrypted_envelope_file_invalid")
        if metadata.st_mode & 0o077:
            raise CustodyError("encrypted_envelope_file_permissions_invalid")
        if metadata.st_size <= 0 or metadata.st_size > _MAX_ENVELOPE_FILE_BYTES:
            raise CustodyError("encrypted_envelope_file_invalid")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise CustodyError("encrypted_envelope_file_invalid")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    return parse_envelope(b"".join(chunks))


def write_envelope_file(path: Path, envelope: EncryptedEnvelope, *, force: bool = False) -> None:
    """Atomically publish an owner-only envelope without following target links.

    A failed write must leave the old credential envelope usable.  The temporary
    file is created in the destination directory, fully synced, then linked or
    atomically replaced only after its contents and permissions are complete.
    """

    serialized = serialize_envelope(envelope)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError:
        raise CustodyError("encrypted_envelope_file_write_failed") from None
    temporary_path = Path(temporary_name)
    try:
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            else:  # pragma: no cover - Windows permission semantics
                os.chmod(temporary_path, 0o600)
            written = 0
            while written < len(serialized):
                count = os.write(descriptor, serialized[written:])
                if count <= 0:
                    raise CustodyError("encrypted_envelope_file_write_failed")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if force:
            # ``replace`` replaces a target symlink itself, never its target.
            os.replace(temporary_path, path)
        else:
            # A hard-link publish is atomic and refuses an existing path. Both
            # files are in the same directory, so this cannot cross devices.
            os.link(temporary_path, path)
    except FileExistsError:
        raise CustodyError("encrypted_envelope_file_write_failed") from None
    except OSError:
        raise CustodyError("encrypted_envelope_file_write_failed") from None
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        except OSError:
            # The published path is safe at this point; an orphaned 0600 temp
            # file is not a reason to replace a valid envelope with an error.
            pass


def _single_organization_id(config: GatewayConfig) -> str:
    organization_ids = config.organization_ids
    if len(organization_ids) != 1:
        raise CustodyError("upstream_credential_tenant_scope_ambiguous")
    return organization_ids[0]
