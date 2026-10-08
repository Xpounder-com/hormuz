"""Bind an actual Linux GTK fixture run to its original development executable.

This records synthetic X11 checks, not GNOME/Wayland/Secret Service acceptance
or a supported customer distribution. Run only after successful build/tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


def command(*arguments: str) -> str:
    return subprocess.check_output(arguments, text=True).strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if platform.system() != "Linux" or len(sys.argv) != 2:
        raise SystemExit("proof requires Linux and one existing evidence directory")
    output = Path(sys.argv[1]).resolve(strict=True)
    source = command("git", "rev-parse", "HEAD")
    head = os.environ.get("HORMUZ_PR_HEAD", "")
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (source, head)):
        raise SystemExit("exact source and proposed head commits are required")
    binary = Path("target/release/hormuz-linux").resolve(strict=True)
    relay = Path("target/release/hormuz-client-relay").resolve(strict=True)
    test_log = output / "gtk-tests.txt"
    text = test_log.read_text()
    required = (
        "actual_gtk_controls_drive_the_existing_session_and_truthful_snapshot ... ok",
        "missing_or_locked_logind_observations_do_not_enable_refresh ... ok",
        "accepted_same_uid_lease_is_owned_until_explicit_stop ... ok",
        "quit_during_launch_does_not_acknowledge_a_delayed_client ... ok",
        "failed_stop_keeps_the_exact_unit_owned_without_automatic_retries ... ok",
    )
    if not all(case in text for case in required) or "test result: FAILED" in text:
        raise SystemExit("successful actual GTK controls and lock-policy cases are required")
    images = [output / name for name in (
        "connected-desktop.png", "connected-narrow.png", "pinned-details.png"
    )]
    if any(image.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n" for image in images):
        raise SystemExit("native screenshots must be actual PNG images")
    proof = {
        "schema_version": 1,
        "artifact_kind": "unsigned_linux_gtk_development_candidate",
        "source_commit": source,
        "proposed_head": head,
        "compiler": command("rustc", "--version"),
        "os": platform.platform(),
        "target": command("rustc", "-vV").split("host: ")[1].splitlines()[0],
        "gtk_version": command("pkg-config", "--modversion", "gtk4"),
        "sha256": digest(binary),
        "relay_sha256": digest(relay),
        "native_fixture": "passed_actual_gtk_controls_isolated_session_x11_xvfb",
        "desktop_lifecycle_policy": "passed_synthetic_logind_observations",
        "auth_and_network": "isolated_fixture_no_external_provider_requests",
        "screenshots": {image.name: digest(image) for image in images},
        "test_log_sha256": digest(test_log),
        "runtime_dependencies": command("ldd", str(binary)),
        "manual_platform_acceptance": "pending",
        "wayland_compositor_acceptance": "pending",
        "secret_service_desktop_acceptance": "pending",
        "interactive_terminal_and_real_user_manager_acceptance": "pending",
        "customer_distribution": False,
    }
    (output / "linux-gtk-build.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
