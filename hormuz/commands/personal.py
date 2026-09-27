"""Standalone personal-optimizer commands; no gateway config is loaded."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from importlib.resources import files
from pathlib import Path

from ..adapters import AdapterError, adapter_catalog, adapter_for, conformance_report
from ..client_relay import ClientRelayError, load_saved_profile
from ..compaction_runtime import (
    ContextPreferenceStore,
    ContextRuntimeError,
    default_state_directory,
)
from ..credential_store import (
    CredentialStoreError,
    ProviderCredentialStore,
)
from ..personal_metrics import PersonalMetricsError, PersonalMetricsStore
from ..personal_qualification import (
    PersonalQualificationError,
    run_product_qualification,
)
from ..personal_profiles import (
    PERSONAL_RELEASE_VERSION,
    PersonalProfile,
    PersonalProfileError,
    PersonalProfileStore,
    validate_personal_endpoint,
    validate_personal_key,
)
from ..personal_runtime import direct_credential_reader, run_personal_client
from ..session_client import SessionClientError, access_token


class PersonalCommandError(RuntimeError):
    def __init__(self, code: str, exit_code: int = 1):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def add_personal_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    personal = subparsers.add_parser(
        "personal", help="Connect and inspect the standalone personal optimizer"
    )
    commands = personal.add_subparsers(dest="personal_command", required=True)

    connect = commands.add_parser("connect", help="Create a direct or managed profile")
    _profile_arguments(connect)
    connect.add_argument("--mode", choices=["direct", "managed"], default="direct")
    connect.add_argument("--agent", choices=["codex", "claude-code", "aider"], default="codex")
    connect.add_argument("--provider", choices=["openai", "anthropic"])
    connect.add_argument("--endpoint")
    connect.add_argument("--model")
    connect.add_argument("--credential-env")
    connect.add_argument("--allow-loopback-http", action="store_true")

    run = commands.add_parser("run", help="Launch the configured coding agent")
    _profile_arguments(run)

    for name, help_text in (
        ("on", "Enable optimization for subsequent requests"),
        ("off", "Restore the ordinary request path"),
        ("benefit", "Show the local content-free benefit view"),
        ("clear", "Clear local numeric measurements"),
        ("remove", "Remove routing, preference, metrics, and direct credential"),
    ):
        command = commands.add_parser(name, help=help_text)
        _profile_arguments(command)
        if name == "benefit":
            command.add_argument("--json", action="store_true")

    adapters = commands.add_parser("adapters", help="List built-in adapter contracts")
    adapters.add_argument("--json", action="store_true")

    contract = commands.add_parser("contract", help="Show the personal release boundary")
    contract.add_argument("--json", action="store_true")

    conformance = commands.add_parser(
        "conformance", help="Run the provider-free adapter conformance kit"
    )
    conformance.add_argument("--agent", choices=["codex", "claude-code", "aider"], required=True)
    conformance.add_argument("--json", action="store_true")

    qualify = commands.add_parser(
        "qualify", help="Run the provider-free product qualification fixtures"
    )
    qualify.add_argument("--json", action="store_true")

    credential = commands.add_parser(
        "credential", help="Replace a direct profile credential from an environment variable"
    )
    _profile_arguments(credential)
    credential.add_argument("--credential-env", required=True)

    feedback = commands.add_parser(
        "feedback", help="Work with voluntary content-free local reports"
    )
    feedback_commands = feedback.add_subparsers(
        dest="personal_feedback_command", required=True
    )
    export = feedback_commands.add_parser(
        "export", help="Write a voluntary content-free local report"
    )
    _profile_arguments(export)
    export.add_argument("--output", type=Path, required=True)


def _profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True)
    parser.add_argument("--state-directory", type=Path, default=default_state_directory())


def run(args: argparse.Namespace) -> int:
    try:
        command = args.personal_command
        if command == "connect":
            return _connect(args)
        if command == "adapters":
            return _adapters(args.json)
        if command == "contract":
            value = json.loads(
                files("hormuz").joinpath("personal-release-v1.json").read_text(encoding="utf-8")
            )
            _print_document(value, as_json=args.json)
            return 0
        if command == "conformance":
            report = conformance_report(adapter_for(args.agent))
            _print_document(report, as_json=args.json)
            return 0 if report["passed"] else 1
        if command == "qualify":
            report = run_product_qualification()
            _print_document(report, as_json=args.json)
            return 0 if report["passed"] else 1
        state = _state_directory(args.state_directory)
        profile = PersonalProfileStore(state).load(validate_personal_key(args.profile))
        if command == "credential":
            if profile.mode != "direct":
                raise PersonalCommandError("managed_credential_update_rejected", 2)
            if args.credential_env != profile.credential_env:
                raise PersonalCommandError("credential_env_mismatch", 2)
            ProviderCredentialStore().set(
                profile.key, _read_provider_credential(args.credential_env)
            )
            print(f"personal_credential_updated profile={profile.key}")
            return 0
        if command == "run":
            return _run_profile(profile, state)
        preference = ContextPreferenceStore(state, profile.key)
        if command in {"on", "off"}:
            enabled = command == "on"
            preference.save(enabled)
            print(f"personal_optimization profile={profile.key} setting={'on' if enabled else 'off'}")
            return 0
        metrics = PersonalMetricsStore(state, profile.key)
        if command == "benefit":
            document = metrics.benefit(enabled=preference.load().enabled)
            _print_document(document, as_json=args.json)
            return 0
        if command == "clear":
            cleared = metrics.clear()
            print(f"personal_measurements profile={profile.key} cleared={str(cleared).lower()}")
            return 0
        if command == "remove":
            return _remove(profile, state)
        if command == "feedback" and args.personal_feedback_command == "export":
            document = metrics.benefit(enabled=preference.load().enabled)
            _write_new(args.output, document)
            print(f"personal_feedback_export written={args.output}")
            return 0
        raise PersonalCommandError("invalid_personal_command", 2)
    except (
        AdapterError,
        ClientRelayError,
        ContextRuntimeError,
        CredentialStoreError,
        PersonalMetricsError,
        PersonalProfileError,
        PersonalQualificationError,
        SessionClientError,
    ) as error:
        code = getattr(error, "code", "personal_failed")
        print(f"personal error: {code}", file=sys.stderr)
        return 1
    except PersonalCommandError as error:
        print(f"personal error: {error.code}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        return 130


def _connect(args: argparse.Namespace) -> int:
    key = validate_personal_key(args.profile)
    state = _state_directory(args.state_directory, create=True)
    store = PersonalProfileStore(state)
    preference = ContextPreferenceStore(state, key)
    credentials: ProviderCredentialStore | None = None
    secret: str | None = None
    if args.mode == "managed":
        if any((args.provider, args.endpoint, args.model, args.credential_env, args.allow_loopback_http)):
            raise PersonalCommandError("managed_profile_arguments_conflict", 2)
        managed = load_saved_profile(state, key)
        profile = PersonalProfile(
            key=key,
            mode="managed",
            agent=managed.client,
            provider="hormuz",
            endpoint=managed.gateway,
            model=managed.model,
            allow_insecure_http=managed.allow_insecure_http,
            managed_profile=managed.key,
        )
    else:
        provider = args.provider or ("anthropic" if args.agent == "claude-code" else "openai")
        endpoint = args.endpoint or (
            "https://api.anthropic.com" if provider == "anthropic" else "https://api.openai.com"
        )
        if args.model is None:
            raise PersonalCommandError("model_required", 2)
        environment_name = args.credential_env or (
            "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        )
        secret = _read_provider_credential(environment_name)
        profile = PersonalProfile(
            key=key,
            mode="direct",
            agent=args.agent,
            provider=provider,
            endpoint=validate_personal_endpoint(
                endpoint, allow_insecure_http=args.allow_loopback_http
            ),
            model=args.model,
            allow_insecure_http=args.allow_loopback_http,
            credential_env=environment_name,
        )
        credentials = ProviderCredentialStore()
    try:
        preference.path.lstat()
    except FileNotFoundError:
        previous_preference = None
    except OSError as error:
        raise ContextRuntimeError("settings_invalid") from error
    else:
        previous_preference = preference.load()
    profile_created = False
    credential_write_attempted = False
    previous_secret = credentials.get(key) if credentials is not None else None
    try:
        # Create-only profile persistence is the transaction guard.  In
        # particular, a repeated connect must not overwrite and then delete an
        # existing keyring credential when the profile creation is rejected.
        store.save(profile)
        profile_created = True
        if credentials is not None:
            assert secret is not None
            credential_write_attempted = True
            credentials.set(key, secret)
        preference.save(True)
    except (Exception, KeyboardInterrupt):
        if not profile_created:
            raise
        try:
            if previous_preference is None:
                preference.clear()
            else:
                preference.save(previous_preference.enabled)
            if credentials is not None and credential_write_attempted:
                if previous_secret is None:
                    credentials.delete(key)
                else:
                    credentials.set(key, previous_secret)
            store.remove(key)
        except Exception as cleanup_error:
            raise PersonalCommandError("personal_connect_rollback_failed") from cleanup_error
        raise
    print(
        "personal_connected "
        f"profile={profile.key} mode={profile.mode} agent={profile.agent} "
        "optimization=on agent_configuration=unchanged"
    )
    return 0


def _run_profile(profile: PersonalProfile, state: Path) -> int:
    if profile.mode == "direct":
        credential = direct_credential_reader(ProviderCredentialStore(), profile.key)
    else:
        managed = load_saved_profile(state, profile.managed_profile or "")
        if (
            managed.gateway != profile.endpoint
            or managed.client != profile.agent
            or managed.model != profile.model
            or managed.allow_insecure_http != profile.allow_insecure_http
        ):
            raise PersonalCommandError("managed_profile_drift")

        def credential() -> str:
            return access_token(
                gateway=managed.gateway,
                profile=managed.key,
                allow_insecure_http=managed.allow_insecure_http,
            )

    return run_personal_client(
        profile=profile,
        state_directory=state,
        upstream_credential=credential,
    )


def _remove(profile: PersonalProfile, state: Path) -> int:
    if profile.mode == "direct":
        ProviderCredentialStore().delete(profile.key)
    ContextPreferenceStore(state, profile.key).clear()
    PersonalMetricsStore(state, profile.key).clear()
    removed = PersonalProfileStore(state).remove(profile.key)
    print(
        "personal_removed "
        f"profile={profile.key} removed={str(removed).lower()} "
        "agent_configuration=unchanged managed_session=preserved"
    )
    return 0


def _adapters(as_json: bool) -> int:
    document = {
        "schema_id": "hormuz.personal-adapter-catalog",
        "schema_version": 1,
        "adapter_api_version": "1.0",
        "adapters": [
            {
                "key": identity.key,
                "display_name": identity.display_name,
                "qualified_version": identity.version,
            }
            for identity in adapter_catalog()
        ],
    }
    _print_document(document, as_json=as_json)
    return 0


def _read_provider_credential(environment_name: str) -> str:
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", environment_name) is None:
        raise PersonalCommandError("credential_env_invalid", 2)
    secret = os.environ.get(environment_name)
    if secret is None:
        raise PersonalCommandError("provider_credential_unavailable", 2)
    return secret


def _print_document(value: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return
    if value.get("schema_id") == "hormuz.personal-benefit":
        traffic = value["traffic"]
        reduction = value["estimated_token_reduction"]
        waiting = value["waiting"]
        print(
            f"Personal optimizer {value['profile']} "
            f"({'On' if value['optimization_enabled'] else 'Off'})"
        )
        print(
            f"Changed {traffic['transformed_requests']} of {traffic['total_requests']} requests; "
            f"eligible {traffic['eligible_requests']}."
        )
        overall = reduction["all_captured_traffic"]
        if overall["tokens"] is None:
            print(
                "Estimated token benefit: unavailable "
                f"(token estimates cover {overall['observed_requests']} of "
                f"{overall['expected_requests']} captured requests)."
            )
        else:
            print(
                f"Estimated token benefit: {overall['tokens']} tokens "
                f"({overall['percent']}% of captured traffic; local estimate, not a bill)."
            )
        provider = value["provider_reported"]
        activity = value["provider_activity"]
        print(
            "Provider usage: "
            f"input={_metric_total(provider['input_tokens'])}, "
            f"output={_metric_total(provider['output_tokens'])}, "
            f"total={_metric_total(provider['total_tokens'])}, "
            f"cache-read={_metric_total(provider['cache_read_tokens'])}, "
            f"cache-write={_metric_total(provider['cache_write_tokens'])}, "
            f"reasoning={_metric_total(provider['reasoning_tokens'])}; "
            f"responses={activity['responses']}/{activity['attempts']}."
        )
        overhead = waiting["optimizer_overhead_us"]
        print(
            "Optimizer overhead: "
            + ("unavailable" if overhead["total"] is None else f"{overhead['total']} us total")
            + "."
        )
        print(
            "Observed waiting: "
            f"TTFT={_metric_total(waiting['time_to_first_byte_ms'])} ms, "
            f"total={_metric_total(waiting['total_latency_ms'])} ms."
        )
        print(f"Exceptions: {json.dumps(value['exceptions'], sort_keys=True)}")
        print(
            "Cost: "
            f"actual={_metric_total(value['cost']['actual_microusd'])} microusd; "
            f"basis={value['cost']['basis']}."
        )
        return
    print(json.dumps(value, indent=2, sort_keys=True))


def _metric_total(value: dict[str, object]) -> str:
    total = value.get("total")
    if total is None:
        return "unavailable"
    return str(round(total, 3)) if isinstance(total, float) else str(total)


def _write_new(path: Path, value: object) -> None:
    target = path.expanduser().resolve(strict=False)
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise PersonalCommandError("output_exists", 2) from error
    except OSError as error:
        raise PersonalCommandError("output_write_failed") from error


def _state_directory(path: Path, *, create: bool = False) -> Path:
    target = path.expanduser()
    try:
        if create:
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        return target.resolve(strict=True)
    except OSError as error:
        raise PersonalCommandError("personal_state_unavailable", 2) from error


__all__ = ["PERSONAL_RELEASE_VERSION", "add_personal_commands", "run"]
