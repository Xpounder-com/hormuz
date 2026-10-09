#!/usr/bin/env python3
"""Run the real work gateway against a local fixture and export bounded proof.

Uses synthetic identities, provider replies and configured rates. It does not
call a paid model, report measured customer savings or establish task quality.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PROOF_SOURCE_FILES = ('hormuz/work_runtime.py', 'hormuz/work_learning.py', 'hormuz/work_accounting.py', 'hormuz/work_provider_costs.py', 'hormuz/work_gateway.py', 'hormuz/work_workflow.py', 'hormuz/work_workflow_http.py', 'hormuz/work_http.py', 'hormuz/work_billing.py', 'hormuz/work_activation.py', 'hormuz/work_recovery.py', 'hormuz/work_pages.py', 'hormuz/work.css', 'hormuz/work_client.py', 'hormuz/commands/work.py', 'hormuz/config.py', 'hormuz/_config_work.py', 'hormuz/server.py', 'hormuz/usage.py', 'hormuz/_hosted_server.py', 'hormuz/_hosted_state.py', 'hormuz/_hosted_config.py', 'hormuz/_hosted_backup.py', 'hormuz/hosted.py', 'tools/ai_work_proof.py', 'tools/ai_work_browser_fixture.py', 'website/scripts/ai-work-browser-qa.mjs')
sys.path.insert(0, str(ROOT))
from hormuz.config import GatewayConfig
from hormuz.server import GatewayServer

CLIENT_TIMEOUT = 5
UPSTREAM_TIMEOUT = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class BoundedProofServer:
    """Limit accepted loopback handlers and bound idle socket reads."""

    daemon_threads = False
    block_on_close = True
    request_queue_size = 4

    def __init__(self, *args, **kwargs):
        self._proof_slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(CLIENT_TIMEOUT)
        return request, address

    def process_request(self, request, address):
        if not self._proof_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._proof_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._proof_slots.release()


class ProofProviderServer(BoundedProofServer, ThreadingHTTPServer):
    pass


class ProofGatewayServer(BoundedProofServer, GatewayServer):
    pass


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        thread.start()
        started = True
        yield server
    finally:
        try:
            if started:
                server.shutdown()
        finally:
            try:
                server.server_close()
            finally:
                if started:
                    thread.join(timeout=CLIENT_TIMEOUT)
                    if thread.is_alive():
                        raise RuntimeError("proof_server_thread_not_closed")


def source_manifest():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROOF_SOURCE_FILES}


class FixtureProvider(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            self.calls.append({"model": body["model"], "path": self.path})
        if body.get("input") == "rate_limit" and body["model"] == "fixture-baseline":
            self.send_response(429)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        time.sleep(0.01 if body["model"] == "fixture-baseline" else 0.02)
        reply = {"id": "fixture-response", "object": "response", "model": body["model"], "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Fixture check passed."}]}],
            "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}}
        if self.path == "/v1/chat/completions":
            reply = {"id": "fixture-chat", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Fixture check passed."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}}
        if body.get("input") == "omit_usage":
            reply.pop("usage")
        payload = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def execute():
    source_files = source_manifest()
    FixtureProvider.calls = []
    assertions, trace = [], []
    with serving(ProofProviderServer(("127.0.0.1", 0), FixtureProvider)) as provider:
        with tempfile.TemporaryDirectory(prefix="hormuz-work-proof-") as folder:
            config_path = Path(folder) / "gateway.json"
            config_path.write_text(json.dumps({
                "listen": {"host": "127.0.0.1", "port": 8088}, "database": "usage.sqlite3",
                "upstream_timeout_seconds": UPSTREAM_TIMEOUT,
                "upstreams": {"openai": {"base_url": f"http://127.0.0.1:{provider.server_port}", "api_key_env": "PROOF_PROVIDER"}},
                "identities": [{"token_env": "PROOF_TOKEN", "actor_id": "proof-actor", "actor_name": "Fixture operator", "team_id": "engineering", "team_name": "Engineering", "organization_id": "proof-company", "allowed_clients": ["codex"]}],
                "model_routes": {"baseline": {"protocol": "openai", "upstream_model": "fixture-baseline", "input_cost_per_million": 3, "output_cost_per_million": 3, "failover_alias": "economy"},
                    "economy": {"protocol": "openai", "upstream_model": "fixture-economy", "input_cost_per_million": 1, "output_cost_per_million": 1}},
                "policies": {"organization": {"allowed_clients": ["codex"], "allowed_models": ["baseline", "economy"], "fallback_model": "baseline", "max_output_tokens": 32}},
                "ai_work": {"enabled": True, "database": "work.sqlite3", "cache_enabled": True, "minimum_samples": 3, "administrator_actor_ids": ["proof-actor"]},
            }))
            environment = {"PROOF_TOKEN": "synthetic-proof-token", "PROOF_PROVIDER": "synthetic-proof-provider-key"}
            config = GatewayConfig.load(config_path, environ=environment)
            config = replace(config, listen=replace(config.listen, port=0))
            with serving(ProofGatewayServer(config, environ=environment)) as gateway, closing(
                http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=CLIENT_TIMEOUT)
            ) as connection:
                def request(path, body=None, *, work_id=None, token="synthetic-proof-token"):
                    headers = {"Authorization": "Bearer " + token}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    if work_id:
                        headers["X-Hormuz-Work-ID"] = work_id
                    started = time.monotonic_ns()
                    try:
                        connection.request("POST" if body is not None else "GET", path, json.dumps(body).encode() if body is not None else None, headers)
                        with connection.getresponse() as response:
                            payload = response.read(MAX_RESPONSE_BYTES + 1)
                            if len(payload) > MAX_RESPONSE_BYTES:
                                raise RuntimeError("proof_response_too_large")
                            result = (response.status, {key.lower(): value for key, value in response.getheaders()}, json.loads(payload))
                    except BaseException:
                        connection.close()
                        raise
                    trace.append({"path": path, "status": result[0], "wall_ms": round((time.monotonic_ns() - started) / 1e6, 3),
                        "cache": result[1].get("x-hormuz-cache"), "route": result[1].get("x-hormuz-work-route")})
                    return result

                def check(label, value):
                    if not value:
                        raise AssertionError(label)
                    assertions.append({"check": label, "passed": True})

                def job(task_type="proof-check"):
                    code, _, value = request("/v1/work/jobs", {"repository": "fixture/repository", "task_type": task_type, "context_revision": "fixture-revision-1"})
                    check("authenticated_job_created", code == 201)
                    return value["work_id"]

                def inference(work_id, *, model="baseline", text="fixture", temperature=1):
                    return request("/v1/responses", {"model": model, "input": text, "max_output_tokens": 16, "temperature": temperature, "store": False}, work_id=work_id)

                request("/v1/work/policies", {"scope_type": "workspace", "scope_id": "proof-company", "budget_microusd": 1_000_000, "objective": "cost"})
                for alias in ("baseline", "economy"):
                    for number in range(3):
                        work_id = job()
                        code, headers, answer = inference(work_id, model=alias, text=f"fixture-check-{alias}-{number}")
                        check("provider_attempt_attached_to_same_job", code == 200 and headers["x-hormuz-work-id"] == work_id)
                        # A real executed assertion is the declared fixture check;
                        # this is workflow evidence, not an independent quality grade.
                        passed = answer["output"][0]["content"][0]["text"] == "Fixture check passed."
                        check("declared_fixture_check_passed", passed)
                        request(f"/v1/work/jobs/{work_id}/observations", {"status": "completed", "source": "workflow", "reference": f"fixture/{alias}/{number}"})
                owner = next(iter(config.identities_by_token.values()))
                target = job()
                gateway.work_runtime.refresh_benchmark(owner, target, "openai", {"_hormuz_endpoint": "/v1/responses"})
                deadline = time.monotonic() + 3
                while True:
                    selected = gateway.work_runtime.choose_route(owner, target, list(config.model_routes.values()), config.model_routes["baseline"], {"input": "fixture", "_hormuz_endpoint": "/v1/responses"})
                    if selected["alias"] == "economy" or time.monotonic() > deadline:
                        break
                    time.sleep(0.02)
                code, headers, _ = inference(target, temperature=0)
                check("local_cost_evidence_selects_lower_observed_cost", code == 200 and headers["x-hormuz-routed-model"] == "fixture-economy")
                calls = len(FixtureProvider.calls)
                code, headers, _ = inference(target, temperature=0)
                check("exact_cache_hit_skips_provider", code == 200 and headers.get("x-hormuz-cache") == "exact-answer-hit" and len(FixtureProvider.calls) == calls)
                value = request(f"/v1/work/jobs/{target}")[2]
                check("cache_hit_does_not_claim_completed_work", value["state"] == "active")
                request(f"/v1/work/jobs/{target}/observations", {"status": "corrected", "source": "workflow", "reference": "fixture/correction"})
                code, headers, _ = inference(target, temperature=0)
                check("correction_bypasses_cached_answer", code == 200 and "x-hormuz-cache" not in headers and len(FixtureProvider.calls) == calls + 1)
                capped = job("budget-proof")
                request("/v1/work/policies", {"scope_type": "job", "scope_id": capped, "budget_microusd": 1, "objective": "cost"})
                calls = len(FixtureProvider.calls)
                code, _, _ = inference(capped)
                check("job_cap_stops_before_provider_egress", code == 402 and len(FixtureProvider.calls) == calls)
                request(f"/v1/work/jobs/{capped}/actions", {"action": "approve_budget", "budget_microusd": 10_000})
                check("explicit_allowance_continues_same_job", inference(capped)[0] == 200)
                fallback = job("fallback-proof")
                check("provider_capacity_fallback_succeeds", inference(fallback, text="rate_limit")[0] == 200)
                attempts = request(f"/v1/work/jobs/{fallback}")[2]["attempts"]
                check("fallback_attempts_join_same_job", len(attempts) == 2 and any(value["retry_of"] for value in attempts))
                unknown = job("unknown-proof")
                check("provider_without_usage_is_relayed", inference(unknown, text="omit_usage")[0] == 200)
                value = request(f"/v1/work/jobs/{unknown}")[2]
                check("uncertain_provider_charge_retains_hold", value["attempts"][0]["state"] == "unknown" and value["attempts"][0]["cost_microusd"] is None and value["attempts"][0]["reserved_microusd"] > 0)
                chat = job("chat-proof")
                code, _, _ = request("/v1/chat/completions", {"model": "baseline", "messages": [{"role": "user", "content": "fixture"}], "max_completion_tokens": 16}, work_id=chat)
                check("openai_compatible_chat_is_accounted", code == 200 and request(f"/v1/work/jobs/{chat}")[2]["attempts"][0]["cost_microusd"] == 48)
                check("missing_authentication_cannot_read_work", request("/v1/work/state", token="incorrect-fixture")[0] == 401)
                state = request("/v1/work/state")[2]
                if source_manifest() != source_files:
                    raise RuntimeError("proof_source_changed_during_execution")
                return {"schema_id": "hormuz.ai-work-proof", "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=CLIENT_TIMEOUT).strip(),
                    "source_boundary": "working_tree_files_identified_by_sha256",
                    "source_files": source_files,
                    "conditions": {"provider": "loopback_synthetic_fixture", "real_provider_calls": 0, "real_payments": 0, "billing_basis": "synthetic_configured_rate_estimate", "observations": "declared_executed_fixture_check_and_correction", "minimum_samples": 3, "production_quality_validated": False, "customer_savings_validated": False},
                    "checks": assertions, "provider_fixture_calls": len(FixtureProvider.calls), "trace": trace,
                    "covered_work": state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = execute()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"proof=passed checks={len(receipt['checks'])} provider_fixture_calls={receipt['provider_fixture_calls']} real_provider_calls=0 output={args.output}")


if __name__ == "__main__":
    main()
