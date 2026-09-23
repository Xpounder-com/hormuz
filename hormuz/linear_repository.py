"""Atomic Linear receipt, context, outcome, coverage, and audit persistence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time
from uuid import UUID, uuid5

from ._portfolio_sql import OUTCOME_LINEAR_TABLES, portfolio_transaction
from .audit_chain import AuditChainSource, build_audit_chain_entry, canonical_json_text
from .linear_connector import LinearProjection, NORMALIZER_DIGEST
from .linear_evidence import linear_source_identity, validate_linear_evidence
from .linear_webhook_auth import LinearVerifiedDelivery
from .outcome_connector_config import LinearOutcomeChannelConfig
from .outcome_ingest import registered_binding, validate_delivery
from .outcome_repository import OutcomeRepository
from .outcome_wire import OutcomeKeys, observation_from_mapping, timestamp
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError, canonical


_BINDING_NAMESPACE = UUID("6268e321-02a7-4d31-80aa-32892546a4f8")
_CONTEXT_NAMESPACE = UUID("8f00af3e-7df5-49c7-8f8c-5bc2901f79bc")
_RECEIPT_NAMESPACE = UUID("d55b452c-a9fb-44e7-82d1-c2fc31b81762")


class LinearConnectorRepository:
    def __init__(
        self,
        config,
        *,
        dsn: str,
        connection_pool=None,
        read_only: bool = False,
        outcomes: OutcomeRepository | None = None,
    ) -> None:
        self.config = config
        self._dsn = dsn
        self._pool = connection_pool
        self._read_only = read_only
        self.outcomes = outcomes or OutcomeRepository(
            config,
            dsn=dsn,
            connection_pool=connection_pool,
            read_only=read_only,
        )

    def _authorize(
        self,
        channel: LinearOutcomeChannelConfig,
        binding: PortfolioConnectorBinding,
        verified: LinearVerifiedDelivery,
    ) -> None:
        runtime = self.config.outcome_connectors
        if (
            self._read_only
            or runtime is None
            or channel not in runtime.linear
            or registered_binding(
                self.config,
                binding.organization_id,
                binding.connector_id,
            ) != binding
        ):
            raise PortfolioError("forbidden")
        validate_delivery(binding, verified)
        if (
            verified.provider_delivery_id is None
            or verified.source_webhook_id != channel.source_webhook_id
            or binding.workspace_id != verified.workspace_id
        ):
            raise PortfolioError("forbidden")

    @staticmethod
    def _remaining_ms(deadline: float) -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise PortfolioError("unavailable")
        return min(4000, remaining)

    @contextmanager
    def _transaction(self, organization: str, deadline: float):
        timeout = self._remaining_ms(deadline)
        with portfolio_transaction(
            self.config,
            organization,
            dsn=self._dsn,
            connection_pool=self._pool,
            tables=OUTCOME_LINEAR_TABLES,
            statement_timeout_ms=timeout,
        ) as sql:
            yield sql
            self._remaining_ms(deadline)

    @staticmethod
    def _fingerprints(verified: LinearVerifiedDelivery) -> dict[int, str]:
        return {int(version): digest for version, digest in verified.body_fingerprints}

    def _response(self, row) -> dict:
        try:
            evidence = json.loads(row["evidence_json"])
            response = evidence["response"]
            if (
                not isinstance(evidence, dict)
                or not isinstance(response, dict)
                or canonical(evidence) != row["evidence_json"]
                or hashlib.sha256(canonical(response).encode("ascii")).hexdigest()
                != row["response_digest"]
            ):
                raise ValueError
            return self.outcomes._public(response)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, PortfolioError):
            raise PortfolioError("unavailable") from None

    def _check_body(self, row, verified: LinearVerifiedDelivery) -> None:
        expected = self._fingerprints(verified).get(int(row["fingerprint_key_version"]))
        if expected is None or not hmac.compare_digest(expected, row["body_fingerprint"]):
            raise PortfolioError("idempotency_conflict")

    def _body_row(self, sql, binding, verified):
        organization, connector = binding.organization_id, binding.connector_id
        delivery = sql.one(
            "SELECT * FROM gateway_linear_delivery_receipts "
            "WHERE organization_id=? AND connector_id=? AND source_delivery_id=?",
            (organization, connector, verified.provider_delivery_id),
        )
        if delivery is not None:
            self._check_body(delivery, verified)
            return delivery
        fingerprints = self._fingerprints(verified)
        clauses = []
        values: list[object] = [organization, connector]
        for version, digest in fingerprints.items():
            clauses.append("(fingerprint_key_version=? AND body_fingerprint=?)")
            values.extend((version, digest))
        rows = sql.execute(
            "SELECT * FROM gateway_linear_delivery_receipts "
            "WHERE organization_id=? AND connector_id=? AND ("
            + " OR ".join(clauses)
            + ")",
            tuple(values),
        ).fetchall()
        if len(rows) > 1:
            raise PortfolioError("unavailable")
        return dict(rows[0]) if rows else None

    def replay_body(
        self,
        *,
        channel,
        binding,
        verified,
        raw,
        keys,
        deadline,
    ) -> dict | None:
        del raw, keys
        self._authorize(channel, binding, verified)
        with self._transaction(binding.organization_id, deadline) as sql:
            row = self._body_row(sql, binding, verified)
            return None if row is None else self._response(row)

    @staticmethod
    def _fact_fingerprints(
        channel: LinearOutcomeChannelConfig,
        binding: PortfolioConnectorBinding,
        keys: OutcomeKeys,
        projection: LinearProjection,
    ) -> dict[int, str]:
        return {
            int(version): keys.metadata_digest(
                version,
                binding.organization_id,
                binding.connector_id,
                "linear-source-fact-v1",
                projection.source_fact,
            )
            for version in channel.identity_key_versions
        }

    @staticmethod
    def _fact_row(sql, binding, fingerprints):
        clauses = []
        values: list[object] = [binding.organization_id, binding.connector_id]
        for version, digest in fingerprints.items():
            clauses.append("(source_fact_key_version=? AND source_fact_fingerprint=?)")
            values.extend((version, digest))
        rows = sql.execute(
            "SELECT * FROM gateway_linear_delivery_receipts "
            "WHERE organization_id=? AND connector_id=? AND ("
            + " OR ".join(clauses)
            + ")",
            tuple(values),
        ).fetchall()
        if len(rows) > 1:
            raise PortfolioError("unavailable")
        return dict(rows[0]) if rows else None

    def accept(
        self,
        *,
        channel,
        binding,
        verified,
        raw,
        keys,
        projection,
        deadline,
    ) -> dict:
        self._authorize(channel, binding, verified)
        if not isinstance(keys, OutcomeKeys) or not isinstance(projection, LinearProjection):
            raise PortfolioError("invalid_request")
        fact_fingerprints = self._fact_fingerprints(channel, binding, keys, projection)
        fact_version = int(channel.source_fact_key_version)
        fact_digest = fact_fingerprints[fact_version]
        outcome_verified = replace(verified, source_delivery_id=fact_digest)
        observations = ()
        if projection.outcome is not None:
            value = dict(projection.outcome)
            value["source_event_id"] = fact_digest + ":0"
            observations = (observation_from_mapping(value, binding),)
        observed_at = datetime.fromtimestamp(
            verified.received_timestamp_ms / 1000,
            timezone.utc,
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self._transaction(binding.organization_id, deadline) as sql:
            replay = self._body_row(sql, binding, verified)
            if replay is not None:
                return self._response(replay)
            replay = self._fact_row(sql, binding, fact_fingerprints)
            if replay is not None:
                return self._response(replay)
            binding_row = self._ensure_binding(sql, channel, binding, keys)
            response = self.outcomes._accept_in_transaction(
                sql,
                binding=binding,
                verified=outcome_verified,
                raw=raw,
                keys=keys,
                observed_at=observed_at,
                observations=observations,
                force_accepted=True,
                accepted_event_count=1 + len(observations),
            )
            committed_at = sql.now()
            receipt_id = str(uuid5(
                _RECEIPT_NAMESPACE,
                f"{binding.organization_id}:{binding.connector_id}:{fact_digest}",
            ))
            context_event_id = str(uuid5(
                _CONTEXT_NAMESPACE,
                f"{binding.organization_id}:{binding.connector_id}:{fact_digest}",
            ))
            receipt_evidence = {
                "schema_id": "hormuz.linear-delivery-receipt",
                "schema_version": 1,
                "organization_id": binding.organization_id,
                "connector_id": binding.connector_id,
                "receipt_id": receipt_id,
                "binding_version": channel.binding_version,
                "source_delivery_id": verified.provider_delivery_id,
                "credential_version": verified.credential_version,
                "body_fingerprint_key_version": int(channel.body_fingerprint_key_version),
                "source_fact_key_version": fact_version,
                "received_at": observed_at,
                "committed_at": committed_at,
                "context_event_id": context_event_id,
                "outcome_source_delivery_id": fact_digest,
                "response": response,
            }
            validate_linear_evidence("hormuz.linear-delivery-receipt", receipt_evidence)
            receipt_json = canonical(receipt_evidence)
            sql.insert("gateway_linear_delivery_receipts", {
                "organization_id": binding.organization_id,
                "connector_id": binding.connector_id,
                "receipt_id": receipt_id,
                "binding_version": channel.binding_version,
                "source_delivery_id": verified.provider_delivery_id,
                "credential_version": verified.credential_version,
                "body_fingerprint": self._fingerprints(verified)[
                    int(channel.body_fingerprint_key_version)
                ],
                "fingerprint_key_version": int(channel.body_fingerprint_key_version),
                "source_fact_fingerprint": fact_digest,
                "source_fact_key_version": fact_version,
                "received_at": observed_at,
                "committed_at": committed_at,
                "response_digest": hashlib.sha256(
                    canonical(response).encode("ascii")
                ).hexdigest(),
                "evidence_json": receipt_json,
            })
            context = self._context(
                sql,
                channel,
                binding,
                verified,
                projection,
                binding_row,
                keys,
                receipt_id,
                context_event_id,
                observed_at,
                committed_at,
            )
            sql.insert("portfolio_linear_context_events", {
                "organization_id": binding.organization_id,
                "connector_id": binding.connector_id,
                "context_event_id": context_event_id,
                "receipt_id": receipt_id,
                "object_kind": verified.object_kind,
                "object_id": verified.object_id,
                "lifecycle": context["lifecycle"],
                "normalized_state": context["normalized_state"],
                "relationship_coverage": context["relationship_coverage"],
                "revision_kind": context["revision"]["kind"],
                "revision_value": context["revision"]["value"],
                "ordering_state": context["ordering_state"],
                "scope_state": context["scope_state"],
                "event_at": context["event_at"],
                "observed_at": observed_at,
                "ingested_at": committed_at,
                "provenance_digest": context["provenance_digest"],
                "commit_sequence": context["commit_sequence"],
                "evidence_json": canonical(context),
            })
            self._append_audit(sql, receipt_evidence, "hormuz.linear-delivery-receipt")
            self._append_audit(sql, context, "hormuz.linear-context-event")
            self._remaining_ms(deadline)
            return response

    def _ensure_binding(self, sql, channel, binding, keys):
        organization, connector = binding.organization_id, binding.connector_id
        typed = {kind: list(values) for kind, values in channel.typed_enrollment.items()}
        basis = {
            "organization_id": organization,
            "connector_id": connector,
            "version": channel.binding_version,
            "binding_state": "active",
            "source_workspace_id": binding.workspace_id,
            "source_webhook_id": channel.source_webhook_id,
            "source_team_ids": list(channel.source_team_ids),
            "typed_enrollment": typed,
            "credential_version": channel.active_webhook_secret.version,
            "fingerprint_key_version": int(channel.body_fingerprint_key_version),
            "registered_by": channel.registered_by,
        }
        content_digest = hashlib.sha256(canonical(basis).encode("ascii")).hexdigest()
        binding_event_id = str(uuid5(
            _BINDING_NAMESPACE,
            f"{organization}:{connector}:{channel.binding_version}:{content_digest}",
        ))
        request_digest = keys.metadata_digest(
            channel.body_fingerprint_key_version,
            organization,
            connector,
            "linear-binding-request-v1",
            basis,
        )
        latest = sql.one(
            "SELECT * FROM portfolio_linear_source_binding_versions "
            "WHERE organization_id=? AND connector_id=? ORDER BY version DESC LIMIT 1",
            (organization, connector),
        )
        if latest is not None and int(latest["version"]) == channel.binding_version:
            if (
                latest["binding_event_id"] != binding_event_id
                or latest["content_digest"] != content_digest
                or latest["request_digest"] != request_digest
                or latest["binding_state"] != "active"
            ):
                raise PortfolioError("version_conflict")
            return latest
        expected_previous = channel.binding_version - 1
        if (
            (channel.binding_version == 1 and latest is not None)
            or (
                channel.binding_version > 1
                and (latest is None or latest["version"] != expected_previous)
            )
        ):
            raise PortfolioError("version_conflict")
        registered_at = sql.now()
        evidence = {
            "schema_id": "hormuz.linear-source-binding-version",
            "schema_version": 1,
            **basis,
            "binding_event_id": binding_event_id,
            "content_digest": content_digest,
            "request_digest": request_digest,
            "registered_at": registered_at,
        }
        validate_linear_evidence("hormuz.linear-source-binding-version", evidence)
        sql.insert("portfolio_linear_source_binding_versions", {
            "organization_id": organization,
            "connector_id": connector,
            "version": channel.binding_version,
            "binding_event_id": binding_event_id,
            "previous_version": None if channel.binding_version == 1 else expected_previous,
            "binding_state": "active",
            "source_workspace_id": binding.workspace_id,
            "source_webhook_id": channel.source_webhook_id,
            "source_team_ids_json": canonical(list(channel.source_team_ids)),
            "typed_enrollment_json": canonical(typed),
            "credential_version": channel.active_webhook_secret.version,
            "fingerprint_key_version": int(channel.body_fingerprint_key_version),
            "content_digest": content_digest,
            "request_digest": request_digest,
            "registered_by": channel.registered_by,
            "registered_at": registered_at,
            "evidence_json": canonical(evidence),
        })
        self._append_audit(sql, evidence, "hormuz.linear-source-binding-version")
        return {
            **evidence,
            "source_workspace_id": binding.workspace_id,
            "source_webhook_id": channel.source_webhook_id,
        }

    def _context(
        self,
        sql,
        channel,
        binding,
        verified,
        projection,
        binding_row,
        keys,
        receipt_id,
        context_event_id,
        observed_at,
        committed_at,
    ):
        del receipt_id
        organization, connector = binding.organization_id, binding.connector_id
        sequence = int(sql.one(
            "SELECT COALESCE(MAX(commit_sequence),0) AS sequence "
            "FROM portfolio_linear_context_events WHERE organization_id=?",
            (organization,),
        )["sequence"]) + 1
        if sequence > 9223372036854775807:
            raise PortfolioError("unavailable")
        revision = projection.context["revision"]
        prior = sql.one(
            "SELECT context_event_id,revision_kind,revision_value,ordering_state "
            "FROM portfolio_linear_context_events WHERE organization_id=? "
            "AND connector_id=? AND object_kind=? AND object_id=? "
            "AND ordering_state='current' ORDER BY commit_sequence DESC LIMIT 1",
            (organization, connector, verified.object_kind, verified.object_id),
        )
        ordering = "unknown"
        supersedes = None
        if revision["kind"] != "unknown" and revision["value"] is not None:
            if prior is None:
                ordering = "current"
            elif prior["revision_kind"] != revision["kind"] or prior["revision_value"] is None:
                ordering = "incomparable"
            else:
                current_revision = timestamp(revision["value"])
                prior_revision = timestamp(prior["revision_value"])
                if current_revision > prior_revision:
                    ordering = "current"
                    if projection.context["relationship_coverage"] == "complete":
                        supersedes = prior["context_event_id"]
                elif current_revision < prior_revision:
                    ordering = "late"
                else:
                    ordering = "incomparable"
        registry_sequence = int(sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_audit_events "
            "WHERE organization_id=?",
            (organization,),
        )["sequence"])
        scope_state, work_binding = self._work_binding(
            sql,
            binding,
            verified,
            projection,
            registry_sequence,
            observed_at,
        )
        event = {
            "schema_id": "hormuz.linear-context-event",
            "schema_version": 1,
            "organization_id": organization,
            "connector_id": connector,
            "context_event_id": context_event_id,
            "source_workspace_id": binding.workspace_id,
            "source_team_ids": projection.context["source_team_ids"],
            "object": projection.context["object"],
            "capture_kind": "webhook",
            "source_delivery_id": verified.provider_delivery_id,
            "authority_binding": {
                "id": connector,
                "version": channel.binding_version,
                "content_digest": binding_row["content_digest"],
            },
            "normalizer": {
                "id": "linear-webhook-normalizer",
                "version": 1,
                "content_digest": NORMALIZER_DIGEST,
            },
            "credential_version": verified.credential_version,
            "source_authentication": "verified_connector",
            "lifecycle": projection.context["lifecycle"],
            "normalized_state": projection.context["normalized_state"],
            "relationships": projection.context["relationships"],
            "relationship_coverage": projection.context["relationship_coverage"],
            "revision": revision,
            "ordering_state": ordering,
            "scope_state": scope_state,
            "binding": work_binding,
            "supersedes_context_event_id": supersedes,
            "event_at": projection.context["event_at"],
            "observed_at": observed_at,
            "ingested_at": committed_at,
            "provenance_digest": "0" * 64,
            "evidence_level": "descriptive",
            "association_eligibility": "inconclusive",
            "reader_role": "portfolio_admin",
            "commit_sequence": sequence,
        }
        event["provenance_digest"] = keys.metadata_digest(
            channel.current_key_version,
            organization,
            connector,
            context_event_id,
            event,
        )
        validate_linear_evidence("hormuz.linear-context-event", event)
        return event

    @staticmethod
    def _work_binding(
        sql,
        binding,
        verified,
        projection,
        registry_sequence,
        observed_at,
    ):
        project_id = None
        if verified.object_kind == "project":
            project_id = verified.object_id
        elif verified.object_kind == "issue":
            for relationship in projection.context["relationships"]:
                if relationship["kind"] == "project_issue":
                    project_id = relationship["parent"]["id"]
                    break
        event_at = projection.context["event_at"]
        if project_id is None or event_at is None:
            return "unmatched", None
        if timestamp(event_at) > timestamp(observed_at):
            return "excluded", None
        bound = sql.one(
            "SELECT * FROM portfolio_binding_events WHERE organization_id=? "
            "AND connector_id=? AND external_object_id=? AND event_at<=? "
            "ORDER BY sequence DESC LIMIT 1",
            (binding.organization_id, binding.connector_id, project_id, event_at),
        )
        if bound is None:
            return "unmatched", None
        scope = sql.one(
            "SELECT * FROM portfolio_work_scope_versions WHERE organization_id=? "
            "AND work_scope_id=? AND event_at<=? ORDER BY version DESC LIMIT 1",
            (binding.organization_id, bound["work_scope_id"], event_at),
        )
        if (
            bound["state"] != "active"
            or scope is None
            or scope["kind"] != "use_case"
            or scope["state"] != "active"
            or scope["version"] != bound["work_scope_version"]
        ):
            return "excluded", None
        return "matched", {
            "binding_event_id": bound["binding_event_id"],
            "work_scope": {
                "work_scope_id": bound["work_scope_id"],
                "version": bound["work_scope_version"],
            },
            "registry_sequence": registry_sequence,
        }

    @staticmethod
    def _append_audit(sql, event, schema_id):
        source_id = linear_source_identity(schema_id, event)
        source = AuditChainSource(schema_id, 1, source_id)
        organization = event["organization_id"]
        now = sql.now()
        if sql.postgres:
            sql.execute(
                "INSERT INTO gateway_audit_chain_epochs (organization_id,chain_version,chain_epoch,created_at,reason_code,predecessor_chain_epoch,predecessor_sequence,predecessor_head_digest) "
                "VALUES (?,1,1,?,'initial_adoption',NULL,NULL,NULL) ON CONFLICT (organization_id,chain_epoch) DO NOTHING",
                (organization, now),
            )
            sql.execute(
                "INSERT INTO gateway_audit_chain_heads (organization_id,chain_version,chain_epoch,sequence,head_digest) "
                "VALUES (?,1,1,0,NULL) ON CONFLICT (organization_id) DO NOTHING",
                (organization,),
            )
            head = sql.one(
                "SELECT * FROM gateway_audit_chain_heads WHERE organization_id=? FOR UPDATE",
                (organization,),
            )
        else:
            sql.execute(
                "INSERT OR IGNORE INTO gateway_audit_chain_epochs (organization_id,chain_version,chain_epoch,created_at,reason_code,predecessor_chain_epoch,predecessor_sequence,predecessor_head_digest) "
                "VALUES (?,1,1,?,'initial_adoption',NULL,NULL,NULL)",
                (organization, now),
            )
            sql.execute(
                "INSERT OR IGNORE INTO gateway_audit_chain_heads (organization_id,chain_version,chain_epoch,sequence,head_digest) "
                "VALUES (?,1,1,0,NULL)",
                (organization,),
            )
            head = sql.one(
                "SELECT * FROM gateway_audit_chain_heads WHERE organization_id=?",
                (organization,),
            )
        if head is None:
            raise PortfolioError("unavailable")
        try:
            entry = build_audit_chain_entry(
                event,
                chain_version=int(head["chain_version"]),
                chain_epoch=int(head["chain_epoch"]),
                sequence=int(head["sequence"]) + 1,
                previous_digest=head["head_digest"],
                entry_schema_version=2,
                source=source,
            )
        except Exception:
            raise PortfolioError("unavailable") from None
        result = sql.execute(
            "INSERT INTO gateway_audit_chain_entries (organization_id,chain_version,chain_epoch,sequence,entry_schema_id,entry_schema_version,event_id,previous_digest,event_digest,event_json,appended_at,source_schema_id,source_schema_version,source_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                organization,
                entry["chain_version"],
                entry["chain_epoch"],
                entry["sequence"],
                entry["schema_id"],
                entry["schema_version"],
                source_id,
                entry["previous_digest"],
                entry["event_digest"],
                canonical_json_text(event),
                now,
                schema_id,
                1,
                source_id,
            ),
        )
        if result.rowcount != 1:
            raise PortfolioError("unavailable")
        result = sql.execute(
            "UPDATE gateway_audit_chain_heads SET sequence=?,head_digest=? "
            "WHERE organization_id=? AND chain_epoch=? AND sequence=?",
            (
                entry["sequence"],
                entry["event_digest"],
                organization,
                head["chain_epoch"],
                head["sequence"],
            ),
        )
        if result.rowcount != 1:
            raise PortfolioError("unavailable")
