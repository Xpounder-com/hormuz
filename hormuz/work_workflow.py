"""Private work bindings and consented acquisition; verified metadata only.

Connector proxies leave predecessor verification and storage intact. A durable
metadata journal spans the two stores: source commits are confirmed before
observations apply, and signed replays finish interrupted bridge operations.
No raw source payload, prompt, repository object ID or public analytics ID is
retained. Association is explicit; event arrival does not infer association.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import hmac
import json
import re
import secrets
import threading
from uuid import uuid4

from .config import Identity
from .outcome_ingest import registered_binding, validate_delivery
from .outcome_wire import observation_from_mapping
from .portfolio_wire import PortfolioError
from .work_runtime import WorkRuntimeError

CAMPAIGN_FIELDS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_content"})
CONDITIONS = {"github.pull_request.merged.v1": "github", "github.check.passed.v1": "github",
              "linear.issue.completed.v1": "linear"}


def _bridge(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except WorkRuntimeError:
        # Existing webhook transport returns a fixed retryable storage error;
        # it must not leak a connector payload or raw SQLite exception.
        raise PortfolioError("unavailable") from None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_work_workflow_keys (id INTEGER PRIMARY KEY CHECK(id=1), material BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS ai_work_bindings (
 binding_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id), provider TEXT NOT NULL,
 connector_id TEXT NOT NULL, object_digest TEXT NOT NULL, container_digest TEXT NOT NULL,
 completion_condition TEXT NOT NULL, created_at REAL NOT NULL, latest_event_at REAL,
 UNIQUE(organization_id,provider,connector_id,object_digest)
);
CREATE TABLE IF NOT EXISTS ai_work_deliveries (
 delivery_digest TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 connector_id TEXT NOT NULL, provider TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_work_bridge_events (
 event_digest TEXT PRIMARY KEY, delivery_digest TEXT NOT NULL,
 organization_id TEXT NOT NULL, connector_id TEXT NOT NULL, provider TEXT NOT NULL,
 object_digest TEXT NOT NULL, container_digest TEXT NOT NULL, event_type TEXT NOT NULL,
 object_type TEXT NOT NULL, quality_state TEXT NOT NULL, event_at REAL,
 status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL DEFAULT 'pending',
 binding_id TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_work_bridge_delivery ON ai_work_bridge_events(delivery_digest,status);
CREATE TABLE IF NOT EXISTS ai_work_acquisition (
 acquisition_reference TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 labels TEXT NOT NULL, consent_at REAL NOT NULL,
 UNIQUE(organization_id,actor_id)
);
CREATE TABLE IF NOT EXISTS ai_work_funnel (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, event TEXT NOT NULL,
 reference TEXT NOT NULL, occurred_at REAL NOT NULL,
 PRIMARY KEY(organization_id,actor_id,event,reference)
);
"""


def campaign_labels(value):
    if not isinstance(value, dict) or set(value) - CAMPAIGN_FIELDS or not value:
        raise WorkRuntimeError("invalid_campaign_labels")
    if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", item) for item in value.values()):
        raise WorkRuntimeError("invalid_campaign_labels")
    return dict(value)


