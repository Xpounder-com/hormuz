"""Offline startup-stage witnesses; Docker and public HTTP are mocked."""

from contextlib import ExitStack
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch

from tools import verify_render_gateway as verifier
from tools.verify_render_gateway import await_work_health, await_work_supervisor


STARTED = json.dumps({
    "event": "hosted_starting", "mode": "ai-work", "inference_enabled": True,
}).encode() + b"\n"


class RenderGatewayStartupTests(unittest.TestCase):
    def maintenance_shutdown_failure(self, *, elapsed=1, exit_code="1", oom_killed="false", logs=None):
        # Reach the real verifier's maintenance stop with disposable external
        # Docker/HTTP results; no daemon, provider or account is contacted.
        clock = [0.0]
        current_path = [None]
        private = "synthetic_private_log_not_for_diagnostics"
        metadata = {"uid": 65532, "gid": 1000, "mode": 0o700, "directory": True}
        account = {"name": "hormuz", "uid": 65532, "gid": 1000,
                   "home": "/home/hormuz", "shell": "/bin/sh", "environment_home": "/home/hormuz",
                   "sshd_present": False, "home_metadata": metadata, "ssh_metadata": metadata}

        def docker(*arguments, input_text=None, **options):
            if options.get("failure_context") == "postgres_application_role_boundary":
                return "1"
            if arguments[0] == "inspect":
                if arguments[2] == "{{.State.Health.Status}}":
                    return "healthy"
                if arguments[2] == "{{.State.ExitCode}}":
                    return exit_code
                if arguments[2] == "{{.State.OOMKilled}}":
                    if isinstance(oom_killed, Exception):
                        raise oom_killed
                    self.assertEqual(options["timeout"], 3)
                    return oom_killed
            if input_text == verifier.IMAGE_SOURCES:
                return "{}"
            if input_text == verifier.SSH_ACCOUNT_BOUNDARY:
                return json.dumps(account)
            if input_text == verifier.PROCESS_BOUNDARY:
                return json.dumps({"backend_present": False,
                    "caddy_names": ["PORT", "XDG_CONFIG_HOME", "XDG_DATA_HOME"]})
            if arguments[0] == "wait":
                return "1"
            if arguments[0] == "port":
                return "127.0.0.1:10000"
            if arguments[0] == "stop":
                self.assertEqual(arguments[1:3], ("--time", "25"))
                self.assertEqual(options["timeout"], 30)
                clock[0] += elapsed
            if arguments[0] == "logs":
                self.assertEqual(arguments[1:3], ("--tail", "8"))
                self.assertEqual(options["timeout"], 3)
                if isinstance(logs, Exception):
                    raise logs
                return logs if logs is not None else private + '\n{"event":"hosted_stopped","clean":false}\n'
            return ""

        connection = Mock()
        connection.putrequest.side_effect = lambda _method, path, **_: current_path.__setitem__(0, path)
        def response():
            value = Mock(status=200 if current_path[0] == "/health" else 503)
            value.getheaders.return_value = []
            value.read.return_value = b'{"status":"maintenance"}'
            return value
        connection.getresponse.side_effect = response
        with patch.object(verifier, "docker", side_effect=docker) as external, \
             patch.object(verifier.subprocess, "run"), \
             patch.object(verifier.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(verifier.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(RuntimeError) as caught:
                verifier.verify("synthetic-image")
        self.assertNotIn(private, str(caught.exception))
        self.assertEqual(sum(item.args[0] == "stop" for item in external.call_args_list), 1)
        return str(caught.exception), external.call_args_list

    def test_failed_maintenance_shutdown_reports_safe_operands_without_relaxing_gate(self):
        error, _ = self.maintenance_shutdown_failure()
        self.assertEqual(error,
            "staging_check_failed:clean_shutdown_maintenance:elapsed_ms=1000:exit_code=1:oom_killed=false:supervisor_clean=false")

    def test_slow_clean_container_still_fails_and_collects_exit_code_after_deadline(self):
        error, calls = self.maintenance_shutdown_failure(elapsed=25, exit_code="0",
            logs='{"event":"hosted_stopped","clean":true}')
        self.assertEqual(error,
            "staging_check_failed:clean_shutdown_maintenance:elapsed_ms=25000:exit_code=0:oom_killed=false:supervisor_clean=true")
        self.assertEqual(sum(item.args[0] == "inspect" and item.args[2] == "{{.State.ExitCode}}" for item in calls), 1)

    def test_oom_exit_has_scalar_evidence_without_requiring_a_stop_event(self):
        error, _ = self.maintenance_shutdown_failure(exit_code="137", oom_killed="true", logs="")
        self.assertEqual(error,
            "staging_check_failed:clean_shutdown_maintenance:elapsed_ms=1000:exit_code=137:oom_killed=true:supervisor_clean=unavailable")

    def test_unavailable_secondary_evidence_cannot_hide_the_original_stop_failure(self):
        error, _ = self.maintenance_shutdown_failure(
            oom_killed=RuntimeError("synthetic_private_inspect_failure"),
            logs=subprocess.TimeoutExpired("docker logs", 3))
        self.assertEqual(error,
            "staging_check_failed:clean_shutdown_maintenance:elapsed_ms=1000:exit_code=1:oom_killed=unavailable:supervisor_clean=unavailable")

    def test_shutdown_logs_are_size_bounded_and_arbitrary_values_are_not_returned(self):
        cases = [
            ('{"event":"hosted_stopped","clean":"synthetic_private_value"}', "synthetic_private_exit", "synthetic_private_oom"),
            ('x' * 16_385 + '\n{"event":"hosted_stopped","clean":true}', "99999999999999999", "synthetic_private_oom"),
        ]
        for logs, exit_code, oom in cases:
            with self.subTest(size=len(logs)):
                error, _ = self.maintenance_shutdown_failure(logs=logs, exit_code=exit_code, oom_killed=oom)
                self.assertEqual(error,
                    "staging_check_failed:clean_shutdown_maintenance:elapsed_ms=1000:exit_code=unavailable:oom_killed=unavailable:supervisor_clean=unavailable")

    def startup_mocks(self, stack, *, chunks, times=(0, 0), events=None):
        process = Mock()
        process.poll.return_value = None
        process.stdout.fileno.return_value = 71
        selector = Mock()
        selector.select.side_effect = events or [[(Mock(), 1)] for _ in chunks]
        spawn = stack.enter_context(patch("tools.verify_render_gateway.subprocess.Popen", return_value=process))
        stack.enter_context(patch("tools.verify_render_gateway.selectors.DefaultSelector", return_value=selector))
        read = stack.enter_context(patch("tools.verify_render_gateway.os.read", side_effect=chunks))
        stack.enter_context(patch("tools.verify_render_gateway.time.monotonic", side_effect=times))
        return process, selector, spawn, read

    def assert_observer_released(self, process, selector):
        selector.close.assert_called_once_with()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        process.stdout.close.assert_called_once_with()

    def test_late_backend_then_proxy_start_preserves_all_four_public_probes(self):
        request = Mock(side_effect=[
            ConnectionRefusedError(), ConnectionRefusedError(),
            (502, {}, b"proxy starting"), (200, {}, b'{"status":"ai_work"}'),
        ])
        with ExitStack() as stack:
            process, selector, _, read = self.startup_mocks(
                stack, chunks=[b"starting backend\n" + STARTED[:20], STARTED[20:]],
                # The supervisor event arrives at 12 seconds, after the old
                # public retry schedule would already have been exhausted.
                times=(0, 0, 12),
            )
            def observe(fd, size):
                request.assert_not_called()
                return [b"starting backend\n" + STARTED[:20], STARTED[20:]][read.call_count - 1]
            read.side_effect = observe
            sleep = stack.enter_context(patch("tools.verify_render_gateway.time.sleep"))
            stack.enter_context(patch("tools.verify_render_gateway.random.uniform", side_effect=[0.02, 0.09, 0.05]))
            await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
            response = await_work_health("owned-fixture", request)
        self.assertEqual(response[0], 200)
        self.assertEqual(request.call_args_list, [call("owned-fixture", "GET", "/health")] * 4)
        self.assertEqual(sleep.call_args_list, [call(0.52), call(1.09), call(2.05)])
        self.assertEqual(selector.select.call_args_list, [call(25), call(13)])

    def test_permanent_supervisor_failure_stops_at_first_event_with_safe_code(self):
        event = b'{"event":"hosted_operation_failed","code":"hosted_backend_not_ready"}\n'
        with ExitStack() as stack:
            process, selector, spawn, read = self.startup_mocks(stack, chunks=[event])
            sleep = stack.enter_context(patch("tools.verify_render_gateway.time.sleep"))
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_failed:hosted_backend_not_ready$"):
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
        read.assert_called_once_with(71, 4096)
        sleep.assert_not_called()
        self.assertEqual(spawn.call_args.args[0], ["docker", "logs", "--follow", "--tail", "8", "owned-fixture"])
        self.assertEqual(spawn.call_args.kwargs, {
            "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT, "close_fds": True,
        })

    def test_machine_shaped_private_value_cannot_be_echoed_as_failure_code(self):
        private = "rk_live_synthetic_private_fixture"
        event = json.dumps({"event": "hosted_operation_failed", "code": private}).encode() + b"\n"
        with ExitStack() as stack:
            process, selector, _, _ = self.startup_mocks(stack, chunks=[event])
            with self.assertRaises(RuntimeError) as caught:
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
        self.assertEqual(str(caught.exception), "integrated_work_supervisor_failed:unclassified")
        self.assertNotIn(private, str(caught.exception))

    def test_single_observation_deadline_closes_and_reaps_without_retries(self):
        with ExitStack() as stack:
            process, selector, _, read = self.startup_mocks(stack, chunks=[], events=[[]])
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_startup_timeout$"):
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
        selector.select.assert_called_once_with(25)
        read.assert_not_called()

    def test_exit_before_start_is_failure_and_already_exited_observer_is_reaped(self):
        with ExitStack() as stack:
            process, selector, _, _ = self.startup_mocks(stack, chunks=[b""])
            process.poll.return_value = 0
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_exited_before_start$"):
                await_work_supervisor("owned-fixture")
        selector.close.assert_called_once_with()
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=2)
        process.stdout.close.assert_called_once_with()

    def test_wrong_mode_event_cannot_start_public_health_probes(self):
        wrong = STARTED.replace(b"ai-work", b"maintenance")
        with ExitStack() as stack:
            process, selector, _, _ = self.startup_mocks(stack, chunks=[wrong])
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_event_invalid$"):
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)

    def test_oversized_line_is_bounded_without_echoing_raw_log(self):
        with ExitStack() as stack:
            process, selector, _, read = self.startup_mocks(
                stack, chunks=[b"synthetic-private-" * 200, b"x" * 4096], times=(0, 0, 1),
            )
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_log_limit$"):
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
        self.assertEqual(read.call_count, 2)

    def test_many_small_log_lines_have_a_total_byte_ceiling(self):
        with ExitStack() as stack:
            process, selector, _, read = self.startup_mocks(
                stack, chunks=[b"[]\n" * 1365] * 5, times=(0, 0, 1, 2, 3, 4),
            )
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_supervisor_log_limit$"):
                await_work_supervisor("owned-fixture")
            self.assert_observer_released(process, selector)
        self.assertEqual(read.call_count, 5)

    def test_public_health_exhaustion_stops_after_exactly_four_attempts(self):
        request = Mock(side_effect=ConnectionRefusedError())
        with patch("tools.verify_render_gateway.time.sleep") as sleep, patch(
            "tools.verify_render_gateway.random.uniform", return_value=0.05,
        ):
            with self.assertRaisesRegex(RuntimeError, "^integrated_work_startup_timeout$"):
                await_work_health("owned-fixture", request)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(0.55), call(1.05), call(2.05)])

    def test_stuck_observer_has_one_finite_termination_escalation(self):
        with ExitStack() as stack:
            process, selector, _, _ = self.startup_mocks(stack, chunks=[STARTED])
            process.wait.side_effect = [subprocess.TimeoutExpired("docker logs", 2), 0]
            await_work_supervisor("owned-fixture")
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_args_list, [call(timeout=2), call(timeout=2)])
        process.stdout.close.assert_called_once_with()
        selector.close.assert_called_once_with()

    def test_actual_local_observer_stream_is_closed_and_child_is_reaped(self):
        # One short local process exercises real selector/pipe cleanup. It has
        # no Docker, socket, provider or account access and no inherited env.
        original_spawn = subprocess.Popen
        owned = []
        def spawn(_arguments, **kwargs):
            child = original_spawn([
                sys.executable, "-I", "-c",
                "import time; print(" + repr(STARTED.decode().strip()) + ", flush=True); time.sleep(20)",
            ], env={}, **kwargs)
            owned.append(child)
            return child
        with patch("tools.verify_render_gateway.subprocess.Popen", side_effect=spawn):
            await_work_supervisor("owned-fixture")
        self.assertEqual(len(owned), 1)
        self.assertIsNotNone(owned[0].poll())
        self.assertTrue(owned[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
