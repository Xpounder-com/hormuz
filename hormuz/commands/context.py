"""Client-side context optimization commands.

These commands intentionally dispatch without loading the gateway runtime
configuration or provider credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, cast

from ..client_relay import (
    ClientRelayError,
    load_saved_profile,
    probe_gateway_capability,
    run_client,
    supported_client_executable,
)
from ..compaction import (
    MAX_SELECTIONS,
    CompactionConfigError,
    Format,
    Protocol,
    Selection,
    optimize_request,
)
from ..compaction_formats import (
    MAX_REQUEST_BYTES,
    TRANSFORM_VERSION,
    canonical_json,
    strict_json_loads,
)
from ..compaction_runtime import (
    ENCODING_SHA256,
    ENCODING_URLS,
    ContextPreferenceStore,
    ContextRuntimeError,
    default_state_directory,
    default_tokenizer_cache_directory,
    load_token_counters,
)


MAX_SELECTION_FILE_BYTES = 64 * 1024
MAX_RESOURCE_BYTES = 8 * 1024 * 1024
_FORMATS = frozenset({"json_table", "line_runs", "search_lines", "path_list"})


class ContextCommandError(RuntimeError):
    def __init__(self, code: str, exit_code: int = 2):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def add_context_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    context = subparsers.add_parser(
        "context", help="Control client-side context optimization"
    )
    commands = context.add_subparsers(dest="context_command", required=True)

    compact = commands.add_parser(
        "compact", help="Optimize an explicit request file on this machine"
    )
    compact.add_argument("--protocol", choices=["responses", "chat", "anthropic"], required=True)
    compact.add_argument("--input", type=Path, required=True)
    compact.add_argument("--selection", type=Path, required=True)
    compact.add_argument("--output", type=Path, required=True)
    compact.add_argument("--metadata", type=Path, required=True)
    compact.add_argument(
        "--tokenizer-cache",
        type=Path,
        help="Directory populated by `hormuz context resources install`",
    )

    settings = commands.add_parser("settings", help="Change the saved preference")
    _profile_arguments(settings)
    settings.add_argument("--enabled", choices=["on", "off"], required=True)

    status = commands.add_parser("status", help="Show local preference and resource readiness")
    _profile_arguments(status)
    status.add_argument("--readiness-only", action="store_true", help=argparse.SUPPRESS)

    run = commands.add_parser("run", help="Launch a supported client through the local relay")
    _profile_arguments(run)
    run.add_argument(
        "--credential-helper",
        type=Path,
        help="Hormuz desktop credential helper; inferred inside the app bundle",
    )

    resources = commands.add_parser("resources", help="Install verified local tokenizer resources")
    resource_commands = resources.add_subparsers(dest="context_resources_command", required=True)
    install = resource_commands.add_parser("install", help="Download and verify tokenizer vocabularies")
    install.add_argument("--directory", type=Path)


def _profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="Local Hormuz profile key")
    parser.add_argument("--state-directory", type=Path, default=default_state_directory())


def run(args: argparse.Namespace) -> int:
    try:
        command = args.context_command
        if command == "compact":
            return _compact(args)
        if command == "settings":
            store = _preference_store(args.state_directory, args.profile)
            preference = store.save(args.enabled == "on")
            state = "on" if preference.enabled else "off"
            print(f"context_optimization setting={state}")
            return 0
        if command == "status":
            return _status(args)
        if command == "run":
            directory = _resolved_state_directory(args.state_directory)
            profile = load_saved_profile(directory, args.profile)
            helper = args.credential_helper or _bundled_credential_helper()
            if helper is None:
                raise ContextCommandError("credential_helper_required", 2)
            return run_client(
                profile=profile,
                state_directory=directory,
                credential_helper=helper.expanduser().resolve(),
            )
        if command == "resources" and args.context_resources_command == "install":
            return _install_resources(args.directory)
        raise ContextCommandError("invalid_command", 2)
    except ContextCommandError as error:
        print(f"context error: {error.code}", file=sys.stderr)
        return error.exit_code
    except ContextRuntimeError as error:
        exit_code = 3 if error.code == "resources_unavailable" else 1 if "write" in error.code or "unavailable" in error.code else 2
        print(f"context error: {error.code}", file=sys.stderr)
        return exit_code
    except ClientRelayError as error:
        print(f"context error: {error.code}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _compact(args: argparse.Namespace) -> int:
    paths = _distinct_paths(args.input, args.selection, args.output, args.metadata)
    input_path, selection_path, output_path, metadata_path = paths
    if output_path.exists() or metadata_path.exists():
        raise ContextCommandError("output_exists", 2)
    input_bytes = _read_regular(input_path, MAX_REQUEST_BYTES, "invalid_input")
    selection_bytes = _read_regular(selection_path, MAX_SELECTION_FILE_BYTES, "invalid_selection")
    try:
        payload = strict_json_loads(input_bytes.decode("utf-8"))
        specification = strict_json_loads(selection_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextCommandError("invalid_input", 2) from error
    if not isinstance(payload, dict):
        raise ContextCommandError("invalid_input", 2)
    enabled, selections = _parse_selection(specification)
    counters = {}
    if enabled:
        with _network_blocked():
            try:
                counters = load_token_counters(args.tokenizer_cache)
            except ContextRuntimeError as error:
                raise ContextCommandError("resources_unavailable", 3) from error
    try:
        result = optimize_request(
            cast(dict[str, object], payload),
            cast(Protocol, args.protocol),
            selections,
            counters,
            enabled=enabled,
        )
    except CompactionConfigError as error:
        raise ContextCommandError(error.code, 2) from error
    output_bytes = (
        canonical_json(result.payload).encode("utf-8") if result.changed else input_bytes
    )
    metadata = {
        "schema_version": 1,
        "transform_version": result.transform_version,
        "action": "compacted" if result.changed else "passthrough",
        "reason": result.reason,
        "changed_blocks": result.changed_blocks,
        "before_bytes": result.before_bytes,
        "after_bytes": result.after_bytes,
        "before_tokens": result.before_tokens,
        "after_tokens": result.after_tokens,
    }
    metadata_bytes = canonical_json(metadata).encode("utf-8")
    _write_pair(output_path, output_bytes, metadata_path, metadata_bytes)
    before = "unknown" if result.before_bytes is None else str(result.before_bytes)
    after = "unknown" if result.after_bytes is None else str(result.after_bytes)
    print(
        "context_compaction completed "
        f"changed_blocks={result.changed_blocks} before_bytes={before} after_bytes={after}"
    )
    return 0


def _parse_selection(value: object) -> tuple[bool, tuple[Selection, ...]]:
    if not isinstance(value, dict) or set(value) - {"enabled", "version", "selections"}:
        raise ContextCommandError("invalid_selection", 2)
    enabled = value.get("enabled", False)
    version = value.get("version", TRANSFORM_VERSION)
    raw = value.get("selections", [])
    if not isinstance(enabled, bool) or version != TRANSFORM_VERSION or not isinstance(raw, list):
        raise ContextCommandError("invalid_selection", 2)
    if len(raw) > MAX_SELECTIONS:
        raise ContextCommandError("invalid_selection", 2)
    selections: list[Selection] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"result_id", "format"}:
            raise ContextCommandError("invalid_selection", 2)
        result_id, format_name = item.get("result_id"), item.get("format")
        if (
            not isinstance(result_id, str)
            or not 1 <= len(result_id) <= 128
            or result_id in seen
            or format_name not in _FORMATS
        ):
            raise ContextCommandError("invalid_selection", 2)
        seen.add(result_id)
        selections.append(Selection(result_id, cast(Format, format_name)))
    return enabled, tuple(selections)


def _status(args: argparse.Namespace) -> int:
    store = _preference_store(args.state_directory, args.profile)
    preference = store.load()
    if not preference.enabled:
        print("context_optimization setting=off status=off")
        return 0
    profile = load_saved_profile(_resolved_state_directory(args.state_directory), args.profile)
    if not args.readiness_only:
        try:
            supported_client_executable(profile.client)
        except ClientRelayError:
            print("context_optimization setting=on status=unsupported_client")
            return 0
    try:
        with _network_blocked():
            cache = (
                None
                if getattr(sys, "frozen", False)
                else default_tokenizer_cache_directory(args.state_directory)
            )
            load_token_counters(cache)
    except ContextRuntimeError:
        print("context_optimization setting=on status=resources_unavailable")
        return 3
    if not probe_gateway_capability(profile.gateway):
        print("context_optimization setting=on status=gateway_incompatible")
        return 0
    print("context_optimization setting=on status=ready")
    return 0


def _install_resources(directory: Path | None) -> int:
    target = (directory or default_tokenizer_cache_directory()).expanduser()
    try:
        if target.is_symlink():
            raise ContextCommandError("resource_directory_unsafe", 1)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode) or target.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ContextCommandError("resource_directory_unsafe", 1)
        target = target.resolve(strict=True)
    except OSError as error:
        raise ContextCommandError("resource_install_failed", 1) from error
    installed = 0
    for name, url in ENCODING_URLS.items():
        destination = target / hashlib.sha1(url.encode("utf-8")).hexdigest()
        if destination.is_file() and not destination.is_symlink():
            try:
                if _sha256_file(destination) == ENCODING_SHA256[name]:
                    continue
            except OSError:
                pass
        request = urllib.request.Request(url, headers={"User-Agent": "Hormuz context resources/1"})
        temporary: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200:
                    raise ContextCommandError("resource_download_failed", 1)
                data = response.read(MAX_RESOURCE_BYTES + 1)
            if len(data) > MAX_RESOURCE_BYTES or hashlib.sha256(data).hexdigest() != ENCODING_SHA256[name]:
                raise ContextCommandError("resource_verification_failed", 1)
            fd, temporary = tempfile.mkstemp(prefix=".tokenizer-", dir=target)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            installed += 1
        except ContextCommandError:
            raise
        except (OSError, ValueError) as error:
            raise ContextCommandError("resource_download_failed", 1) from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    print(f"context_resources ready installed={installed} encodings={len(ENCODING_URLS)}")
    return 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_regular(path: Path, maximum: int, code: str) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ContextCommandError(code, 2)
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > maximum:
                raise ContextCommandError(code, 2)
            return data
        finally:
            os.close(fd)
    except ContextCommandError:
        raise
    except OSError as error:
        raise ContextCommandError(code, 2) from error


def _write_pair(first: Path, first_data: bytes, second: Path, second_data: bytes) -> None:
    created: list[Path] = []
    try:
        for path, data in ((first, first_data), (second, second_data)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    except OSError as error:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise ContextCommandError("output_write_failed", 1) from error


def _distinct_paths(*paths: Path) -> tuple[Path, ...]:
    try:
        resolved = tuple(path.expanduser().resolve(strict=False) for path in paths)
    except OSError as error:
        raise ContextCommandError("invalid_path", 2) from error
    if len(set(resolved)) != len(resolved):
        raise ContextCommandError("path_collision", 2)
    return resolved


def _resolved_state_directory(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ContextCommandError("profile_unavailable", 2) from error


def _preference_store(directory: Path, profile: str) -> ContextPreferenceStore:
    resolved = _resolved_state_directory(directory)
    try:
        load_saved_profile(resolved, profile)
    except ClientRelayError as error:
        raise ContextCommandError(error.code, 2) from error
    return ContextPreferenceStore(resolved, profile.lower())


def _bundled_credential_helper() -> Path | None:
    executable = Path(sys.argv[0]).expanduser().resolve()
    if executable.parent.name == "Helpers" and executable.parent.parent.name == "Contents":
        candidate = executable.parent.parent / "MacOS" / "Hormuz"
        if candidate.is_file():
            return candidate
    return None


@contextmanager
def _network_blocked() -> Iterator[None]:
    active = True

    def audit(event: str, _arguments: tuple[object, ...]) -> None:
        if active and event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
            raise ContextCommandError("network_forbidden", 2)

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active = False
