"""Pure verification of normalized metadata-only audit-chain inputs."""

from __future__ import annotations

import hmac

from ._persistence import (
    AuditChainEntryInput,
    AuditChainVerificationInputs,
    audit_chain_source_event_map,
    is_sha256_digest,
)
from .audit_chain import AuditChainError, AuditChainHead, verify_audit_chain_entry
from .contracts import AUDIT_CHAIN_VERSION


def verify_audit_chain_inputs(inputs: AuditChainVerificationInputs) -> AuditChainHead:
    """Verify one backend-owned snapshot without performing storage I/O."""

    head = inputs.head
    if head.chain_version != AUDIT_CHAIN_VERSION or head.chain_epoch < 1 or head.sequence < 0:
        raise AuditChainError("audit_chain_head_malformed")
    if head.head_digest is not None and not is_sha256_digest(head.head_digest):
        raise AuditChainError("audit_chain_head_malformed")
    if inputs.checkpoint is not None and inputs.checkpoint.chain_version != head.chain_version:
        raise AuditChainError("audit_chain_checkpoint_mismatch")

    sources = audit_chain_source_event_map(
        inputs.source_events,
        error_factory=AuditChainError,
    )
    entries_by_epoch: dict[int, list[AuditChainEntryInput]] = {}
    for entry_input in inputs.entries:
        entries_by_epoch.setdefault(entry_input.chain_epoch, []).append(entry_input)

    verified: dict[tuple[int, int], str] = {}
    active_epoch_seen = False
    checkpoint_matched = False
    previous_epoch_number = 0
    for epoch in inputs.epochs:
        chain_version = epoch.chain_version
        epoch_number = epoch.chain_epoch
        if (
            chain_version != head.chain_version
            or epoch_number < 1
            or epoch_number <= previous_epoch_number
            or epoch_number > head.chain_epoch
        ):
            raise AuditChainError("audit_chain_epoch_malformed")
        previous_epoch_number = epoch_number
        reason_code = epoch.reason_code
        predecessor_digest = epoch.predecessor_head_digest
        predecessor_epoch = epoch.predecessor_chain_epoch
        predecessor_sequence = epoch.predecessor_sequence
        if epoch_number == 1:
            if (
                reason_code != "initial_adoption"
                or predecessor_epoch is not None
                or predecessor_sequence is not None
                or predecessor_digest is not None
            ):
                raise AuditChainError("audit_chain_epoch_malformed")
            previous_digest: str | None = None
        else:
            if (
                reason_code not in {"restore", "migration"}
                or isinstance(predecessor_epoch, bool)
                or not isinstance(predecessor_epoch, int)
                or isinstance(predecessor_sequence, bool)
                or not isinstance(predecessor_sequence, int)
                or predecessor_epoch < 1
                or predecessor_epoch >= epoch_number
                or predecessor_sequence < 1
                or not is_sha256_digest(predecessor_digest)
            ):
                raise AuditChainError("audit_chain_epoch_malformed")
            predecessor = verified.get((predecessor_epoch, predecessor_sequence))
            if predecessor is not None:
                if not hmac.compare_digest(predecessor, predecessor_digest):
                    raise AuditChainError("audit_chain_predecessor_invalid")
            elif (
                inputs.checkpoint is None
                or inputs.checkpoint.chain_version != chain_version
                or inputs.checkpoint.chain_epoch != predecessor_epoch
                or inputs.checkpoint.sequence != predecessor_sequence
                or inputs.checkpoint.head_digest != predecessor_digest
            ):
                raise AuditChainError("audit_chain_checkpoint_required")
            else:
                checkpoint_matched = True
            previous_digest = predecessor_digest

        expected_sequence = 1
        for entry_input in entries_by_epoch.get(epoch_number, []):
            digest = verify_audit_chain_entry(
                entry_input.entry,
                expected_organization_id=inputs.organization_id,
                expected_chain_version=chain_version,
                expected_chain_epoch=epoch_number,
                expected_sequence=expected_sequence,
                expected_previous_digest=previous_digest,
                source_event=sources.get(entry_input.source),
            )
            verified[(epoch_number, expected_sequence)] = digest
            previous_digest = digest
            expected_sequence += 1
        if epoch_number == head.chain_epoch:
            active_epoch_seen = True
            if head.sequence != expected_sequence - 1 or head.head_digest != previous_digest:
                raise AuditChainError("audit_chain_head_mismatch")

    if not active_epoch_seen or previous_epoch_number != head.chain_epoch:
        raise AuditChainError("audit_chain_head_mismatch")
    if set(entries_by_epoch).difference(verified_epoch for verified_epoch, _ in verified):
        raise AuditChainError("audit_chain_epoch_malformed")
    if inputs.checkpoint is not None and not checkpoint_matched:
        digest = verified.get((inputs.checkpoint.chain_epoch, inputs.checkpoint.sequence))
        if digest is None or not hmac.compare_digest(digest, inputs.checkpoint.head_digest):
            raise AuditChainError("audit_chain_checkpoint_mismatch")
    return head
