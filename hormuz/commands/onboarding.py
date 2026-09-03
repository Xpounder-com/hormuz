"""Server-local team administration. Never emit invitation codes to stdout."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from ..config import GatewayConfig
from ..console_store import CONSOLE_ROLES, ConsoleStore
from ..onboarding import Invitation, TeamDirectory
from ..session_store import SQLiteSessionStore, SessionStoreError


def add_onboarding_commands(subparsers: argparse._SubParsersAction) -> None:
    root = subparsers.add_parser("team", help="Manage local broker teams and invitations (server operator only)")
    commands = root.add_subparsers(dest="team_command", required=True)
    organization = commands.add_parser("organization", help="Create or list managed organizations")
    organizations = organization.add_subparsers(dest="organization_command", required=True)
    create_org = organizations.add_parser("create")
    _scope(create_org)
    create_org.add_argument("--name", required=True)
    create_org.add_argument("--issuer", required=True)
    _pagination(organizations.add_parser("list"))
    create = commands.add_parser("create", help="Create a team with a globally unique ID")
    _scope(create)
    create.add_argument("--team", required=True)
    create.add_argument("--name", required=True)
    listing = commands.add_parser("list")
    _scope(listing)
    _pagination(listing)
    invite = commands.add_parser("invite", help="Write a one-time invitation to a new private file")
    _scope(invite)
    invite.add_argument("--team", required=True)
    invite.add_argument("--email-file", required=True, help="Private UTF-8 file containing just the recipient email")
    invite.add_argument("--name", required=True, help="Member display name; not an identity or role claim")
    invite.add_argument("--client", required=True, action="append", choices=["codex", "claude-code"])
    invite.add_argument("--clearance", default="internal", choices=["public", "internal", "confidential", "restricted"])
    _invitation_output(invite)
    members = commands.add_parser("members").add_subparsers(dest="member_command", required=True)
    member_list = members.add_parser("list")
    _scope(member_list)
    _pagination(member_list)
    for action in ("disable", "reinvite"):
        command = members.add_parser(action)
        _scope(command)
        command.add_argument("--member", required=True)
        if action == "reinvite":
            _invitation_output(command)
    invitations = commands.add_parser("invitations").add_subparsers(dest="invitation_command", required=True)
    invitation_list = invitations.add_parser("list")
    _scope(invitation_list)
    _pagination(invitation_list)
    revoke = invitations.add_parser("revoke")
    _scope(revoke)
    revoke.add_argument("--invitation", required=True)
    events = commands.add_parser("events", help="List metadata-only local operator/member transitions")
    _scope(events)
    _pagination(events)
    administrators = commands.add_parser("administrators", help="Manage separate console grants (server operator only)")
    admin_commands = administrators.add_subparsers(dest="administrator_command", required=True)
    for action in ("grant", "list", "revoke"):
        command = admin_commands.add_parser(action)
        _scope(command)
        if action == "list":
            _pagination(command)
        else:
            command.add_argument("--member", required=True)
        if action == "grant":
            command.add_argument("--role", required=True, choices=CONSOLE_ROLES)


def _scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization", required=True)


def _pagination(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--after", default="", help="Cursor from the preceding listing")
    parser.add_argument("--limit", type=int, default=50, help="Page size, 1 to 100")


def _invitation_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, help="New private invitation JSON file; existing files are refused")
    parser.add_argument("--expires-in", type=int, default=3600, help="Invitation lifetime in seconds, 300 to 86400")


def run(config: GatewayConfig, args: argparse.Namespace) -> int:
    settings = config.session_broker
    if not settings.enabled or not settings.onboarding_enabled:
        raise SessionStoreError("onboarding_disabled")
    store = SQLiteSessionStore(
        settings.database_path, master_key=settings.master_key, audience=settings.public_base_url,
        access_ttl_seconds=settings.access_ttl_seconds, absolute_ttl_seconds=settings.absolute_ttl_seconds,
        enrollment_ttl_seconds=settings.enrollment_ttl_seconds, trusted_parent_path=settings.trusted_parent_path,
    )
    directory = TeamDirectory(config, store)
    action = args.team_command
    if action == "organization" and args.organization_command == "create":
        result = {"changed": directory.create_organization(organization_id=args.organization, name=args.name, issuer=args.issuer)}
    elif action == "create":
        result = {"changed": directory.create_team(organization_id=args.organization, team_id=args.team, name=args.name)}
    elif action == "invite" or action == "members" and args.member_command == "reinvite":
        # Validate the recipient before opening the output or changing the DB.
        email = _read_email_file(Path(args.email_file)) if action == "invite" else None

        def issue() -> Invitation:
            if action == "invite":
                return directory.invite(organization_id=args.organization, team_id=args.team, email=email, name=args.name,
                                        allowed_clients=tuple(args.client), clearance=args.clearance, expires_in=args.expires_in)
            return directory.reinvite(organization_id=args.organization, membership_id=args.member, expires_in=args.expires_in)

        result = _write_invitation(Path(args.output).expanduser(), directory, issue, settings.public_base_url)
    elif action == "members" and args.member_command == "disable":
        result = directory.disable_member(organization_id=args.organization, membership_id=args.member)
    elif action == "invitations" and args.invitation_command == "revoke":
        result = {"revoked": directory.revoke_invitation(organization_id=args.organization, invitation_id=args.invitation)}
    elif action == "administrators":
        console = ConsoleStore(store, directory)
        if args.administrator_command == "grant":
            result = console.grant(organization_id=args.organization, membership_id=args.member, role=args.role)
        elif args.administrator_command == "revoke":
            result = console.revoke(organization_id=args.organization, membership_id=args.member)
        else:
            result = console.list_grants(organization_id=args.organization, after=args.after, limit=args.limit)
    else:
        kind = {"organization": "organizations", "list": "teams", "members": "memberships", "invitations": "invitations", "events": "events"}[action]
        result = directory.list_records(kind, organization_id=getattr(args, "organization", None), after=args.after, limit=args.limit)
    print(json.dumps(result, sort_keys=True))
    return 0


def _read_email_file(path: Path) -> str:
    try:
        descriptor = os.open(path.expanduser(), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or os.name != "nt" and info.st_mode & 0o077:
                raise SessionStoreError("onboarding_private_email_file_required")
            value = source.read(256)
            if len(value) == 256:
                raise SessionStoreError("onboarding_invalid_email")
            email = value.decode("utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        raise SessionStoreError("onboarding_private_email_file_unavailable") from None
    from ..onboarding import normalize_email
    return normalize_email(email)


def _write_invitation(path: Path, directory: TeamDirectory, issue, gateway: str) -> dict[str, object]:
    if os.name == "nt":
        # POSIX modes do not provide a private ACL on Windows. This operator
        # command targets the Linux/macOS server; client login is separate.
        raise SessionStoreError("onboarding_posix_operator_required")
    invitation = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            invitation = issue()
            json.dump({"schema_version": 1, "gateway": gateway, "organization_id": invitation.organization_id,
                       "membership_id": invitation.membership_id, "invitation_id": invitation.invitation_id,
                       "expires_at": invitation.expires_at.isoformat(), "invitation_code": invitation.code}, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        return {"invitation_id": invitation.invitation_id, "membership_id": invitation.membership_id,
                "expires_at": invitation.expires_at.isoformat(), "private_file_written": True}
    except (OSError, SessionStoreError) as error:
        try:
            if invitation is not None:
                directory.revoke_invitation(organization_id=invitation.organization_id, invitation_id=invitation.invitation_id)
        finally:
            if created:
                try:
                    path.unlink()
                except OSError:
                    pass
        if isinstance(error, SessionStoreError):
            raise
        raise SessionStoreError("onboarding_private_output_unavailable") from None
