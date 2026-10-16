"""Content-free native execution observations scoped to this router process."""
import secrets
import threading
import hmac
import json
import time
from dataclasses import dataclass
from ._contract_schemas.native_codex import COUNTERS


class NativeConversationBusy(Exception):
    """A native conversation already owns one provider attempt."""


@dataclass(frozen=True)
class NativeConversationBinding:
    digest: bytes
    identified: bool


def _tool_execution(item):
    namespace, caller, asynchronous = (item.get(field) for field in ("namespace", "caller", "async"))
    if (namespace is not None and type(namespace) is not str
            or asynchronous is not None and type(asynchronous) is not bool):
        raise ValueError("native_tool_execution_invalid")
    if caller is not None:
        if (type(caller) is not dict or type(caller.get("type")) is not str
                or caller["type"] not in {"direct", "program"}):
            raise ValueError("native_tool_execution_invalid")
        fields = {"type", "caller_id"} if caller["type"] == "program" else {"type"}
        if set(caller) != fields:
            raise ValueError("native_tool_execution_invalid")
        if caller["type"] == "program":
            identifier = caller["caller_id"]
            if (type(identifier) is not str or not 1 <= len(identifier) <= 256
                    or any(not 33 <= ord(c) <= 126 for c in identifier)):
                raise ValueError("native_tool_execution_invalid")
    return [namespace, caller, asynchronous]


def continuation_tags(items):
    tags = []
    if not isinstance(items, list):
        return tags
    for item in items:
        if type(item) is not dict:
            continue
        kind = item.get("type")
        if kind is not None and type(kind) is not str:
            raise ValueError("native_item_invalid")
        if (kind not in {None, "message", "reasoning", "compaction", "function_call", "custom_tool_call",
                         "function_call_output", "custom_tool_call_output"}
                or "type" in item and kind is None):
            raise ValueError("native_item_unsupported")
        if "role" in item and type(item["role"]) is not str:
            raise ValueError("native_item_invalid")
        message = kind in {None, "message"}
        if "role" in item and not message:
            raise ValueError("native_item_invalid")
        if message and (item.get("role") not in {"user", "developer", "system", "assistant"}
                        or type(item.get("content")) not in {str, list}):
            raise ValueError("native_item_invalid")
        if kind in {"reasoning", "compaction"} and "id" not in item:
            raise ValueError("native_item_invalid")
        if kind == "reasoning" and type(item.get("summary")) is not list:
            raise ValueError("native_item_invalid")
        if kind == "compaction" and "encrypted_content" not in item:
            raise ValueError("native_item_invalid")
        if kind in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"}:
            call = item.get("call_id")
            if type(call) is not str or not 1 <= len(call) <= 256 or any(not 33 <= ord(c) <= 126 for c in call):
                raise ValueError("native_call_invalid")
            call_kind = kind.removesuffix("_output")
            tags.append(("call", [call_kind, call]))
            execution = _tool_execution(item)
            context = (("tool_namespace", execution[0]), ("tool_caller", execution[1]), ("tool_name", item.get("name")))
            if kind in {"function_call", "custom_tool_call"}:
                field = "arguments" if kind == "function_call" else "input"
                if type(item.get("name")) is not str or type(item.get(field)) is not str:
                    raise ValueError("native_call_invalid")
                status = item.get("status")
                if status is not None and (type(status) is not str or status not in {"in_progress", "completed", "incomplete"}):
                    raise ValueError("native_call_invalid")
                tags.append(("tool", [kind, call, item["name"], item[field], execution, item.get("id"), status]))
                tags.extend((field, [call_kind, call, value]) for field, value in context)
            else:
                if (type(item.get("output")) not in {str, list} or execution[2] is not None
                        or item.get("name") is not None and type(item["name"]) is not str):
                    raise ValueError("native_tool_result_invalid")
                tags.extend((field, [call_kind, call, value]) for field, value in context if value is not None)
        if kind in {"reasoning", "compaction"}:
            opaque = item.get("encrypted_content")
            if opaque is not None or kind == "compaction":
                if type(opaque) is not str or not 1 <= len(opaque) <= 1024 * 1024:
                    raise ValueError("native_opaque_invalid")
                opaque.encode("utf-8")
            tags.append(("state", [kind, item["id"], opaque]))
        elif "encrypted_content" in item:
            raise ValueError("native_opaque_invalid")
        if ("id" in item and kind not in {"function_call_output", "custom_tool_call_output"}
                and not (kind in {"function_call", "custom_tool_call"} and item["id"] is None)
                and not (message and item.get("role") in {"user", "developer", "system"})):
            if (type(item["id"]) is not str or not 1 <= len(item["id"]) <= 256
                    or any(not 33 <= ord(c) <= 126 for c in item["id"])):
                raise ValueError("native_item_invalid")
            tags.append(("item", ["message" if message else kind, item["id"]]))
        if len(tags) > 256:
            raise ValueError("native_continuation_oversized")
    return tags


