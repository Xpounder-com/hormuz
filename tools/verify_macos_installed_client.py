#!/usr/bin/env python3
"""Verify a Mac app session and generated settings with a pinned CLI on loopback.

Run only after signing in to tools/verify_macos_client.py --serve in the native
app and saving its connector. This captures helper stdout without displaying or
retaining credentials. Only the synthetic model simulator receives a request.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shlex
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def gateway_get(origin: str, path: str, token: str) -> dict:
    parsed = urlsplit(origin)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request("GET", path, headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("fixture_gateway_rejected_credential")
        return json.loads(response.read(128 * 1024))
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--client-command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.state_directory.resolve()
    profile = json.loads((root / "profile.json").read_text())
    origin = profile["gateway"]
    parsed = urlsplit(origin)
    client = profile["client"]
    expected_model = {"codex": "safe-openai", "claude-code": "safe-claude"}.get(client)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or profile["organization"] != "org-a" or profile["model"] != expected_model
            or profile["allowLoopbackHTTP"] is not True):
        raise ValueError("only_the_explicit_local_fixture_profile_is_allowed")
    check = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        check.request("GET", "/__hormuz_macos_fixture")
        response = check.getresponse()
        if response.status != 200 or json.loads(response.read(1024)) != {"service": "hormuz-macos-test-fixture", "external_providers": False}:
            raise ValueError("dedicated_provider_free_fixture_required")
    finally:
        check.close()
    key = profile["id"].lower()
    if re.fullmatch(r"[0-9a-f-]{36}", key) is None:
        raise ValueError("invalid_profile_id")
    binary = args.bundle.resolve() / "Contents/MacOS/Hormuz"
    helper = [str(binary), "credential", "--profile", key, "--state-directory", str(root)]
    environment = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    native = subprocess.run(helper, capture_output=True, check=False, timeout=20, env=environment)
    if native.returncode != 0 or re.fullmatch(rb"hox_a_[A-Za-z0-9_-]{43}\n", native.stdout) is None:
        raise ValueError("native_keychain_helper_failed")
    token = native.stdout.decode().strip()
    rotated = subprocess.run([*helper, "--force-refresh"], capture_output=True, check=False, timeout=20, env=environment)
    if rotated.returncode != 0 or re.fullmatch(rb"hox_a_[A-Za-z0-9_-]{43}\n", rotated.stdout) is None or rotated.stdout == native.stdout:
        raise ValueError("native_refresh_rotation_failed")
    check = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        check.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + token})
        response = check.getresponse()
        if response.status != 401:
            raise ValueError("old_native_access_not_rejected")
        response.read()
    finally:
        check.close()
    token = rotated.stdout.decode().strip()
    identity = gateway_get(origin, "/v1/gateway/whoami", token)
    if identity["actor_id"] != "alice" or identity["organization_id"] != "org-a" or identity["allowed_clients"] != [client]:
        raise ValueError("wrong_fixture_identity")
    usage_before = gateway_get(origin, "/v1/gateway/usage", token)
    version_result = subprocess.run([str(args.client_command), "--version"], text=True, capture_output=True,
                                    timeout=15, check=False, env=environment)
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_result.stdout)
    expected_version = {"codex": "0.147.0", "claude-code": "2.1.233"}[client]
    if version_result.returncode != 0 or match is None or match[1] != expected_version:
        raise ValueError("pinned_client_version_required")
    source = (root / (client + "-" + key + ".command")).read_text()
    lines = [line for line in source.splitlines() if line.startswith("exec ")]
    if len(lines) != 1:
        raise ValueError("invalid_connector_launcher")
    generated = shlex.split(lines[0])[1:]
    expected_executable = "codex" if client == "codex" else "claude"
    if generated[0] != expected_executable:
        raise ValueError("unexpected_connector_executable")
    if client == "codex":
        if len(generated) != 7 or generated[1::2] != ["-c", "-c", "-c"]:
            raise ValueError("unexpected_codex_overrides")
        settings = tomllib.loads("\n".join(generated[2::2]))
        expected_provider = {"name": "Hormuz", "base_url": origin + "/v1", "wire_api": "responses",
            "requires_openai_auth": False, "auth": {"command": str(binary), "args": helper[1:], "refresh_interval_ms": 60000}}
        if settings != {"model_provider": "hormuz_connector", "model_providers": {"hormuz_connector": expected_provider}, "model": expected_model}:
            raise ValueError("connector_does_not_match_fixture")
    else:
        settings_path = root / (client + "-" + key + ".settings.json")
        if generated != ["claude", "--settings", str(settings_path), "--model", expected_model]:
            raise ValueError("unexpected_claude_overrides")
        settings = json.loads(settings_path.read_text())
        if shlex.split(settings["apiKeyHelper"]) != helper or settings["env"]["ANTHROPIC_BASE_URL"] != origin:
            raise ValueError("connector_does_not_match_fixture")
    real_settings = [Path.home() / ".codex/config.toml", Path.home() / ".claude/settings.json"]
    before = [fingerprint(path) for path in real_settings]
    with tempfile.TemporaryDirectory(prefix="hormuz-native-cli-fixture-") as temporary:
        isolated = Path(temporary)
        (isolated / ".codex").mkdir(mode=0o700)
        (isolated / ".claude").mkdir(mode=0o700)
        # Isolate the official clients using their supported configuration roots.
        # Do not replace HOME: macOS file Keychain lookup is login-environment
        # dependent, and a synthetic home makes its items unavailable to helpers.
        environment.update({"CODEX_HOME": str(isolated / ".codex"), "CLAUDE_CONFIG_DIR": str(isolated / ".claude"),
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0", "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1"})
        if client == "codex":
            command = [str(args.client_command), "exec", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral",
                       "--sandbox", "read-only", "-C", temporary, *generated[1:], "Reply with exactly GATEWAY_OK. Do not call tools."]
            marker = "GATEWAY_OK"
        else:
            command = [str(args.client_command), "-p", "--bare", "--no-session-persistence", "--tools", "", *generated[1:],
                       "Reply with exactly ok. Do not call tools."]
            marker = "ok"
        result = subprocess.run(command, text=True, capture_output=True, timeout=60, check=False, env=environment, cwd=temporary)
        if result.returncode != 0 or marker not in result.stdout + result.stderr:
            # Do not retain raw CLI logs, model text, or credential-helper output.
            diagnostic = re.sub(r"hox_[ar]_[A-Za-z0-9_-]+", "[credential redacted]", (result.stderr + "\n" + result.stdout).replace(token, "[credential redacted]"))
            print(json.dumps({"return_code": result.returncode, "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr),
                "known_conditions": [word for word in ("unauthorized", "keychain", "login", "configuration", "expired", "401", "403", "invalid", "stream") if word in diagnostic.lower()]}))
            errors = [line for line in diagnostic.splitlines() if "error" in line.lower()]
            if errors:
                print("Fixture-only CLI diagnostic: " + " ".join(errors)[:1200])
            raise ValueError("official_client_fixture_request_failed")
    usage_after = gateway_get(origin, "/v1/gateway/usage", token)
    unchanged = before == [fingerprint(path) for path in real_settings]
    if not unchanged or usage_after["requests"] <= usage_before["requests"] or usage_after["input_tokens"] <= usage_before["input_tokens"]:
        raise ValueError("usage_or_configuration_preservation_failed")
    summary = {"schema_id": "hormuz.macos-installed-client-proof", "schema_version": 1,
        "client": client, "client_version": expected_version, "credential_store": "macos_keychain",
        "native_helper": True, "native_refresh_rotation": True, "old_native_access_rejected": True,
        "generated_configuration": True, "governed_simulator_request": True,
        "personal_usage_increased": True, "existing_settings_unchanged": True, "live_provider_calls": 0,
        "bundle_executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"Installed {client} proof passed: native Keychain helper, governed simulator request, unchanged user settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
