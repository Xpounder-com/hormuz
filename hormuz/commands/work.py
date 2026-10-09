"""Network client commands: no local gateway configuration or provider key needed."""

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from ..work_client import WorkClient, WorkClientError
from ..work_learning import REQUEST_KINDS


def add_work_commands(subparsers):
    work = subparsers.add_parser("work", help="Control captured AI work through an authenticated gateway")
    work.add_argument("--gateway", default=os.environ.get("HORMUZ_GATEWAY_URL", ""))
    work.add_argument("--credential-env", default="HORMUZ_TOKEN", help="Environment variable containing an authorized Hormuz credential")
    work.add_argument("--allow-loopback-http", action="store_true", help="Permit HTTP only to a local loopback gateway")
    commands = work.add_subparsers(dest="work_command", required=True)
    commands.add_parser("connect", help="Show verified identity's configured API coverage")
    commands.add_parser("state", help="Show captured work, plans, and accounting evidence")
    commands.add_parser("activation", help="Show qualification and signed payment activation state")
    commands.add_parser("support", help="Export an authenticated metadata support receipt")
    for name in ("checkout", "portal", "reverify"):
        commands.add_parser(name, help="Open the approved paid lifecycle action; operator-only for reverification")
    qualify = commands.add_parser("qualify", help="Request compatibility review before subscribing")
    qualify.add_argument("--model", required=True)
    qualify.add_argument("--client", required=True, choices=("codex", "claude-code"))
    review = commands.add_parser("review", help="Operator: approve or reject a requested workspace qualification")
    review.add_argument("--action", choices=("approve", "reject"), required=True)
    review.add_argument("--reference", required=True)
    reset = commands.add_parser("reset", help="Operator: close activation authority for recovery")
    reset.add_argument("--reference", required=True)
    create = commands.add_parser("create", help="Create a durable work ID for existing agents")
    create.add_argument("--repository", required=True)
    create.add_argument("--title")
    create.add_argument("--task-type", help="Declare a task cohort, or omit for conservative automatic characteristics")
    create.add_argument("--context-revision")
    create.add_argument("--completion-condition", default="workflow.completed.v1", choices=("workflow.completed.v1", "github.pull_request.merged.v1", "github.check.passed.v1", "linear.issue.completed.v1"))
    plan = commands.add_parser("plan", help="Set an authorized workspace, repository, or owned job plan")
    plan.add_argument("--scope", choices=("workspace", "repository", "job"), required=True)
    plan.add_argument("--scope-id", required=True)
    amount = plan.add_mutually_exclusive_group(required=True)
    amount.add_argument("--budget-usd")
    amount.add_argument("--uncapped", action="store_true")
    plan.add_argument("--objective", choices=("cost", "speed", "quality"), default="cost")
    plan.add_argument("--expected-version", type=int)
    exploration = plan.add_mutually_exclusive_group()
    exploration.add_argument("--exploration", action="store_true", help="Enable bounded operator-approved alternative-route learning")
    exploration.add_argument("--no-exploration", action="store_true")
    exploration.add_argument("--inherit-exploration", action="store_true", help="Clear this plan's override and inherit its parent")
    bindings = commands.add_parser("bindings", help="List an owned job's signed-source associations")
    bindings.add_argument("--work-id", required=True)
    bind = commands.add_parser("bind", help="Associate one owned job with an enrolled signed source object")
    bind.add_argument("--work-id", required=True)
    bind.add_argument("--provider", choices=("github", "linear"), required=True)
    for field in ("connector-id", "container-id", "object-id", "completion-condition"):
        bind.add_argument("--" + field, required=True)
    for name in ("headers", "pause", "resume", "stop", "approve"):
        command = commands.add_parser(name, help="Print work header" if name == "headers" else "Change owned job state or allowance")
        command.add_argument("--work-id", required=True)
        if name == "approve":
            command.add_argument("--budget-usd", required=True, help="New total job allowance, not an increment")
            command.add_argument("--expected-version", type=int)
    observe = commands.add_parser("observe", help="Submit explicitly sourced workflow evidence")
    observe.add_argument("--work-id", required=True)
    observe.add_argument("--status", choices=("completed", "corrected", "reopened", "unknown", "canceled"), required=True)
    observe.add_argument("--source", choices=("workflow", "agent", "operator"), default="workflow")
    observe.add_argument("--reference")
    check = commands.add_parser("check", help="Run a real local check and report its declared completion criterion")
    check.add_argument("--work-id", required=True)
    check.add_argument("--reference", required=True)
    check.add_argument("--completes-work", action="store_true", help="A passing check is the declared completion condition for this job")
    check.add_argument("check_command", nargs=argparse.REMAINDER)
    request = commands.add_parser("request", help="Send a non-streaming supported API request attached to an owned job")
    request.add_argument("--work-id", required=True)
    request.add_argument("--path", choices=("/v1/responses", "/v1/chat/completions", "/v1/messages"), required=True)
    request.add_argument("--body-file", type=Path, required=True)
    request.add_argument("--request-kind", choices=sorted(REQUEST_KINDS), help="Identify retries, polling or legitimate continuation for recurrence accounting")


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,6})?", value):
        raise WorkClientError("invalid_budget_usd")
    return int(Decimal(value) * 1_000_000)


