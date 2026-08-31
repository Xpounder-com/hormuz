#!/usr/bin/env python3
"""Verify the built Hormuz distribution and isolated wheel boundaries."""

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
    "deploy/compose/runtime/",
)
REQUIRED_COMPOSE_SDIST_PATHS = (
    "deploy/compose/README.md",
    "deploy/compose/compose.external-postgres.yaml",
    "deploy/compose/compose.verify.yaml",
    "deploy/compose/compose.yaml",
    "deploy/compose/hormuz-compose",
    "deploy/compose/hormuz.example.json",
    "deploy/compose/scripts/prepare.sh",
    "deploy/compose/scripts/postgres-entrypoint.sh",
    "deploy/compose/verification/fake_provider.py",
    "tools/verify_compose_profile.py",
    "tools/verify_compose_profile.sh",
)
REQUIRED_HELM_SDIST_PATHS = (
    "deploy/helm/hormuz/Chart.yaml",
    "deploy/helm/hormuz/values.schema.json",
    "deploy/helm/hormuz/values.yaml",
    "deploy/helm/hormuz/templates/deployment.yaml",
    "deploy/helm/hormuz/templates/networkpolicy.yaml",
    "deploy/kubernetes/README.md",
    "deploy/kubernetes/conformance/cilium-values.yaml",
    "deploy/kubernetes/conformance/helm-values.yaml",
    "deploy/kubernetes/conformance/kind.yaml",
    "deploy/kubernetes/conformance/probes.yaml",
    "deploy/kubernetes/conformance/postgres-ha/README.md",
    "deploy/kubernetes/conformance/postgres-ha/cluster.yaml",
    "deploy/kubernetes/conformance/postgres-ha/bootstrap-job.yaml",
    "deploy/kubernetes/conformance/postgres-ha/bootstrap.py",
    "deploy/kubernetes/conformance/postgres-ha/helm-values.yaml",
    "deploy/kubernetes/conformance/postgres-ha/kind.yaml",
    "deploy/kubernetes/conformance/postgres-ha/probes.yaml",
    "deploy/kubernetes/conformance/postgres-ha/state-config.json",
    "deploy/kubernetes/conformance/postgres-ha/state_probe.py",
    "tools/verify_helm_profile.py",
    "tools/verify_helm_profile.sh",
    "tests/test_helm_lifecycle_helpers.py",
    "tools/verify_multi_replica_operation.py",
    "tools/verify_postgres_ha_reference.py",
    "tools/verify_postgres_ha_reference.sh",
)
REQUIRED_POLICY_ADMIN_USABILITY_SDIST_PATHS = (
    "config.example.json",
    "docs/POLICY_ADMIN_USABILITY.md",
    "examples/policy-admin-usability-baseline.json",
    "examples/policy-admin-usability-scenarios.json",
    "requirements/v1-source-build.lock",
    "tools/promote_v1_candidate.sh",
    "tools/run_v1_internal_repeatability.py",
    "tools/set_v1_release_publisher_secret.zsh",
    "tools/v1_candidate.py",
    "tools/verify_policy_admin_usability_evidence.py",
    "tools/verify_v1_internal_repeatability_evidence.py",
)
REQUIRED_REGISTRY_PREFLIGHT_SDIST_PATHS = (
    "docs/REGISTRY.md",
    "docs/REGISTRY_TRANSITION.md",
    "docs/registry-transition-plan-v1.json",
    "docs/registry-transition-plan-v2.json",
    "hormuz/portfolio-registry-wire-v1.json",
    "tests/_portfolio_fixture.py",
    "tests/test_sqlite_portfolio_registry.py",
    "tests/test_postgres_portfolio_registry.py",
    "tests/test_portfolio_api_cli.py",
    "docs/portfolio-intelligence-contract-v1.json",
    "docs/portfolio-intelligence-wire-v1.json",
    "tools/verify_registry_transition_plan.py",
    "tools/verify_core_wheel.py",
    "tools/verify_portfolio_intelligence_contract.py",
    "tools/_portfolio_wire_contract.py",
    "tools/v1_candidate.py",
    "tests/_registry_transition_fixture.py",
    "tests/_postgres_fixture.py",
    "tests/test_registry_transition_plan.py",
    "tests/test_sqlite_registry_transition.py",
    "tests/test_postgres_registry_transition.py",
    "tests/test_postgres_test_boundaries.py",
    "tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json",
)
REQUIRED_ATTRIBUTION_PREFLIGHT_SDIST_PATHS = (
    "docs/ATTRIBUTION.md",
    "docs/ATTRIBUTION_TRANSITION.md",
    "docs/attribution-transition-plan-v1.json",
    "docs/attribution-transition-plan-v2.json",
    "hormuz/portfolio-attribution-wire-v1.json",
    "hormuz/migrations/postgresql/0010_governed_run_attribution.sql",
    "tools/verify_attribution_transition_plan.py",
    "tests/_attribution_fixture.py",
    "tests/_attribution_gateway_fixture.py",
    "tests/_attribution_predecessor_fixture.py",
    "tests/test_attribution_admission.py",
    "tests/test_attribution_schema.py",
    "tests/test_sqlite_attribution.py",
    "tests/test_postgres_attribution.py",
    "tests/test_attribution_gateway.py",
    "tests/test_postgres_attribution_gateway.py",
    "tests/test_attribution_transition_plan.py",
    "tests/test_sqlite_attribution_transition.py",
    "tests/test_postgres_attribution_transition.py",
)
REQUIRED_OUTCOME_PREFLIGHT_SDIST_PATHS = (
    "docs/OUTCOMES.md",
    "docs/OUTCOME_TRANSITION.md",
    "docs/outcome-transition-plan-v1.json",
    "docs/outcome-transition-plan-v2.json",
    "hormuz/portfolio-outcome-wire-v1.json",
    "hormuz/migrations/postgresql/0011_work_outcomes.sql",
    "hormuz/_outcome_schema.py",
    "hormuz/outcome_wire.py",
    "hormuz/outcome_ingest.py",
    "hormuz/outcome_repository.py",
    "tests/_outcome_fixture.py",
    "tests/test_outcome_contract.py",
    "tests/test_outcome_ingest.py",
    "tests/test_outcome_schema.py",
    "tests/test_sqlite_outcomes.py",
    "tests/test_postgres_outcomes.py",
    "tools/verify_outcome_transition_plan.py",
    "tests/_outcome_predecessor_fixture.py",
    "tests/test_outcome_transition_plan.py",
    "tests/test_sqlite_outcome_transition.py",
    "tests/test_postgres_outcome_transition.py",
)
REQUIRED_FINANCE_PREFLIGHT_SDIST_PATHS = (
    "docs/FINANCE_TRANSITION.md",
    "docs/finance-transition-plan-v1.json",
    "docs/finance-source-contract-v1.json",
    "tools/verify_finance_transition_plan.py",
    "tests/_finance_predecessor_fixture.py",
    "tests/test_finance_transition_plan.py",
    "tests/test_sqlite_finance_transition.py",
    "tests/test_postgres_finance_transition.py",
)
REQUIRED_FINANCE_VALUES_SDIST_PATHS = (
    "docs/FINANCE_VALUES.md",
    "hormuz/finance_values.py",
    "hormuz/finance_usage.py",
    "hormuz/finance_rate_cards.py",
    "tests/_finance_values_fixture.py",
    "tests/test_finance_values.py",
    "tests/test_finance_usage.py",
    "tests/test_finance_rate_cards.py",
    "tests/test_finance_packaging.py",
)
REQUIRED_FINANCE_HISTORY_SDIST_PATHS = (
    "docs/FINANCE_RATE_CARDS.md",
    "docs/finance-transition-plan-v2.json",
    "hormuz/_finance_schema.py",
    "hormuz/finance_repository.py",
    "hormuz/migrations/postgresql/0012_finance_rate_cards.sql",
    "tests/_finance_fixture.py",
    "tests/test_sqlite_finance.py",
    "tests/test_postgres_finance.py",
)
REQUIRED_PORTFOLIO_EXTENSION_SDIST_PATHS = (
    "docs/portfolio-extension-contract-v1.json",
    "docs/work-budget-reports-wire-v1.json",
    "docs/linear-context-wire-v1.json",
    "docs/PORTFOLIO_EXTENSIONS.md",
    "docs/decisions/0011-additive-budget-reports-and-linear-context.md",
    "tests/fixtures/portfolio_intelligence/extension-v1-examples.json",
    "tests/fixtures/portfolio_intelligence/wire-v1-examples.json",
    "tools/verify_portfolio_extensions.py",
    "tools/_portfolio_wire_contract.py",
    "tools/verify_core_wheel.py",
    "tests/test_portfolio_extensions.py",
    "tests/test_portfolio_extension_packaging.py",
    "docs/portfolio-intelligence-contract-v1.json",
    "docs/portfolio-intelligence-wire-v1.json",
    "hormuz/portfolio-registry-wire-v1.json",
    "hormuz/portfolio-attribution-wire-v1.json",
    "hormuz/portfolio-outcome-wire-v1.json",
    "docs/finance-transition-plan-v1.json",
    "docs/finance-source-contract-v1.json",
    "tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json",
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
    _assert_compose_sdist_boundary(sdist)
    _assert_helm_sdist_boundary(sdist)
    _assert_policy_admin_usability_sdist_boundary(sdist)
    _assert_registry_preflight_sdist_boundary(sdist)
    _assert_attribution_preflight_sdist_boundary(sdist)
    _assert_outcome_preflight_sdist_boundary(sdist)
    _assert_finance_preflight_sdist_boundary(sdist)
    _assert_finance_values_sdist_boundary(sdist)
    _assert_finance_history_sdist_boundary(sdist)
    _assert_portfolio_extension_sdist_boundary(sdist)
    _verify_isolated_install(wheel, config, python)
    print(
        "verified core distribution boundary: no context/runtime data and complete deployment/usability assets"
    )
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


def _assert_compose_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required
        for required in REQUIRED_COMPOSE_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(
            f"Compose profile incomplete in {path.name}: {', '.join(sorted(missing))}"
        )


def _assert_helm_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required
        for required in REQUIRED_HELM_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(
            f"Helm profile incomplete in {path.name}: {', '.join(sorted(missing))}"
        )


def _assert_policy_admin_usability_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required
        for required in REQUIRED_POLICY_ADMIN_USABILITY_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(
            "Policy-administrator usability kit incomplete in "
            f"{path.name}: {', '.join(sorted(missing))}"
        )


def _assert_registry_preflight_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_REGISTRY_PREFLIGHT_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Registry preflight incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_attribution_preflight_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_ATTRIBUTION_PREFLIGHT_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Attribution preflight incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_outcome_preflight_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_OUTCOME_PREFLIGHT_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Outcome preflight incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_finance_preflight_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_FINANCE_PREFLIGHT_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Finance preflight incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_finance_values_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_FINANCE_VALUES_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Finance values incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_finance_history_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_FINANCE_HISTORY_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Finance history incomplete in {path.name}: {', '.join(sorted(missing))}")


def _assert_portfolio_extension_sdist_boundary(path: Path) -> None:
    members = tuple(name.lstrip("./") for name in _sdist_members(path))
    missing = [
        required for required in REQUIRED_PORTFOLIO_EXTENSION_SDIST_PATHS
        if not any(f"/{member}".endswith(f"/{required}") for member in members)
    ]
    if missing:
        raise RuntimeError(f"Portfolio extensions incomplete in {path.name}: {', '.join(sorted(missing))}")


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
            [python, "-I", "-m", "hormuz", "contract", "manifest"],
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
