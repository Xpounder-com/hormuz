"""Audit command registration and execution.

The public CLI entry points and shared exception translation remain in
``hormuz.cli``. This module owns the audit command family while deliberately
leaving SQLite and PostgreSQL audit-chain verification inside their backends.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..audit_chain import (
    AuditChainError,
    audit_chain_checkpoint_summary,
    build_audit_chain_checkpoint,
    parse_audit_chain_checkpoint,
    serialize_audit_chain_checkpoint,
)
from ..config import GatewayConfig
from ..custody import (
    AuditAnchorSink,
    CustodyError,
    audit_anchor_summary,
    build_audit_anchor_artifact,
    serialize_audit_anchor_artifact,
)


@dataclass(frozen=True)
class AuditCommandDependencies:
    """Narrow backend and tenant seam supplied by :mod:`hormuz.cli`."""

    create_usage_store: Callable[[GatewayConfig], Any]
    create_audit_anchor_sink: Callable[[GatewayConfig], AuditAnchorSink]
    required_organization: Callable[[GatewayConfig], str]


def add_audit_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    audit = subparsers.add_parser("audit", help="Export and verify metadata-only audit evidence")
    audit_subparsers = audit.add_subparsers(dest="audit_command", required=True)
    audit_export = audit_subparsers.add_parser(
        "export",
        help="Export metadata-only usage and security events as JSONL",
    )
    _audit_export_arguments(audit_export)

    audit_anchor = audit_subparsers.add_parser(
        "anchor",
        help="Export and immutably retain a metadata-only audit snapshot",
    )
    _audit_anchor_arguments(audit_anchor)

    audit_chain = audit_subparsers.add_parser(
        "chain",
        help="Operate the per-organization commit-time audit chain",
    )
    audit_chain_subparsers = audit_chain.add_subparsers(dest="audit_chain_command", required=True)
    audit_chain_subparsers.add_parser(
        "status",
        help="Show local chain and checkpoint freshness without contacting Object Lock",
    )
    audit_chain_anchor = audit_chain_subparsers.add_parser(
        "anchor",
        help="Externally retain the current chain checkpoint outside the request path",
    )
    audit_chain_anchor.add_argument(
        "--output",
        required=True,
        help="Write the canonical metadata-only checkpoint artifact to this path",
    )
    audit_chain_anchor.add_argument(
        "--force",
        action="store_true",
        help="Allow replacing an existing checkpoint path",
    )
    audit_chain_verify = audit_chain_subparsers.add_parser(
        "verify",
        help="Verify chain order, event correspondence, and an external checkpoint",
    )
    audit_chain_verify.add_argument(
        "--checkpoint",
        required=True,
        help="Canonical externally retained checkpoint artifact",
    )
    audit_chain_epoch = audit_chain_subparsers.add_parser(
        "epoch",
        help="Explicitly begin a restore or migration epoch from a trusted checkpoint",
    )
    audit_chain_epoch.add_argument(
        "--checkpoint",
        required=True,
        help="Trusted canonical checkpoint artifact",
    )
    audit_chain_epoch.add_argument("--reason", required=True, choices=["restore", "migration"])
    audit_chain_epoch.add_argument(
        "--confirm",
        required=True,
        help="Type START_NEW_AUDIT_CHAIN_EPOCH to confirm this controlled recovery action",
    )


def _audit_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", choices=["all", "usage", "security"], default="all")
    parser.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")
    parser.add_argument("--output", default="-", help="Output path or - for stdout (default: -)")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing output file")


def _audit_anchor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", choices=["all", "usage", "security"], default="all")
    parser.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")


def _audit(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: AuditCommandDependencies,
) -> int:
    if args.audit_command == "export":
        return _audit_export(config, args, dependencies)
    if args.audit_command == "anchor":
        return _audit_anchor(config, args, dependencies)
    if args.audit_command == "chain":
        return _audit_chain(config, args, dependencies)
    return 2


def _audit_export(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: AuditCommandDependencies,
) -> int:
    try:
        since = _audit_since(args.since)
    except ValueError as error:
        print(f"invalid --since: {error}", file=sys.stderr)
        return 2
    events = dependencies.create_usage_store(config).audit_events(
        since=since,
        kind=args.kind,
        organization_id=dependencies.required_organization(config),
    )
    stream = sys.stdout
    should_close = False
    output_path: Path | None = None
    if args.output != "-":
        output_path = Path(args.output).expanduser().absolute()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | (os.O_TRUNC if args.force else os.O_EXCL)
        )
        try:
            descriptor = os.open(output_path, flags, 0o600)
        except FileExistsError:
            print(f"audit export already exists: {output_path} (use --force to replace it)", file=sys.stderr)
            return 2
        except OSError as error:
            print(f"cannot open audit export {output_path}: {error}", file=sys.stderr)
            return 2
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows permission semantics
            os.chmod(output_path, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        should_close = True

    digest = hashlib.sha256()
    try:
        for event in events:
            line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            stream.write(line)
            digest.update(line.encode("utf-8"))
        stream.flush()
        if should_close:
            os.fsync(stream.fileno())
    finally:
        if should_close:
            stream.close()
    destination = str(output_path) if output_path is not None else "stdout"
    print(
        f"exported {len(events)} events to {destination}; sha256={digest.hexdigest()}",
        file=sys.stderr,
    )
    return 0


def _audit_anchor(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: AuditCommandDependencies,
) -> int:
    """Create and externally retain one verified, metadata-only audit snapshot."""

    try:
        since = _audit_since(args.since)
    except ValueError as error:
        print(f"invalid --since: {error}", file=sys.stderr)
        return 2
    organization_id = dependencies.required_organization(config)
    events = dependencies.create_usage_store(config).audit_events(
        since=since,
        kind=args.kind,
        organization_id=organization_id,
    )
    artifact = build_audit_anchor_artifact(events, organization_id=organization_id)
    artifact_id, head_digest, event_count = audit_anchor_summary(artifact)
    serialized = serialize_audit_anchor_artifact(artifact)
    anchor_config = config.audit_anchor
    if anchor_config is None:
        raise CustodyError("audit_anchor_unconfigured")
    retention_until = datetime.now(timezone.utc) + timedelta(days=anchor_config.retention_days)
    receipt = dependencies.create_audit_anchor_sink(config).anchor(
        serialized,
        artifact_id=artifact_id,
        organization_id=organization_id,
        head_digest=head_digest,
        retention_until=retention_until,
        legal_hold=anchor_config.legal_hold,
    )
    version = f" object_version={receipt.object_version}" if receipt.object_version else ""
    print(
        f"audit_anchor={receipt.backend} artifact_id={receipt.artifact_id} events={event_count} "
        f"artifact_sha256={receipt.artifact_sha256} "
        f"head_digest={receipt.head_digest}{version}",
        file=sys.stderr,
    )
    return 0


def _audit_chain(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: AuditCommandDependencies,
) -> int:
    """Operate commit-time evidence without placing Object Lock on request egress."""

    organization_id = dependencies.required_organization(config)
    store = dependencies.create_usage_store(config)
    if args.audit_chain_command == "status":
        head = store.audit_chain_head(organization_id=organization_id)
        maximum_age = (
            config.audit_chain.maximum_anchor_age_seconds
            if config.audit_chain is not None
            else None
        )
        status = store.audit_chain_anchor_status(
            organization_id=organization_id,
            maximum_age_seconds=maximum_age,
        )
        checkpoint_at = status.latest_checkpoint_at.isoformat() if status.latest_checkpoint_at is not None else "none"
        oldest_unanchored = (
            status.oldest_unanchored_at.isoformat() if status.oldest_unanchored_at is not None else "none"
        )
        digest = head.head_digest or "none"
        print(
            f"audit_chain=ready organization={organization_id} chain_version={head.chain_version} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence} head_digest={digest} "
            f"latest_checkpoint_at={checkpoint_at} oldest_unanchored_at={oldest_unanchored} "
            f"anchor_overdue={str(status.overdue).lower()}"
        )
        return 0
    if args.audit_chain_command == "anchor":
        anchor_config = config.audit_anchor
        if anchor_config is None:
            raise CustodyError("audit_anchor_unconfigured")
        head = store.audit_chain_head(organization_id=organization_id)
        checkpoint = build_audit_chain_checkpoint(head)
        serialized = serialize_audit_chain_checkpoint(checkpoint)
        # Preserve the exact canonical input required by a later recovery
        # operation before egress. It contains metadata only, but is still
        # owner-only by default to avoid casually exposing tenant topology.
        _write_audit_chain_checkpoint(Path(args.output).expanduser().absolute(), serialized, force=args.force)
        checkpoint_id, checkpoint_organization, _, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        if checkpoint_organization != organization_id:
            raise CustodyError("audit_chain_tenant_mismatch")
        retention_until = datetime.now(timezone.utc) + timedelta(days=anchor_config.retention_days)
        receipt = dependencies.create_audit_anchor_sink(config).anchor(
            serialized,
            artifact_id=checkpoint_id,
            organization_id=organization_id,
            head_digest=head_digest,
            retention_until=retention_until,
            legal_hold=anchor_config.legal_hold,
        )
        if (
            receipt.artifact_id != checkpoint_id
            or receipt.head_digest != head_digest
            or not _is_sha256_digest(receipt.artifact_sha256)
        ):
            raise CustodyError("audit_chain_anchor_receipt_invalid")
        store.record_audit_chain_checkpoint(
            checkpoint=checkpoint,
            artifact_sha256=receipt.artifact_sha256,
            anchor_backend=receipt.backend,
            object_version=receipt.object_version,
        )
        version = f" object_version={receipt.object_version}" if receipt.object_version else ""
        print(
            f"audit_chain_anchor={receipt.backend} checkpoint_id={checkpoint_id} "
            f"chain_epoch={head.chain_epoch} sequence={sequence} artifact_sha256={receipt.artifact_sha256} "
            f"head_digest={head_digest}{version}"
        )
        return 0
    checkpoint = _read_audit_chain_checkpoint(Path(args.checkpoint).expanduser().absolute())
    if args.audit_chain_command == "verify":
        head = store.verify_audit_chain(organization_id=organization_id, checkpoint=checkpoint)
        checkpoint_id, _, _, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        print(
            f"audit_chain_verified=true organization={organization_id} checkpoint_id={checkpoint_id} "
            f"checkpoint_sequence={sequence} checkpoint_head_digest={head_digest} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence}"
        )
        return 0
    if args.audit_chain_command == "epoch":
        if args.confirm != "START_NEW_AUDIT_CHAIN_EPOCH":
            raise CustodyError("audit_chain_epoch_confirmation_required")
        _, checkpoint_organization, _, _, _ = audit_chain_checkpoint_summary(checkpoint)
        if checkpoint_organization != organization_id:
            raise CustodyError("audit_chain_tenant_mismatch")
        head = store.begin_audit_chain_epoch(checkpoint=checkpoint, reason_code=args.reason)
        print(
            f"audit_chain_epoch_started=true organization={organization_id} reason={args.reason} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence} head_digest={head.head_digest}"
        )
        return 0
    raise CustodyError("audit_chain_command_unsupported")


def _write_audit_chain_checkpoint(
    path: Path,
    serialized: bytes,
    *,
    force: bool,
    write: Callable[..., int] = os.write,
) -> None:
    """Publish one canonical checkpoint without exposing a partial target file."""

    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        # Stage in the target directory so the final hard-link or replacement
        # is atomic. In particular, --force must preserve the prior recovery
        # artifact if staging fails halfway through a disk write.
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = write(descriptor, remaining)
            if written <= 0:
                raise OSError("checkpoint write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if force:
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise CustodyError("audit_chain_checkpoint_exists") from None
        try:
            os.unlink(temporary_path)
        except OSError:
            # The published checkpoint is valid; a private staging remnant is
            # safe to clean up later rather than converting success to failure.
            pass
        temporary_path = None
    except CustodyError:
        raise
    except OSError:
        raise CustodyError("audit_chain_checkpoint_write_unavailable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _read_audit_chain_checkpoint(path: Path) -> dict[str, object]:
    try:
        artifact = path.read_bytes()
    except OSError:
        raise CustodyError("audit_chain_checkpoint_unavailable") from None
    try:
        return parse_audit_chain_checkpoint(artifact)
    except AuditChainError:
        raise


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _audit_since(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
