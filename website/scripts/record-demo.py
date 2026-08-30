"""Record real, credential-free CLI output; never invent terminal lines or timings.

Run with an interpreter that can import the installed Hormuz dependencies.
The current checkout supplies the product module. Generated data is public,
synthetic evidence, not an external-user validation result.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from recording_support import collect_output, verified_revision

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "website/public/demo"


def record(name: str, arguments: list[str]) -> dict:
    revision = verified_revision(ROOT)
    # An allowlist prevents accidental provider credentials/proxy settings from
    # reaching the child. Hormuz's demo creates its own synthetic identities.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "LANG": "en_US.UTF-8"}
    events, exit_code, duration = collect_output(
        [sys.executable, "-u", "-m", "hormuz", *arguments], cwd=ROOT, env=env,
    )
    if verified_revision(ROOT) != revision:
        raise RuntimeError("Source HEAD changed while recording")
    transcript = "".join(event[2] for event in events)
    if exit_code != 0 or not events:
        raise RuntimeError(f"{name} failed (exit {exit_code}): {transcript}")
    if name == "gateway" and (transcript.count("PASS ") != 6 or "external provider calls: 0" not in transcript):
        raise RuntimeError("Gateway recording did not contain the six expected checks")
    command = "hormuz " + " ".join(arguments)
    manifest = {
        "schema": "hormuz.website-recording.v1", "command": command,
        "executed_as": ["python", "-u", "-m", "hormuz", *arguments],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": revision, "python": sys.version.split()[0],
        "exit_code": exit_code, "duration_seconds": duration,
        "boundary": "Real CLI output from synthetic local inputs. Not a live-provider benchmark, customer result, or independent-user study.",
        "events": events, "transcript": transcript,
        "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
    }
    (OUTPUT / f"{name}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUTPUT / f"{name}.txt").write_text(transcript)
    header = {"version": 2, "width": 110, "height": 36, "title": command, "command": command}
    (OUTPUT / f"{name}.cast").write_text("\n".join(json.dumps(row) for row in [header, *events]) + "\n")
    return {"recording": name, "exit_code": exit_code, "events": len(events), "source_revision": revision}


if __name__ == "__main__":
    verified_revision(ROOT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for recording, args in [("gateway", ["demo"]), ("policy", ["policy", "demo"])]:
        print(json.dumps(record(recording, args)))
