"""Acquired artifacts feed the approved connection workflow, with external stand-ins."""
from contextlib import contextmanager
import http.server
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import threading
import tarfile
import time
import unittest

from hormuz.contracts import validate_contract
from tests import test_connected_client_lifetime_workflow as client_workflow
from tests.test_execution_destination_workflow import provider


@contextmanager
def artifacts(payloads):
    observed = []
    controls = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            observed.append((self.path, dict(self.headers)))
            payload = payloads[self.path]
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            if controls.get("trickle_headers") and self.path == "/runtime":
                try:
                    self.connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: ")
                    for _ in range(8):
                        time.sleep(0.3)
                        self.connection.sendall(b" ")
                    self.connection.sendall(str(len(payload)).encode() + b"\r\n\r\n" + payload)
                except OSError:
                    pass
                self.close_connection = True
                return
            if controls.get("redirect"):
                self.send_response(302)
                self.send_header("Location", controls["redirect"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if controls.get("status"):
                self.send_response(controls["status"])
                self.send_header("Retry-After", "60")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if controls.get("ignore_range"):
                offset = 0
            self.send_response(206 if offset else 200)
            self.send_header("Content-Length", str(len(payload) - offset))
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(payload)-1}/{len(payload)}")
            self.end_headers()
            if controls.pop("truncate", None) == self.path:
                self.wfile.write(payload[offset:len(payload)//2])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                self.close_connection = True
            else:
                self.wfile.write(payload[offset:])

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.2
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observed, controls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class LocalAcquisitionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = client_workflow.ConnectedClientLifetimeWorkflowTests()
        self.client.setUp()
        self.addCleanup(self.client.doCleanups)
        self.root = self.client.root
        self.directory = self.root / "assets"

    def acquire(self, *options, check=True):
        result = subprocess.run([sys.executable, "-m", "hormuz", "client", "acquire", "codex",
            "--directory", str(self.directory), "--catalog", str(self.root / "catalog.json"), *options],
            env=self.client.environment, capture_output=True, text=True, timeout=18)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def prepare(self):
        value, _, _ = self.client.managed_configuration("http://127.0.0.1:1")
        self.client.local_setup_options(value)
        managed = value["managed_llama"]
        return value, {"/runtime": Path(managed["runtime_archive"]).read_bytes(),
                       "/model": Path(managed["model_file"]).read_bytes()}

    def catalog(self, value, origin):
        managed = value["managed_llama"]
        profile = json.loads((self.root / "qualification.json").read_text())
        result = {"schema_id": "hormuz.local-acquisition-catalog", "schema_version": 1,
                  "catalog_id": "synthetic-acquisition-v1", "profile": profile,
                  "runtime": {}, "model": {}}
        for role, source, digest, license_id in (
            ("runtime", managed["runtime_archive"], managed["runtime_sha256"], "MIT"),
            ("model", managed["model_file"], managed["model_sha256"], "Apache-2.0")):
            result[role] = {"url": origin + "/" + role, "sha256": digest,
                            "size_bytes": Path(source).stat().st_size,
                            "license": license_id, "license_url": "https://example.invalid/" + role,
                            "allowed_hosts": ["127.0.0.1"]}
        (self.root / "catalog.json").write_text(json.dumps(result))
        return result

    def test_preview_download_offline_reuse_and_connect_share_verified_assets(self):
        for name in ("local-acquisition-catalog-v1.json", "local-acquisition-v1.json", "local-acquisition-error-v1.json"):
            validate_contract(json.loads((Path(__file__).parent / "fixtures" / name).read_text()))
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, cloud_requests):
            value, _, _ = self.client.managed_configuration(cloud)
            self.client.local_setup_options(value)
            managed = value["managed_llama"]
            payloads = {"/runtime": Path(managed["runtime_archive"]).read_bytes(),
                        "/model": Path(managed["model_file"]).read_bytes()}
            with artifacts(payloads) as (origin, observed, _):
                self.catalog(value, origin)
                original = (self.client.home / "config.toml").read_text()
                preview = json.loads(self.acquire().stdout)
                validate_contract(preview)
                self.assertEqual((preview["operation"], preview["state"]), ("preview", "planned"))
                self.assertEqual(preview["setup_arguments"], [])
                self.assertFalse(self.directory.exists())
                self.assertEqual(observed, [])
                ready = json.loads(self.acquire("--download").stdout)
                validate_contract(ready)
                self.assertEqual((ready["operation"], ready["state"]), ("acquire", "ready"))
                self.assertEqual([item[0] for item in observed], ["/runtime", "/model"])
                self.assertTrue(all("Authorization" not in headers for _, headers in observed))
                self.assertEqual((self.client.home / "config.toml").read_text(), original)
                self.assertFalse(self.client.path.exists())
            reused = json.loads(self.acquire("--offline").stdout)
            self.assertEqual((reused["operation"], reused["state"]), ("reuse", "ready"))
            self.assertEqual(reused["directory"], ready["directory"])
            self.addCleanup(lambda: self.client.command("disconnect", check=False))
            connected = json.loads(self.client.command("setup", *reused["setup_arguments"]).stdout)
            self.assertEqual(connected["connection"]["phase"], "connected")
            self.assertEqual(self.client.client()["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
            self.assertEqual(json.loads(self.client.command("status").stdout)["phase"], "connected")
            self.assertEqual(json.loads(self.client.command("disconnect").stdout)["local_runtime"]["state"], "stopped")
            self.assertEqual((self.client.home / "config.toml").read_text(), original)
            self.assertTrue(Path(ready["directory"]).is_dir())
            self.assertEqual(cloud_requests, [])

    def test_interrupted_download_resumes_only_the_owned_pinned_range(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, controls):
            self.catalog(value, origin)
            controls["truncate"] = "/runtime"
            failed = self.acquire("--download", check=False)
            self.assertEqual(failed.returncode, 2, failed.stderr)
            validate_contract(json.loads(failed.stderr))
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "download_unavailable")
            self.assertEqual([path for path, _ in observed], ["/runtime"])
            preview = json.loads(self.acquire().stdout)
            self.assertEqual(preview["state"], "planned")
            self.assertEqual(preview["storage"]["download_bytes"],
                             len(payloads["/runtime"]) - len(payloads["/runtime"])//2 + len(payloads["/model"]))
            ready = json.loads(self.acquire("--download").stdout)
            self.assertEqual(ready["state"], "ready")
            self.assertEqual(observed[1][1]["Range"], f"bytes={len(payloads['/runtime'])//2}-")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/runtime", "/model"])
            self.assertFalse(self.client.path.exists())
            self.assertEqual(self.client.client("inspect")["model"], "original-model")

    def test_changed_published_installation_is_preserved_and_not_redownloaded(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, _):
            self.catalog(value, origin)
            ready = json.loads(self.acquire("--download").stdout)
            foreign = Path(ready["directory"]) / "my-notes.txt"
            foreign.write_text("keep my work")
            failed = self.acquire("--download", check=False)
            self.assertEqual(failed.returncode, 2, failed.stdout)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "installation_changed")
            self.assertEqual(foreign.read_text(), "keep my work")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/model"])
            self.assertEqual(self.client.client("inspect")["model"], "original-model")

    def test_corrupt_download_is_not_published_or_retried_and_can_be_explicitly_recovered(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, _):
            self.catalog(value, origin)
            expected = payloads["/model"]
            payloads["/model"] = b"x" * len(expected)
            failed = self.acquire("--download", check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "download_invalid")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/model"])
            missing = self.acquire("--offline", check=False)
            self.assertEqual(json.loads(missing.stderr)["error"]["code"], "offline_missing")
            self.assertFalse(self.client.path.exists())
            payloads["/model"] = expected
            ready = json.loads(self.acquire("--download").stdout)
            self.assertEqual(ready["state"], "ready")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/model", "/model"])
            self.assertEqual(self.client.client("inspect")["model"], "original-model")

    def test_unsafe_archive_cannot_write_outside_owned_generation_or_connect(self):
        value, payloads = self.prepare()
        malicious = io.BytesIO()
        with tarfile.open(fileobj=malicious, mode="w:gz") as archive:
            member = tarfile.TarInfo("../../outside.txt")
            member.size = 6
            archive.addfile(member, io.BytesIO(b"unsafe"))
        payloads["/runtime"] = malicious.getvalue()
        value["managed_llama"]["runtime_sha256"] = hashlib.sha256(payloads["/runtime"]).hexdigest()
        profile = json.loads((self.root / "qualification.json").read_text())
        profile["runtime_sha256"] = value["managed_llama"]["runtime_sha256"]
        (self.root / "qualification.json").write_text(json.dumps(profile))
        Path(value["managed_llama"]["runtime_archive"]).write_bytes(payloads["/runtime"])
        with artifacts(payloads) as (origin, observed, _):
            self.catalog(value, origin)
            failed = self.acquire("--download", check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "runtime_invalid")
            self.assertFalse((self.directory / "outside.txt").exists())
            self.assertFalse((self.root / "outside.txt").exists())
            self.assertFalse(self.client.path.exists())
            self.assertEqual(self.client.client("inspect")["model"], "original-model")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/model"])

    def test_rate_limit_and_unapproved_redirect_stop_without_retries_or_credential_disclosure(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, controls):
            self.catalog(value, origin)
            controls["status"] = 429
            failed = self.acquire("--download", check=False)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "download_unavailable")
            self.assertEqual([path for path, _ in observed], ["/runtime"])
            controls.clear()
            controls["redirect"] = "http://localhost:1/credential-must-not-appear"
            redirected = self.acquire("--download", check=False)
            self.assertEqual(json.loads(redirected.stderr)["error"]["code"], "download_invalid")
            self.assertNotIn("credential-must-not-appear", redirected.stderr)
            self.assertEqual([path for path, _ in observed], ["/runtime", "/runtime"])
            self.assertTrue(all(not {"authorization", "cookie", "proxy-authorization"} & {key.lower() for key in headers}
                                for _, headers in observed))
            self.assertFalse(self.client.path.exists())

    def test_ignored_range_restarts_owned_partial_without_combining_two_responses(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, controls):
            self.catalog(value, origin)
            controls["truncate"] = "/runtime"
            self.assertEqual(self.acquire("--download", check=False).returncode, 2)
            controls["ignore_range"] = True
            ready = json.loads(self.acquire("--download").stdout)
            self.assertEqual(ready["state"], "ready")
            self.assertEqual(observed[1][1]["Range"], f"bytes={len(payloads['/runtime'])//2}-")
            self.assertEqual([path for path, _ in observed], ["/runtime", "/runtime", "/model"])

    def test_unsupported_catalog_and_platform_fail_before_any_download_or_settings_change(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, _):
            catalog = self.catalog(value, origin)
            catalog["schema_version"] = 99
            catalog["untrusted-secret"] = self.client.environment["DESTINATION_CLOUD_KEY"]
            (self.root / "catalog.json").write_text(json.dumps(catalog))
            invalid = self.acquire("--download", check=False)
            self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "catalog_invalid")
            self.assertNotIn(self.client.environment["DESTINATION_CLOUD_KEY"], invalid.stderr)
            catalog = self.catalog(value, origin)
            catalog["profile"]["system"] = "UnsupportedFixtureOS"
            (self.root / "catalog.json").write_text(json.dumps(catalog))
            unsupported = self.acquire("--download", check=False)
            self.assertEqual(json.loads(unsupported.stderr)["error"]["code"], "platform_unsupported")
            self.assertEqual(observed, [])
            self.assertFalse(self.directory.exists())
            self.assertEqual(self.client.client("inspect")["model"], "original-model")

    def test_absolute_download_deadline_closes_a_source_that_keeps_headers_incomplete(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, controls):
            self.catalog(value, origin)
            controls["trickle_headers"] = True
            started = time.monotonic()
            failed = self.acquire("--download", "--timeout-seconds", "1", check=False)
            elapsed = time.monotonic() - started
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "download_unavailable")
            self.assertLess(elapsed, 2.0, "the artifact deadline must include response headers")
            self.assertEqual([path for path, _ in observed], ["/runtime"])
            self.assertFalse(self.client.path.exists())

    def test_reuse_detects_changed_model_bytes_and_preserves_them_without_network(self):
        value, payloads = self.prepare()
        with artifacts(payloads) as (origin, observed, _):
            self.catalog(value, origin)
            ready = json.loads(self.acquire("--download").stdout)
            model = Path(ready["artifacts"]["model"]["path"])
            changed = b"x" * len(payloads["/model"])
            model.write_bytes(changed)
            failed = self.acquire("--offline", check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "runtime_invalid")
            self.assertEqual(model.read_bytes(), changed)
            self.assertEqual([path for path, _ in observed], ["/runtime", "/model"])
            self.assertEqual(self.client.client("inspect")["model"], "original-model")

    def test_linked_asset_directory_cannot_mutate_its_target_or_activate_routing(self):
        value, payloads = self.prepare()
        foreign = self.root / "foreign"
        foreign.mkdir(mode=0o700)
        sentinel = foreign / "user.txt"
        sentinel.write_text("keep unrelated files")
        self.directory.symlink_to(foreign, target_is_directory=True)
        with artifacts(payloads) as (origin, observed, _):
            self.catalog(value, origin)
            failed = self.acquire("--download", check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "directory_unsafe")
            self.assertEqual(list(foreign.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_text(), "keep unrelated files")
            self.assertEqual(observed, [])
            self.assertFalse(self.client.path.exists())
