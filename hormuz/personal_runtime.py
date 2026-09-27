"""Runtime assembly for personal direct and managed profiles."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from .adapters import adapter_for, client_launch_values, sanitized_client_environment
from .client_relay import (
    ClientRelayError,
    LocalRelayServer,
    RelayOptimizer,
    new_local_credential,
    probe_gateway_capability,
    supported_client_executable,
)
from .client_versions import SUPPORTED_CLIENT_VERSIONS
from .compaction_runtime import ContextPreferenceStore
from .personal_metrics import PersonalMetricsError, PersonalMetricsStore
from .personal_profiles import PersonalProfile


def run_personal_client(
    *,
    profile: PersonalProfile,
    state_directory: Path,
    upstream_credential: Callable[[], str],
    counters: Mapping[str, Callable[[str], int]] | None = None,
    executable: str | None = None,
) -> int:
    """Launch one agent with transient routing and restore-by-construction.

    No agent configuration file is edited.  The process receives only a fresh
    loopback credential, so exiting or removing the profile restores the
    pre-existing agent setup without a rollback write.
    """

    adapter = adapter_for(profile.agent)
    expected_version = (
        adapter.identity.version
        if profile.mode == "direct"
        else SUPPORTED_CLIENT_VERSIONS.get(profile.agent, adapter.identity.version)
    )
    direct_secret = upstream_credential() if profile.mode == "direct" else None
    probe_values = sanitized_client_environment(adapter)
    launch_inherited_values = client_launch_values(adapter)
    if direct_secret is not None:
        # The version probe is an execution boundary too. Remove the persisted
        # source name and any alias carrying the same secret before invoking it.
        probe_values = {
            name: value
            for name, value in probe_values.items()
            if name != profile.credential_env and value != direct_secret
        }
        launch_inherited_values = {
            name: value
            for name, value in launch_inherited_values.items()
            if name != profile.credential_env and value != direct_secret
        }
    selected_executable = executable or supported_client_executable(
        profile.agent,
        expected_version=expected_version,
        environment=probe_values,
    )
    preference = ContextPreferenceStore(state_directory, profile.key)
    metrics_candidate = PersonalMetricsStore(state_directory, profile.key)
    try:
        metrics_candidate.record_session()
    except PersonalMetricsError:
        # Measurement is deliberately fail-open: a damaged or unavailable
        # local ledger must not strand the developer or bypass routing safety.
        metrics: PersonalMetricsStore | None = None
    else:
        metrics = metrics_candidate
    optimizer = RelayOptimizer(
        preference_store=preference,
        client=profile.agent,
        gateway_compatible=(
            True if profile.mode == "direct" else probe_gateway_capability(profile.endpoint)
        ),
        counters=counters,
        metrics=metrics,
    )
    local_credential = new_local_credential()
    upstream_auth = "hormuz" if profile.mode == "managed" else profile.provider
    server = LocalRelayServer(
        gateway=profile.endpoint,
        client=profile.agent,
        local_credential=local_credential,
        gateway_credential=upstream_credential,
        optimizer=optimizer,
        upstream_auth=upstream_auth,
        metrics=metrics,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="hormuz-personal-relay",
        daemon=True,
    )
    thread.start()
    try:
        plan = adapter.launch_plan(
            executable=selected_executable,
            relay_origin=server.origin,
            local_credential=local_credential,
            model=profile.model,
            inherited_values=launch_inherited_values,
        )
        launch_values = plan.environment
        if direct_secret is not None:
            # A built-in adapter must not reintroduce a direct-secret alias.
            launch_values = {
                name: value
                for name, value in launch_values.items()
                if value != direct_secret
            }
        completed = subprocess.run(list(plan.argv), env=launch_values, check=False)
        return int(completed.returncode)
    except OSError as error:
        raise ClientRelayError("client_launch_failed") from error
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if metrics is not None:
            try:
                metrics.record_session(completed=True)
            except PersonalMetricsError:
                pass


def direct_credential_reader(store, profile: str) -> Callable[[], str]:
    def read() -> str:
        value = store.get(profile)
        if value is None:
            raise ClientRelayError("provider_credential_unavailable")
        return value

    return read


__all__ = ["direct_credential_reader", "run_personal_client"]