class WorkWorkflow:
    def __init__(self, runtime, config):
        self.runtime, self.config = runtime, config
        self._apply_lock = threading.RLock()
        with closing(runtime._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.execute("INSERT OR IGNORE INTO ai_work_workflow_keys VALUES(1,?)", (secrets.token_bytes(32),))
            self._key = bytes(connection.execute("SELECT material FROM ai_work_workflow_keys WHERE id=1").fetchone()[0])

    def _digest(self, domain, *values):
        return hmac.new(self._key, json.dumps([domain, *values], separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    def bind(self, identity, work_id, *, provider, connector_id, container_id, object_id, completion_condition):
        work = self.runtime.get_work(identity, work_id)
        if CONDITIONS.get(completion_condition) != provider or completion_condition != work["completion_condition"]:
            raise WorkRuntimeError("binding_completion_condition_conflict", 409)
        binding = registered_binding(self.config, identity.organization_id, connector_id)
        active = getattr(self.config, "outcome_connectors", None)
        channels = getattr(active, provider, ()) if provider in {"github", "linear"} else ()
        if binding.provider != provider or not any((row.organization_id, row.connector_id) == (identity.organization_id, connector_id) for row in channels) or container_id not in binding.external_object_ids:
            raise WorkRuntimeError("binding_connector_not_qualified", 403)
        pattern = r"[1-9][0-9]{0,19}" if provider == "github" else r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        if not isinstance(object_id, str) or not re.fullmatch(pattern, object_id):
            raise WorkRuntimeError("binding_invalid_object")
        object_digest = self._digest("object", identity.organization_id, provider, connector_id, object_id)
        container_digest = self._digest("container", identity.organization_id, provider, connector_id, container_id)
        now = self.runtime._now()
        with self.runtime._transaction(write=True) as connection:
            prior = connection.execute("SELECT * FROM ai_work_bindings WHERE organization_id=? AND provider=? AND connector_id=? AND object_digest=?", (identity.organization_id, provider, connector_id, object_digest)).fetchone()
            if prior:
                if (prior["actor_id"], prior["work_id"], prior["container_digest"], prior["completion_condition"]) != (identity.actor_id, work_id, container_digest, completion_condition):
                    raise WorkRuntimeError("binding_association_conflict", 409)
                return self._binding_view(prior)
            if connection.execute("SELECT 1 FROM ai_work_bindings WHERE work_id=?", (work_id,)).fetchone():
                raise WorkRuntimeError("binding_completion_object_already_associated", 409)
            identifier = "binding-" + uuid4().hex
            connection.execute("INSERT INTO ai_work_bindings(binding_id,organization_id,actor_id,work_id,provider,connector_id,object_digest,container_digest,completion_condition,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (identifier, identity.organization_id, identity.actor_id, work_id, provider, connector_id, object_digest, container_digest, completion_condition, now))
            row = connection.execute("SELECT * FROM ai_work_bindings WHERE binding_id=?", (identifier,)).fetchone()
            return self._binding_view(row)

    @staticmethod
    def _binding_view(row):
        return {key: row[key] for key in ("binding_id", "work_id", "provider", "connector_id", "completion_condition", "created_at")}

    def bindings(self, identity, work_id):
        self.runtime.get_work(identity, work_id)
        with self.runtime._transaction() as connection:
            rows = connection.execute("SELECT * FROM ai_work_bindings WHERE organization_id=? AND actor_id=? AND work_id=? ORDER BY created_at LIMIT 100", (identity.organization_id, identity.actor_id, work_id)).fetchall()
        return {"bindings": [self._binding_view(row) for row in rows], "coverage": "explicit_signed_object_associations_only"}

    def _delivery_digest(self, binding, verified, raw):
        body = hmac.new(self._key, b"verified-source-body\0" + raw, hashlib.sha256).hexdigest()
        return self._digest("delivery", binding.organization_id, binding.provider, binding.connector_id, verified.source_delivery_id, body), body

    def _stage(self, *, binding, verified, raw, observations, source_kind=None):
        validate_delivery(binding, verified)
        delivery, body = self._delivery_digest(binding, verified, raw)
        now = self.runtime._now()
        with self.runtime._transaction(write=True) as connection:
            connection.execute("INSERT OR IGNORE INTO ai_work_deliveries VALUES(?,?,?,?,0,?)", (delivery, binding.organization_id, binding.connector_id, binding.provider, now))
            for item in observations:
                # Called only after the predecessor's signature/parent verifier.
                object_digest = self._digest("object", binding.organization_id, binding.provider, binding.connector_id, item.external_object_id)
                container_digest = self._digest("container", binding.organization_id, binding.provider, binding.connector_id, item.container_id)
                event_digest = self._digest("event", binding.organization_id, binding.provider, binding.connector_id, item.source_event_id, body)
                event_at = None
                if item.event_at:
                    event_at = datetime.fromisoformat(item.event_at.replace("Z", "+00:00")).timestamp()
                connection.execute("INSERT OR IGNORE INTO ai_work_bridge_events(event_digest,delivery_digest,organization_id,connector_id,provider,object_digest,container_digest,event_type,object_type,quality_state,event_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (event_digest, delivery, binding.organization_id, binding.connector_id, binding.provider, object_digest, container_digest, item.event_type, source_kind or item.object_type, item.quality_state, event_at, now))
        return delivery

    def _commit(self, binding, verified, raw):
        delivery, _ = self._delivery_digest(binding, verified, raw)
        with self.runtime._transaction(write=True) as connection:
            connection.execute("UPDATE ai_work_deliveries SET committed=1 WHERE delivery_digest=?", (delivery,))
        with self._apply_lock:
            self._apply(delivery)

    @staticmethod
    def _status(event, binding):
        if event["event_type"] in {"reopened", "defect_reported"}:
            return "reopened" if event["event_type"] == "reopened" else "corrected"
        if event["event_type"] == "canceled":
            return "canceled"
        condition = binding["completion_condition"]
        if event["event_type"] == "completed" and (condition == "github.pull_request.merged.v1" and event["object_type"] == "pull_request_lifecycle" or condition == "linear.issue.completed.v1" and event["object_type"] == "issue"):
            return "completed"
        if condition == "github.check.passed.v1" and event["object_type"] == "check_run" and event["event_type"] == "completed" and event["quality_state"] == "accepted":
            return "completed"
        return None

    def _apply(self, delivery):
        # Observation replay is idempotent if the process dies before journal ACK.
        with self.runtime._transaction() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM ai_work_bridge_events WHERE delivery_digest=? AND status='pending' ORDER BY event_at,event_digest LIMIT 100", (delivery,))]
        for event in rows:
            with self.runtime._transaction() as connection:
                binding = connection.execute("SELECT * FROM ai_work_bindings WHERE organization_id=? AND provider=? AND connector_id=? AND object_digest=? AND container_digest=?", (event["organization_id"], event["provider"], event["connector_id"], event["object_digest"], event["container_digest"])).fetchone()
                binding = dict(binding) if binding else None
            reason, status = "unmatched", None
            if binding:
                instant = event["event_at"]
                if instant is None:
                    reason = "source_time_unknown"
                elif instant < binding["created_at"] or binding["latest_event_at"] is not None and (instant < binding["latest_event_at"] or instant == binding["latest_event_at"] and event["event_type"] not in {"reopened", "defect_reported", "canceled"}):
                    reason = "late_or_ambiguous"
                else:
                    status = self._status(event, binding)
                    reason = "observed" if status else "intermediate_or_unsupported"
            if status:
                identity = Identity(token_env="", token="", actor_id=binding["actor_id"], actor_name="", team_id="workflow", team_name="", organization_id=binding["organization_id"])
                self.runtime.observe(identity, binding["work_id"], status, source="verified_" + event["provider"], reference="source-" + event["event_digest"], completion_condition=binding["completion_condition"])
            with self.runtime._transaction(write=True) as connection:
                connection.execute("UPDATE ai_work_bridge_events SET status='processed',reason=?,binding_id=? WHERE event_digest=?", (reason, binding["binding_id"] if binding else None, event["event_digest"]))
                if binding and reason in {"observed", "intermediate_or_unsupported"}:
                    connection.execute("UPDATE ai_work_bindings SET latest_event_at=MAX(COALESCE(latest_event_at,0),?) WHERE binding_id=?", (event["event_at"], binding["binding_id"]))

    def acquire(self, identity, labels, *, consent):
        if consent is not True:
            raise WorkRuntimeError("acquisition_consent_required")
        labels = campaign_labels(labels)
        with self.runtime._transaction(write=True) as connection:
            prior = connection.execute("SELECT acquisition_reference FROM ai_work_acquisition WHERE organization_id=? AND actor_id=?", (identity.organization_id, identity.actor_id)).fetchone()
            reference = prior[0] if prior else "acquisition-" + uuid4().hex
            if not prior:
                connection.execute("INSERT INTO ai_work_acquisition VALUES(?,?,?,?,?)", (reference, identity.organization_id, identity.actor_id, json.dumps(labels, sort_keys=True), self.runtime._now()))
        return {"acquisition_reference": reference, "scope": "private_authenticated_actor", "consent": True}

    def event(self, identity, event, reference):
        if event != "receipt_opened":
            raise WorkRuntimeError("invalid_funnel_event")
        with self.runtime._transaction(write=True) as connection:
            # No acquisition tracking is enabled merely by opening /work.
            if connection.execute("SELECT 1 FROM ai_work_acquisition WHERE organization_id=? AND actor_id=?", (identity.organization_id, identity.actor_id)).fetchone():
                connection.execute("INSERT OR IGNORE INTO ai_work_funnel VALUES(?,?,?,?,?)", (identity.organization_id, identity.actor_id, event, self._digest("funnel", reference), self.runtime._now()))

    def observed_connection(self, identity, request_id):
        """Record a delivered, protocol-validated API response after settlement.

        The gateway supplies terminal validation. Recheck its owned, uncached
        settled attempt here; neither a browser nor a configured credential can
        submit a connection claim. This establishes no native-client grade.
        """
        with self.runtime._transaction(write=True) as connection:
            if not connection.execute("SELECT 1 FROM ai_work_acquisition WHERE organization_id=? AND actor_id=?", (identity.organization_id, identity.actor_id)).fetchone():
                return
            attempt = connection.execute("SELECT model,protocol,route_fingerprint FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND request_id=? AND state='succeeded' AND response_succeeded=1 AND cache_source IS NULL", (identity.organization_id, identity.actor_id, request_id)).fetchone()
            if attempt is None:
                return
            connection.execute("INSERT OR IGNORE INTO ai_work_funnel VALUES(?,?,?,?,?)", (identity.organization_id, identity.actor_id, "api_response_observed", self._digest("api-response", *attempt), self.runtime._now()))

    def funnel(self, identity):
        with self.runtime._transaction() as connection:
            acquisition = connection.execute("SELECT acquisition_reference,labels,consent_at FROM ai_work_acquisition WHERE organization_id=? AND actor_id=?", (identity.organization_id, identity.actor_id)).fetchone()
            if not acquisition:
                return {"consent": False, "events": {}}
            since = acquisition["consent_at"]
            attempts = connection.execute("SELECT COUNT(DISTINCT work_id) FROM ai_work_attempts WHERE organization_id=? AND actor_id=? AND created_at>=? AND state<>'denied' AND work_id IN (SELECT work_id FROM ai_work_jobs WHERE repository!='unattributed')", (identity.organization_id, identity.actor_id, since)).fetchone()[0]
            events = {row[0]: row[1] for row in connection.execute("SELECT event,COUNT(*) FROM ai_work_funnel WHERE organization_id=? AND actor_id=? AND occurred_at>=? AND event IN ('api_response_observed','receipt_opened') GROUP BY event", (identity.organization_id, identity.actor_id, since))}
        # Predecessor 'qualified_connection' rows meant a setup read. Preserve
        # their history, but never promote those rows to actual response proof.
        events["qualified_connection"] = events.pop("api_response_observed", 0)
        events.update(first_attributed_work=int(attempts > 0), repeat_work=int(attempts > 1))
        return {"consent": True, "acquisition_reference": acquisition[0], "labels": json.loads(acquisition[1]), "events": events, "scope": "private_actor"}


class GitHubWorkRepository:
    def __init__(self, repository, workflow):
        self.repository, self.workflow = repository, workflow

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def _accept_verified(self, **values):
        self.repository._authorize_connector(values["binding"], values["verified"])
        # These exact bytes have already passed signature, registered parent
        # and normalized-shape checks. A check success cannot mean PR merge.
        body = json.loads(values["raw"])
        kind = "check_run" if "check_run" in body else "review" if "review" in body else "pull_request_lifecycle"
        _bridge(self.workflow._stage, binding=values["binding"], verified=values["verified"], raw=values["raw"], observations=values["observations"], source_kind=kind)
        result = self.repository._accept_verified(**values)
        _bridge(self.workflow._commit, values["binding"], values["verified"], values["raw"])
        return result

    def _replay_verified(self, **values):
        result = self.repository._replay_verified(**values)
        if result is not None:
            _bridge(self.workflow._commit, values["binding"], values["verified"], values["raw"])
        return result


class LinearWorkRepository:
    def __init__(self, repository, workflow):
        self.repository, self.workflow = repository, workflow

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def accept(self, **values):
        binding, verified, projection = values["binding"], values["verified"], values["projection"]
        self.repository._authorize(values["channel"], binding, verified)
        observations = ()
        if projection.outcome:
            value = dict(projection.outcome)
            value["source_event_id"] = self.workflow._digest("linear-event", verified.source_delivery_id) + ":0"
            observations = (observation_from_mapping(value, binding),)
        _bridge(self.workflow._stage, binding=binding, verified=verified, raw=values["raw"], observations=observations)
        result = self.repository.accept(**values)
        _bridge(self.workflow._commit, binding, verified, values["raw"])
        return result

    def replay_body(self, **values):
        result = self.repository.replay_body(**values)
        if result is not None:
            _bridge(self.workflow._commit, values["binding"], values["verified"], values["raw"])
        return result


def attach(server):
    workflow = WorkWorkflow(server.work_runtime, server.config)
    server.work_workflow = workflow
    receiver = getattr(server, "github_outcome_receiver", None)
    if receiver:
        for ingestor in receiver._ingestors:
            ingestor.repository = GitHubWorkRepository(ingestor.repository, workflow)
    receiver = getattr(server, "linear_outcome_receiver", None)
    if receiver:
        receiver.repository = LinearWorkRepository(receiver.repository, workflow)
