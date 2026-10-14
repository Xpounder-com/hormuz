"""Launch the separately installed Agent terminal without gateway configuration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys


_CLEANUP_SECONDS = 5
_OFFLINE_COMMANDS = frozenset({"--help", "-h", "--version", "init", "doctor", "sessions"})


class AgentLaunchError(ValueError):
    pass


def add_agent_commands(subparsers: argparse._SubParsersAction) -> None:
    agent = subparsers.add_parser(
        "agent", help="Open the supervised Codex and Claude Agent terminal", add_help=False
    )
    agent.add_argument("agent_arguments", nargs=argparse.REMAINDER)


def _source_entry() -> Path | None:
    # Anchor discovery to this installed module, never the caller's directory or
    # an environment-supplied script. Wheels do not contain this source entry.
    root = Path(__file__).resolve().parents[2]
    candidate = root / "clients/agent-runtime/bin/hormuz-agent.mjs"
    if not (root / "pyproject.toml").is_file() or not candidate.is_file():
        return None
    entry = candidate.resolve()
    if not entry.is_relative_to(root):
        raise AgentLaunchError("The checkout's Agent entry must remain inside the Hormuz checkout.")
    return entry


def _executable(name: str) -> str | None:
    executable = shutil.which(name)
    # Relative PATH entries can resolve executables from the ambient workspace.
    if not executable or not Path(executable).is_absolute():
        return None
    return str(Path(executable).resolve())


def _launch_command(argv: list[str]) -> list[str]:
    entry = _source_entry()
    installed = None if entry else _executable("hormuz-agent")
    if not entry and not installed:
        raise AgentLaunchError(
            "Agent runtime is not installed. Install the package providing 'hormuz-agent', "
            "or use a Hormuz checkout and run 'npm ci --prefix clients/agent-runtime'."
        )
    node = _executable("node")
    if not node:
        raise AgentLaunchError("Agent terminal requires Node.js 24 or later. Install Node.js and retry.")
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AgentLaunchError("Unable to verify Node.js. Install Node.js 24 or later and retry.") from None
    match = re.fullmatch(r"v([0-9]{1,3})\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\s*", version.stdout)
    if version.returncode != 0 or not match or int(match.group(1)) < 24:
        raise AgentLaunchError("Agent terminal requires Node.js 24 or later. Upgrade Node.js and retry.")
    if entry:
        runtime = entry.parent.parent
        # These explicit operations never import/query the SDK. Leave full Agent
        # option parsing to Node and keep the interactive preflight conservative.
        offline = bool(argv) and argv[0] in _OFFLINE_COMMANDS
        if not offline and not (runtime / "node_modules/@anthropic-ai/claude-agent-sdk/package.json").is_file():
            raise AgentLaunchError(
                "Agent runtime dependencies are missing. From the Hormuz checkout run "
                "'npm ci --prefix clients/agent-runtime' and retry."
            )
        return [node, str(entry)]
    if Path(installed).suffix.lower() in {".bat", ".cmd"}:
        # Windows can implicitly execute these through cmd.exe even with
        # shell=False; do not expose raw Agent arguments to shell expansion.
        raise AgentLaunchError(
            "This launcher requires a direct 'hormuz-agent' executable. "
            "Use a Hormuz source checkout with Node.js 24+ instead of a Windows shell shim."
        )
    return [installed]


def _exit_code(returncode: int) -> int:
    if 0 <= returncode <= 255:
        return returncode
    if -127 <= returncode < 0:
        return 128 - returncode
    return 1


def _stop_child(child: subprocess.Popen, *, interrupted: bool) -> None:
    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        pass  # A caller outside the main thread cannot install signal handlers.
    try:
        actions = (
            (lambda: child.send_signal(signal.SIGINT), child.terminate, child.kill)
            if interrupted and os.name == "posix" else (child.terminate, child.kill)
        )
        for action in actions:
            try:
                action()
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=_CLEANUP_SECONDS)
                return
            except subprocess.TimeoutExpired:
                continue
        raise AgentLaunchError("Agent process did not stop after bounded cleanup.")
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)


def run(argv: list[str], *, require_tty: bool = False, _replace_process: bool = True) -> int:
    if require_tty and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        print(
            "Agent terminal requires an interactive terminal. Run 'hormuz' in a terminal, "
            "or use 'hormuz agent --help' for explicit Agent commands.",
            file=sys.stderr,
        )
        return 2
    try:
        command = [*_launch_command(argv), *argv]
        if os.name == "posix" and _replace_process:
            # Node owns the foreground terminal and SIGINT directly, allowing
            # active work to stop while its REPL remains open. No parent sends a
            # second SIGINT or terminates the host after that interruption.
            os.execv(command[0], command)
            return 0  # Successful exec never returns; reachable only in tests.
        # Windows and trusted test callers use the supervised fallback. There is
        # no CLI option or environment override for selecting this path.
        child = subprocess.Popen(command, shell=False, stdin=None, stdout=None, stderr=None, cwd=None)
    except AgentLaunchError as error:
        print(f"Agent launch error: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("Agent launch error: unable to start the installed runtime. Check its permissions and retry.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    interrupted = False
    result = 2
    try:
        result = _exit_code(child.wait())
    except KeyboardInterrupt:
        interrupted = True
        result = 130
    except OSError:
        print("Agent launch error: unable to wait for the runtime.", file=sys.stderr)
    finally:
        try:
            if child.poll() is None:
                _stop_child(child, interrupted=interrupted)
        except (OSError, AgentLaunchError):
            print("Agent launch error: runtime cleanup failed.", file=sys.stderr)
            result = 2
    return result
