#!/usr/bin/env python3
"""Verify that a built Hormuz wheel has no retired context implementation."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import zipfile
from pathlib import Path


FORBIDDEN_ARCHIVE_PATHS = (
    "hormuz/context.py",
    "hormuz/context/",
    "hormuz/mcp.py",
    "hormuz/benchmark_data/",
    "hormuz_context_experiment/",
    "docs/CONTEXT.md",
    "examples/context-records.jsonl",
    "experiments/context/",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--sdist", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python executable used to create the isolated virtual environment",
    )
    args = parser.parse_args(argv)

    wheel = args.wheel.resolve()
    sdist = args.sdist.resolve()
    config = args.config.resolve()
    python = args.python.resolve()
    _assert_archive_boundary(wheel, _wheel_members)
    _assert_archive_boundary(sdist, _sdist_members)
    _verify_isolated_install(wheel, config, python)
    print("verified core wheel boundary: no context implementation or initialization")
    return 0


def _assert_archive_boundary(path: Path, members) -> None:
    if not path.is_file():
        raise RuntimeError(f"distribution does not exist: {path}")
    forbidden = [name for name in members(path) if _is_forbidden_archive_path(name)]
    if forbidden:
        raise RuntimeError(f"retired context assets found in {path.name}: {', '.join(sorted(forbidden))}")


def _wheel_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _sdist_members(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def _is_forbidden_archive_path(name: str) -> bool:
    normalized = name.lstrip("./")
    return any(f"/{forbidden}" in f"/{normalized}" for forbidden in FORBIDDEN_ARCHIVE_PATHS)


def _verify_isolated_install(wheel: Path, config_template: Path, base_python: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hormuz-core-wheel-") as temporary:
        root = Path(temporary)
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        virtual_environment = root / "venv"
        subprocess.run(
            [base_python, "-m", "venv", str(virtual_environment)],
            check=True,
            cwd=root,
            env=environment,
        )
        python = virtual_environment / "bin" / "python"
        subprocess.run(
            [python, "-m", "pip", "install", str(wheel.resolve())],
            check=True,
            cwd=root,
            env=environment,
        )

        payload = json.loads(config_template.read_text(encoding="utf-8"))
        payload["database"] = str(root / "usage.sqlite3")
        payload["listen"]["host"] = "127.0.0.1"
        payload["listen"]["port"] = _available_port()
        config_path = root / "hormuz.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")

        help_result = subprocess.run(
            [python, "-I", "-m", "hormuz", "--help"],
            capture_output=True,
            check=False,
            cwd=root,
            env=environment,
            text=True,
        )
        if help_result.returncode != 0 or "context-pack" in help_result.stdout:
            raise RuntimeError("installed core wheel exposes the retired context command")

        manifest_result = subprocess.run(
            [python, "-I", "-m", "hormuz", "contract-manifest"],
            capture_output=True,
            check=False,
            cwd=root,
            env=environment,
            text=True,
        )
        if manifest_result.returncode != 0:
            raise RuntimeError("installed core wheel cannot print the policy/evidence manifest")
        try:
            manifest = json.loads(manifest_result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("installed core wheel emitted an invalid policy/evidence manifest") from error
        if (
            manifest.get("schema_id") != "hormuz.policy-evidence-manifest"
            or manifest.get("schema_version") != 1
        ):
            raise RuntimeError("installed core wheel emitted an unsupported policy/evidence manifest")

        legacy_result = subprocess.run(
            [python, "-I", "-m", "hormuz", "--config", str(config_path), "context-pack"],
            capture_output=True,
            check=False,
            cwd=root,
            env=environment,
            text=True,
        )
        if legacy_result.returncode != 2 or "context_experiment_moved" not in legacy_result.stderr:
            raise RuntimeError("installed core wheel does not return the stable context migration error")

        runner = root / "verify_startup.py"
        runner.write_text(
            textwrap.dedent(
                f"""
                import importlib.util
                import sys
                from pathlib import Path

                import hormuz
                from hormuz._secret_inventory import load_secret_inventory
                from hormuz.config import GatewayConfig
                from hormuz.server import GatewayServer

                root = Path({str(root)!r})
                assert importlib.util.find_spec("hormuz.context") is None
                package_root = Path(hormuz.__file__).resolve().parents[1]
                secret_inventory = load_secret_inventory(source_root=package_root)
                assert secret_inventory["schema_id"] == "hormuz.secret-inventory"
                assert secret_inventory["schema_version"] == 1
                config = GatewayConfig.load(
                    Path({str(config_path)!r}),
                    environ={{"HORMUZ_TOKEN": "test-identity-token"}},
                )
                server = GatewayServer(config)
                try:
                    assert (root / "usage.sqlite3").is_file()
                    assert not any("context" in path.name.lower() for path in root.iterdir())
                    assert not any(
                        name == "hormuz.context" or name.startswith("hormuz.context.")
                        for name in sys.modules
                    )
                finally:
                    server.server_close()
                """
            ),
            encoding="utf-8",
        )
        subprocess.run([python, "-I", str(runner)], check=True, cwd=root, env=environment)


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


if __name__ == "__main__":
    raise SystemExit(main())
