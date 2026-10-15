"""Pinned artifact catalogs and local acquisition receipts."""
import re
from urllib.parse import urlsplit

from .common import ContractValidationError, _exact_keys


CATALOG_FIELDS = {"schema_id", "schema_version", "catalog_id", "profile", "runtime", "model"}
ARTIFACT_FIELDS = {"url", "sha256", "size_bytes", "license", "license_url", "allowed_hosts"}
RECEIPT_FIELDS = {"schema_id", "schema_version", "catalog_id", "catalog_sha256", "operation", "state",
                  "directory", "artifacts", "storage", "setup_arguments"}
ERROR_ACTIONS = {
    "catalog_invalid": "select_reviewed_catalog",
    "platform_unsupported": "select_qualified_platform",
    "directory_unsafe": "select_private_directory",
    "installation_changed": "preserve_assets_and_select_new_directory",
    "space_unavailable": "free_space_and_preview_again",
    "offline_missing": "preview_then_explicitly_request_download",
    "busy": "wait_for_current_acquisition",
    "download_unavailable": "retry_explicitly_when_source_is_available",
    "download_invalid": "inspect_pinned_source_and_retry_explicitly",
    "runtime_invalid": "inspect_archive_and_preserve_existing_assets",
    "interrupted": "repeat_the_same_explicit_acquisition",
}


def _check(condition):
    if not condition:
        raise ContractValidationError("invalid local acquisition contract")


def _text(value, limit=4096):
    return type(value) is str and 1 <= len(value) <= limit and all(ord(c) >= 32 and ord(c) != 127 for c in value)


def _digest(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_artifact(value, limit):
    _check(type(value) is dict)
    _exact_keys(value, ARTIFACT_FIELDS)
    _check(_digest(value["sha256"]) and type(value["size_bytes"]) is int and 1 <= value["size_bytes"] <= limit)
    _check(value["license"] in {"MIT", "Apache-2.0"} and _text(value["license_url"]))
    license_url = urlsplit(value["license_url"])
    _check(license_url.scheme == "https" and license_url.hostname and not license_url.username
           and not license_url.password and not license_url.fragment and not license_url.query)
    hosts = value["allowed_hosts"]
    _check(type(hosts) is list and 1 <= len(hosts) <= 8
           and all(type(host) is str and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host) for host in hosts)
           and len(set(hosts)) == len(hosts))
    _check(_text(value["url"]))
    source = urlsplit(value["url"])
    _check(source.hostname in hosts and source.scheme in {"https", "http"}
           and not source.username and not source.password and not source.query and not source.fragment
           and (source.scheme == "https" or source.hostname == "127.0.0.1")
           and (source.port is None or 1 <= source.port <= 65535))


def validate_catalog(value):
    from ..contracts import validate_contract
    _exact_keys(value, CATALOG_FIELDS)
    _check(type(value["catalog_id"]) is str and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", value["catalog_id"]))
    _check(type(value["profile"]) is dict and value["profile"].get("schema_id") == "hormuz.local-setup-profile")
    validate_contract(value["profile"])
    for role, limit in (("runtime", 256 * 1024**2), ("model", 32 * 1024**3)):
        validate_artifact(value[role], limit)
        _check(value[role]["sha256"] == value["profile"][role + "_sha256"])


def validate_receipt(value):
    _exact_keys(value, RECEIPT_FIELDS)
    _check(_text(value["catalog_id"], 128) and _digest(value["catalog_sha256"]) and _text(value["directory"]))
    _check(value["operation"] in {"preview", "acquire", "reuse"} and value["state"] in {"planned", "ready"})
    _check(value["state"] == "ready" or value["operation"] == "preview")
    _check(type(value["artifacts"]) is dict and set(value["artifacts"]) == {"runtime", "model"})
    for role, limit in (("runtime", 256 * 1024**2), ("model", 32 * 1024**3)):
        artifact = value["artifacts"][role]
        _check(type(artifact) is dict)
        _exact_keys(artifact, ARTIFACT_FIELDS | {"path"})
        validate_artifact({key: item for key, item in artifact.items() if key != "path"}, limit)
        _check(_text(artifact["path"]))
    storage = value["storage"]
    _check(type(storage) is dict and set(storage) == {"download_bytes", "required_free_bytes", "available_bytes"})
    _check(all(type(item) is int and item >= 0 for item in storage.values()))
    arguments = value["setup_arguments"]
    _check(type(arguments) is list and all(_text(argument) for argument in arguments))
    _check(arguments == [] if value["state"] == "planned" else
           len(arguments) == 8 and arguments[::2] == ["--qualification", "--runtime-archive", "--llama-executable", "--model-file"])


def validate_error(value):
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    error = value["error"]
    _check(type(error) is dict and set(error) == {"code", "action"})
    _check(type(error["code"]) is str and error["code"] in ERROR_ACTIONS
           and error["action"] == ERROR_ACTIONS[error["code"]])
