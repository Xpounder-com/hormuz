"""Policy command registration and execution.

The public CLI entry points, argv normalization, and cross-command error
conventions remain in :mod:`hormuz.cli`.  This module owns only the policy
command family and intentionally does not introduce a command framework.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, GatewayConfig, PolicyAnalysisContext, PolicyValidationContext
from ..contracts import (
    POLICY_COMPARISON_SCHEMA_ID,
    POLICY_CONTROL_STATUS_SCHEMA_ID,
    POLICY_DECISION_SCHEMA_ID,
    POLICY_EVALUATION_SCHEMA_ID,
    POLICY_HISTORY_SCHEMA_ID,
    POLICY_PREVIEW_SCHEMA_ID,
    contract_envelope,
)
from ..evidence import EvidenceStorageError
from ..policy import PolicyDecision, PolicyEngine
from ..policy_analysis import (
    PolicyAnalysisError,
    PolicyComparison,
    PolicyEvaluation,
    PolicyPreview,
    PolicyVersionIdentity,
    compare_policy_documents,
    evaluate_policy_scenario_suite,
    preview_policy_request,
)
from ..policy_control import PolicyControlService, load_policy_document
from ..policy_document import PolicyDocument, PolicyDocumentError
from ..policy_repository import (
    POLICY_HISTORY_DEFAULT_LIMIT,
    POLICY_HISTORY_MAX_LIMIT,
    PolicyActivation,
    PolicyControlError,
    PolicyControlStatus,
    PolicyHistory,
    PolicyVersionRecord,
)
from ..policy_scenarios import (
    PolicyScenarioError,
    add_policy_scenario_to_suite,
    create_policy_scenario,
    create_policy_scenario_suite,
    load_policy_scenario_suite,
    write_policy_evaluation,
    write_policy_scenario_suite,
)
from ..policy_templates import (
    PolicyTemplateError,
    create_policy_document,
    policy_templates as available_policy_templates,
)
from ..store import MonthlyTotals, StorageSchemaError, UsageRepository, UsageStore


_POLICY_DEMO_ORGANIZATION_ID = "demo-organization"
_POLICY_DEMO_ACTOR_ID = "demo-administrator"
_POLICY_DEMO_IDENTITY_ENV = "HORMUZ_POLICY_DEMO_IDENTITY"


@dataclass(frozen=True)
class PolicyCommandDependencies:
    """Narrow compatibility seam supplied by :mod:`hormuz.cli`.

    The factories remain explicit so existing CLI-level tests and callers can
    replace them without creating mutable module-global command state.
    """

    policy_control_service: Callable[[GatewayConfig], PolicyControlService]
    create_usage_store: Callable[..., UsageRepository]
    write_policy_document: Callable[..., None]


def add_policy_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    policy_control = subparsers.add_parser(
        "policy",
        help="Bootstrap and administer immutable tenant policy versions",
    )
    policy_control_subparsers = policy_control.add_subparsers(dest="policy_control_command", required=True)

    policy_check = policy_control_subparsers.add_parser(
        "check",
        help="Evaluate the active policy without sending a provider request",
    )
    _policy_request_arguments(policy_check)

    policy_control_subparsers.add_parser(
        "templates",
        help="List built-in policy templates without loading configuration",
    )

    policy_demo = policy_control_subparsers.add_parser(
        "demo",
        help="Run the zero-network policy administrator workflow",
    )
    policy_demo.add_argument(
        "--output",
        help="Create a new owner-only directory and retain the demo artifacts",
    )

    create = policy_control_subparsers.add_parser(
        "create",
        help="Create a complete policy document from a built-in template",
    )
    create.add_argument(
        "--template",
        default="standard",
        help="Built-in template name (default: standard; list with `policy templates`)",
    )
    create.add_argument(
        "--organization",
        help="Tenant organization ID (optional when exactly one is configured)",
    )
    create.add_argument(
        "--monthly-budget-usd",
        type=float,
        help="Optional organization monthly budget override in USD",
    )
    create.add_argument(
        "--per-actor-monthly-budget-usd",
        type=float,
        help="Optional per-actor monthly budget override in USD",
    )
    create.add_argument("--output", required=True, help="New policy-document JSON path")
    create.add_argument("--force", action="store_true", help="Replace an existing regular output file")

    validate = policy_control_subparsers.add_parser(
        "validate",
        help="Validate a policy file offline without credentials or PostgreSQL",
    )
    validate.add_argument("file", help="Policy-document JSON path")

    show = policy_control_subparsers.add_parser(
        "show",
        help="Print the active or selected immutable policy document",
    )
    _policy_control_auth_arguments(show)
    show.add_argument("--version", help="Immutable sha256 policy version (default: active)")

    history = policy_control_subparsers.add_parser(
        "history",
        help="Show the bounded policy lifecycle timeline",
    )
    _policy_control_auth_arguments(history)
    history.add_argument(
        "--limit",
        type=int,
        default=POLICY_HISTORY_DEFAULT_LIMIT,
        help=(
            f"Maximum newest lifecycle events to return "
            f"(default: {POLICY_HISTORY_DEFAULT_LIMIT}; max: {POLICY_HISTORY_MAX_LIMIT})"
        ),
    )
    history.add_argument("--json", action="store_true", help="Emit the versioned metadata-only JSON contract")

    export = policy_control_subparsers.add_parser(
        "export",
        help="Export the active or selected immutable policy document",
    )
    _policy_control_auth_arguments(export)
    export.add_argument("--version", help="Immutable sha256 policy version (default: active)")
    export.add_argument("--output", required=True, help="New policy-document JSON path")
    export.add_argument("--force", action="store_true", help="Replace an existing regular output file")

    compare = policy_control_subparsers.add_parser(
        "compare",
        help="Semantically compare a local or saved candidate with the active or selected baseline",
    )
    _policy_control_auth_arguments(compare)
    _policy_candidate_arguments(compare)
    _policy_baseline_arguments(compare)
    compare.add_argument("--json", action="store_true", help="Emit the versioned comparison contract")

    preview = policy_control_subparsers.add_parser(
        "preview",
        help="Preview one request against the pinned active policy and a candidate",
    )
    _policy_control_auth_arguments(preview)
    _policy_candidate_arguments(preview)
    _policy_baseline_arguments(preview)
    _policy_request_arguments(preview)
    preview.add_argument("--json", action="store_true", help="Emit the versioned preview contract")

    scenarios = policy_control_subparsers.add_parser(
        "scenarios",
        help="Create, extend, and validate portable policy request suites",
    )
    scenario_subparsers = scenarios.add_subparsers(dest="policy_scenarios_command", required=True)
    scenario_create = scenario_subparsers.add_parser(
        "create",
        help="Create a canonical scenario suite from one explicit request",
    )
    scenario_create.add_argument("--organization", required=True, help="Tenant organization ID")
    _policy_scenario_arguments(scenario_create)
    scenario_create.add_argument("--output", required=True, help="New scenario-suite JSON path")
    scenario_create.add_argument("--force", action="store_true", help="Replace an existing regular output file")

    scenario_add = scenario_subparsers.add_parser(
        "add",
        help="Atomically add one explicit request to a scenario suite",
    )
    scenario_add.add_argument("file", help="Existing scenario-suite JSON path")
    _policy_scenario_arguments(scenario_add)

    scenario_validate = scenario_subparsers.add_parser(
        "validate",
        help="Validate and identify a scenario suite without credentials or PostgreSQL",
    )
    scenario_validate.add_argument("file", help="Scenario-suite JSON path")

    evaluate = policy_control_subparsers.add_parser(
        "evaluate",
        help="Evaluate a saved scenario suite against two pinned policies",
    )
    _policy_control_auth_arguments(evaluate)
    _policy_candidate_arguments(evaluate)
    _policy_baseline_arguments(evaluate)
    evaluate.add_argument("--scenarios", required=True, help="Portable scenario-suite JSON path")
    evaluate.add_argument("--json", action="store_true", help="Emit the versioned evaluation contract")
    evaluate.add_argument("--output", help="Atomically save the versioned evaluation contract")
    evaluate.add_argument("--force", action="store_true", help="Replace an existing regular result file")

    bootstrap = policy_control_subparsers.add_parser(
        "bootstrap",
        help="Persist one-time configuration-seeded policy administrators",
    )
    _policy_control_auth_arguments(bootstrap)

    stage = policy_control_subparsers.add_parser("stage", help="Validate and stage an immutable policy document")
    _policy_control_auth_arguments(stage)
    stage.add_argument("--file", required=True, help="Policy-document JSON path")

    apply_policy = policy_control_subparsers.add_parser(
        "apply",
        help="Validate, stage, and atomically activate a policy document",
    )
    _policy_control_auth_arguments(apply_policy)
    apply_policy.add_argument("file", help="Policy-document JSON path")
    apply_policy.add_argument(
        "--if-active",
        help="Proceed only if this immutable sha256 policy version is active",
    )

    activate = policy_control_subparsers.add_parser("activate", help="Atomically activate a staged policy version")
    _policy_control_auth_arguments(activate)
    activate.add_argument("--version", required=True, help="Immutable sha256 policy version")
    activate.add_argument(
        "--if-active",
        help="Proceed only if this immutable sha256 policy version is active",
    )

    rollback = policy_control_subparsers.add_parser(
        "rollback",
        help="Undo the latest activation generation, or reactivate a selected prior version",
    )
    _policy_control_auth_arguments(rollback)
    rollback.add_argument(
        "--version",
        help="Previously active sha256 policy version (default: prior activation generation)",
    )
    rollback.add_argument(
        "--if-active",
        help="Proceed only if this immutable sha256 policy version is active",
    )

    policy_status = policy_control_subparsers.add_parser("status", help="Show tenant policy-control metadata")
    _policy_control_auth_arguments(policy_status)
    policy_status.add_argument("--json", action="store_true", help="Emit machine-readable metadata-only JSON")

    administrator = policy_control_subparsers.add_parser("administrator", help="Manage governed policy administrators")
    administrator_subparsers = administrator.add_subparsers(dest="policy_administrator_command", required=True)
    for action in ("grant", "revoke"):
        command = administrator_subparsers.add_parser(action, help=f"{action.title()} an OIDC policy administrator")
        _policy_control_auth_arguments(command)
        command.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
        command.add_argument("--subject", required=True, help="Stable OIDC subject")
    retire = administrator_subparsers.add_parser("retire", help="Retire a persisted bootstrap authority")
    retire_subparsers = retire.add_subparsers(dest="policy_administrator_retire_command", required=True)
    retire_static = retire_subparsers.add_parser(
        "static",
        help="Retire a persisted static bootstrap policy administrator",
    )
    _policy_control_auth_arguments(retire_static)
    retire_static.add_argument("--actor-id", required=True, help="Persisted static bootstrap actor ID")

    recover = policy_control_subparsers.add_parser(
        "recover",
        help="Recover OIDC policy authority only after every administrator is lost",
    )
    recover.add_argument("--organization", required=True, help="Tenant organization ID")
    recover.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
    recover.add_argument("--subject", required=True, help="Stable OIDC subject")
    recover.add_argument(
        "--reason-code",
        required=True,
        choices=["all_administrators_lost", "administrator_store_recovered"],
        help="Controlled recovery reason",
    )


def _policy_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True, help="Configured actor ID")
    parser.add_argument("--client", required=True, choices=["codex", "claude-code"])
    parser.add_argument("--protocol", required=True, choices=["openai", "anthropic"])
    parser.add_argument("--model", required=True, help="Company model alias")
    parser.add_argument("--max-output-tokens", type=int)


def _policy_scenario_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", required=True, help="Stable scenario ID")
    _policy_request_arguments(parser)


def _policy_candidate_arguments(parser: argparse.ArgumentParser) -> None:
    candidate = parser.add_mutually_exclusive_group(required=True)
    candidate.add_argument("file", nargs="?", help="Local policy-document JSON candidate")
    candidate.add_argument("--version", dest="candidate_version", help="Saved immutable candidate version")


def _policy_baseline_arguments(parser: argparse.ArgumentParser) -> None:
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument(
        "--baseline",
        dest="baseline_file",
        help="Local policy-document JSON baseline",
    )
    baseline.add_argument(
        "--against-version",
        help="Saved immutable baseline version (default without --baseline: active)",
    )


def _policy_control_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization", required=True, help="Tenant organization ID")
    parser.add_argument(
        "--credential-env",
        default="HORMUZ_POLICY_ADMIN_TOKEN",
        help="Environment variable holding an authenticated policy-admin credential",
    )


class PolicyDemoError(RuntimeError):
    """Content-safe failure for the zero-network administrator demo."""

    def __init__(self, code: str, reason: str, *, hint: str | None = None) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.hint = hint


@dataclass(frozen=True)
class PolicyDemoResult:
    artifact_directory: Path | None
    comparison_change_count: int
    scenario_count: int
    changed_count: int
    candidate_version_id: str


def _policy_demo(args: argparse.Namespace, dependencies: PolicyCommandDependencies) -> int:
    """Run the honest local administrator workflow and print managed next steps."""

    try:
        if args.output is None:
            with tempfile.TemporaryDirectory(prefix="hormuz-policy-demo-") as temporary:
                result = _run_policy_demo(Path(temporary), retain_artifacts=False, dependencies=dependencies)
        else:
            root = _create_policy_demo_directory(Path(args.output))
            result = _run_policy_demo(root, retain_artifacts=True, dependencies=dependencies)
    except PolicyDemoError:
        raise
    except (
        ConfigError,
        EvidenceStorageError,
        OSError,
        PolicyAnalysisError,
        PolicyControlError,
        PolicyDocumentError,
        PolicyScenarioError,
        PolicyTemplateError,
        StorageSchemaError,
        sqlite3.Error,
    ) as error:
        raise PolicyDemoError(
            "policy_demo_execution_failed",
            "the local policy workflow could not be completed",
            hint="Rerun the demo; use a new --output path if retaining artifacts.",
        ) from error
    _print_policy_demo(result)
    return 0


def _run_policy_demo(
    root: Path,
    *,
    retain_artifacts: bool,
    dependencies: PolicyCommandDependencies,
) -> PolicyDemoResult:
    """Exercise authoring and read-only analysis without managed state or network."""

    config_path = root / "hormuz.json"
    database_path = root / "usage.sqlite3"
    baseline_path = root / "baseline.json"
    candidate_path = root / "candidate.json"
    comparison_path = root / "comparison.json"
    scenarios_path = root / "scenarios.json"
    evaluation_path = root / "evaluation.json"

    _write_policy_demo_json(config_path, _policy_demo_config())
    analysis_context = GatewayConfig.load_policy_analysis_context(config_path)
    context: PolicyValidationContext = analysis_context
    baseline = create_policy_document(
        template_name="standard",
        context=context,
        organization_id=_POLICY_DEMO_ORGANIZATION_ID,
    )
    strict = create_policy_document(
        template_name="strict",
        context=context,
        organization_id=_POLICY_DEMO_ORGANIZATION_ID,
    )
    candidate_mapping = strict.to_mapping()
    policies = candidate_mapping.get("policies")
    if not isinstance(policies, dict):
        raise PolicyDemoError("policy_demo_candidate_invalid", "the generated candidate is invalid")
    organization_policy = policies.get("organization")
    if not isinstance(organization_policy, dict):
        raise PolicyDemoError("policy_demo_candidate_invalid", "the generated candidate is invalid")
    organization_policy["allowed_models"] = ["demo-fast"]
    candidate = PolicyDocument.from_mapping(candidate_mapping, config=context)

    dependencies.write_policy_document(baseline_path, baseline, force=False)
    dependencies.write_policy_document(candidate_path, candidate, force=False)
    baseline = load_policy_document(context, baseline_path)
    candidate = load_policy_document(context, candidate_path)

    comparison = compare_policy_documents(baseline, candidate)
    comparison_payload = _policy_comparison_payload(comparison)
    _write_policy_demo_json(comparison_path, comparison_payload)

    suite = create_policy_scenario_suite(
        organization_id=_POLICY_DEMO_ORGANIZATION_ID,
        scenario_id="output-cap",
        actor_id=_POLICY_DEMO_ACTOR_ID,
        client="codex",
        protocol="openai",
        requested_model="demo-fast",
        requested_output_tokens=8_000,
    ).with_scenario(
        create_policy_scenario(
            organization_id=_POLICY_DEMO_ORGANIZATION_ID,
            scenario_id="model-denial",
            actor_id=_POLICY_DEMO_ACTOR_ID,
            client="codex",
            protocol="openai",
            requested_model="demo-deep",
            requested_output_tokens=1_000,
        )
    )
    write_policy_scenario_suite(scenarios_path, suite, force=False)
    suite = load_policy_scenario_suite(scenarios_path)

    UsageStore(database_path)
    try:
        os.chmod(database_path, 0o600)
    except OSError as error:
        raise PolicyDemoError(
            "policy_demo_storage_unavailable",
            "the disposable SQLite store could not be protected",
        ) from error
    usage_store = _policy_analysis_usage_store(analysis_context, dependencies)
    if usage_store.monthly_totals(organization_id=_POLICY_DEMO_ORGANIZATION_ID) != MonthlyTotals():
        raise PolicyDemoError(
            "policy_demo_usage_not_zero",
            "the disposable SQLite store did not begin with zero current usage",
        )

    evaluation = evaluate_policy_scenario_suite(
        config=analysis_context,
        usage_store=usage_store,
        suite=suite,
        baseline=baseline,
        candidate=candidate,
    )
    _require_policy_demo_behavior(evaluation)
    write_policy_evaluation(
        evaluation_path,
        _policy_evaluation_payload(evaluation),
        force=False,
    )
    _protect_policy_demo_artifacts(root)
    return PolicyDemoResult(
        artifact_directory=root if retain_artifacts else None,
        comparison_change_count=len(comparison.changes),
        scenario_count=len(evaluation.scenarios),
        changed_count=evaluation.changed_count,
        candidate_version_id=candidate.version_id,
    )


def _require_policy_demo_behavior(evaluation: PolicyEvaluation) -> None:
    results = {result.scenario.scenario_id: result for result in evaluation.scenarios}
    output_cap = results.get("output-cap")
    model_denial = results.get("model-denial")
    if (
        output_cap is None
        or not output_cap.baseline_decision.allowed
        or not output_cap.candidate_decision.allowed
        or output_cap.baseline_decision.max_output_tokens != 16_000
        or output_cap.candidate_decision.max_output_tokens != 4_000
        or model_denial is None
        or not model_denial.baseline_decision.allowed
        or model_denial.candidate_decision.allowed
    ):
        raise PolicyDemoError(
            "policy_demo_behavior_mismatch",
            "the generated policies did not produce the documented model and output changes",
        )


def _policy_demo_config() -> dict[str, object]:
    return {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "./usage.sqlite3",
        "upstreams": {
            "openai": {
                "base_url": "https://unused.invalid",
                "api_key_env": "HORMUZ_POLICY_DEMO_UNUSED_PROVIDER_KEY",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": "https://unused.invalid",
                "api_key_env": "HORMUZ_POLICY_DEMO_UNUSED_ANTHROPIC_KEY",
            },
        },
        "authentication": {"oidc": {"issuers": []}},
        "identities": [
            {
                "token_env": _POLICY_DEMO_IDENTITY_ENV,
                "actor_id": _POLICY_DEMO_ACTOR_ID,
                "actor_name": "Demo Administrator",
                "team_id": "platform",
                "team_name": "Platform",
                "organization_id": _POLICY_DEMO_ORGANIZATION_ID,
                "identity_type": "human",
                "clearance": "confidential",
                "allowed_clients": ["codex"],
            }
        ],
        "model_routes": {
            "demo-fast": {"protocol": "openai", "upstream_model": "demo-fast"},
            "demo-deep": {"protocol": "openai", "upstream_model": "demo-deep"},
        },
        "egress_controls": {
            "secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []}
        },
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["demo-fast", "demo-deep"],
                "max_output_tokens": 16_000,
            },
            "teams": {},
            "actors": {},
        },
    }


def _create_policy_demo_directory(path: Path) -> Path:
    selected = path.expanduser().absolute()
    try:
        os.mkdir(selected, 0o700)
        os.chmod(selected, 0o700)
    except FileExistsError:
        raise PolicyDemoError(
            "policy_demo_output_exists",
            "the selected output path already exists",
            hint="Choose a new directory; policy demo never replaces an existing path.",
        ) from None
    except OSError:
        raise PolicyDemoError(
            "policy_demo_output_unavailable",
            "the owner-only output directory could not be created",
            hint="Choose a new path under an existing writable parent directory.",
        ) from None
    return selected


def _write_policy_demo_json(path: Path, value: dict[str, object]) -> None:
    try:
        serialized = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (OverflowError, RecursionError, ValueError):
        raise PolicyDemoError(
            "policy_demo_artifact_invalid",
            "a generated policy demo artifact could not be serialized",
        ) from None
    if len(serialized) > 1024 * 1024:
        raise PolicyDemoError(
            "policy_demo_artifact_too_large",
            "a generated policy demo artifact exceeds the 1 MiB limit",
        )
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        created = True
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("policy demo artifact write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except FileExistsError:
        raise PolicyDemoError(
            "policy_demo_artifact_exists",
            "a policy demo artifact path already exists",
            hint="Choose a new --output directory.",
        ) from None
    except OSError:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise PolicyDemoError(
            "policy_demo_artifact_unavailable",
            "a policy demo artifact could not be written",
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _protect_policy_demo_artifacts(root: Path) -> None:
    try:
        artifacts = tuple(root.iterdir())
    except OSError as error:
        raise PolicyDemoError(
            "policy_demo_artifact_unavailable",
            "the policy demo artifacts could not be inspected",
        ) from error
    for artifact in artifacts:
        try:
            target = os.lstat(artifact)
            if not stat.S_ISREG(target.st_mode):
                raise PolicyDemoError(
                    "policy_demo_artifact_not_regular",
                    "the policy demo produced a non-regular artifact",
                )
            os.chmod(artifact, 0o600)
        except PolicyDemoError:
            raise
        except OSError as error:
            raise PolicyDemoError(
                "policy_demo_artifact_unavailable",
                "a policy demo artifact could not be protected",
            ) from error


def _print_policy_demo(result: PolicyDemoResult) -> None:
    print("Hormuz zero-network policy administrator demo")
    print("PASS created standard baseline and strict local candidate policies")
    print("PASS validated both local policy documents")
    print(f"PASS semantic comparison found {result.comparison_change_count} policy changes")
    print(f"PASS created {result.scenario_count} explicit policy scenarios")
    print("PASS disposable SQLite current usage: 0 requests, 0 tokens, USD 0")
    print(
        f"PASS evaluated {result.scenario_count} scenarios with {result.changed_count} behavior changes: "
        "8000-token request uncapped -> capped at 4000; demo-deep allowed -> denied"
    )
    print("PASS network calls: 0; provider credentials: 0; policy mutations: 0")
    if result.artifact_directory is None:
        print("Temporary artifacts removed; rerun with --output DIRECTORY to inspect owner-only files")
        candidate_path = Path("policy-demo") / "candidate.json"
        print("Retain the local candidate before applying it:")
        print("  hormuz policy demo --output policy-demo")
    else:
        print(f"Owner-only artifacts retained in: {result.artifact_directory}")
        candidate_path = result.artifact_directory / "candidate.json"
    print("This policy-UX demo is not evidence that the enterprise v1 release gate is complete.")
    print("Managed next steps (shown only; never executed by this demo):")
    print(
        "  "
        + shlex.join(
            [
                "hormuz",
                "--config",
                "hormuz.json",
                "policy",
                "apply",
                str(candidate_path),
                "--organization",
                _POLICY_DEMO_ORGANIZATION_ID,
            ]
        )
    )
    print(
        "  "
        + shlex.join(
            [
                "hormuz",
                "--config",
                "hormuz.json",
                "policy",
                "history",
                "--organization",
                _POLICY_DEMO_ORGANIZATION_ID,
                "--limit",
                "20",
            ]
        )
    )
    print(
        "  "
        + shlex.join(
            [
                "hormuz",
                "--config",
                "hormuz.json",
                "policy",
                "rollback",
                "--organization",
                _POLICY_DEMO_ORGANIZATION_ID,
                "--if-active",
                result.candidate_version_id,
            ]
        )
    )


def _policy_check(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: PolicyCommandDependencies,
) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    store = dependencies.create_usage_store(config)
    decision = PolicyEngine(config, store).evaluate(
        identity=identity,
        client=args.client,
        protocol=args.protocol,
        requested_model=args.model,
        requested_output_tokens=args.max_output_tokens,
    )
    print(json.dumps(_policy_decision_contract(decision), indent=2))
    return 0 if decision.allowed else 3


def _policy_decision_contract(decision: PolicyDecision) -> dict[str, object]:
    return contract_envelope(
        POLICY_DECISION_SCHEMA_ID,
        {
            "allowed": decision.allowed,
            "action": decision.action,
            "reason": decision.reason,
            "requested_model": decision.requested_model,
            "resolved_alias": decision.resolved_alias,
            "routed_model": decision.route.upstream_model if decision.route else None,
            "max_output_tokens": decision.max_output_tokens,
            "policy_version": decision.policy_version,
        },
    )


def _policy_control(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: PolicyCommandDependencies,
) -> int:
    """Run a CLI command through the authenticated policy-control service."""

    command = args.policy_control_command
    if command == "validate":
        return _policy_validate(config, args.file)
    if command in {"compare", "preview", "evaluate"}:
        return _policy_analysis(config, args, dependencies)
    service = dependencies.policy_control_service(config)
    if command == "bootstrap":
        administrators = service.bootstrap(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        print(f"policy bootstrap initialized: organization={args.organization} administrators={len(administrators)}")
        return 0
    if command == "stage":
        version = service.stage(
            organization_id=args.organization,
            credential_env=args.credential_env,
            policy_path=args.file,
        )
        _print_policy_version("policy staged", version)
        return 0
    if command == "apply":
        activation = service.apply(
            organization_id=args.organization,
            credential_env=args.credential_env,
            policy_path=args.file,
            if_active_version_id=args.if_active,
        )
        _print_policy_activation("policy applied", activation)
        return 0
    if command == "activate":
        activation = service.activate(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
            if_active_version_id=args.if_active,
        )
        _print_policy_activation("policy activated", activation)
        return 0
    if command == "rollback":
        activation = service.rollback(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
            if_active_version_id=args.if_active,
        )
        _print_policy_activation("policy rolled back", activation)
        return 0
    if command == "status":
        status = service.status(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        _print_policy_status(status, as_json=args.json)
        return 0
    if command == "show":
        version = service.policy_version(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
        )
        print(json.dumps(version.document.to_mapping(), indent=2, sort_keys=True))
        return 0
    if command == "history":
        history = service.history(
            organization_id=args.organization,
            credential_env=args.credential_env,
            limit=args.limit,
        )
        _print_policy_history(history, as_json=args.json)
        return 0
    if command == "export":
        version = service.policy_version(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
        )
        try:
            dependencies.write_policy_document(
                Path(args.output).expanduser().absolute(),
                version.document,
                force=args.force,
            )
        except PolicyTemplateError as error:
            _print_policy_export_failure(error.code, error.reason, hint=error.hint)
            return 2
        _print_policy_version("policy exported", version)
        return 0
    if command == "administrator":
        if args.policy_administrator_command == "grant":
            administrator = service.grant_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "policy administrator granted: "
                f"organization={administrator.organization_id} issuer={administrator.issuer} subject={administrator.subject}"
            )
            return 0
        if args.policy_administrator_command == "revoke":
            service.revoke_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(f"policy administrator revoked: organization={args.organization} issuer={args.issuer} subject={args.subject}")
            return 0
        if (
            args.policy_administrator_command == "retire"
            and args.policy_administrator_retire_command == "static"
        ):
            service.revoke_static_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                actor_id=args.actor_id,
            )
            print(f"static policy administrator revoked: organization={args.organization} actor_id={args.actor_id}")
            return 0
    if command == "recover":
        try:
            recovery_secret = getpass.getpass("Hormuz break-glass recovery secret: ")
        except (EOFError, OSError):
            raise PolicyControlError("policy_break_glass_credential_unavailable") from None
        administrator = service.break_glass_recover(
            organization_id=args.organization,
            recovery_secret=recovery_secret,
            issuer=args.issuer,
            subject=args.subject,
            reason_code=args.reason_code,
        )
        print(
            "policy break-glass recovery completed: "
            f"organization={administrator.organization_id} issuer={administrator.issuer} subject={administrator.subject}"
        )
        return 0
    raise ConfigError("unsupported policy control command")


def _policy_analysis(
    config: GatewayConfig | PolicyAnalysisContext,
    args: argparse.Namespace,
    dependencies: PolicyCommandDependencies,
) -> int:
    """Compare or evaluate pinned policies through an explicit trust mode."""

    command = args.policy_control_command
    offline = isinstance(config, PolicyAnalysisContext)
    if offline and not _policy_analysis_is_offline(config, args):
        raise PolicyAnalysisError("policy_analysis_managed_mode_required")
    service: PolicyControlService | None = None
    if not offline:
        assert isinstance(config, GatewayConfig)
        service = dependencies.policy_control_service(config)
        # Authenticate and confirm persisted administrator authority before
        # any managed policy lookup or PostgreSQL usage-store construction.
        service.authorize(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )

    # Resolve and retain the baseline before the candidate. Managed versions
    # are immutable, and an activation racing this command cannot mix the
    # baseline across one preview or scenario-suite evaluation.
    baseline = _policy_baseline_document(service, config, args)
    candidate = _policy_candidate_document(service, config, args)

    if command == "compare":
        comparison = compare_policy_documents(baseline, candidate)
        _print_policy_comparison(comparison, as_json=args.json)
        return 0 if comparison.identical else 1
    if command == "preview":
        identity = config.identities_by_actor.get(args.actor)
        if identity is None:
            raise PolicyAnalysisError("policy_preview_actor_not_found")
        if identity.organization_id != args.organization:
            raise PolicyAnalysisError("policy_preview_actor_organization_mismatch")
        if args.max_output_tokens is not None and args.max_output_tokens < 1:
            raise PolicyAnalysisError("policy_preview_output_tokens_invalid")
        preview = preview_policy_request(
            config=config,
            usage_store=_policy_analysis_usage_store(config, dependencies),
            identity=identity,
            baseline=baseline,
            candidate=candidate,
            client=args.client,
            protocol=args.protocol,
            requested_model=args.model,
            requested_output_tokens=args.max_output_tokens,
        )
        _print_policy_preview(preview, as_json=args.json)
        return 0 if preview.candidate_decision.allowed else 3
    if command == "evaluate":
        if args.force and args.output is None:
            raise PolicyScenarioError(
                "policy_evaluation_output_required",
                "--force requires an explicit --output path",
                hint="Remove --force or select a regular result file with --output.",
            )
        suite = load_policy_scenario_suite(args.scenarios)
        if suite.organization_id != args.organization:
            raise PolicyAnalysisError("policy_evaluation_organization_mismatch")
        evaluation = evaluate_policy_scenario_suite(
            config=config,
            usage_store=_policy_analysis_usage_store(config, dependencies),
            suite=suite,
            baseline=baseline,
            candidate=candidate,
        )
        payload = _policy_evaluation_payload(evaluation)
        if args.output is not None:
            output_path = Path(args.output).expanduser().absolute()
            write_policy_evaluation(output_path, payload, force=args.force)
            print(f"policy evaluation saved: {output_path}", file=sys.stderr)
        _print_policy_evaluation(evaluation, payload=payload, as_json=args.json)
        return 0 if evaluation.identical else 1
    raise PolicyAnalysisError("policy_analysis_command_unsupported")


def _policy_analysis_requests_local_documents(args: argparse.Namespace) -> bool:
    """Return whether both policy inputs were explicitly selected as files."""

    return args.baseline_file is not None and args.candidate_version is None


def _policy_analysis_is_offline(
    config: GatewayConfig | PolicyAnalysisContext,
    args: argparse.Namespace,
) -> bool:
    """Return whether every policy and usage dependency is local and bounded."""

    return (
        isinstance(config, PolicyAnalysisContext)
        and _policy_analysis_requests_local_documents(args)
        and config.usage_storage.backend == "sqlite"
    )


def _policy_analysis_usage_store(
    config: GatewayConfig | PolicyAnalysisContext,
    dependencies: PolicyCommandDependencies,
) -> UsageRepository:
    """Open current usage without widening an offline context into runtime config."""

    if isinstance(config, GatewayConfig):
        return dependencies.create_usage_store(config, read_only=True)
    if config.usage_storage.backend != "sqlite":
        raise PolicyAnalysisError("policy_analysis_managed_mode_required")
    return UsageStore(
        config.database_path,
        audit_chain_maximum_anchor_age_seconds=(
            config.audit_chain.maximum_anchor_age_seconds if config.audit_chain is not None else None
        ),
        audit_chain_organization_ids=config.organization_ids,
        read_only=True,
    )


def _policy_baseline_document(
    service: PolicyControlService | None,
    config: GatewayConfig | PolicyAnalysisContext,
    args: argparse.Namespace,
) -> PolicyDocument:
    if args.baseline_file is not None:
        baseline = load_policy_document(config, args.baseline_file)
    else:
        if service is None:
            raise PolicyAnalysisError("policy_analysis_managed_mode_required")
        baseline = service.policy_version(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.against_version,
        ).document
    if baseline.organization_id != args.organization:
        raise PolicyAnalysisError("policy_baseline_organization_mismatch")
    return baseline


def _policy_candidate_document(
    service: PolicyControlService | None,
    config: GatewayConfig | PolicyAnalysisContext,
    args: argparse.Namespace,
) -> PolicyDocument:
    if args.candidate_version is not None:
        if service is None:
            raise PolicyAnalysisError("policy_analysis_managed_mode_required")
        candidate = service.policy_version(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.candidate_version,
        ).document
    else:
        candidate = load_policy_document(config, args.file)
    if candidate.organization_id != args.organization:
        raise PolicyAnalysisError("policy_candidate_organization_mismatch")
    return candidate


def _policy_validate(config: GatewayConfig | PolicyValidationContext, policy_path: str) -> int:
    """Validate one policy document without requiring managed-policy infrastructure."""

    try:
        document = load_policy_document(config, policy_path)
    except PolicyDocumentError as error:
        _print_policy_document_failure(error.code, error.reason, hint=error.hint)
        return 2
    except PolicyControlError as error:
        if error.code == "policy_document_unavailable":
            _print_policy_document_failure(
                error.code,
                "the policy file could not be read",
                hint="Check that the path exists and the current user can read it.",
            )
            return 2
        if error.code == "policy_document_too_large":
            _print_policy_document_failure(
                error.code,
                "the policy file exceeds the 1 MiB limit",
                hint="Remove unsupported content and keep only the policy schema fields.",
            )
            return 2
        raise
    print(
        f"policy valid: organization={document.organization_id} version={document.version_id} "
        f"teams={len(document.team_policies)} actors={len(document.actor_policies)}"
    )
    return 0


def _policy_template_catalog() -> int:
    """Print the stable credential-free template catalog."""

    print("Available policy templates:")
    for template in available_policy_templates():
        print(f"  {template.name:<9} {template.description}")
    return 0


def _policy_scenarios(args: argparse.Namespace) -> int:
    """Create and inspect portable scenario files without runtime credentials."""

    command = args.policy_scenarios_command
    if command == "create":
        suite = create_policy_scenario_suite(
            organization_id=args.organization,
            scenario_id=args.id,
            actor_id=args.actor,
            client=args.client,
            protocol=args.protocol,
            requested_model=args.model,
            requested_output_tokens=args.max_output_tokens,
        )
        write_policy_scenario_suite(
            Path(args.output).expanduser().absolute(),
            suite,
            force=args.force,
        )
        print(
            f"policy scenarios created: organization={suite.organization_id} "
            f"suite={suite.suite_id} scenarios={len(suite.scenarios)}"
        )
        return 0
    if command == "add":
        path = Path(args.file).expanduser().absolute()
        updated = add_policy_scenario_to_suite(
            path,
            scenario_id=args.id,
            actor_id=args.actor,
            client=args.client,
            protocol=args.protocol,
            requested_model=args.model,
            requested_output_tokens=args.max_output_tokens,
        )
        print(
            f"policy scenario added: organization={updated.organization_id} "
            f"suite={updated.suite_id} scenarios={len(updated.scenarios)}"
        )
        return 0
    if command == "validate":
        suite = load_policy_scenario_suite(args.file)
        print(
            f"policy scenarios valid: organization={suite.organization_id} "
            f"suite={suite.suite_id} scenarios={len(suite.scenarios)}"
        )
        return 0
    raise PolicyScenarioError(
        "policy_scenario_command_unsupported",
        "the selected policy scenario command is unsupported",
    )


def _policy_create(
    context: PolicyValidationContext,
    args: argparse.Namespace,
    dependencies: PolicyCommandDependencies,
) -> int:
    """Create one validated local policy document without runtime credentials."""

    try:
        document = create_policy_document(
            template_name=args.template,
            context=context,
            organization_id=args.organization,
            monthly_budget_usd=args.monthly_budget_usd,
            per_actor_monthly_budget_usd=args.per_actor_monthly_budget_usd,
        )
        dependencies.write_policy_document(
            Path(args.output).expanduser().absolute(),
            document,
            force=args.force,
        )
    except (PolicyTemplateError, PolicyDocumentError) as error:
        _print_policy_creation_failure(error.code, error.reason, hint=error.hint)
        return 2
    print(
        f"policy created: template={args.template} organization={document.organization_id} "
        f"version={document.version_id}"
    )
    return 0


def _write_policy_document(
    path: Path,
    document: PolicyDocument,
    *,
    force: bool,
) -> None:
    """Atomically publish an owner-only policy document without following links."""

    _validate_policy_output_target(path, force=force)
    serialized = (json.dumps(document.to_mapping(), indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("policy document write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if force:
            # Recheck immediately before publication so --force remains
            # limited to an absent target or an existing regular file.
            _validate_policy_output_target(path, force=True)
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise PolicyTemplateError(
                    "policy_output_exists",
                    "the selected output path already exists",
                    hint="Choose another path or pass --force to replace a regular file.",
                ) from None
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        temporary_path = None
    except PolicyTemplateError:
        raise
    except OSError:
        raise PolicyTemplateError(
            "policy_output_unavailable",
            "the policy document could not be written",
            hint="Check that the output directory exists and is writable.",
        ) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _validate_policy_output_target(path: Path, *, force: bool) -> None:
    """Refuse links and limit forced replacement to regular files."""

    try:
        target = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise PolicyTemplateError(
            "policy_output_unavailable",
            "the policy output path could not be inspected",
            hint="Check that the output directory exists and is accessible.",
        ) from None
    if stat.S_ISLNK(target.st_mode):
        raise PolicyTemplateError(
            "policy_output_symlink_refused",
            "the selected output path is a symbolic link",
            hint="Choose a regular file path; Hormuz never follows policy output links.",
        )
    if force and not stat.S_ISREG(target.st_mode):
        raise PolicyTemplateError(
            "policy_output_not_regular",
            "the selected output path is not a regular file",
            hint="Choose a regular file path; --force never replaces special files or directories.",
        )


def _print_policy_creation_failure(code: str, reason: str, *, hint: str | None = None) -> None:
    print(f"policy creation failed: {code}", file=sys.stderr)
    print(f"reason: {reason}", file=sys.stderr)
    if hint is not None:
        print(f"hint: {hint}", file=sys.stderr)


def _print_policy_export_failure(code: str, reason: str, *, hint: str | None = None) -> None:
    print(f"policy export failed: {code}", file=sys.stderr)
    print(f"reason: {reason}", file=sys.stderr)
    if hint is not None:
        print(f"hint: {hint}", file=sys.stderr)


def _print_policy_document_failure(code: str, reason: str, *, hint: str | None = None) -> None:
    print(f"policy validation failed: {code}", file=sys.stderr)
    print(f"reason: {reason}", file=sys.stderr)
    if hint is not None:
        print(f"hint: {hint}", file=sys.stderr)


def _print_policy_scenario_failure(code: str, reason: str, *, hint: str | None = None) -> None:
    print(f"policy scenario failed: {code}", file=sys.stderr)
    print(f"reason: {reason}", file=sys.stderr)
    if hint is not None:
        print(f"hint: {hint}", file=sys.stderr)


def _print_policy_demo_failure(code: str, reason: str, *, hint: str | None = None) -> None:
    print(f"policy demo failed: {code}", file=sys.stderr)
    print(f"reason: {reason}", file=sys.stderr)
    if hint is not None:
        print(f"hint: {hint}", file=sys.stderr)


def _print_policy_version(prefix: str, version: PolicyVersionRecord) -> None:
    print(
        f"{prefix}: organization={version.organization_id} version={version.version_id} "
        f"sha256={version.content_sha256} created_at={version.created_at.isoformat()}"
    )


def _print_policy_activation(prefix: str, activation: PolicyActivation) -> None:
    print(
        f"{prefix}: organization={activation.organization_id} version={activation.version_id} "
        f"generation={activation.generation} activated_at={activation.activated_at.isoformat()}"
    )


def _print_policy_status(status: PolicyControlStatus, *, as_json: bool) -> None:
    payload = {
        "organization_id": status.organization_id,
        "initialized": status.initialized,
        "active": (
            {
                "version_id": status.active.version_id,
                "generation": status.active.generation,
                "activated_at": status.active.activated_at.isoformat(),
                "activated_by_kind": status.active.activated_by_kind,
                "activated_by_identity_key": status.active.activated_by_identity_key,
            }
            if status.active is not None
            else None
        ),
        "versions": [
            {
                "version_id": version.version_id,
                "content_sha256": version.content_sha256,
                "created_at": version.created_at.isoformat(),
                "author_kind": version.author_kind,
                "author_identity_key": version.author_identity_key,
                "change_summary": version.change_summary,
            }
            for version in status.versions
        ],
        "administrators": [administrator.audit_ref() for administrator in status.administrators],
    }
    if as_json:
        print(json.dumps(contract_envelope(POLICY_CONTROL_STATUS_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    active = payload["active"]
    print(f"organization: {status.organization_id}")
    print(f"initialized: {str(status.initialized).lower()}")
    print(f"active policy: {active['version_id'] if isinstance(active, dict) else '-'}")
    print(f"active generation: {active['generation'] if isinstance(active, dict) else '-'}")
    print(f"policy versions: {len(status.versions)}")
    print(f"active policy administrators: {len(status.administrators)}")


def _print_policy_history(history: PolicyHistory, *, as_json: bool) -> None:
    payload = {
        "organization_id": history.organization_id,
        "limit": history.limit,
        "has_more": history.has_more,
        "events": [
            {
                "event_type": event.event_type,
                "version_id": event.version_id,
                "content_sha256": event.content_sha256,
                "occurred_at": event.occurred_at.isoformat(),
                "actor_kind": event.actor_kind,
                "actor_identity_key": event.actor_identity_key,
                "generation": event.generation,
                "change_summary": event.change_summary,
            }
            for event in history.events
        ],
    }
    if as_json:
        print(json.dumps(contract_envelope(POLICY_HISTORY_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    print(f"organization: {history.organization_id}")
    print(f"lifecycle events: {len(history.events)} (limit={history.limit} has_more={str(history.has_more).lower()})")
    for event in history.events:
        generation = str(event.generation) if event.generation is not None else "-"
        print(
            f"{event.occurred_at.isoformat()} {event.event_type} version={event.version_id} "
            f"digest={event.content_sha256} generation={generation} "
            f"actor={event.actor_identity_key}"
        )
        print(
            "  change_summary="
            + json.dumps(event.change_summary, sort_keys=True, separators=(",", ":"))
        )


def _policy_comparison_payload(comparison: PolicyComparison) -> dict[str, object]:
    return contract_envelope(
        POLICY_COMPARISON_SCHEMA_ID,
        {
            "organization_id": comparison.organization_id,
            "baseline": _policy_version_identity_payload(comparison.baseline),
            "candidate": _policy_version_identity_payload(comparison.candidate),
            "identical": comparison.identical,
            "changes": [
                {
                    "path": change.path,
                    "change_type": change.change_type,
                    "before": change.before,
                    "after": change.after,
                }
                for change in comparison.changes
            ],
        },
    )


def _print_policy_comparison(comparison: PolicyComparison, *, as_json: bool) -> None:
    payload = _policy_comparison_payload(comparison)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"organization: {comparison.organization_id}")
    _print_policy_version_identity("baseline", comparison.baseline)
    _print_policy_version_identity("candidate", comparison.candidate)
    print(f"semantic changes: {len(comparison.changes)}")
    if comparison.identical:
        print("result: identical")
        return
    for change in comparison.changes:
        print(f"{change.change_type} {change.path}")
        print(f"  before: {json.dumps(change.before, sort_keys=True, separators=(',', ':'))}")
        print(f"  after:  {json.dumps(change.after, sort_keys=True, separators=(',', ':'))}")


def _print_policy_preview(preview: PolicyPreview, *, as_json: bool) -> None:
    payload = {
        "organization_id": preview.organization_id,
        "evaluated_at": preview.evaluated_at.isoformat(),
        "usage_period": {
            "starts_at": preview.usage_period.starts_at.isoformat(),
            "ends_before": preview.usage_period.ends_before.isoformat(),
        },
        "usage_basis": preview.usage_basis,
        "request": {
            "actor_id": preview.identity.actor_id,
            "client": preview.client,
            "protocol": preview.protocol,
            "requested_model": preview.requested_model,
            "requested_output_tokens": preview.requested_output_tokens,
        },
        "baseline": {
            **_policy_version_identity_payload(preview.baseline),
            "decision": _policy_decision_contract(preview.baseline_decision),
        },
        "candidate": {
            **_policy_version_identity_payload(preview.candidate),
            "decision": _policy_decision_contract(preview.candidate_decision),
        },
    }
    if as_json:
        print(json.dumps(contract_envelope(POLICY_PREVIEW_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    print(f"organization: {preview.organization_id}")
    print(f"evaluated at: {preview.evaluated_at.isoformat()}")
    print(
        "usage: current "
        f"[{preview.usage_period.starts_at.isoformat()}, {preview.usage_period.ends_before.isoformat()})"
    )
    print(
        f"request: actor={preview.identity.actor_id} client={preview.client} protocol={preview.protocol} "
        f"model={preview.requested_model} max_output_tokens={preview.requested_output_tokens or '-'}"
    )
    _print_policy_preview_decision("baseline", preview.baseline, preview.baseline_decision)
    _print_policy_preview_decision("candidate", preview.candidate, preview.candidate_decision)


def _policy_evaluation_payload(evaluation: PolicyEvaluation) -> dict[str, object]:
    scenario_count = len(evaluation.scenarios)
    return contract_envelope(
        POLICY_EVALUATION_SCHEMA_ID,
        {
            "organization_id": evaluation.organization_id,
            "evaluated_at": evaluation.evaluated_at.isoformat(),
            "usage_period": {
                "starts_at": evaluation.usage_period.starts_at.isoformat(),
                "ends_before": evaluation.usage_period.ends_before.isoformat(),
            },
            "usage_basis": evaluation.usage_basis,
            "suite": {
                "suite_id": evaluation.suite.suite_id,
                "content_sha256": evaluation.suite.content_sha256,
                "scenario_count": scenario_count,
            },
            "baseline": _policy_version_identity_payload(evaluation.baseline),
            "candidate": _policy_version_identity_payload(evaluation.candidate),
            "summary": {
                "scenario_count": scenario_count,
                "changed_count": evaluation.changed_count,
                "unchanged_count": scenario_count - evaluation.changed_count,
                "baseline_allowed_count": evaluation.baseline_allowed_count,
                "candidate_allowed_count": evaluation.candidate_allowed_count,
            },
            "scenarios": [
                {
                    "scenario_id": result.scenario.scenario_id,
                    "request": {
                        "actor_id": result.scenario.actor_id,
                        "client": result.scenario.client,
                        "protocol": result.scenario.protocol,
                        "requested_model": result.scenario.requested_model,
                        "requested_output_tokens": result.scenario.requested_output_tokens,
                    },
                    "changed": result.changed,
                    "baseline": {"decision": _policy_decision_contract(result.baseline_decision)},
                    "candidate": {"decision": _policy_decision_contract(result.candidate_decision)},
                }
                for result in evaluation.scenarios
            ],
        },
    )


def _print_policy_evaluation(
    evaluation: PolicyEvaluation,
    *,
    payload: dict[str, object],
    as_json: bool,
) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"organization: {evaluation.organization_id}")
    print(
        f"suite: {evaluation.suite.suite_id} scenarios={len(evaluation.scenarios)}"
    )
    _print_policy_version_identity("baseline", evaluation.baseline)
    _print_policy_version_identity("candidate", evaluation.candidate)
    print(f"evaluated at: {evaluation.evaluated_at.isoformat()}")
    print(
        "usage: current "
        f"[{evaluation.usage_period.starts_at.isoformat()}, {evaluation.usage_period.ends_before.isoformat()})"
    )
    print(
        f"behavior changes: {evaluation.changed_count} "
        f"unchanged={len(evaluation.scenarios) - evaluation.changed_count}"
    )
    for result in evaluation.scenarios:
        status = "changed" if result.changed else "unchanged"
        scenario = result.scenario
        print(
            f"{status} {scenario.scenario_id}: actor={scenario.actor_id} client={scenario.client} "
            f"protocol={scenario.protocol} model={scenario.requested_model} "
            f"max_output_tokens={scenario.requested_output_tokens or '-'}"
        )
        _print_policy_preview_decision("  baseline", evaluation.baseline, result.baseline_decision)
        _print_policy_preview_decision("  candidate", evaluation.candidate, result.candidate_decision)


def _policy_version_identity_payload(identity: PolicyVersionIdentity) -> dict[str, str]:
    return {"version_id": identity.version_id, "content_sha256": identity.content_sha256}


def _print_policy_version_identity(prefix: str, identity: PolicyVersionIdentity) -> None:
    print(f"{prefix}: version={identity.version_id} digest={identity.content_sha256}")


def _print_policy_preview_decision(
    prefix: str,
    identity: PolicyVersionIdentity,
    decision: PolicyDecision,
) -> None:
    print(
        f"{prefix}: version={identity.version_id} digest={identity.content_sha256} "
        f"allowed={str(decision.allowed).lower()} action={decision.action}"
    )
    print(f"  reason: {decision.reason}")
    print(f"  routed_model: {decision.route.upstream_model if decision.route else '-'}")
    print(f"  max_output_tokens: {decision.max_output_tokens if decision.max_output_tokens is not None else '-'}")
