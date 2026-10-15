"""Explicit local-history management without model credentials or a live router."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def add_history_commands(subparsers: argparse._SubParsersAction) -> None:
    history = subparsers.add_parser("history", help="Manage opt-in encrypted history on this device")
    commands = history.add_subparsers(dest="history_command", required=True)
    commands.add_parser("status", help="Show collection consent and local storage metadata")
    enable = commands.add_parser("enable", help="Opt in to future redacted content and usage capture")
    enable.add_argument("--actor", help="Configured actor ID (required when there are multiple actors)")
    enable.add_argument("--retention-days", type=int, default=30, help="Retention in days (default 30)")
    commands.add_parser("disable", help="Stop future capture; retain existing history")
    listing = commands.add_parser("list", help="List retained request metadata without content")
    listing.add_argument("--limit", type=int, default=50, help="Maximum entries (default 50)")
    show = commands.add_parser("show", help="Explicitly review one entry including decrypted redacted content")
    show.add_argument("id", help="Retained history entry ID")
    commands.add_parser("insights", help="Summarize retained usage and configured cost estimates without content")
    export = commands.add_parser("export", help="Write decrypted confidential content to a local file; no uploads or overwrite")
    export.add_argument("--output", required=True, help="New local file containing decrypted confidential content; never overwritten")
    export.add_argument("--id", action="append", dest="entry_ids", help="Export only this entry ID (repeatable; default all)")
    delete = commands.add_parser("delete", help="Permanently delete retained history; usage accounting is retained")
    selection = delete.add_mutually_exclusive_group(required=True)
    selection.add_argument("id", nargs="?", help="Delete one entry ID")
    selection.add_argument("--all", action="store_true", help="Delete all retained entries and invalidate pending captures")


def _actor(config_path: str, selected: str | None) -> str:
    from .._config_input import load_configuration_input
    from ..local_history import LocalHistoryError

    try:
        raw = load_configuration_input(Path(config_path).expanduser())
        identities = raw.get("identities", [])
        if not isinstance(identities, list):
            raise ValueError
        actors = {item["actor_id"] for item in identities if isinstance(item, dict)
                  and isinstance(item.get("actor_id"), str) and item["actor_id"].strip()}
    except (OSError, ValueError, AttributeError, TypeError):
        raise LocalHistoryError("history_configuration_invalid") from None
    if selected is not None:
        if selected not in actors:
            raise LocalHistoryError("history_actor_unknown")
        return selected
    if len(actors) != 1:
        raise LocalHistoryError("history_actor_required")
    return next(iter(actors))


def run(args: argparse.Namespace) -> int:
    from ..local_history import LocalHistory, LocalHistoryError

    try:
        history = LocalHistory(Path(args.config))
        if args.history_command == "enable":
            result = history.enable(_actor(args.config, args.actor), args.retention_days)
        elif args.history_command == "disable":
            result = history.disable()
        elif args.history_command == "list":
            result = history.list_entries(args.limit)
        elif args.history_command == "show":
            result = history.get(args.id)
        elif args.history_command == "insights":
            result = history.insights()
        elif args.history_command == "export":
            result = history.export(Path(args.output), args.entry_ids)
        elif args.history_command == "delete":
            result = history.delete_all() if args.all else history.delete(args.id)
        else:
            result = history.status()
        print(json.dumps({"version": 1, **result}, indent=2, sort_keys=True))
        return 0
    except LocalHistoryError as error:
        code = error.code
    except (OSError, ValueError, TypeError):
        code = "history_unavailable"
    print(json.dumps({"version": 1, "schema_id": "hormuz.local-history-error", "schema_version": 1,
                      "error": {"code": code}}, sort_keys=True))
    return 2
