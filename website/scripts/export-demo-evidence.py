"""Export only validated synthetic events from a separate provider-free run.

No product source is modified. The hook first runs the product's own content
exclusion/schema checks, then copies exactly the validated event projection.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hormuz import demo

original = demo._verify_evidence
captured = []


def capture(gateway, *, forbidden_values):
    counts = original(gateway, forbidden_values=forbidden_values)
    events = gateway.store.audit_events(since=demo._SINCE_ALL_EVENTS, organization_id="provider-free-demo")
    serialized = json.dumps(events, sort_keys=True)
    if any(value in serialized for value in forbidden_values):
        raise RuntimeError("Synthetic content entered evidence")
    captured.extend(events)
    return counts


if __name__ == "__main__":
    demo._verify_evidence = capture
    try:
        result = demo.run_provider_free_demo()
    finally:
        demo._verify_evidence = original
    if len(captured) != 5:
        raise RuntimeError("Expected exactly five synthetic events")
    destination = ROOT / "website/public/demo/synthetic-evidence.jsonl"
    destination.write_text("\n".join(json.dumps(event, sort_keys=True) for event in captured) + "\n")
    print(json.dumps({"synthetic_events": len(captured), "usage_events": result.usage_events, "security_events": result.security_events, "provider_simulator_calls": result.provider_simulator_calls, "validation": "product schema and forbidden-content checks passed"}))
