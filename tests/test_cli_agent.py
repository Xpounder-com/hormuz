from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from hormuz import cli
from hormuz.commands import agent


class AgentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        # An unexpectedly unmocked POSIX exec must fail the test, never replace
        # the unittest process with a real Node host.
        self.enterContext(patch.object(agent.os, "execv", side_effect=AssertionError("POSIX exec must be mocked explicitly")))

    def _checkout(self, root: Path, *, dependencies: bool = True) -> Path:
        module = root / "hormuz/commands/agent.py"
        module.parent.mkdir(parents=True)
        module.write_text("", encoding="utf-8")
        (root / "pyproject.toml").write_text("", encoding="utf-8")
        entry = root / "clients/agent-runtime/bin/hormuz-agent.mjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        if dependencies:
            package = entry.parent.parent / "node_modules/@anthropic-ai/claude-agent-sdk/package.json"
            package.parent.mkdir(parents=True)
            package.write_text("{}", encoding="utf-8")
        return module

    def _child(self, returncode: int = 0) -> MagicMock:
        child = MagicMock(spec=subprocess.Popen)
        child.wait.return_value = returncode
        child.poll.return_value = returncode
        return child

    def test_bare_cli_and_agent_arguments_route_without_gateway_configuration(self) -> None:
        for argv, forwarded, require_tty in (
            ([], [], True),
            (["agent"], [], True),
            (["agent", "--help"], ["--help"], False),
            (["agent", "--config", "Agent config.json", "context-pack"], ["--config", "Agent config.json", "context-pack"], False),
        ):
            with self.subTest(argv=argv), patch.object(agent, "run", return_value=17) as launch, patch.object(cli.GatewayConfig, "load") as gateway:
                self.assertEqual(cli.main(argv), 17)
                launch.assert_called_once_with(forwarded, require_tty=require_tty)
                gateway.assert_not_called()

    def test_root_help_remains_root_help_and_lists_agent(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), patch.object(agent, "run") as launch, self.assertRaises(SystemExit) as exit:
            cli.main(["--help"])
        self.assertEqual(exit.exception.code, 0)
        self.assertIn("agent", output.getvalue())
        self.assertIn("serve", output.getvalue())
        launch.assert_not_called()

    def test_gateway_globals_cannot_become_agent_configuration(self) -> None:
        output = io.StringIO()
        with redirect_stderr(output), patch.object(agent, "run") as launch, patch.object(cli.GatewayConfig, "load") as gateway:
            self.assertEqual(cli.main(["--config", "gateway.json", "agent"]), 2)
        self.assertIn("Gateway --config", output.getvalue())
        launch.assert_not_called()
        gateway.assert_not_called()

    def test_legacy_commands_keep_their_parsing_and_normalization(self) -> None:
        parser = cli.build_parser()
        for arguments, command in (
            (["--config", "gateway.json", "serve"], "serve"),
            (["--config", "gateway.json", "status"], "status"),
            (["personal", "off", "--profile", "test-profile"], "personal"),
            (["work", "state"], "work"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(parser.parse_args(arguments).command, command)
        self.assertEqual(cli._normalize_command_argv(["--config", "gateway.json", "client-config", "codex"]), ["--config", "gateway.json", "client", "config", "codex"])

    def test_bare_non_tty_and_eof_do_not_start_a_runtime(self) -> None:
        for input_tty, output_tty in ((False, True), (True, False), (False, False)):
            with self.subTest(input_tty=input_tty, output_tty=output_tty), patch.object(agent.sys.stdin, "isatty", return_value=input_tty), patch.object(agent.sys.stdout, "isatty", return_value=output_tty), patch.object(agent, "_launch_command") as resolve, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(agent.run([], require_tty=True), 2)
                self.assertIn("interactive terminal", error.getvalue())
                resolve.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process replacement")
    def test_source_launch_preserves_raw_arguments_cwd_stdio_and_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hormuz cli ; ") as temporary:
            root = Path(temporary).resolve()
            module = self._checkout(root)
            child = self._child()
            raw = ["--config", "file with spaces.json", "$(touch SHOULD_NOT_RUN)", ";echo nope", "--", "a\nb"]
            before = Path.cwd()
            with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", side_effect=lambda name: "/opt/tools/node" if name == "node" else "/opt/tools/hormuz-agent") as which, patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="v24.13.0\n")) as probe, patch.object(agent.os, "execv", return_value=None) as replace, patch.object(agent.subprocess, "Popen", return_value=child) as spawn:
                self.assertEqual(agent.run(raw), 0)
            which.assert_called_once_with("node")
            probe.assert_called_once_with(["/opt/tools/node", "--version"], capture_output=True, text=True, check=False, timeout=5)
            replace.assert_called_once_with("/opt/tools/node", ["/opt/tools/node", str(root / "clients/agent-runtime/bin/hormuz-agent.mjs"), *raw])
            spawn.assert_not_called()
            self.assertEqual(Path.cwd(), before)
            child.wait.assert_not_called()
            child.terminate.assert_not_called()

    def test_bare_interactive_launch_and_explicit_non_tty_commands_are_supported(self) -> None:
        for arguments, require_tty, is_tty in (([], True, True), (["--help"], False, False)):
            child = self._child()
            with self.subTest(arguments=arguments), patch.object(agent.sys.stdin, "isatty", return_value=is_tty), patch.object(agent.sys.stdout, "isatty", return_value=is_tty), patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child):
                self.assertEqual(agent.run(arguments, require_tty=require_tty, _replace_process=False), 0)

    def test_installed_runtime_fallback_uses_only_explicit_path_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            child = self._child(7)
            module = Path(temporary) / "site-packages/hormuz/commands/agent.py"
            with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", side_effect=lambda name: {"node": "/opt/tools/node", "hormuz-agent": "/opt/tools/hormuz-agent"}.get(name)), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="v25.1.0\n")), patch.object(agent.subprocess, "Popen", return_value=child) as spawn:
                self.assertEqual(agent.run(["--help"], _replace_process=False), 7)
            self.assertEqual(spawn.call_args.args[0], ["/opt/tools/hormuz-agent", "--help"])

    @unittest.skipUnless(os.name == "posix", "POSIX process replacement")
    def test_posix_installed_runtime_replaces_python_and_preserves_raw_arguments(self) -> None:
        arguments = ["--prompt", "Spaces ; $(literal) and\nnewlines", "--", "--help"]
        before = Path.cwd()
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.os, "execv", return_value=None) as replace, patch.object(agent.subprocess, "Popen") as spawn, patch.object(agent.signal, "signal") as signal_handler:
            self.assertEqual(agent.run(arguments), 0)
        replace.assert_called_once_with("/opt/tools/hormuz-agent", ["/opt/tools/hormuz-agent", *arguments])
        spawn.assert_not_called()
        signal_handler.assert_not_called()
        self.assertEqual(Path.cwd(), before)

    @unittest.skipUnless(os.name == "posix", "POSIX process replacement")
    def test_posix_exec_failure_is_actionable_without_popen_fallback_or_retry(self) -> None:
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.os, "execv", side_effect=PermissionError("sensitive local path")) as replace, patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.run(["--help"]), 2)
        self.assertIn("Check its permissions", error.getvalue())
        self.assertNotIn("sensitive local path", error.getvalue())
        self.assertEqual(replace.call_count, 1)
        spawn.assert_not_called()

    def test_windows_launch_uses_the_supervised_fallback(self) -> None:
        child = self._child(7)
        with patch.object(agent, "_launch_command", return_value=["installed-agent.exe"]), patch.object(agent.os, "name", "nt"), patch.object(agent.os, "execv") as replace, patch.object(agent.subprocess, "Popen", return_value=child) as spawn:
            self.assertEqual(agent.run(["--help"]), 7)
        replace.assert_not_called()
        spawn.assert_called_once_with(["installed-agent.exe", "--help"], shell=False, stdin=None, stdout=None, stderr=None, cwd=None)
        child.wait.assert_called_once_with()

    def test_windows_interrupt_reaps_child_without_unsupported_sigint_forwarding(self) -> None:
        child = self._child()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch.object(agent, "_launch_command", return_value=["installed-agent.exe"]), patch.object(agent.os, "name", "nt"), patch.object(agent.subprocess, "Popen", return_value=child):
            self.assertEqual(agent.run([]), 130)
        child.send_signal.assert_not_called()
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_ambient_workspace_and_environment_scripts_are_never_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ambient = root / "ambient"
            ambient.mkdir()
            self._checkout(ambient)
            executable = ambient / "hormuz-agent"
            executable.write_text("malicious ambient script", encoding="utf-8")
            executable.chmod(0o700)
            original = Path.cwd()
            try:
                os.chdir(ambient)
                with patch.object(agent, "__file__", str(root / "wheel/hormuz/commands/agent.py")), patch.dict(os.environ, {"HORMUZ_AGENT_SCRIPT": str(executable), "HORMUZ_AGENT_ENTRY": str(executable)}), patch.object(agent.shutil, "which", return_value="hormuz-agent"), patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(agent.run(["--help"]), 2)
                    self.assertIn("Agent runtime is not installed", error.getvalue())
                    spawn.assert_not_called()
            finally:
                os.chdir(original)

    def test_source_entry_cannot_escape_the_module_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = self._checkout(root / "checkout")
            entry = root / "checkout/clients/agent-runtime/bin/hormuz-agent.mjs"
            outside = root / "ambient.mjs"
            outside.write_text("", encoding="utf-8")
            entry.unlink()
            entry.symlink_to(outside)
            with patch.object(agent, "__file__", str(module)), patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(agent.run(["--help"]), 2)
                self.assertIn("inside the Hormuz checkout", error.getvalue())
                spawn.assert_not_called()

    def test_missing_node_old_node_and_missing_dependencies_have_actionable_errors(self) -> None:
        for node, version, dependencies, expected in (
            (None, "v24.0.0\n", True, "Install Node.js"),
            ("/opt/tools/node", "v23.9.0\n", True, "Upgrade Node.js"),
            ("/opt/tools/node", "invalid version", True, "Upgrade Node.js"),
            ("/opt/tools/node", "v24.0.0\n", False, "npm ci --prefix clients/agent-runtime"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                module = self._checkout(Path(temporary), dependencies=dependencies)
                with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", return_value=node), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=version)), patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(agent.run(["Investigate this project"] if not dependencies else ["--help"]), 2)
                    self.assertIn(expected, error.getvalue())
                    spawn.assert_not_called()

    def test_explicit_offline_commands_work_without_installed_sdk_dependencies(self) -> None:
        for arguments in (["--help"], ["-h"], ["--version"], ["init", "--model", "synthetic-model"], ["doctor", "--cwd", "Folder with spaces"], ["sessions"]):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                module = self._checkout(root, dependencies=False)
                child = self._child()
                with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", return_value="/opt/tools/node"), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="v24.0.0\n")) as probe, patch.object(agent.subprocess, "Popen", return_value=child) as spawn:
                    self.assertEqual(agent.run(arguments, _replace_process=False), 0)
                    probe.assert_called_once_with(["/opt/tools/node", "--version"], capture_output=True, text=True, check=False, timeout=5)
                    self.assertEqual(spawn.call_args.args[0], ["/opt/tools/node", str(root / "clients/agent-runtime/bin/hormuz-agent.mjs"), *arguments])

    def test_default_interactive_launch_still_requires_installed_sdk_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self._checkout(Path(temporary), dependencies=False)
            with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", return_value="/opt/tools/node"), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="v24.0.0\n")), patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(agent.run([]), 2)
                self.assertIn("dependencies are missing", error.getvalue())
                spawn.assert_not_called()

    def test_node_probe_timeout_and_launch_error_do_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self._checkout(Path(temporary))
            with patch.object(agent, "__file__", str(module)), patch.object(agent.shutil, "which", return_value="/opt/tools/node"), patch.object(agent.subprocess, "run", side_effect=subprocess.TimeoutExpired("node", 5)) as probe, patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()):
                self.assertEqual(agent.run(["--help"]), 2)
                self.assertEqual(probe.call_count, 1)
                spawn.assert_not_called()
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", side_effect=FileNotFoundError("sensitive local path")) as spawn, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.run(["--help"], _replace_process=False), 2)
            self.assertNotIn("sensitive local path", error.getvalue())
            self.assertEqual(spawn.call_count, 1)

    def test_windows_shell_shims_cannot_expand_forwarded_agent_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(agent, "__file__", str(Path(temporary) / "wheel/hormuz/commands/agent.py")), patch.object(agent.shutil, "which", side_effect=lambda name: "/opt/tools/node" if name == "node" else "/opt/tools/hormuz-agent.cmd"), patch.object(agent.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="v24.0.0\n")), patch.object(agent.subprocess, "Popen") as spawn, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(agent.run(["prompt", "& run unwanted command"]), 2)
                self.assertIn("Windows shell shim", error.getvalue())
                spawn.assert_not_called()

    def test_interrupt_forwards_signal_and_reaps_the_owned_child(self) -> None:
        child = self._child()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child):
            self.assertEqual(agent.run([], _replace_process=False), 130)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {"timeout": 5})
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_interrupt_cleanup_is_bounded_then_terminates_kills_and_reaps(self) -> None:
        child = self._child()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired("agent", 5), subprocess.TimeoutExpired("agent", 5), 0]
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child):
            self.assertEqual(agent.run([], _replace_process=False), 130)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_count, 4)
        self.assertTrue(all(call.kwargs == {"timeout": 5} for call in child.wait.call_args_list[1:]))

    def test_wait_failure_still_cleans_up_the_owned_child(self) -> None:
        child = self._child()
        child.poll.return_value = None
        child.wait.side_effect = [OSError("wait failed"), 0]
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child), redirect_stderr(io.StringIO()):
            self.assertEqual(agent.run(["--help"], _replace_process=False), 2)
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_cleanup_failure_is_visible_and_does_not_retry_indefinitely(self) -> None:
        child = self._child()
        child.poll.return_value = None
        child.wait.side_effect = [KeyboardInterrupt(), *[subprocess.TimeoutExpired("agent", 5) for _ in range(3)]]
        with patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child), redirect_stderr(io.StringIO()) as error:
            self.assertEqual(agent.run([], _replace_process=False), 2)
            self.assertIn("runtime cleanup failed", error.getvalue())
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_count, 4)

    def test_signal_and_invalid_exit_codes_are_sanitized(self) -> None:
        for returned, expected in ((0, 0), (17, 17), (-signal.SIGINT, 130), (-signal.SIGTERM, 143), (300, 1), (-300, 1)):
            child = self._child(returned)
            with self.subTest(returned=returned), patch.object(agent, "_launch_command", return_value=["/opt/tools/hormuz-agent"]), patch.object(agent.subprocess, "Popen", return_value=child):
                self.assertEqual(agent.run(["--help"], _replace_process=False), expected)


if __name__ == "__main__":
    unittest.main()
