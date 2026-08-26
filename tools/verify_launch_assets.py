#!/usr/bin/env python3
"""Fail-closed validation for Hormuz's evidence-grounded launch drafts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = 1
MANIFEST_PATH = Path("docs/launch/claims-v1.json")
PUBLICATION_STATUS = "draft_do_not_publish"
RELEASE_ID = "v0.1.3-public-alpha"
PROJECT_URL = "https://github.com/Xpounder-com/hormuz"
ASSET_PATHS = {
    "landing_page": "docs/launch/LANDING_PAGE.md",
    "terminal_demo": "docs/launch/TERMINAL_DEMO.md",
    "architecture_security": "docs/launch/ARCHITECTURE_AND_SECURITY.md",
    "technical_article": "docs/launch/TECHNICAL_ARTICLE.md",
    "social_show_hn": "docs/launch/SOCIAL_AND_SHOW_HN.md",
    "conversion_analytics": "docs/launch/CONVERSION_AND_ANALYTICS.md",
}
CLASSIFICATIONS = {
    "implemented_alpha",
    "verified_alpha",
    "roadmap",
    "nonclaim",
}
IMPLEMENTED_CLASSIFICATIONS = {"implemented_alpha", "verified_alpha"}
EVIDENCE_KINDS = {"closed_issue", "open_issue", "repository_path"}
CTA_CONTRACT = {
    "governance_review": (
        "Book an AI Governance Review",
        "{{AI_GOVERNANCE_REVIEW_URL}}",
    ),
    "paid_pilot": (
        "Apply for a paid design-partner pilot",
        "{{PAID_PILOT_URL}}",
    ),
}
ANALYTIC_IDS = (
    "successful_installations",
    "completed_demos",
    "governed_requests",
    "returning_users",
    "useful_reports",
    "design_partner_conversations",
    "pilot_applications",
)
REQUIRED_CLOSED_ISSUES = [101, 102, 104, 105, 108, 110, 111, 113, 114, 115, 116]
MARKER = re.compile(r"^<!-- hormuz-launch-asset-v1 (\{.+\}) -->$")
CLAIM_REFERENCE = re.compile(r"<!-- claims: ([A-Z0-9_ ]+) -->")
CLAIM_ID = re.compile(r"^[A-Z][A-Z0-9_]+$")
ISSUE_URL = re.compile(
    r"^https://github\.com/Xpounder-com/hormuz/issues/([1-9][0-9]*)$"
)
TOKEN = re.compile(r"\{\{[A-Z][A-Z0-9_]+\}\}")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
RAW_HTTPS_URL = re.compile(r"https://[^\s)>]+")
PROHIBITED_COPY = (
    "production-grade",
    "enterprise-ready",
    "vulnerability-free",
    "guaranteed secure",
    "guaranteed compliance",
)
SOURCE_MANIFEST_ENTRIES = (
    "include docs/launch/claims-v1.json",
    "include tools/verify_launch_assets.py",
    "recursive-include docs *.md",
    "recursive-include tests *.py *.json",
)


class LaunchAssetError(ValueError):
    """Raised when a launch asset exceeds or loses its evidence boundary."""


def _read_text(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LaunchAssetError(f"cannot read required file: {path}") from exc
    if not value.strip():
        raise LaunchAssetError(f"required file is empty: {path}")
    return value


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise LaunchAssetError(f"invalid strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LaunchAssetError(f"{path} must contain one object")
    return value


def _require_fields(
    value: dict[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise LaunchAssetError(f"{label} fields changed")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LaunchAssetError(f"{label} must be a non-empty string")
    return value


def _require_unique_strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise LaunchAssetError(f"{label} must be a unique string list")
    return value


def _repository_file(root: Path, relative: str, label: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise LaunchAssetError(f"{label} escapes the repository")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LaunchAssetError(f"{label} escapes the repository") from exc
    if not resolved.is_file():
        raise LaunchAssetError(f"{label} is missing: {relative}")
    return resolved


def _validate_issue_url(value: str, label: str) -> None:
    if ISSUE_URL.fullmatch(value) is None:
        raise LaunchAssetError(f"{label} must use an exact Hormuz issue URL")


def _validate_claims(
    root: Path, value: object
) -> dict[str, dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise LaunchAssetError("claims must be a non-empty object list")
    claims: dict[str, dict[str, object]] = {}
    for index, raw_claim in enumerate(value):
        label = f"claims[{index}]"
        if not isinstance(raw_claim, dict):
            raise LaunchAssetError(f"{label} must be an object")
        _require_fields(
            raw_claim, {"id", "text", "classification", "evidence"}, label
        )
        claim_id = _require_string(raw_claim["id"], f"{label}.id")
        if CLAIM_ID.fullmatch(claim_id) is None or claim_id in claims:
            raise LaunchAssetError(f"{label}.id is invalid or duplicated")
        _require_string(raw_claim["text"], f"{label}.text")
        classification = raw_claim["classification"]
        if classification not in CLASSIFICATIONS:
            raise LaunchAssetError(f"{label}.classification is unsupported")
        evidence = raw_claim["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise LaunchAssetError(f"{label}.evidence must be a non-empty list")
        seen_evidence: set[tuple[str, str]] = set()
        evidence_kinds: set[str] = set()
        for evidence_index, raw_item in enumerate(evidence):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if not isinstance(raw_item, dict):
                raise LaunchAssetError(f"{evidence_label} must be an object")
            _require_fields(raw_item, {"kind", "value"}, evidence_label)
            kind = _require_string(raw_item["kind"], f"{evidence_label}.kind")
            item_value = _require_string(
                raw_item["value"], f"{evidence_label}.value"
            )
            if kind not in EVIDENCE_KINDS:
                raise LaunchAssetError(f"{evidence_label}.kind is unsupported")
            key = (kind, item_value)
            if key in seen_evidence:
                raise LaunchAssetError(f"{evidence_label} is duplicated")
            seen_evidence.add(key)
            evidence_kinds.add(kind)
            if kind == "repository_path":
                _repository_file(root, item_value, evidence_label)
            else:
                _validate_issue_url(item_value, evidence_label)
        if "repository_path" not in evidence_kinds:
            raise LaunchAssetError(f"{label} lacks repository-path evidence")
        if (
            classification in IMPLEMENTED_CLASSIFICATIONS
            and "closed_issue" not in evidence_kinds
        ):
            raise LaunchAssetError(f"{label} lacks a closed release-gate issue")
        if classification == "roadmap" and "open_issue" not in evidence_kinds:
            raise LaunchAssetError(f"{label} lacks an open roadmap issue")
        claims[claim_id] = raw_claim
    return claims


def _validate_relative_link(root: Path, source: Path, raw_target: str) -> None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    parsed = urlparse(target)
    if parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc:
            raise LaunchAssetError(f"{source.name} contains a non-HTTPS link")
        if not target.startswith(PROJECT_URL):
            raise LaunchAssetError(
                f"{source.name} contains an unapproved external link"
            )
        return
    if target.startswith("#"):
        return
    relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not relative:
        return
    resolved = (source.parent / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LaunchAssetError(f"{source.name} link escapes the repository") from exc
    if not resolved.is_file():
        raise LaunchAssetError(f"{source.name} links to missing file: {relative}")


def _validate_asset_links_and_tokens(
    root: Path, path: Path, text: str, allowed_tokens: set[str]
) -> None:
    tokens = set(TOKEN.findall(text))
    unknown_tokens = tokens - allowed_tokens
    if unknown_tokens:
        raise LaunchAssetError(
            f"{path.name} contains an unapproved template token"
        )
    for target in MARKDOWN_LINK.findall(text):
        if target in allowed_tokens:
            continue
        _validate_relative_link(root, path, target)
    for raw_url in RAW_HTTPS_URL.findall(text):
        url = raw_url.rstrip(".,;:")
        if not url.startswith(PROJECT_URL):
            raise LaunchAssetError(f"{path.name} contains an unapproved raw URL")


def _validate_assets(
    root: Path,
    value: object,
    claims: dict[str, dict[str, object]],
    allowed_tokens: set[str],
) -> tuple[dict[str, str], set[str]]:
    if not isinstance(value, list) or len(value) != len(ASSET_PATHS):
        raise LaunchAssetError("asset set is incomplete")
    texts: dict[str, str] = {}
    used_claims: set[str] = set()
    seen_assets: set[str] = set()
    for index, raw_asset in enumerate(value):
        label = f"assets[{index}]"
        if not isinstance(raw_asset, dict):
            raise LaunchAssetError(f"{label} must be an object")
        _require_fields(raw_asset, {"id", "path", "claim_ids"}, label)
        asset_id = _require_string(raw_asset["id"], f"{label}.id")
        path_value = _require_string(raw_asset["path"], f"{label}.path")
        if (
            asset_id in seen_assets
            or asset_id not in ASSET_PATHS
            or ASSET_PATHS[asset_id] != path_value
        ):
            raise LaunchAssetError(f"{label} identity or path changed")
        seen_assets.add(asset_id)
        claim_ids = _require_unique_strings(
            raw_asset["claim_ids"], f"{label}.claim_ids"
        )
        if claim_ids != sorted(claim_ids) or not claim_ids:
            raise LaunchAssetError(f"{label}.claim_ids must be sorted and non-empty")
        unknown_claims = set(claim_ids) - set(claims)
        if unknown_claims:
            raise LaunchAssetError(f"{label} references an unknown claim")

        path = _repository_file(root, path_value, label)
        text = _read_text(path)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        marker_match = MARKER.fullmatch(first_line)
        if marker_match is None:
            raise LaunchAssetError(f"{path.name} lacks the strict launch marker")
        try:
            marker = json.loads(marker_match.group(1))
        except json.JSONDecodeError as exc:
            raise LaunchAssetError(f"{path.name} launch marker is invalid") from exc
        if not isinstance(marker, dict):
            raise LaunchAssetError(f"{path.name} launch marker must be an object")
        _require_fields(
            marker,
            {"asset_id", "publication_status", "claim_ids"},
            f"{path.name} marker",
        )
        if (
            marker["asset_id"] != asset_id
            or marker["publication_status"] != PUBLICATION_STATUS
            or marker["claim_ids"] != claim_ids
        ):
            raise LaunchAssetError(f"{path.name} launch marker drifted")
        if "# DRAFT — DO NOT PUBLISH" not in text:
            raise LaunchAssetError(f"{path.name} lacks the publication safety label")
        lowered = text.lower()
        for phrase in PROHIBITED_COPY:
            if phrase in lowered:
                raise LaunchAssetError(
                    f"{path.name} contains prohibited readiness copy"
                )

        referenced: set[str] = set()
        for group in CLAIM_REFERENCE.findall(text):
            referenced.update(group.split())
        if referenced != set(claim_ids):
            raise LaunchAssetError(
                f"{path.name} claim references do not match manifest"
            )
        _validate_asset_links_and_tokens(root, path, text, allowed_tokens)
        texts[asset_id] = text
        used_claims.update(referenced)
    if seen_assets != set(ASSET_PATHS):
        raise LaunchAssetError("asset set changed")
    return texts, used_claims


def _validate_ctas(value: object) -> set[str]:
    if not isinstance(value, list) or len(value) != len(CTA_CONTRACT):
        raise LaunchAssetError("CTA set is incomplete")
    seen: set[str] = set()
    tokens: set[str] = set()
    for index, raw_cta in enumerate(value):
        label = f"ctas[{index}]"
        if not isinstance(raw_cta, dict):
            raise LaunchAssetError(f"{label} must be an object")
        _require_fields(
            raw_cta, {"id", "label", "url_token", "status", "mode"}, label
        )
        cta_id = _require_string(raw_cta["id"], f"{label}.id")
        if cta_id in seen or cta_id not in CTA_CONTRACT:
            raise LaunchAssetError(f"{label}.id is invalid or duplicated")
        expected_label, expected_token = CTA_CONTRACT[cta_id]
        if (
            raw_cta["label"] != expected_label
            or raw_cta["url_token"] != expected_token
            or raw_cta["status"] != "owner_url_required"
            or raw_cta["mode"] != "human_review"
        ):
            raise LaunchAssetError(f"{label} contract changed")
        seen.add(cta_id)
        tokens.add(expected_token)
    return tokens


def _validate_analytics(value: object) -> None:
    if not isinstance(value, list) or len(value) != len(ANALYTIC_IDS):
        raise LaunchAssetError("launch analytics set is incomplete")
    ids: list[str] = []
    for index, raw_metric in enumerate(value):
        label = f"analytics[{index}]"
        if not isinstance(raw_metric, dict):
            raise LaunchAssetError(f"{label} must be an object")
        _require_fields(
            raw_metric,
            {"id", "definition", "source", "privacy_boundary"},
            label,
        )
        metric_id = _require_string(raw_metric["id"], f"{label}.id")
        ids.append(metric_id)
        _require_string(raw_metric["definition"], f"{label}.definition")
        _require_string(raw_metric["source"], f"{label}.source")
        privacy = _require_string(
            raw_metric["privacy_boundary"], f"{label}.privacy_boundary"
        ).lower()
        if not any(
            marker in privacy
            for marker in (
                "no ",
                "never",
                "private",
                "prompt",
                "credential",
                "identity",
            )
        ):
            raise LaunchAssetError(f"{label} lacks a privacy boundary")
    if tuple(ids) != ANALYTIC_IDS:
        raise LaunchAssetError("launch analytics identity or order changed")


def _validate_source_manifest(root: Path) -> None:
    manifest = _read_text(root / "MANIFEST.in")
    lines = {line.strip() for line in manifest.splitlines() if line.strip()}
    missing = set(SOURCE_MANIFEST_ENTRIES) - lines
    if missing:
        raise LaunchAssetError("source distribution omits launch contract material")


def validate_launch_assets(root: Path) -> dict[str, object]:
    root = root.resolve()
    manifest = _read_json(root / MANIFEST_PATH)
    _require_fields(
        manifest,
        {
            "schema_version",
            "repository",
            "release",
            "publication_status",
            "publication_gate",
            "assets",
            "claims",
            "ctas",
            "analytics",
        },
        "manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise LaunchAssetError("unsupported launch-manifest schema version")
    if manifest["repository"] != "Xpounder-com/hormuz":
        raise LaunchAssetError("launch repository identity changed")
    if manifest["release"] != RELEASE_ID:
        raise LaunchAssetError("launch release identity changed")
    if manifest["publication_status"] != PUBLICATION_STATUS:
        raise LaunchAssetError("draft publication status changed without approval")

    gate = manifest["publication_gate"]
    if not isinstance(gate, dict):
        raise LaunchAssetError("publication_gate must be an object")
    _require_fields(
        gate,
        {
            "owner_copy_approval",
            "commercial_urls",
            "quiet_alpha_issue",
            "required_closed_issues",
        },
        "publication_gate",
    )
    if gate != {
        "owner_copy_approval": "required",
        "commercial_urls": "required",
        "quiet_alpha_issue": 110,
        "required_closed_issues": REQUIRED_CLOSED_ISSUES,
    }:
        raise LaunchAssetError("draft publication gate changed")

    claims = _validate_claims(root, manifest["claims"])
    cta_tokens = _validate_ctas(manifest["ctas"])
    _validate_analytics(manifest["analytics"])
    _validate_source_manifest(root)
    texts, used_claims = _validate_assets(
        root, manifest["assets"], claims, cta_tokens
    )
    if used_claims != set(claims):
        raise LaunchAssetError("claim ledger contains an unused public claim")
    for token in cta_tokens:
        for asset_id in ("landing_page", "conversion_analytics"):
            if token not in texts[asset_id]:
                raise LaunchAssetError(
                    f"{asset_id} omits an owner-controlled commercial CTA"
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed_draft",
        "publication_status": PUBLICATION_STATUS,
        "publishable": False,
        "asset_count": len(ASSET_PATHS),
        "claim_count": len(claims),
        "cta_count": len(CTA_CONTRACT),
        "analytic_count": len(ANALYTIC_IDS),
        "required_closed_issue_count": len(REQUIRED_CLOSED_ISSUES),
        "source_distribution_bound": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root",
    )
    args = parser.parse_args(argv)
    try:
        result = validate_launch_assets(args.root)
    except LaunchAssetError as exc:
        print(f"launch_assets_failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
