"""Opt-in, encrypted device-local review history, separate from accounting.

No network or model-training capabilities belong to this module. The local key
protects files at rest; it does not protect against code running as their owner.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .local_history_files import (
    MAX_FILE_BYTES, LocalHistoryError, export_private, local_path, locked, private_directory,
    read_private, safe_path, sync_directory, write_private,
)


_MAX_ENTRIES = 1000
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAGIC = b"Hormuz local history v1\x00"
_CONTENT_STATUSES = frozenset({"captured", "unsupported", "omitted_limit", "partial"})
_ENTRY_FIELDS = frozenset({
    "id", "recorded_at", "actor_id", "client", "protocol", "requested_model", "routed_model",
    "destination", "locality", "outcome", "http_status", "input_tokens", "output_tokens",
    "estimated_cost_microusd", "cost_reason", "latency_ms", "request_text", "response_text",
    "request_content_status", "response_content_status",
})


def _result(kind: str, **values) -> dict:
    return {"schema_id": "hormuz.local-history-" + kind, "schema_version": 2 if kind == "entry" and "routing_evidence" in values else 1, **values}


def _id(value) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LocalHistoryError("local_history_entry_id_invalid")
    return value


def _text(value, maximum=1024) -> bool:
    return isinstance(value, str) and len(value.encode("utf-8")) <= maximum


def _number(value, *, nullable=False, maximum=2**63 - 1) -> bool:
    return nullable and value is None or type(value) is int and 0 <= value <= maximum


def _validate_entry(entry: dict) -> dict:
    if not isinstance(entry, dict) or set(entry) not in (_ENTRY_FIELDS, _ENTRY_FIELDS | {"routing_evidence"}):
        raise LocalHistoryError("local_history_entry_invalid")
    if "routing_evidence" in entry:
        evidence = entry["routing_evidence"]
        if (type(evidence) is not dict or set(evidence) != {"schema_version", "policy_action", "routing_mode", "routing_reason", "fallback"}
                or type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
                or type(evidence["fallback"]) is not bool
                or any(not _text(evidence[key], 128) for key in ("policy_action", "routing_mode", "routing_reason"))):
            raise LocalHistoryError("local_history_entry_invalid")
    _id(entry["id"])
    if (type(entry["recorded_at"]) not in {int, float} or not math.isfinite(entry["recorded_at"])
            or not 0 <= entry["recorded_at"] <= 253402300799):
        raise LocalHistoryError("local_history_entry_invalid")
    if any(not _text(entry[name]) for name in (
        "actor_id", "client", "protocol", "requested_model", "routed_model", "destination", "cost_reason",
        "request_content_status", "response_content_status",
    )):
        raise LocalHistoryError("local_history_entry_invalid")
    if (entry["protocol"] not in {"openai", "anthropic"} or entry["locality"] not in {"local", "cloud"}
            or entry["outcome"] not in {"succeeded", "failed", "outcome_unknown", "rate_limited"}
            or entry["request_content_status"] not in _CONTENT_STATUSES
            or entry["response_content_status"] not in _CONTENT_STATUSES
            or not _number(entry["http_status"], nullable=True, maximum=599)
            or any(not _number(entry[name], nullable=True) for name in (
                "input_tokens", "output_tokens", "estimated_cost_microusd"))
            or not _number(entry["latency_ms"])):
        raise LocalHistoryError("local_history_entry_invalid")
    request = entry["request_text"]
    if (not isinstance(request, list) or len(request) > 256
            or any(not isinstance(item, dict) or set(item) != {"role", "text"}
                   or not _text(item["role"], 64) or not _text(item["text"], MAX_FILE_BYTES)
                   for item in request)
            or not _text(entry["response_text"], MAX_FILE_BYTES)):
        raise LocalHistoryError("local_history_entry_invalid")
    return dict(entry)


class LocalHistory:
    """A small consent, capture, review and deletion boundary for one profile."""

    def __init__(self, source_path: Path, *, environ=None):
        environment = os.environ if environ is None else environ
        home = Path(environment.get("HOME") or Path.home()).expanduser()
        if environment.get("HORMUZ_HISTORY_HOME"):
            base = Path(environment["HORMUZ_HISTORY_HOME"]).expanduser().absolute()
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support" / "Hormuz" / "history-v1"
        else:
            base = Path(environment.get("XDG_DATA_HOME") or home / ".local" / "share") / "hormuz" / "history-v1"
        # Arbitrary user-controlled symlinks reach safety checks unchanged.
        base = local_path(base)
        fingerprint = hashlib.sha256(str(Path(source_path).expanduser().resolve()).encode()).hexdigest()
        self.directory = base / fingerprint
        self.entries_directory = self.directory / "entries"

    def _default_state(self):
        return {"schema_id": "hormuz.local-history-settings", "schema_version": 1,
                "enabled": False, "actor_id": None, "retention_days": 30, "generation": uuid4().hex}

    def _key(self, *, create=False):
        key_path = self.directory / "device.key"
        key = read_private(key_path, maximum=32)
        if key is None and create:
            key = os.urandom(32)
            write_private(key_path, key)
        if key is None or len(key) != 32:
            raise LocalHistoryError("local_history_key_unavailable")
        return key

    def _encode(self, value, *, purpose: str):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(payload) + len(_MAGIC) + 28 > MAX_FILE_BYTES:
            raise LocalHistoryError("local_history_entry_too_large")
        nonce = os.urandom(12)
        return _MAGIC + nonce + AESGCM(self._key()).encrypt(nonce, payload, purpose.encode("ascii"))

    def _decode(self, data: bytes, *, purpose: str):
        if not data.startswith(_MAGIC) or len(data) < len(_MAGIC) + 28:
            raise LocalHistoryError("local_history_data_invalid")
        offset = len(_MAGIC)
        try:
            raw = AESGCM(self._key()).decrypt(data[offset:offset + 12], data[offset + 12:], purpose.encode("ascii"))
            value = json.loads(raw)
        except (InvalidTag, ValueError, UnicodeError):
            raise LocalHistoryError("local_history_data_invalid") from None
        if not isinstance(value, dict):
            raise LocalHistoryError("local_history_data_invalid")
        return value

    def _state(self):
        data = read_private(self.directory / "settings.enc")
        if data is None:
            if self._files():
                raise LocalHistoryError("local_history_settings_unavailable")
            return self._default_state()
        state = self._decode(data, purpose="settings-v1")
        if (set(state) != {"schema_id", "schema_version", "enabled", "actor_id", "retention_days", "generation"}
                or state["schema_id"] != "hormuz.local-history-settings"
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or type(state["enabled"]) is not bool or not _number(state["retention_days"], maximum=365)
                or state["retention_days"] < 1 or not _ID.fullmatch(str(state["generation"]))
                or state["actor_id"] is not None and not _text(state["actor_id"])):
            raise LocalHistoryError("local_history_settings_invalid")
        return state

    def _save_state(self, state):
        write_private(self.directory / "settings.enc", self._encode(state, purpose="settings-v1"))

    def _files(self):
        if not safe_path(self.entries_directory, private=True):
            return []
        result = []
        total = 0
        scanned = 0
        removed_temporary = False
        with os.scandir(self.entries_directory) as directory:
            for entry in directory:
                scanned += 1
                if scanned > _MAX_ENTRIES + 32:
                    raise LocalHistoryError("local_history_data_invalid")
                path = self.entries_directory / entry.name
                if re.fullmatch(r"\.history-[A-Za-z0-9_-]{1,64}", entry.name):
                    data = read_private(path)
                    # A killed atomic write can leave an empty or partial
                    # ciphertext file. Never remove unrelated names/content,
                    # symlinks, shared files or permissively owned files.
                    if data is None or not (data.startswith(_MAGIC) or _MAGIC.startswith(data)):
                        raise LocalHistoryError("local_history_data_invalid")
                    path.unlink()
                    removed_temporary = True
                    continue
                if len(result) >= _MAX_ENTRIES or not re.fullmatch(r"[0-9a-f]{32}\.enc", entry.name):
                    raise LocalHistoryError("local_history_data_invalid")
                data = read_private(path)
                if data is None:
                    raise LocalHistoryError("local_history_data_invalid")
                total += len(data)
                if total > _MAX_TOTAL_BYTES:
                    raise LocalHistoryError("local_history_data_invalid")
                result.append((path, data))
        if removed_temporary:
            sync_directory(self.entries_directory)
        return result

    def _entries(self, state):
        entries = []
        cutoff = time.time() - state["retention_days"] * 86400
        deleted = False
        for path, data in self._files():
            entry = self._decode(data, purpose=path.stem)
            if (entry.get("schema_id") != "hormuz.local-history-entry"
                    or type(entry.get("schema_version")) is not int or entry["schema_version"] not in (1, 2)
                    or (entry["schema_version"] == 2) != ("routing_evidence" in entry)):
                raise LocalHistoryError("local_history_data_invalid")
            record = _validate_entry({name: value for name, value in entry.items() if name not in {"schema_id", "schema_version"}})
            if record["id"] != path.stem:
                raise LocalHistoryError("local_history_data_invalid")
            if record["recorded_at"] < cutoff:
                path.unlink()
                deleted = True
            else:
                entries.append((path, record, len(data)))
        if deleted:
            sync_directory(self.entries_directory)
        return sorted(entries, key=lambda item: (item[1]["recorded_at"], item[1]["id"]), reverse=True)

    def _status(self, state, entries):
        return _result("status", enabled=state["enabled"], actor_id=state["actor_id"],
                       retention_days=state["retention_days"], encrypted=True, entry_count=len(entries),
                       storage_path=str(self.directory), retention_cleanup="on_use",
                       capture_mode="redacted_text_and_metadata")

    def status(self):
        with locked(self.directory) as exists:
            state = self._state() if exists else self._default_state()
            return self._status(state, self._entries(state) if exists else [])

    def enable(self, actor_id, retention_days=30):
        if not _text(actor_id) or not actor_id or not _number(retention_days, maximum=365) or retention_days < 1:
            raise LocalHistoryError("local_history_settings_invalid")
        with locked(self.directory, create=True):
            state = self._state()
            self._key(create=True)
            state.update(enabled=True, actor_id=actor_id, retention_days=retention_days, generation=uuid4().hex)
            self._save_state(state)
            return self._status(state, self._entries(state))

    def disable(self):
        with locked(self.directory) as exists:
            state = self._state() if exists else self._default_state()
            state.update(enabled=False, generation=uuid4().hex)
            if exists:
                self._save_state(state)
            return self._status(state, self._entries(state) if exists else [])

    def begin(self, actor_id):
        with locked(self.directory) as exists:
            if not exists:
                return None
            state = self._state()
            if not state["enabled"] or state["actor_id"] != actor_id:
                return None
            self._entries(state)
            return (state["generation"], actor_id)

    def append(self, generation, entry):
        if generation is None:
            return False
        record = _validate_entry(entry)
        with locked(self.directory) as exists:
            if not exists:
                return False
            state = self._state()
            if not state["enabled"] or generation != (state["generation"], state["actor_id"]) or record["actor_id"] != state["actor_id"]:
                return False
            entries = self._entries(state)
            if record["recorded_at"] < time.time() - state["retention_days"] * 86400:
                return False
            private_directory(self.entries_directory)
            path = self.entries_directory / (record["id"] + ".enc")
            if any(item[1]["id"] == record["id"] for item in entries):
                return False
            data = self._encode(_result("entry", **record), purpose=record["id"])
            total = sum(item[2] for item in entries)
            while entries and (len(entries) >= _MAX_ENTRIES or total + len(data) > _MAX_TOTAL_BYTES):
                old_path, _, old_size = entries.pop()
                old_path.unlink()
                total -= old_size
            write_private(path, data)
            return True

    @staticmethod
    def _summary(record):
        return {name: value for name, value in record.items() if name not in {"request_text", "response_text", "actor_id"}}

    def list_entries(self, limit=50):
        if not _number(limit, maximum=1000) or limit < 1:
            raise LocalHistoryError("local_history_limit_invalid")
        with locked(self.directory) as exists:
            state = self._state() if exists else self._default_state()
            entries = self._entries(state) if exists else []
            return _result("list", enabled=state["enabled"], total=len(entries),
                           entries=[self._summary(item[1]) for item in entries[:limit]])

    def get(self, entry_id):
        _id(entry_id)
        with locked(self.directory) as exists:
            entries = self._entries(self._state()) if exists else []
            for _, record, _ in entries:
                if record["id"] == entry_id:
                    return _result("review", entry=_result("entry", **record))
        raise LocalHistoryError("local_history_entry_not_found")

    def delete(self, entry_id):
        _id(entry_id)
        with locked(self.directory) as exists:
            if not exists:
                return _result("delete", deleted=0)
            state = self._state()
            state["generation"] = uuid4().hex
            self._save_state(state)
            entries = self._entries(state)
            for path, record, _ in entries:
                if record["id"] == entry_id:
                    path.unlink()
                    sync_directory(self.entries_directory)
                    return _result("delete", deleted=1)
            return _result("delete", deleted=0)

    def delete_all(self):
        with locked(self.directory) as exists:
            if not exists:
                return _result("delete", deleted=0)
            state = self._state()
            state["generation"] = uuid4().hex
            self._save_state(state)
            files = self._files()
            for path, _ in files:
                path.unlink()
            if files:
                sync_directory(self.entries_directory)
            return _result("delete", deleted=len(files))

    def export(self, output_path, entry_ids=None):
        if entry_ids is not None:
            if not isinstance(entry_ids, (list, tuple)) or len(entry_ids) > _MAX_ENTRIES:
                raise LocalHistoryError("local_history_entry_id_invalid")
            selected = {_id(value) for value in entry_ids}
        else:
            selected = None
        with locked(self.directory) as exists:
            entries = self._entries(self._state()) if exists else []
            records = [_result("entry", **record) for _, record, _ in entries if selected is None or record["id"] in selected]
            if selected is not None and selected != {record["id"] for record in records}:
                raise LocalHistoryError("local_history_entry_not_found")
            artifact = _result("export", exported_at=int(time.time()), entries=records)
            path = local_path(Path(output_path))
            export_private(path, json.dumps(artifact, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n")
            return _result("export-result", exported=len(records), output_path=str(path))

    def insights(self, *, detailed=False):
        with locked(self.directory) as exists:
            state = self._state() if exists else self._default_state()
            records = [item[1] for item in self._entries(state)] if exists else []
            def aggregate(values):
                known = [record["estimated_cost_microusd"] for record in values if record["estimated_cost_microusd"] is not None]
                return {"requests": len(values), "known_estimate_requests": len(known),
                        "unknown_cost_requests": len(values) - len(known),
                        "estimated_cost_microusd": sum(known) if known else None,
                        "local_requests": sum(record["locality"] == "local" for record in values),
                        "outcome_unknown_requests": sum(record["outcome"] == "outcome_unknown" for record in values)}
            models, days = {}, {}
            for record in records:
                models.setdefault(record["routed_model"], []).append(record)
                day = datetime.fromtimestamp(record["recorded_at"], timezone.utc).date().isoformat()
                days.setdefault(day, []).append(record)
            result = _result("insights", enabled=state["enabled"], currency="USD", cost_basis="configured_rate_card_estimate",
                           provider_confirmed_cost_available=False, local_infrastructure_cost="unknown",
                           coverage="retained_opt_in_history_only", totals=aggregate(records),
                           by_model=[{"model": name, **aggregate(values)} for name, values in sorted(models.items())],
                           by_day=[{"day_utc": name, **aggregate(values)} for name, values in sorted(days.items())])

            if not detailed:
                return result
            known = [entry for entry in records if entry["input_tokens"] is not None and entry["output_tokens"] is not None]
            observations = []
            if result["totals"]["local_requests"]:
                observations.append("local_compute_cost_unknown")
            if any(entry["locality"] == "cloud" and entry["estimated_cost_microusd"] is None for entry in records):
                observations.append("inspect_unknown_cloud_pricing")
            if any(entry["outcome"] != "succeeded" for entry in records):
                observations.append("inspect_failed_or_unknown_requests")
            return {**result, "schema_id": "hormuz.usage-insights", "schema_version": 1,
                    "observed_tokens": {"input": sum(entry["input_tokens"] for entry in known),
                                        "output": sum(entry["output_tokens"] for entry in known),
                                        "unknown_usage_requests": len(records) - len(known)},
                    "observations": observations, "savings_measured": False,
                    "observed_period": {"start": min((entry["recorded_at"] for entry in records), default=None),
                                        "end": max((entry["recorded_at"] for entry in records), default=None)}}