class NativeCodexState:
    def __init__(self):
        self.generation = secrets.token_hex(32)
        self._lock = threading.Lock()
        self._counts = {}
        self._key = secrets.token_bytes(32)
        self._affinity = {}
        self._active = set()

    def binding(self, credentials, destination, value, session_headers):
        identity = credentials.identity
        metadata = value.get("client_metadata", {})
        identifiers = [metadata.get("thread_id"), metadata.get("session_id"), value.get("prompt_cache_key")]
        facts = [identity.organization_id, identity.team_id, identity.actor_id,
                 destination.generation, destination.model, credentials.authorization, credentials.account,
                 *identifiers, session_headers]
        digest = hmac.digest(self._key, json.dumps(facts, sort_keys=True, separators=(",", ":")).encode(), "sha256")
        return NativeConversationBinding(digest, any(item is not None for item in (*identifiers, *session_headers.values())))

    def _keys(self, binding, items):
        return [hmac.digest(self._key, binding.digest + json.dumps([kind, value], sort_keys=True,
                separators=(",", ":")).encode(), "sha256") for kind, value in continuation_tags(items)]

    def permits(self, binding, items):
        keys = self._keys(binding, items)
        with self._lock:
            return all(self._affinity.get(key, 0) > time.monotonic() for key in keys)

    def remember(self, binding, items):
        keys = self._keys(binding, items)
        with self._lock:
            now = time.monotonic()
            self._affinity = {key: deadline for key, deadline in self._affinity.items() if deadline > now}
            for key in keys:
                self._affinity.pop(key, None)
                self._affinity[key] = now + 900
            while len(self._affinity) > 1024:
                self._affinity.pop(next(iter(self._affinity)))

    def _counters(self, identity):
        owner = (identity.organization_id, identity.team_id, identity.actor_id)
        return self._counts.setdefault(owner, dict.fromkeys(COUNTERS, 0))

    def begin(self, identity, binding, items):
        # Affinity stays stable for replay. An identifier-free fresh attempt has
        # no shared conversation to serialize, so its active lease is separate.
        lease = (binding.digest if binding.identified or continuation_tags(items)
                 else secrets.token_bytes(32))
        with self._lock:
            if lease in self._active:
                raise NativeConversationBusy()
            self._active.add(lease)
            counters = self._counters(identity)
            counters["requests"] += 1
            counters["active"] += 1
        return lease

    def finish(self, identity, lease, outcome, usage):
        with self._lock:
            self._active.discard(lease)
            counters = self._counters(identity)
            counters["active"] -= 1
            counters[outcome] += 1
            if usage.evidence_complete:
                counters["observed_usage_requests"] += 1
                counters["observed_input_tokens"] += usage.input_tokens
                counters["observed_output_tokens"] += usage.output_tokens
            else:
                counters["usage_unknown_requests"] += 1

    def view(self, identity, *, enabled):
        with self._lock:
            return {"schema_id": "hormuz.native-codex-status", "schema_version": 1,
                "scope": "identity_process_observation", "generation": self.generation,
                "enabled": enabled, "billing": "chatgpt_plan", "cost_status": "unknown", "cost_usd": None,
                **self._counters(identity)}
