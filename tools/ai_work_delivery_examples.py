#!/usr/bin/env python3
"""Preview or execute explicit work bindings and trusted CI observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CONDITIONS = {"github": "github.pull_request.merged.v1", "linear": "linear.issue.completed.v1"}


def failure_reason(failure):
    if isinstance(failure, BindingFailure):
        return failure.reason
    local_reasons = {"invalid_provider_object_or_container_id", "invalid_workflow_reference"}
    if type(failure) is ValueError and str(failure) in local_reasons:
        return str(failure)
    from hormuz.work_client import WorkClientError
    if isinstance(failure, WorkClientError) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,126}", failure.reason):
        return failure.reason
    return "delivery_example_unavailable"


class BindingFailure(ValueError):
    """Retain a safely formatted created ID when the separate binding fails."""

    def __init__(self, failure, work_id):
        self.reason = failure_reason(failure)
        super().__init__(self.reason)
        self.failure_type = type(failure).__name__
        self.work_id = work_id if isinstance(work_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_id) else None


def validate_binding(provider, container_id, object_id):
    numeric = r"[1-9][0-9]{0,19}"
    uuid = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    pattern = numeric if provider == "github" else uuid
    if provider not in CONDITIONS or not re.fullmatch(pattern, container_id) or not re.fullmatch(pattern, object_id):
        raise ValueError("invalid_provider_object_or_container_id")


def observation(result, complete_on_pass):
    if result == "success":
        return "completed" if complete_on_pass else None
    # An interrupted check does not establish abandonment of the whole job.
    return {"failure": "corrected", "cancelled": "unknown", "skipped": "unknown", "unknown": "unknown"}[result]


def perform(args, client=None):
    if args.operation == "bind":
        validate_binding(args.provider, args.container_id, args.object_id)
        if client is None:
            return {"operation": "create_and_bind_work" if not args.work_id else "bind_existing_work", "provider": args.provider, "completion_condition": CONDITIONS[args.provider], "executed": False, "provider_calls": 0}
        work_id = args.work_id
        if not work_id:
            work = client.create_job(args.repository, title="Explicit workflow delivery", task_type="workflow-delivery", context_revision=args.context_revision, completion_condition=CONDITIONS[args.provider])
            work_id = work["work_id"]
        try:
            result = client.job(work_id).bindings(provider=args.provider, connector_id=args.connector_id, container_id=args.container_id, object_id=args.object_id, completion_condition=CONDITIONS[args.provider])
            binding_id = result["binding_id"]
        except Exception as failure:
            if not args.work_id:
                raise BindingFailure(failure, work_id) from None
            raise
        return {"operation": "binding_created", "provider": args.provider, "work_id": work_id, "binding_id": binding_id, "completion_condition": CONDITIONS[args.provider], "executed": True, "provider_calls": 0}
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", args.reference):
        raise ValueError("invalid_workflow_reference")
    status = observation(args.result, args.complete_on_pass)
    if client is not None:
        job = client.job(args.work_id)
        if status:
            job.observe(status, source="workflow", reference=args.reference)
        else:
            job.state()  # An intermediate pass does not submit completion.
    return {"operation": "workflow_report", "observation_status": status, "observation_submitted": client is not None and status is not None, "source_claim": "authenticated_workflow_reporter", "independently_signed_connector": False, "executed": client is not None, "provider_calls": 0}


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="operation", required=True)
    bind = commands.add_parser("bind", help="Create/bind work to an enrolled GitHub PR or Linear issue; preview by default.")
    bind.add_argument("--provider", choices=tuple(CONDITIONS), required=True)
    bind.add_argument("--connector-id", required=True)
    bind.add_argument("--container-id", required=True, help="GitHub repository database ID or enrolled Linear project UUID.")
    bind.add_argument("--object-id", required=True, help="GitHub pull request database ID (not PR number), or Linear issue UUID.")
    bind.add_argument("--repository", required=True)
    bind.add_argument("--context-revision", default="workflow-example-v1")
    bind.add_argument("--work-id", help="Use an owned job already created with the matching completion condition.")
    report = commands.add_parser("report", help="Report an actually observed CI outcome for an owned job; preview by default.")
    report.add_argument("--work-id", required=True)
    report.add_argument("--result", choices=("success", "failure", "cancelled", "skipped", "unknown"), required=True)
    report.add_argument("--reference", required=True, help="Stable bounded workflow/run identity, used for idempotency.")
    report.add_argument("--complete-on-pass", action="store_true", help="Use only when this check is the job's declared completion criterion.")
    for command in (bind, report):
        command.add_argument("--execute", action="store_true", help="Send through your authorized gateway; otherwise preview only.")
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        client = None
        if args.execute:
            from hormuz.work_client import WorkClient
            client = WorkClient.from_environment()
        print(json.dumps(perform(args, client), sort_keys=True))
        return 0
    except Exception as failure:
        receipt = {"status": "failed", "reason": failure_reason(failure), "failure_type": type(failure).__name__}
        if isinstance(failure, BindingFailure):
            receipt["failure_type"] = failure.failure_type
            if failure.work_id is not None:
                receipt.update(work_id=failure.work_id, work_created=True, binding_status="unknown", retry_with_existing_work=True)
        print(json.dumps(receipt, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
