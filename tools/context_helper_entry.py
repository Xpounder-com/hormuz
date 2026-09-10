"""Credential-free entry point for the packaged local context helper.

Keep this entry point separate from ``hormuz.cli`` so the desktop helper does
not collect gateway-only dependencies or commands into either native backend.
"""

from __future__ import annotations

import argparse

from hormuz.commands.context import add_context_commands, run


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="hormuz-context",
        description="Hormuz client-side context optimization helper.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    add_context_commands(commands)
    args = parser.parse_args()
    if args.command != "context":
        parser.error("unsupported command")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