def run(args):
    try:
        client = WorkClient(args.gateway, os.environ.get(args.credential_env, ""), allow_loopback_http=args.allow_loopback_http)
        command = args.work_command
        if command in {"state", "connect"}:
            result = getattr(client, command)()
        elif command == "activation":
            result = client.activation()
        elif command == "support":
            result = client.support_receipt()
        elif command in {"checkout", "portal"}:
            result = client.billing(command)
        elif command == "qualify":
            result = client.activation("request", model=args.model, client=args.client)
        elif command == "review":
            result = client.activation("review", action=args.action, reference=args.reference)
        elif command == "reset":
            result = client.activation("reset", reference=args.reference)
        elif command == "reverify":
            result = client.activation("reverify")
        elif command == "create":
            result = client.create_job(args.repository, title=args.title, task_type=args.task_type, context_revision=args.context_revision, completion_condition=args.completion_condition)
        elif command == "plan":
            options = {}
            if args.exploration or args.no_exploration or args.inherit_exploration:
                options["exploration_enabled"] = True if args.exploration else False if args.no_exploration else None
            result = client.set_plan(args.scope, args.scope_id, budget_microusd=None if args.uncapped else _amount(args.budget_usd), objective=args.objective, expected_version=args.expected_version, **options)
        else:
            job = client.job(args.work_id)
            if command == "bind":
                result = job.bindings(provider=args.provider, connector_id=args.connector_id, container_id=args.container_id, object_id=args.object_id, completion_condition=args.completion_condition)
            elif command == "bindings":
                result = job.bindings()
            elif command == "headers":
                result = job.headers
            elif command == "observe":
                result = job.observe(args.status, source=args.source, reference=args.reference)
            elif command == "request":
                if args.body_file.stat().st_size > 1_048_576:
                    raise WorkClientError("request_body_too_large")
                result = job.request(args.path, json.loads(args.body_file.read_text(encoding="utf-8")), request_kind=args.request_kind)
            elif command == "check":
                words = args.check_command[1:] if args.check_command[:1] == ["--"] else args.check_command
                if not words:
                    raise WorkClientError("check_command_required")
                # Explicit local command, never shell interpolation. The check
                # exit status is evidence; model response text is not.
                completed = subprocess.run(words, check=False)
                job.observe_check(completed.returncode, reference=args.reference, completes_work=args.completes_work)
                return completed.returncode
            else:
                result = job.act("approve_budget" if command == "approve" else command, budget_microusd=_amount(args.budget_usd) if command == "approve" else None, expected_version=args.expected_version if command == "approve" else None)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (WorkClientError, OSError, ValueError) as error:
        reason = error.reason if isinstance(error, WorkClientError) else "work_client_failed"
        print("Hormuz work: " + reason, file=sys.stderr)
        return 1
