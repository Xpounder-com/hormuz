"""Replay the retained development tool calls without model inference.

Exit 1 means the independent repair checker still fails. This is a diagnosis
loop, not a qualification receipt or a replay of a model conversation.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[3]
EVIDENCE = REPOSITORY / "docs/campaigns/local-repair-20261016-v5"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    pins = json.loads((EVIDENCE / "pins.json").read_text())
    client = Path(pins["client"]["path"])
    if hashlib.sha256(client.read_bytes()).hexdigest() != pins["client"]["sha256"]:
        raise RuntimeError("Pinned native client changed")
    case = json.loads((EVIDENCE / "corpus.json").read_text())["entries"][0]
    if case["case_id"] != "dev-clamp":
        raise RuntimeError("Unexpected retained development case")
    events = [json.loads(line) for line in (EVIDENCE / "development-native.jsonl").read_text().splitlines()]
    commands = [event["item"]["command"] for event in events
                if event["type"] == "item.completed" and event.get("item", {}).get("type") == "command_execution"]
    expected = ["/bin/zsh -lc \"cat solution.py | rg 'def clamp'\"",
                "/bin/zsh -lc apply_patch", "/bin/zsh -lc apply_patch"]
    if commands != expected:
        raise RuntimeError("Captured commands differ from the reviewed replay allowlist")
    if arguments.minimal:
        commands = commands[1:2]
    with tempfile.TemporaryDirectory(prefix="hormuz-537-patch-replay-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        binary = root / "bin"
        binary.mkdir()
        (binary / "apply_patch").symlink_to(client)
        ripgrep = shutil.which("rg")
        if ripgrep:
            (binary / "rg").symlink_to(ripgrep)
        implementation = project / "solution.py"
        implementation.write_text(case["files"]["solution.py"])
        oracle = root / "oracle.py"
        oracle.write_text(case["checks"])
        oracle.chmod(0o400)
        before = implementation.read_bytes()
        environment = {"HOME": str(root), "CODEX_HOME": str(root / ".codex"),
                       "TMPDIR": str(root), "PATH": str(binary) + ":/opt/homebrew/bin:/usr/bin:/bin",
                       "LANG": "en_US.UTF-8", "OTEL_SDK_DISABLED": "true"}
        outputs = []
        for command in commands:
            result = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)",
                                     "/bin/zsh", "-c", command], cwd=project, env=environment,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
            outputs.append({"command": command, "exit_code": result.returncode,
                            "output": (result.stdout + result.stderr).strip()})
        wrapper = ("import runpy,sys\noracle=sys.argv.pop(1)\nsys.argv[0]=oracle\ntry:\n"
                   " runpy.run_path(oracle,run_name='__main__')\nexcept AssertionError:\n sys.exit(1)\n"
                   "except BaseException:\n sys.exit(2)\n")
        check = subprocess.run(["/usr/bin/sandbox-exec", "-p",
                                "(version 1)(allow default)(deny network*)(deny file-write*)",
                                sys.executable, "-I", "-B", "-c", wrapper, str(oracle), str(implementation)],
                               cwd=root, env=environment, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=2)
        report = {"native_model_inferences": 0, "commands": outputs,
                  "implementation_changed": implementation.read_bytes() != before,
                  "independent_check_exit_code": check.returncode,
                  "oracle_unchanged": hashlib.sha256(oracle.read_bytes()).hexdigest() == case["checks_sha256"]}
        if arguments.output:
            arguments.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        if not report["oracle_unchanged"] or check.returncode not in (0, 1):
            return 2
        return check.returncode


if __name__ == "__main__":
    raise SystemExit(main())
