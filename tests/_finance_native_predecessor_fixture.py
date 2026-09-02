"""Use the exact accepted budget-runtime main binary as the #8 predecessor.

The archive is an intermediate Git checkpoint, not a published release. The
isolated driver verifies and executes only fixture/runtime bytes from that
digest-bound archive; it never imports fixture code from the evolving checkout.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import ModuleType
from urllib.parse import unquote, urlsplit


SOURCE_COMMIT = "4e3133f19db4c34d7a181848ebc36754bce164ea"
ARCHIVE_SHA256 = "86a29497ac0f4e9a2ba177fba54a3b36179077ce402a1ce0fbe37a95c61920a0"
ARCHIVE_PREFIX = "hormuz-budget-runtime-baseline/"


def verify_installed_runtime(source_tar, package_root):
    """Reject install metadata whose runtime/data bytes drifted after install."""

    prefix = ARCHIVE_PREFIX + "hormuz/"
    try:
        expected = {}
        for member in source_tar.getmembers():
            if not member.name.startswith(prefix):
                continue
            relative = Path(member.name[len(prefix):])
            if relative.suffix not in {".py", ".json", ".sql"}:
                continue
            if (
                not member.isfile()
                or member.size > 2 * 1024 * 1024
                or relative.is_absolute()
                or ".." in relative.parts
                or relative in expected
            ):
                raise RuntimeError("finance_native_predecessor_runtime_mismatch")
            expected[relative] = member
        actual = {
            path.relative_to(package_root)
            for path in package_root.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".json", ".sql", ".so", ".pyd"}
        }
        if not expected or actual != set(expected):
            raise RuntimeError("finance_native_predecessor_runtime_mismatch")
        for relative, member in expected.items():
            path = package_root / relative
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(package_root.resolve())
            ):
                raise RuntimeError("finance_native_predecessor_runtime_mismatch")
            with path.open("rb") as installed:
                payload = installed.read(member.size + 1)
            archived = source_tar.extractfile(member)
            if archived is None or payload != archived.read(member.size + 1):
                raise RuntimeError("finance_native_predecessor_runtime_mismatch")
        return len(expected)
    except (OSError, ValueError):
        raise RuntimeError("finance_native_predecessor_runtime_mismatch") from None


def finance_native_predecessor_call(request):
    executable = os.environ["HORMUZ_TEST_FINANCE_NATIVE_PYTHON"]
    result = subprocess.run(
        [executable, "-I", str(Path(__file__).resolve())],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=60,
        cwd=Path(executable).parent,
    )
    if result.returncode:
        raise AssertionError("finance_native_predecessor_driver_failed")
    return json.loads(result.stdout)


def _driver():
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone

    import hormuz
    from hormuz.config import UsageStorageConfig
    from hormuz.finance_rate_cards import rate_card_from_mapping
    from hormuz.finance_repository import create_finance_repository
    from hormuz.portfolio_config import PortfolioPrincipal
    from hormuz.portfolio_repository import create_portfolio_repository
    from hormuz.portfolio_service import PortfolioService
    from hormuz.portfolio_wire import ATTRIBUTIONS, OUTCOMES, SCOPES, canonical
    from hormuz.postgres import (
        POSTGRES_SCHEMA_VERSION,
        PostgresStorageError,
        migrate_postgres,
    )
    from hormuz.postgres_usage_store import PostgresUsageStore
    from hormuz.provider_reliability import (
        ProviderAttemptMetrics,
        ProviderFailoverContext,
    )
    from hormuz.store import StorageSchemaError, UsageStore
    from hormuz.store_router import create_provider_reliability_repository

    distribution = importlib.metadata.distribution("hormuz")
    source = json.loads(distribution.read_text("direct_url.json") or "{}")
    url = urlsplit(source.get("url", ""))
    archive = Path(unquote(url.path))
    if (
        distribution.version != "1.0.0"
        or UsageStore.schema_version != 10
        or POSTGRES_SCHEMA_VERSION != 14
        or not Path(hormuz.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        or source.get("archive_info", {}).get("hashes", {}).get("sha256")
        != ARCHIVE_SHA256
        or url.scheme != "file"
        or url.netloc not in {"", "localhost"}
    ):
        raise RuntimeError("finance_native_predecessor_binding_invalid")
    with archive.open("rb") as source_file:
        archive_bytes = source_file.read(32 * 1024 * 1024 + 1)
    if (
        len(archive_bytes) > 32 * 1024 * 1024
        or hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256
    ):
        raise RuntimeError("finance_native_predecessor_binding_invalid")

    fixture_names = (
        "_registry_transition_fixture",
        "_portfolio_fixture",
        "_attribution_fixture",
        "test_outcome_contract",
        "_outcome_fixture",
        "_finance_values_fixture",
        "_finance_fixture",
    )
    # Verify and execute from the same immutable byte buffer. Reopening a
    # mutable archive path after its digest check would leave a TOCTOU gap.
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as source_tar:
        runtime_files_verified = verify_installed_runtime(
            source_tar,
            Path(hormuz.__file__).resolve().parent,
        )
        for name in fixture_names:
            member = source_tar.getmember(ARCHIVE_PREFIX + f"tests/{name}.py")
            if not member.isfile() or member.size > 128 * 1024:
                raise RuntimeError("finance_native_predecessor_fixture_invalid")
            stream = source_tar.extractfile(member)
            if stream is None:
                raise RuntimeError("finance_native_predecessor_fixture_invalid")
            module = ModuleType(name)
            module.__file__ = str(archive) + "/" + name + ".py"
            module.__package__ = ""
            sys.modules[name] = module
            exec(compile(stream.read(), module.__file__, "exec"), module.__dict__)

    ledger = sys.modules["_registry_transition_fixture"]
    registry = sys.modules["_portfolio_fixture"]
    attribution = sys.modules["_attribution_fixture"]
    outcome = sys.modules["_outcome_fixture"]
    finance = sys.modules["_finance_fixture"]

    def finance_receipt(value):
        return {
            "card": value.card.as_mapping(),
            "receipt_id": value.receipt_id,
            "registered_by": value.registered_by,
            "registered_at": value.registered_at,
            "sequence": value.sequence,
        }

    def seed_budget(config, registry_writes, environment):
        repositories = create_portfolio_repository(config, environ=environment)
        repository = repositories.budgets
        if repository is None:
            raise RuntimeError("finance_native_predecessor_budget_missing")
        scope = registry_writes[2][3][1]
        now = datetime.now(timezone.utc)
        principal = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
        plan = repository.create_plan(
            principal,
            {
                "schema_id": "hormuz.work-budget-plan-request",
                "schema_version": 1,
                "budget_plan_id": None,
                "expected_version": None,
                "work_scope": {
                    "work_scope_id": scope["work_scope_id"],
                    "version": scope["version"],
                },
                "window": {
                    "start_at": (now - timedelta(days=1)).isoformat(
                        timespec="microseconds"
                    ).replace("+00:00", "Z"),
                    "end_at": (now + timedelta(days=1)).isoformat(
                        timespec="microseconds"
                    ).replace("+00:00", "Z"),
                },
                "currency": "USD",
                "amount": "25",
                "allowed_models": None,
                "output_token_cap": None,
                "per_request_cost_cap": None,
                "reason_code": "created",
            },
        )
        active = repository.activate_plan(
            principal,
            plan["budget_plan_id"],
            {
                "schema_id": "hormuz.work-budget-plan-activation-request",
                "schema_version": 1,
                "version": plan["version"],
                "expected_active_version": None,
                "expected_activation_generation": 0,
                "reason_code": "accepted",
            },
        )
        return {
            "budget_plan_id": active["budget_plan_id"],
            "version": active["version"],
            "active_version": active["active_version"],
            "activation_generation": active["activation_generation"],
            "work_scope": active["work_scope"],
            "amount": active["amount"],
            "currency": active["currency"],
        }

    def seed_provider_reliability(store, identity):
        reliability = create_provider_reliability_repository(store)
        if reliability is None:
            raise RuntimeError("finance_native_predecessor_reliability_missing")
        arguments = {
            "identity": identity,
            "client": "codex",
            "protocol": "openai",
            "requested_model": "synthetic",
            "resolved_alias": "synthetic",
            "upstream_model": "synthetic-primary",
            "policy_version": "finance-native-predecessor-policy",
            "policy_action": "allowed",
            "redaction_count": 0,
            "redaction_rules": (),
            "scopes": (),
            "reserved_tokens": 10,
            "reserved_cost_microusd": 20,
            "ttl_seconds": 60,
        }
        original = store.begin_request_attempt(**arguments)
        reliability.finalize_request_attempt(
            attempt=original,
            organization_id="acme",
            status="rate_limited",
            provider_metrics=ProviderAttemptMetrics(429, 1000, None, 1500, 0, 0),
        )
        failover = reliability.begin_request_attempt(
            **{
                **arguments,
                "resolved_alias": "synthetic-failover",
                "upstream_model": "synthetic-secondary",
            },
            work_budget=None,
            provider_failover=ProviderFailoverContext(
                original_attempt_id=original.attempt_id,
                trigger_status=429,
                reason_code="provider_rate_limited",
            ),
        )
        reliability.finalize_request_attempt(
            attempt=failover,
            organization_id="acme",
            status="succeeded",
            input_tokens=7,
            output_tokens=3,
            cost_microusd=11,
            provider_metrics=ProviderAttemptMetrics(200, 800, 1100, 1600, 12, 12),
        )
        return {
            "original_attempt_id": original.attempt_id,
            "failover_attempt_id": failover.attempt_id,
            "metric_count": 2,
            "failover_count": 1,
        }

    request = json.load(sys.stdin)
    try:
        config = registry.registry_config(
            Path(request.get("path", "/unused/predecessor/usage.sqlite3")).parent
        )
        if request["backend"] == "sqlite":
            config = replace(config, database_path=Path(request["path"]))
            store = UsageStore(
                config.database_path,
                read_only=request["mode"] != "seed",
            )
            environment = None
        elif request["backend"] == "postgresql":
            config = replace(
                config,
                usage_storage=UsageStorageConfig(
                    backend="postgresql",
                    postgres_schema=request["schema"],
                    postgres_runtime_role=request["runtime_role"],
                ),
            )
            if request["mode"] == "seed":
                migrate_postgres(
                    request["owner_dsn"],
                    schema=request["schema"],
                    runtime_role=request["runtime_role"],
                    policy_control_role=request["policy_control_role"],
                    custody_control_role=request["custody_control_role"],
                    custody_executor_role=request["custody_executor_role"],
                )
            store = PostgresUsageStore(
                request["runtime_dsn"],
                schema=request["schema"],
                runtime_role=request["runtime_role"],
                organization_ids=("acme", "beta"),
            )
            environment = {"HORMUZ_POSTGRES_DSN": request["runtime_dsn"]}
        else:
            raise RuntimeError("finance_native_predecessor_backend_invalid")

        result = {
            "status": "ready",
            "runtime_files_verified": runtime_files_verified,
        }
        if request["mode"] == "seed":
            ledger.seed_registry_ledger(store)
            result["registry_writes"], result["registry_page"] = (
                registry.seed_registry_metadata(config, environ=environment)
            )
            (
                _,
                result["attribution_write"],
                result["attribution_page"],
                result["attempt_id"],
            ) = attribution.seed_attribution_metadata(config, environ=environment)
            result["outcome_seed"] = outcome.seed_outcome_metadata(
                config,
                environ=environment,
            )
            result["outcome_page"] = result["outcome_seed"]["page"]
            result["finance_registration"] = finance_receipt(
                finance.seed_finance(config, environ=environment)
            )
            result["provider_reliability"] = seed_provider_reliability(
                store,
                config.identities_by_token[registry.ADMIN],
            )
            result["budget_registration"] = seed_budget(
                config,
                result["registry_writes"],
                environment,
            )
        elif request["mode"] in {"replay", "page", "facts"}:
            repositories = create_portfolio_repository(config, environ=environment)
            service = PortfolioService(config, repositories)
            if request["mode"] == "replay":
                result["registry_replays"] = [
                    service.dispatch(
                        registry.ADMIN,
                        "POST",
                        path,
                        body=canonical(body).encode(),
                        idempotency_key=key,
                    )
                    for path, body, key, _expected in request["registry_writes"]
                ]
                body, key, _expected = request["attribution_write"]
                result["attribution_replay"] = service.dispatch(
                    registry.ADMIN,
                    "POST",
                    ATTRIBUTIONS,
                    body=canonical(body).encode(),
                    idempotency_key=key,
                )
                (
                    result["outcome_replays"],
                    result["outcome_retention"],
                ) = outcome.replay_outcome_metadata(
                    config,
                    request["outcome_seed"],
                    environ=environment,
                )
                repository = create_finance_repository(config, environ=environment)
                result["finance_replay"] = finance_receipt(
                    repository.register_rate_card(
                        finance.ADMIN,
                        rate_card_from_mapping(
                            request["finance_registration"]["card"]
                        ),
                    )
                )
            elif request["mode"] == "page":
                path = {
                    "registry": SCOPES,
                    "attribution": ATTRIBUTIONS,
                    "outcome": OUTCOMES,
                }[request["resource"]]
                result["page"] = service.dispatch(
                    registry.ADMIN,
                    "GET",
                    path,
                    query="cursor=" + request["cursor"],
                )[1]
            else:
                result["facts"] = repositories.attributions.attempt_facts(
                    service.authenticate(registry.ADMIN),
                    request["attempt_id"],
                )
        elif request["mode"] != "verify":
            raise RuntimeError("finance_native_predecessor_mode_invalid")
        result.update(ledger.ledger_observation(store))
    except (StorageSchemaError, PostgresStorageError) as error:
        result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _driver()
