"""Runtime assembly for personal direct and managed profiles."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from .adapters import adapter_for
from .client_relay import (
    ClientRelayError,
    LocalRelayServer,
    RelayOptimizer,
    new_local_credential,
    probe_gateway_capability,
    supported_client_executable,
)
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
    selected_executable = executable or supported_client_executable(
        profile.agent, expected_version=adapter.identity.version
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
        )
        completed = subprocess.run(list(plan.argv), env=plan.environment, check=False)
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
