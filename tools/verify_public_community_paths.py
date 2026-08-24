#!/usr/bin/env python3
"""Fail-closed validation for Hormuz's public contribution and support paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = 1
REQUIRED_DOCUMENTS = (
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "SUPPORT.md",
)
FORM_CONTRACT = {
    "bug.yml": ("Bug report", "[Bug]: ", ("bug",)),
    "installation.yml": (
        "Installation or first-run failure",
        "[Install]: ",
        ("bug",),
    ),
    "feature.yml": ("Feature request", "[Feature]: ", ("enhancement",)),
    "documentation.yml": (
        "Documentation problem",
        "[Docs]: ",
        ("documentation",),
    ),
}
REQUIRED_CONTACTS = {
    "https://github.com/Xpounder-com/hormuz/security/advisories/new",
    "https://github.com/Xpounder-com/hormuz/blob/main/SUPPORT.md",
}
ALLOWED_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}
PROHIBITED_PLACEHOLDERS = (
    "[insert contact method]",
    "todo",
    "tbd",
    "your@email",
    "example.com",
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FORM_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class CommunityPathError(ValueError):
    """Raised when a public community surface is incomplete or unsafe."""


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CommunityPathError(f"cannot read required file: {path.name}") from exc
    if not text.strip():
        raise CommunityPathError(f"required file is empty: {path.name}")
    return text


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise CommunityPathError(
            f"{path.name} must use the JSON-compatible YAML subset"
        ) from exc
    if not isinstance(value, dict):
        raise CommunityPathError(f"{path.name} must contain one object")
    return value


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommunityPathError(f"{field} must be a non-empty string")
    return value


def _validate_form(path: Path, expected: tuple[str, str, tuple[str, ...]]) -> None:
    form = _read_json(path)
    if set(form) != {"name", "description", "title", "labels", "body"}:
        raise CommunityPathError(f"{path.name} has unsupported or missing top-level fields")

    expected_name, expected_title, expected_labels = expected
    if form["name"] != expected_name or form["title"] != expected_title:
        raise CommunityPathError(f"{path.name} name/title contract changed")
    _nonempty_string(form["description"], f"{path.name}.description")
    labels = form["labels"]
    if not isinstance(labels, list) or tuple(labels) != expected_labels:
        raise CommunityPathError(f"{path.name} label contract changed")

    body = form["body"]
    if not isinstance(body, list) or len(body) < 4:
        raise CommunityPathError(f"{path.name}.body must contain a complete form")

    seen_ids: set[str] = set()
    has_required_disclosure = False
    warning_text = ""
    for index, item in enumerate(body):
        field = f"{path.name}.body[{index}]"
        if not isinstance(item, dict):
            raise CommunityPathError(f"{field} must be an object")
        item_type = item.get("type")
        if item_type not in ALLOWED_FORM_TYPES:
            raise CommunityPathError(f"{field} has unsupported type")
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            raise CommunityPathError(f"{field}.attributes must be an object")

        if item_type == "markdown":
            warning_text += " " + _nonempty_string(
                attributes.get("value"), f"{field}.attributes.value"
            )
            if set(item) != {"type", "attributes"}:
                raise CommunityPathError(f"{field} markdown has unsupported fields")
            continue

        item_id = _nonempty_string(item.get("id"), f"{field}.id")
        if not FORM_ID.fullmatch(item_id) or item_id in seen_ids:
            raise CommunityPathError(f"{field}.id is invalid or duplicated")
        seen_ids.add(item_id)
        _nonempty_string(attributes.get("label"), f"{field}.attributes.label")

        if item_type in {"input", "textarea", "dropdown"}:
            validations = item.get("validations")
            if not isinstance(validations, dict) or set(validations) != {"required"}:
                raise CommunityPathError(f"{field}.validations must declare required")
            if not isinstance(validations["required"], bool):
                raise CommunityPathError(f"{field}.validations.required must be boolean")
        elif item_type == "checkboxes":
            if set(item) != {"type", "id", "attributes"}:
                raise CommunityPathError(f"{field} checkbox has unsupported fields")
            options = attributes.get("options")
            if not isinstance(options, list) or not options:
                raise CommunityPathError(f"{field} checkbox must contain options")
            for option in options:
                if not isinstance(option, dict) or set(option) != {"label", "required"}:
                    raise CommunityPathError(f"{field} checkbox option is invalid")
                _nonempty_string(option["label"], f"{field}.option.label")
                if option["required"] is not True:
                    raise CommunityPathError(f"{field} checkbox option must be required")
            if item_id == "disclosure":
                has_required_disclosure = True

        if item_type == "dropdown":
            options = attributes.get("options")
            if (
                not isinstance(options, list)
                or len(options) < 2
                or any(not isinstance(option, str) or not option for option in options)
            ):
                raise CommunityPathError(f"{field} dropdown options are invalid")

    lowered_warning = warning_text.lower()
    if "credential" not in lowered_warning or "customer" not in lowered_warning:
        raise CommunityPathError(f"{path.name} lacks a disclosure warning")
    if not has_required_disclosure:
        raise CommunityPathError(f"{path.name} lacks a required disclosure checkbox")


def _validate_config(path: Path) -> None:
    config = _read_json(path)
    if set(config) != {"blank_issues_enabled", "contact_links"}:
        raise CommunityPathError("issue-template config fields changed")
    if config["blank_issues_enabled"] is not False:
        raise CommunityPathError("blank public issues must remain disabled")
    contacts = config["contact_links"]
    if not isinstance(contacts, list) or len(contacts) != len(REQUIRED_CONTACTS):
        raise CommunityPathError("issue-template contact paths are incomplete")
    urls: set[str] = set()
    for contact in contacts:
        if not isinstance(contact, dict) or set(contact) != {"name", "url", "about"}:
            raise CommunityPathError("issue-template contact entry is invalid")
        _nonempty_string(contact["name"], "contact.name")
        _nonempty_string(contact["about"], "contact.about")
        urls.add(_nonempty_string(contact["url"], "contact.url"))
    if urls != REQUIRED_CONTACTS:
        raise CommunityPathError("issue-template contact URLs changed")


def _validate_link(root: Path, source: Path, raw_target: str) -> None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    parsed = urlparse(target)
    if parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc:
            raise CommunityPathError(f"{source.name} contains a non-HTTPS public link")
        if parsed.netloc == "github.com" and parsed.path.startswith("/Xpounder-com/"):
            if not parsed.path.startswith("/Xpounder-com/hormuz"):
                raise CommunityPathError(f"{source.name} links to the wrong project")
        return
    if target.startswith("#"):
        return
    relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not relative:
        return
    resolved = (source.parent / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CommunityPathError(f"{source.name} link escapes the repository") from exc
    if not resolved.is_file():
        raise CommunityPathError(f"{source.name} links to missing file: {relative}")


def _validate_markdown(root: Path, path: Path) -> str:
    text = _read_text(path)
    lowered = text.lower()
    for placeholder in PROHIBITED_PLACEHOLDERS:
        if placeholder in lowered:
            raise CommunityPathError(f"{path.name} contains placeholder text")
    for target in MARKDOWN_LINK.findall(text):
        _validate_link(root, path, target)
    return text


def _extract_required(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise CommunityPathError(f"cannot derive {label} from release configuration")
    return match.group(1)


def _validate_support_matrix(root: Path, support: str) -> None:
    ci = _read_text(root / ".github/workflows/ci.yml")
    python_line = _extract_required(
        r'python-version:\s*\[([^\]]+)\]', ci, "Python matrix"
    )
    python_versions = re.findall(r'"(3\.\d+)"', python_line)
    if python_versions != ["3.11", "3.12", "3.13", "3.14"]:
        raise CommunityPathError("blocking Python matrix changed without support review")
    for version in python_versions:
        if version not in support:
            raise CommunityPathError(f"SUPPORT.md omits Python {version}")

    expected = {
        "Codex": _extract_required(r'CODEX_VERSION:\s*"([^"]+)"', ci, "Codex version"),
        "Claude Code": _extract_required(
            r'CLAUDE_CODE_VERSION:\s*"([^"]+)"', ci, "Claude Code version"
        ),
        "Node.js": _extract_required(
            r'node-version:\s*"([^"]+)"', ci, "Node.js version"
        ),
        "Buildx": _extract_required(
            r'docker/setup-buildx-action[^\n]*\n\s+with:\n\s+version:\s*(v[^\s]+)',
            ci,
            "Buildx version",
        ),
    }
    for label, version in expected.items():
        if version not in support:
            raise CommunityPathError(f"SUPPORT.md omits pinned {label} {version}")

    preflight = _read_text(root / "tools/verify_oci_release_preflight.py")
    platform = _extract_required(
        r'^SUPPORTED_PLATFORM\s*=\s*"([^"]+)"', preflight, "OCI platform"
    )
    if platform != "linux/amd64" or "Linux `amd64` only" not in support:
        raise CommunityPathError("SUPPORT.md OCI platform boundary drifted")


def validate_public_community_paths(root: Path) -> dict[str, object]:
    root = root.resolve()
    markdown_paths = [root / name for name in REQUIRED_DOCUMENTS]
    markdown_paths.extend((root / "README.md", root / ".github/pull_request_template.md"))
    markdown = {path.name: _validate_markdown(root, path) for path in markdown_paths}

    for document in REQUIRED_DOCUMENTS:
        if f"]({document})" not in markdown["README.md"] and document != "README.md":
            raise CommunityPathError(f"README.md does not expose {document}")

    issue_root = root / ".github/ISSUE_TEMPLATE"
    _validate_config(issue_root / "config.yml")
    for filename, expected in FORM_CONTRACT.items():
        _validate_form(issue_root / filename, expected)

    pr_template = markdown["pull_request_template.md"]
    for heading in (
        "## Outcome",
        "## Verification",
        "## Contract and migration boundary",
        "## Security and disclosure review",
        "## Remaining nonclaims",
    ):
        if heading not in pr_template:
            raise CommunityPathError(f"pull-request template omits {heading}")
    if pr_template.count("- [ ]") < 5:
        raise CommunityPathError("pull-request template lacks required review checks")

    contributing = markdown["CONTRIBUTING.md"]
    for command in (
        "python3 -m venv .venv",
        "python -m pip install --editable .",
        "python -m hormuz --help",
        "python tools/verify_secret_inventory.py",
        "python -m unittest -v",
        "python tools/verify_public_community_paths.py",
        "python -m unittest -v tests.test_public_community_paths",
    ):
        if command not in contributing:
            raise CommunityPathError(f"CONTRIBUTING.md omits command: {command}")

    security = markdown["SECURITY.md"]
    if "security/advisories/new" not in security or "five business days" not in security:
        raise CommunityPathError("SECURITY.md lacks private reporting or alpha response boundary")
    support = markdown["SUPPORT.md"]
    for phrase in ("best-effort", "no response", "not release-gated", "not a managed"):
        if phrase not in support:
            raise CommunityPathError(f"SUPPORT.md omits boundary phrase: {phrase}")
    _validate_support_matrix(root, support)

    manifest = _read_text(root / "MANIFEST.in")
    for name in (*REQUIRED_DOCUMENTS, "tools/verify_public_community_paths.py"):
        if f"include {name}" not in manifest:
            raise CommunityPathError(f"source distribution omits {name}")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "document_count": len(REQUIRED_DOCUMENTS),
        "issue_form_count": len(FORM_CONTRACT),
        "contact_link_count": len(REQUIRED_CONTACTS),
        "pull_request_template": True,
        "support_matrix_bound": True,
    }


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    result = validate_public_community_paths(root)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
