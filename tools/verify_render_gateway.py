#!/usr/bin/env python3
"""Exercise only disposable, provider-free containers and synthetic credentials.

No cloud account, production endpoint or ambient credential is accepted. Every
Docker object is freshly named and removed; output contains checks and digests.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import subprocess
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANARY = "synthetic_hormuz_hosted_log_canary"
SECRETS = {
    "HORMUZ_INGRESS_CREDENTIAL": "synthetic_ingress_" + "i" * 43,
    "HORMUZ_SESSION_MASTER_KEY": base64.b64encode(b"m" * 32).decode(),
    "HORMUZ_OIDC_CLIENT_SECRET": "synthetic-oidc-client-secret",
}
PROVIDER_SECRETS = {
    "HORMUZ_OPENAI_PROVIDER_KEY": "synthetic-openai-provider-key",
    "HORMUZ_ANTHROPIC_PROVIDER_KEY": "synthetic-anthropic-provider-key",
}
PYTHON = "/opt/hormuz/bin/python"
SETUP = '''
import base64, json
from pathlib import Path
root = Path('/var/lib/hormuz')
for filename, state in [('profile.json', 'state'), ('restored-profile.json', 'restored')]:
    path = root / filename
    path.write_text(json.dumps({'schema': 'hormuz.hosted-auth-staging/v1',
        'public_origin': 'https://gateway.example.test', 'oidc_issuer': 'https://idp.example.test',
        'oidc_client_id': 'fixture-login', 'state_directory': str(root / state)}))
    path.chmod(0o600)
routes = {
    'openai-primary': {'protocol': 'openai', 'upstream_model': 'openai-primary-model',
        'input_cost_per_million': 1, 'cache_read_cost_per_million': 1,
        'cache_write_cost_per_million': 1, 'output_cost_per_million': 2,
        'failover_alias': 'openai-secondary'},
    'openai-secondary': {'protocol': 'openai', 'upstream_model': 'openai-secondary-model',
        'input_cost_per_million': 2, 'cache_read_cost_per_million': 2,
        'cache_write_cost_per_million': 2, 'output_cost_per_million': 4},
    'anthropic-primary': {'protocol': 'anthropic', 'upstream_model': 'anthropic-primary-model',
        'input_cost_per_million': 3, 'cache_read_cost_per_million': 3,
        'cache_write_cost_per_million': 3, 'output_cost_per_million': 6,
        'failover_alias': 'anthropic-secondary'},
    'anthropic-secondary': {'protocol': 'anthropic', 'upstream_model': 'anthropic-secondary-model',
        'input_cost_per_million': 4, 'cache_read_cost_per_million': 4,
        'cache_write_cost_per_million': 4, 'output_cost_per_million': 8},
}
provider = root / 'provider.json'
provider.write_text(json.dumps({
    'listen': {'host': '127.0.0.1', 'port': 8787},
    'ingress': {'mode': 'external_tls_proxy', 'trusted_proxy_cidrs': ['127.0.0.1/32'],
        'credential_env': 'HORMUZ_INGRESS_CREDENTIAL'},
    'database': str(root / 'state' / 'usage.sqlite3'),
    'max_request_bytes': 2097152, 'upstream_timeout_seconds': 60,
    'upstreams': {
        'openai': {'base_url': 'https://api.openai.com', 'api_key_env': 'HORMUZ_OPENAI_PROVIDER_KEY',
            'allow_response_storage': False, 'allow_background': False},
        'anthropic': {'base_url': 'https://api.anthropic.com', 'api_key_env': 'HORMUZ_ANTHROPIC_PROVIDER_KEY',
            'allow_response_storage': False, 'allow_background': False}},
    'authentication': {
        'session_broker': {'enabled': True, 'public_base_url': 'https://gateway.example.test',
            'database': str(root / 'state' / 'sessions.sqlite3'), 'master_key_env': 'HORMUZ_SESSION_MASTER_KEY',
            'access_ttl_seconds': 600, 'absolute_ttl_seconds': 43200, 'enrollment_ttl_seconds': 300,
            'onboarding_enabled': True, 'console_enabled': True},
        'oidc': {'issuers': [{'issuer': 'https://idp.example.test', 'audiences': ['hormuz-staging-api'],
            'login': {'client_id': 'fixture-login', 'client_secret_env': 'HORMUZ_OIDC_CLIENT_SECRET',
                'scopes': ['openid', 'email'], 'token_endpoint_auth_method': 'client_secret_basic'},
            'subjects': []}]}},
    'identities': [], 'model_routes': routes,
    'egress_controls': {'secrets': {'mode': 'redact', 'builtins': True, 'custom_secret_envs': []}},
    'policies': {'organization': {'allowed_clients': ['codex', 'claude-code'],
        'allowed_models': list(routes), 'fallback_models': {'openai': 'openai-primary', 'anthropic': 'anthropic-primary'},
        'max_output_tokens': 4096, 'monthly_budget_usd': 100, 'per_actor_monthly_budget_usd': 25},
        'teams': {}, 'actors': {}}
}))
provider.chmod(0o600)
key = root / 'backup.key'
key.write_bytes(base64.b64encode(b'b' * 32) + b'\\n')
key.chmod(0o600)
'''
PROCESS_BOUNDARY = '''
import json
from pathlib import Path
caddy = backend = None
for path in Path('/proc').iterdir():
    if not path.name.isdecimal():
        continue
    try:
        command = (path / 'cmdline').read_bytes().split(b'\\0')
        if command[:2] == [b'/usr/bin/caddy', b'run']:
            caddy = {item.split(b'=', 1)[0].decode() for item in (path / 'environ').read_bytes().split(b'\\0') if item}
        elif b'hormuz.hosted' in command and any(item in {b'backend', b'provider-backend'} for item in command):
            backend = {item.split(b'=', 1)[0].decode() for item in (path / 'environ').read_bytes().split(b'\\0') if item}
    except FileNotFoundError:
        continue
print(json.dumps({'uid': __import__('os').getuid(), 'caddy_names': sorted(caddy or []),
    'backend_names': sorted(backend or []), 'backend_present': backend is not None}))
'''
IMAGE_SOURCES = '''
import hashlib, json, hormuz
from pathlib import Path
package = Path(hormuz.__file__).parent
paths = {'hormuz/' + name: package / name for name in
    ('hosted.py', '_hosted_backup.py', '_hosted_config.py', '_hosted_provider.py', '_hosted_server.py', '_hosted_state.py', 'secret-inventory-v1.json')}
paths.update({'deploy/render/gateway/' + name: Path('/etc/hormuz/caddy') / name
    for name in ('active.Caddyfile', 'maintenance.Caddyfile', 'provider-pilot.Caddyfile')})
print(json.dumps({name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}))
'''
SSH_ACCOUNT_BOUNDARY = '''
import json, os, pwd, stat
from pathlib import Path
account = pwd.getpwuid(os.getuid())
home = Path(account.pw_dir)
ssh = home / '.ssh'
def metadata(path):
    value = path.stat()
    return {'uid': value.st_uid, 'gid': value.st_gid,
            'mode': stat.S_IMODE(value.st_mode), 'directory': stat.S_ISDIR(value.st_mode)}
print(json.dumps({'name': account.pw_name, 'uid': account.pw_uid, 'gid': account.pw_gid,
    'home': account.pw_dir, 'shell': account.pw_shell, 'environment_home': os.environ.get('HOME'),
    'home_metadata': metadata(home), 'ssh_metadata': metadata(ssh),
    'sshd_present': Path('/usr/sbin/sshd').exists()}))
'''


def docker(*arguments, input_text=None, timeout=45, expected=0):
    result = subprocess.run(["docker", *arguments], input=input_text, text=True, capture_output=True, timeout=timeout)
    if result.returncode != expected:
        raise RuntimeError("staging_docker_command_failed:" + arguments[0])
    return (result.stdout + (result.stderr if arguments[0] == "logs" or expected != 0 else "")).strip()


def verify(image: str) -> dict:
    prefix = "hormuz-staging-check-" + uuid.uuid4().hex[:12]
    volume = prefix + "-state"
    containers = []
    checks = []
    environment = ["--env", "HORMUZ_CONFIG=/var/lib/hormuz/profile.json",
                   "--env", "HORMUZ_PROVIDER_CONFIG=/var/lib/hormuz/provider.json"]
    for name, value in {**SECRETS, **PROVIDER_SECRETS}.items():
        environment.extend(["--env", name + "=" + value])
    environment.extend(["--env", "UNRELATED_SECRET=" + CANARY, "--env", "HTTP_PROXY=http://untrusted.example.test:8080"])
    # Match the paid pilot's compute ceiling. Provider hostnames resolve only to
    # container loopback so a regression cannot turn this provider-free proof
    # into an external request.
    common = ["--platform", "linux/amd64", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
              "--cpus", "0.5", "--memory", "512m", "--pids-limit", "128",
              "--add-host", "api.openai.com:127.0.0.1",
              "--add-host", "api.anthropic.com:127.0.0.1",
              "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
              "--mount", "type=volume,source=" + volume + ",target=/var/lib/hormuz", *environment]

    def passed(name, condition=True):
        if not condition:
            raise RuntimeError("staging_check_failed:" + name)
        checks.append(name)

    def start(suffix, mode):
        name = prefix + "-" + suffix
        containers.append(name)
        docker("run", "--detach", "--name", name, *common, "--env", "HORMUZ_HOSTED_MODE=" + mode,
               "-p", "127.0.0.1::10000", image)
        return name

    def execute(name, *command, input_text=None, expected=0):
        return docker("exec", "-i", name, *command, input_text=input_text, expected=expected)

    def operator(*command, expected=0):
        return docker("run", "--rm", *common, image, *command, expected=expected)

    def port(name):
        return int(docker("port", name, "10000/tcp").rsplit(":", 1)[1])

    def request(name, method, path, *, fields=(), body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port(name), timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if not any(key.lower() == "host" for key, _ in fields):
                connection.putheader("Host", "gateway.example.test")
            for key, value in fields:
                connection.putheader(key, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(65536)
        finally:
            connection.close()

    def await_health(name, status):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                response = request(name, "GET", "/health")
                if json.loads(response[2]).get("status") == status:
                    return response
            except (OSError, ValueError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        raise RuntimeError("staging_startup_timeout")

    def stop(name):
        started = time.monotonic()
        docker("stop", "--time", "25", name, timeout=30)
        passed("clean_shutdown_" + name.rsplit("-", 1)[1],
               time.monotonic() - started < 25 and docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0")

    docker("volume", "create", volume)
    try:
        installed_sources = json.loads(docker("run", "--rm", "-i", *common, "--entrypoint", PYTHON, image,
                                              "-I", "-", input_text=IMAGE_SOURCES))
        passed("installed_sources_match_checkout", all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                                                       for name, digest in installed_sources.items()))
        ssh_account = json.loads(docker("run", "--rm", "-i", *common, "--entrypoint", PYTHON, image,
                                        "-I", "-", input_text=SSH_ACCOUNT_BOUNDARY))
        expected_private_directory = {"uid": 65532, "gid": 1000, "mode": 0o700, "directory": True}
        passed("render_ssh_account_is_nonroot_and_private",
               {name: ssh_account[name] for name in ("name", "uid", "gid", "home", "shell", "environment_home", "sshd_present")} == {
                   "name": "hormuz", "uid": 65532, "gid": 1000, "home": "/home/hormuz",
                   "shell": "/bin/sh", "environment_home": "/home/hormuz", "sshd_present": False,
               }
               and ssh_account["home_metadata"] == expected_private_directory
               and ssh_account["ssh_metadata"] == expected_private_directory)
        passed("render_ssh_home_is_outside_persistent_disk", not ssh_account["home"].startswith("/var/lib/hormuz"))
        docker("run", "--rm", "-i", *common, "--entrypoint", PYTHON, image, "-I", "-", input_text=SETUP)
        # Model Render's root-owned, group-1000 readable secret-file mount.
        # Elevated permissions apply ONLY to this disposable fixture setup.
        docker("run", "--rm", *common, "--user", "0:0", "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE",
               "--entrypoint", PYTHON, image, "-I", "-c",
               "import os; from pathlib import Path; files=[Path('/var/lib/hormuz')/name for name in ('profile.json','restored-profile.json','provider.json')]; [(os.chown(path,0,1000),path.chmod(0o640)) for path in files]")
        uninitialized = start("uninitialized", "active")
        passed("uninitialized_active_start_refused", docker("wait", uninitialized, timeout=25) == "1")
        maintenance = start("maintenance", "maintenance")
        await_health(maintenance, "maintenance")
        for path in ("/ready", "/console", "/v1/auth/callback", "/v1/admin/auth/callback", "/v1/responses"):
            passed("maintenance_closed_" + path, request(maintenance, "GET", path)[0] == 503)
        boundary = json.loads(execute(maintenance, PYTHON, "-I", "-", input_text=PROCESS_BOUNDARY))
        passed("maintenance_has_no_backend_or_proxy_secrets", not boundary["backend_present"] and set(boundary["caddy_names"]) == {"PORT", "XDG_CONFIG_HOME", "XDG_DATA_HOME"})
        execute(maintenance, PYTHON, "-I", "-m", "hormuz.hosted", "initialize")
        passed("initialization_does_not_open_maintenance", request(maintenance, "GET", "/ready")[0] == 503)
        execute(maintenance, PYTHON, "-I", "-m", "hormuz.hosted", "team", "organization", "create",
                "--organization", "staging-fixture", "--name", "Synthetic fixture", "--issuer", "https://idp.example.test")
        stop(maintenance)
        active = start("active", "active")
        await_health(active, "authentication_staging")
        passed("public_ready", request(active, "GET", "/ready")[0] == 200)
        passed("public_console", request(active, "GET", "/console")[0] == 200)
        passed("public_identity_requires_session", request(active, "GET", "/v1/gateway/whoami")[0] == 401)
        boundary = json.loads(execute(active, PYTHON, "-I", "-", input_text=PROCESS_BOUNDARY))
        passed("nonroot_runtime", boundary["uid"] == 65532)
        passed("render_secret_file_group_readable", execute(active, PYTHON, "-I", "-c", "import os; print(os.getgid())") == "1000")
        passed("proxy_has_only_ingress_secret", set(boundary["caddy_names"]) == {"PORT", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HORMUZ_INGRESS_CREDENTIAL"})
        passed("backend_has_only_owned_secrets", boundary["backend_present"] and set(boundary["backend_names"]) - {"LC_CTYPE"} == set(SECRETS))
        forged = [("X-Hormuz-Ingress-Credential", CANARY), ("X-Hormuz-Ingress-Credential", "second-forged-value"),
                  ("X-Forwarded-Host", "foreign.example.test"), ("Forwarded", "host=foreign.example.test;proto=http")]
        passed("caller_ingress_values_are_replaced", request(active, "GET", "/ready", fields=forged)[0] == 200)
        passed("caller_host_cannot_be_rewritten", request(active, "GET", "/console", fields=[("Host", "foreign.example.test")])[0] == 400)
        for path in ("/v1/responses", "/v1/messages", "/v1/models", "/v1/portfolio/projects"):
            status, headers, body = request(active, "POST", path, body=CANARY.encode())
            passed("inference_closed_" + path, status == 503 and b'"inference_enabled":false' in body and CANARY.encode() not in body and "Server" not in headers)
        passed("large_body_rejected", request(active, "POST", "/v1/auth/enrollments", body=b"x" * 16385)[0] == 413)
        denied = execute(active, PYTHON, "-I", "-m", "hormuz.hosted", "snapshot", "--output-directory", "/var/lib/hormuz/blocked", expected=1)
        passed("online_snapshot_refused", json.loads(denied)["code"] == "hosted_state_in_use")
        # No backend port is published, and every direct hop still needs its secret.
        direct = '''import http.client; c=http.client.HTTPConnection('127.0.0.1',8787,timeout=2); c.request('GET','/health',headers={'Host':'gateway.example.test'}); print(c.getresponse().status); c.close()'''
        passed("direct_backend_requires_ingress_secret", execute(active, PYTHON, "-I", "-c", direct) == "401")
        published = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", active))
        passed("only_proxy_port_published", set(published) == {"10000/tcp"})
        before = execute(active, PYTHON, "-I", "-c", "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('/var/lib/hormuz/state/initialized.json').read_bytes()).hexdigest())")
        stop(active)
        docker("start", active)
        await_health(active, "authentication_staging")
        after = execute(active, PYTHON, "-I", "-c", "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('/var/lib/hormuz/state/initialized.json').read_bytes()).hexdigest())")
        passed("restart_preserves_state_binding", before == after)
        organizations = json.loads(execute(active, PYTHON, "-I", "-m", "hormuz.hosted", "team", "organization", "list"))
        passed("restart_preserves_operator_directory", "staging-fixture" in json.dumps(organizations))
        stop(active)
        provider_check = json.loads(operator(
            "--provider-config", "/var/lib/hormuz/provider.json", "provider-check",
        ))
        passed("provider_profile_preflight_is_content_free_and_closed",
               provider_check.get("provider_configuration_valid") is True
               and provider_check.get("inference_enabled") is False)
        provider_pilot = start("provider", "provider-pilot")
        await_health(provider_pilot, "provider_pilot")
        boundary = json.loads(execute(provider_pilot, PYTHON, "-I", "-", input_text=PROCESS_BOUNDARY))
        passed("provider_proxy_has_only_ingress_secret",
               set(boundary["caddy_names"]) == {"PORT", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HORMUZ_INGRESS_CREDENTIAL"})
        passed("provider_backend_has_only_owned_secrets",
               boundary["backend_present"]
               and set(boundary["backend_names"]) - {"LC_CTYPE"} == set(SECRETS) | set(PROVIDER_SECRETS))
        status, headers, body = request(
            provider_pilot, "POST", "/v1/responses",
            body=b'{"model":"openai-primary","input":"synthetic-no-egress"}',
        )
        passed("provider_route_requires_session_before_egress",
               status == 401 and b"synthetic-no-egress" not in body and "Server" not in headers)
        status, headers, body = request(provider_pilot, "POST", "/v1/models", body=CANARY.encode())
        passed("provider_unknown_route_fails_closed",
               status == 503 and b'"inference_enabled":true' in body
               and CANARY.encode() not in body and "Server" not in headers)
        passed("provider_direct_backend_requires_ingress_secret",
               execute(provider_pilot, PYTHON, "-I", "-c", direct) == "401")
        published = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", provider_pilot))
        passed("provider_only_proxy_port_published", set(published) == {"10000/tcp"})
        stop(provider_pilot)
        exported = json.loads(operator("backup-export", "--key-file", "/var/lib/hormuz/backup.key",
                                       "--output-file", "/var/lib/hormuz/offsite.hzb"))
        verified = json.loads(operator("backup-verify", "--key-file", "/var/lib/hormuz/backup.key",
                                       "--archive-file", "/var/lib/hormuz/offsite.hzb"))
        passed("encrypted_archive_digest_matches", exported["archive_sha256"] == verified["archive_sha256"]
               and exported["archive_bytes"] == verified["archive_bytes"])
        ciphertext_only = docker(
            "run", "--rm", "-i", *common, "--entrypoint", PYTHON, image, "-I", "-",
            input_text="from pathlib import Path\nv=Path('/var/lib/hormuz/offsite.hzb').read_bytes()\nprint(int(b'SQLite format 3' not in v and b'staging-fixture' not in v))\n",
        )
        passed("encrypted_archive_hides_database_and_fixture_identity", ciphertext_only == "1")
        recovery = json.loads(operator(
            "--config", "/var/lib/hormuz/restored-profile.json", "backup-restore",
            "--key-file", "/var/lib/hormuz/backup.key", "--archive-file", "/var/lib/hormuz/offsite.hzb",
        ))
        recovery_check = json.loads(operator(
            "--config", "/var/lib/hormuz/restored-profile.json", "recovery-check",
        ))
        export_metadata = {"event", "operation", "inference_enabled", "archive_bytes",
                           "archive_sha256", "backup_schema"}
        command_metadata = {"event", "operation", "inference_enabled"}
        restored_state = {name: value for name, value in recovery.items() if name not in export_metadata}
        checked_state = {name: value for name, value in recovery_check.items() if name not in command_metadata}
        passed("recovered_authority_counts_are_zero", restored_state == checked_state
               and restored_state.pop("recovered_closed", None) is True
               and all(value == 0 for value in restored_state.values()))
        passed("encrypted_archive_restores_closed_and_checks")
        for name in containers:
            logs = docker("logs", name)
            passed("no_canary_or_secret_in_logs_" + name.rsplit("-", 1)[1], all(
                value not in logs for value in (CANARY, *SECRETS.values(), *PROVIDER_SECRETS.values())
            ))
        return {"schema_id": "hormuz.hosted-staging-verification", "schema_version": 1,
                "passed": True, "image_id": docker("image", "inspect", "--format", "{{.Id}}", image),
                "checks": checks, "check_count": len(checks), "real_idp_used": False,
                "public_tls_or_browser_qualified": False, "provider_requests": 0,
                "source_files_sha256": installed_sources}
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="hormuz-hosted-staging:development")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.image)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "passed": result["passed"],
        "check_count": result["check_count"],
        "provider_requests": result["provider_requests"],
    }))


if __name__ == "__main__":
    main()
