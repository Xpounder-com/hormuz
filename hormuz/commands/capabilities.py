"""Public, content-free plugin operations shared by all skill hosts."""
import json
from pathlib import Path
import subprocess

from ..config import ConfigError
from ..local_history import LocalHistory, LocalHistoryError
from ..plugin_policy import CapabilityError, configure
from ..connected_client import ConnectedClientError


def add_commands(commands):
    usage = commands.add_parser("usage", help="Content-free retained usage and spending observations")
    usage.add_subparsers(dest="usage_command", required=True).add_parser("insights")
    request = commands.add_parser("request", help="Explain retained request metadata without content")
    explanation = request.add_subparsers(dest="request_command", required=True).add_parser("explain")
    explanation.add_argument("id", help="ID from history list; explicit prior history opt-in required")
    policy = commands.add_parser("policy", help="Preview and stage local policy changes")
    configuration = policy.add_subparsers(dest="policy_command", required=True).add_parser("configure")
    configuration.add_argument("--local-only", action="store_true")
    configuration.add_argument("--monthly-budget-usd", help="Positive configured-cost estimate ceiling")
    configuration.add_argument("--output", required=True, help="New private configuration beside the source")
    configuration.add_argument("--dry-run", action="store_true")
    model = commands.add_parser("model", help="Bounded local qualification and evidence-based recommendation")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    for operation in ("qualify", "recommend"):
        command = model_commands.add_parser(operation)
        command.add_argument("--model", required=True, help="Configured local Responses alias")
        command.add_argument("--client-executable", required=True)
        command.add_argument("--runtime-executable", required=True)
        command.add_argument("--model-file", required=True)
        command.add_argument("--actor")
        if operation == "qualify":
            command.add_argument("--output", required=True, help="New private local report; never overwritten")
        else:
            command.add_argument("--qualification", required=True, help="Existing private user-owned report")


def run(args):
    try:
        if args.command == "policy":
            result = configure(args)
        elif args.command == "usage":
            result = LocalHistory(Path(args.config)).insights(detailed=True)
        elif args.command == "model":
            from ..model_qualification import qualify, recommend
            result = qualify(args) if args.model_command == "qualify" else recommend(args)
        else:
            history = LocalHistory(Path(args.config))
            observations = history.list_entries(1000)["entries"]
            observation = next((item for item in observations if item["id"] == args.id), None)
            if observation is None:
                raise CapabilityError("request_evidence_unavailable")
            result = {"schema_id": "hormuz.request-explanation", "schema_version": 1,
                  "coverage": "retained_opt_in_history_only", "observation": observation,
                  "policy_decision": observation.get("routing_evidence", {}).get("policy_action", "unobserved"),
                  "fallback": observation.get("routing_evidence", {}).get("fallback", "unobserved"),
                  "optimization": "unobserved", "scope": "intercepted_requests_only"}
        print(json.dumps(result, sort_keys=True))
        return 2 if result.get("result") == "failed" else 0
    except (LocalHistoryError, ConnectedClientError, ConfigError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        code = error.code if isinstance(error, CapabilityError) else "capability_unavailable"
        print(json.dumps({"schema_id": "hormuz.capability-error", "schema_version": 1,
                          "error": {"code": code}}))
        return 2
