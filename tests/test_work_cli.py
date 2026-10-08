"""Work client boundary: explicit credentials, no redirects, real check results."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import http.client
from pathlib import Path
import tomllib
from unittest import TestCase, mock

from hormuz.commands import work
from hormuz.work_client import WorkClient, WorkClientError


class WorkClientTests(TestCase):
    def client(self):
        return WorkClient("https://gateway.example", "secret-fixture-token")

    def test_transport_requires_https_or_explicit_loopback(self):
        for url in ("http://gateway.example", "https://user:pass@gateway.example", "https://gateway.example/path", "https://gateway.example?token=abc", "https://gateway.example#token"):
            with self.assertRaises(WorkClientError):
                WorkClient(url, "secret-fixture-token")
        with self.assertRaises(WorkClientError):
            WorkClient("http://127.0.0.1:8787", "secret-fixture-token")
        WorkClient("http://127.0.0.1:8787", "secret-fixture-token", allow_loopback_http=True)
        with self.assertRaises(WorkClientError):
            WorkClient("http://example.com:8787", "secret-fixture-token", allow_loopback_http=True)

    def test_job_attaches_id_without_leaking_credential(self):
        client = self.client()
        job = client.job("work-123")
        self.assertEqual(job.headers, {"X-Hormuz-Work-Id": "work-123"})
        with mock.patch.object(client, "_request", return_value={"result": "ok"}) as request:
            for _ in range(2):
                job.request("/v1/chat/completions", {"model": "fixture", "messages": []})
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.kwargs["headers"], job.headers)
        with self.assertRaises(WorkClientError):
            job.request("/v1/chat/completions", {"stream": True})

    def test_checks_do_not_infer_completion_from_response_or_intermediate_pass(self):
        client, reference = self.client(), "ci/run-1"
        job = client.job("work-123")
        with mock.patch.object(client, "_request", return_value={}) as request:
            job.observe_check(0, reference=reference)
            self.assertEqual(request.call_args.args[:2], ("GET", "/v1/work/jobs/work-123"))
            job.observe_check(0, reference=reference, completes_work=True)
            self.assertEqual(request.call_args.args[2], {"status": "completed", "source": "workflow", "reference": reference})
            job.observe_check(1, reference="ci/run-2")
            self.assertEqual(request.call_args.args[2]["status"], "corrected")

    def test_cli_parses_budget_exactly_and_prints_safe_work_header(self):
        parser = argparse.ArgumentParser()
        work.add_work_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["work", "--gateway", "https://gateway.example", "headers", "--work-id", "work-123"])
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"HORMUZ_TOKEN": "secret-fixture-token"}), redirect_stdout(output):
            self.assertEqual(work.run(args), 0)
        self.assertEqual(json.loads(output.getvalue()), {"X-Hormuz-Work-Id": "work-123"})
        self.assertNotIn("secret-fixture-token", output.getvalue())
        self.assertEqual(work._amount("0.000001"), 1)
        for value in ("-1", "NaN", "1e9", "0.0000001"):
            with self.assertRaises(WorkClientError):
                work._amount(value)

    def test_native_session_config_attaches_work_without_opening_server_secrets(self):
        from hormuz.cli import main
        for name in ("codex", "claude"):
            output = io.StringIO()
            argv = ["client", "config", name, "--auth-mode", "session", "--url", "https://gateway.example", "--model", "safe-openai", "--work-id", "work-123"]
            with mock.patch("hormuz.cli.GatewayConfig.load", side_effect=AssertionError("server configuration read")), redirect_stdout(output):
                self.assertEqual(main(argv), 0)
            if name == "codex":
                provider = tomllib.loads(output.getvalue())["model_providers"]["hormuz"]
                self.assertEqual(provider["http_headers"], {"X-Hormuz-Work-Id": "work-123"})
                self.assertEqual(provider["auth"]["command"], "hormuz")
            else:
                settings = json.loads(output.getvalue())
                self.assertEqual(settings["env"]["ANTHROPIC_CUSTOM_HEADERS"], "X-Hormuz-Work-Id: work-123")
                self.assertIn("auth session", settings["apiKeyHelper"])
            self.assertNotIn("Authorization", output.getvalue())
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(main([*argv[:-1], 'work-123\r\nAuthorization: secret-fixture-token']), 1)
        self.assertNotIn("secret-fixture-token", output.getvalue())

    def test_native_static_config_preserves_private_credential_reference(self):
        from hormuz.commands.client import _client_config
        from hormuz.config import GatewayConfig
        config = GatewayConfig.load(Path(__file__).parents[1] / "config.example.json", environ={"HORMUZ_TOKEN": "secret-fixture-token"})
        for name in ("codex", "claude"):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(_client_config(config, name, "https://gateway.example", work_id="work-123"), 0)
            self.assertIn("X-Hormuz-Work-Id", output.getvalue())
            self.assertIn("HORMUZ_TOKEN", output.getvalue())
            self.assertNotIn("secret-fixture-token", output.getvalue())

    def test_managed_native_launch_and_relay_preserve_work_and_context_only(self):
        from hormuz.client_relay import _client_command, _forward_headers, SavedClientProfile, ClientRelayError
        local_token = "hox_l_" + "B" * 43
        upstream_token = "hox_a_" + "A" * 43
        for name in ("codex", "claude-code"):
            profile = SavedClientProfile("fixture", "https://gateway.example", name, "approved", False)
            with mock.patch.dict("os.environ", {"ANTHROPIC_CUSTOM_HEADERS": "Authorization: attacker", "OPENAI_API_KEY": "direct-provider-secret"}, clear=True):
                argv, environment = _client_command(profile, "http://127.0.0.1:8787", local_token, executable="/usr/bin/" + name, work_id="work-123")
            if name == "codex":
                self.assertIn('model_providers.hormuz_context_relay.http_headers={"X-Hormuz-Work-Id"="work-123"}', argv)
                self.assertEqual(environment["HORMUZ_LOCAL_RELAY_TOKEN"], local_token)
            else:
                self.assertEqual(environment["ANTHROPIC_CUSTOM_HEADERS"], "X-Hormuz-Work-Id: work-123")
                self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], local_token)
            self.assertNotIn("direct-provider-secret", argv)
            with self.assertRaises(ClientRelayError):
                _client_command(profile, "http://127.0.0.1:8787", local_token, executable="fixture", work_id="bad\nAuthorization: attacker")
        headers = http.client.HTTPMessage()
        for name, value in (("X-Hormuz-Work-Id", "work-123"), ("Authorization", "Bearer client-secret"), ("X-Api-Key", "client-key")):
            headers[name] = value
        context = {"X-Hormuz-Context-Format": "fixture-format"}
        forwarded = _forward_headers(headers, upstream_token, 20, context)
        self.assertEqual(forwarded["X-Hormuz-Work-Id"], "work-123")
        self.assertEqual(forwarded["X-Hormuz-Context-Format"], "fixture-format")
        self.assertEqual(forwarded["Authorization"], "Bearer " + upstream_token)
        self.assertNotIn("X-Api-Key", forwarded)
        for mode in ("openai", "anthropic"):
            self.assertNotIn("X-Hormuz-Work-Id", _forward_headers(headers, "provider-secret", 20, context, upstream_auth=mode))
        headers["X-Hormuz-Work-Id"] = "second-job"
        self.assertNotIn("X-Hormuz-Work-Id", _forward_headers(headers, upstream_token, 20, context))

    def test_cli_real_check_argv_and_completion_criterion(self):
        parser = argparse.ArgumentParser()
        work.add_work_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["work", "--gateway", "https://gateway.example", "check", "--work-id", "work-123", "--reference", "ci/run-2", "--completes-work", "--", "python", "-m", "unittest"])
        with mock.patch.dict("os.environ", {"HORMUZ_TOKEN": "secret-fixture-token"}), mock.patch.object(work.subprocess, "run", return_value=mock.Mock(returncode=0)) as command, mock.patch("hormuz.work_client.WorkJob.observe_check") as observe:
            self.assertEqual(work.run(args), 0)
        command.assert_called_once_with(["python", "-m", "unittest"], check=False)
        observe.assert_called_once_with(0, reference="ci/run-2", completes_work=True)

    def test_errors_do_not_print_credentials(self):
        parser = argparse.ArgumentParser()
        work.add_work_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["work", "--gateway", "http://gateway.example", "state"])
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"HORMUZ_TOKEN": "secret-fixture-token"}), redirect_stderr(output):
            self.assertEqual(work.run(args), 1)
        self.assertNotIn("secret-fixture-token", output.getvalue())
