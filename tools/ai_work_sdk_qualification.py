#!/usr/bin/env python3
"""Qualify six installed SDK requests against synthetic loopback providers only.

This exercises the public SDK example's execute path, the authenticated gateway
and SQLite. It makes no live-provider request or payment and emits no response
body, credential, prompt, provider exception text or customer-quality claim.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hormuz.config import GatewayConfig
from hormuz.work_client import WorkClient
from tools.ai_work_proof import PROOF_SOURCE_FILES, ProofGatewayServer, serving
from tools.ai_work_provider_examples import ProviderFixture, RATES, gateway_profile
from tools.provider_example_transport import BoundedFixtureServer, LoopbackTransport

SOURCE_FILES = tuple(dict.fromkeys((*PROOF_SOURCE_FILES,
    "examples/sdk/work_sdk.py", "tools/ai_work_provider_examples.py",
    "tools/provider_example_transport.py", "tools/provider_example_responses.py",
    "tools/ai_work_sdk_qualification.py")))
CASES = tuple((api, stream) for api in
    ("openai-responses", "openai-chat", "anthropic-messages") for stream in (False, True))
PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


class QualificationFailure(ValueError):
    """Only fixed classifications may cross the shareable receipt boundary."""


class QualificationCaseFailure(QualificationFailure):
    def __init__(self, api, stream, cause_type):
        self.api, self.stream = api, stream
        self.cause_type = cause_type if isinstance(cause_type, str) and cause_type.isascii() and cause_type.isidentifier() and len(cause_type) <= 128 else "Exception"
        super().__init__("sdk_case_failed")


def source_manifest():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


class LoopbackBoundary:
    """Pass real socket calls through only to the two owned loopback listeners.

    The installed SDKs use Python's socket backend. These guards do not mock
    HTTP or SDK responses: allowed connects call the original implementation.
    Nonlocal DNS and other destinations are refused before network activity.
    """
    def __init__(self, gateway_port, provider_port):
        self.ports = {gateway_port: "gateway", provider_port: "provider"}
        self.connections = {"gateway": 0, "provider": 0}
        self.refused = 0
        self._lock = threading.Lock()

    def _destination(self, address):
        if (not isinstance(address, tuple) or len(address) < 2
                or address[0] != "127.0.0.1" or address[1] not in self.ports):
            with self._lock:
                self.refused += 1
            raise QualificationFailure("non_fixture_network_refused")
        return self.ports[address[1]]

    @contextmanager
    def enforce(self):
        connect, connect_ex, lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
        saved = {name: os.environ.get(name) for name in PROXY_NAMES}
        boundary = self

        def allowed_connect(connection, address):
            category = boundary._destination(address)
            with boundary._lock:
                boundary.connections[category] += 1
            return connect(connection, address)

        def allowed_connect_ex(connection, address):
            category = boundary._destination(address)
            with boundary._lock:
                boundary.connections[category] += 1
            return connect_ex(connection, address)

        def allowed_lookup(host, port, *args, **kwargs):
            boundary._destination((host, port))
            return lookup(host, port, *args, **kwargs)

        try:
            for name in PROXY_NAMES:
                os.environ.pop(name, None)
            os.environ["NO_PROXY"] = "127.0.0.1"
            socket.socket.connect, socket.socket.connect_ex = allowed_connect, allowed_connect_ex
            socket.getaddrinfo = allowed_lookup
            yield self
        finally:
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, lookup
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def checked_case(api, stream, sdk_receipt, state, provider_calls):
    attempts = state.get("attempts", [])
    confirmation_reads = sdk_receipt.get("gateway_confirmation_reads")
    if (sdk_receipt.get("response_confirmed_by_gateway") is not True
            or sdk_receipt.get("endpoint_completion_verified") is not True
            or type(confirmation_reads) is not int or not 1 <= confirmation_reads <= 4
            or len(attempts) != 1 or attempts[0].get("response_succeeded") is not True
            or attempts[0].get("state") != "succeeded" or state.get("state") != "active"
            or state.get("observations") != [] or provider_calls != 1):
        raise QualificationFailure("sdk_case_not_confirmed")
    return {"api": api, "stream": stream, "new_attempts": 1,
            "response_succeeded": True, "endpoint_completion_verified": True, "attempt_state": "succeeded",
            "gateway_confirmation_reads": confirmation_reads,
            "job_state": "active", "outcome_observations": 0,
            "provider_fixture_calls": provider_calls,
            "configured_cost_microusd": attempts[0].get("cost_microusd"),
            "provider_invoice_reconciled": False, "passed": True}


def load_example():
    spec = importlib.util.spec_from_file_location("hormuz_sdk_qualification_example", ROOT / "examples/sdk/work_sdk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute():
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5).stdout.strip()
    sources = source_manifest()
    versions = {name: importlib.metadata.version(name) for name in ("openai", "anthropic", "httpx2")}
    example = load_example()
    initial_threads = set(threading.enumerate())
    ProviderFixture.calls = []
    checks = []
    with tempfile.TemporaryDirectory(prefix="hormuz-sdk-qualification-") as folder:
        provider = BoundedFixtureServer(("127.0.0.1", 0), ProviderFixture)
        with serving(provider):
            routes = {name: {"protocol": name, "upstream_model": "synthetic-" + name,
                            **{rate: 3 for rate in RATES}} for name in ("openai", "anthropic")}
            upstreams = {name: {"base_url": f"http://127.0.0.1:{provider.server_port}",
                               "api_key_env": "HORMUZ_SYNTHETIC_PROVIDER_KEY"} for name in routes}
            environment = {"HORMUZ_EXAMPLE_TOKEN": secrets.token_urlsafe(32),
                           "HORMUZ_SYNTHETIC_PROVIDER_KEY": "synthetic-sdk-qualification-key"}
            profile = gateway_profile(routes, upstreams, live=False)
            profile["ai_work"]["cache_enabled"] = False
            config_path = Path(folder) / "gateway.json"
            config_path.write_text(json.dumps(profile))
            config = GatewayConfig.load(config_path, environ=environment)
            config = replace(config, listen=replace(config.listen, port=0))
            gateway = ProofGatewayServer(config, environ=environment)
            boundary = LoopbackBoundary(gateway.server_port, provider.server_port)
            with boundary.enforce(), serving(gateway), LoopbackTransport(gateway.server_port) as transport:
                # Vendor imports can take longer than the bounded control
                # listener's keep-alive interval. Warm them before opening its
                # persistent connection, while the same network guard applies.
                import_module("openai")
                import_module("anthropic")
                token = environment["HORMUZ_EXAMPLE_TOKEN"]
                client = WorkClient(transport.endpoint, token, allow_loopback_http=True, timeout=10)
                client._opener = transport
                client.set_plan("workspace", "example-workspace", budget_microusd=1_000_000, objective="cost")
                for api, stream in CASES:
                    alias = "openai" if api.startswith("openai-") else "anthropic"
                    arguments = ["--api", api, "--model", alias, "--execute",
                                 "--repository", "examples/sdk-qualification", "--max-output-tokens", "32"]
                    if stream:
                        arguments.append("--stream")
                    before = len(ProviderFixture.calls)
                    try:
                        receipt = example.execute(example.parser().parse_args(arguments), client, token)
                    except Exception as failure:
                        raise QualificationCaseFailure(api, stream,
                            getattr(failure, "failure_type", type(failure).__name__)) from None
                    state = client.job(receipt["work_id"]).state()
                    checks.append(checked_case(api, stream, receipt, state, len(ProviderFixture.calls) - before))
            # The gateway has drained and closed before inspecting its actual
            # SQLite owner; no test substitutes an API or ledger implementation.
            with closing(sqlite3.connect(config.ai_work.database_path.absolute().as_uri() + "?mode=ro", uri=True)) as ledger:
                counts = {"jobs": ledger.execute("SELECT COUNT(*) FROM ai_work_jobs").fetchone()[0],
                          "active_jobs": ledger.execute("SELECT COUNT(*) FROM ai_work_jobs WHERE state='active'").fetchone()[0],
                          "attempts": ledger.execute("SELECT COUNT(*) FROM ai_work_attempts").fetchone()[0],
                          "succeeded_responses": ledger.execute("SELECT COUNT(*) FROM ai_work_attempts WHERE response_succeeded=1 AND state='succeeded'").fetchone()[0],
                          "observations": ledger.execute("SELECT COUNT(*) FROM ai_work_observations").fetchone()[0]}
                if counts != {"jobs": 6, "active_jobs": 6, "attempts": 6, "succeeded_responses": 6, "observations": 0}:
                    raise QualificationFailure("sqlite_ledger_not_confirmed")
                if ledger.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or ledger.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise QualificationFailure("sqlite_integrity_failed")
        if provider.fileno() != -1 or gateway.fileno() != -1 or any(thread not in initial_threads for thread in threading.enumerate()):
            raise QualificationFailure("qualification_resources_not_closed")
    if source_manifest() != sources:
        raise QualificationFailure("qualification_source_changed")
    if boundary.refused or len(ProviderFixture.calls) != 6:
        raise QualificationFailure("qualification_network_not_confirmed")
    return {"schema_id": "hormuz.ai-work-sdk-qualification", "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(), "source_commit": commit,
            "source_boundary": "working_tree_files_identified_by_sha256", "source_files": sources,
            "installed_versions": versions, "python_version": platform.python_version(),
            "conditions": {"sdk_calls": "actual_installed_vendor_sdk_execute", "sequential_inference_calls": 6,
                "provider": "synthetic_loopback_only", "authenticated_gateway": True, "storage": "actual_sqlite",
                "external_network_calls": 0, "refused_network_attempts": boundary.refused,
                "real_provider_calls": 0, "real_payments": 0, "provider_fixture_calls": len(ProviderFixture.calls),
                "outcome_observations_submitted": 0, "sdk_retries": 0, "sdk_redirects": False,
                "maximum_metadata_confirmation_rereads": 3,
                "maximum_fixture_handlers_per_listener": 4,
                "billing_basis": "synthetic_configured_rate_estimate",
                "customer_savings_validated": False, "model_quality_validated": False,
                "live_provider_qualified": False, "native_agent_qualified": False},
            "checks": checks, "sqlite_counts": counts,
            "cleanup": {"listener_sockets_closed": True, "new_server_threads_remaining": 0,
                        "temporary_state_removed": True, "sdk_clients_and_streams_closed": True},
            "loopback_socket_connects": boundary.connections}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new metadata receipt; existing files are preserved.")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise QualificationFailure("receipt_already_exists")
        receipt = execute()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as output:
            output.write(json.dumps(receipt, indent=2) + "\n")
    except Exception as failure:
        diagnosis = {"status": "qualification_failed", "failure_type": type(failure).__name__,
                     "detail": "No passing receipt was written; inspect installed SDK compatibility privately."}
        if isinstance(failure, QualificationCaseFailure):
            diagnosis.update(api=failure.api, stream=failure.stream, sdk_failure_type=failure.cause_type)
        print(json.dumps(diagnosis))
        return 1
    print(json.dumps({"status": "qualification_passed", "checks": len(receipt["checks"]),
                      "provider_fixture_calls": receipt["conditions"]["provider_fixture_calls"],
                      "real_provider_calls": 0, "real_payments": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
