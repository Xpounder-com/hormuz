"""Authenticated local commands for provider aggregate finance collection."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

from ..auth import AuthenticationError, Authenticator
from ..config import GatewayConfig
from ..custody import CustodyError
from ..custody_runtime import resolve_upstream_credentials
from ..finance_collection import (
    CollectionQuery,
    FinanceCollectionError,
    fetch_collection_pages,
    normalize_collection_file,
    normalize_collection_pages,
)
from ..finance_collection_repository import (
    FinanceCollectionRepository,
    create_finance_collection_repository,
)
from ..portfolio_config import PortfolioPrincipal, authorize
from ..portfolio_wire import PortfolioError


_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class FinanceCommandDependencies:
    authenticator: Callable[[GatewayConfig], Authenticator] = Authenticator
    create_repository: Callable[..., FinanceCollectionRepository] = (
        create_finance_collection_repository
    )
    resolve_credentials: Callable[..., dict[str, str]] = resolve_upstream_credentials
    fetch_pages: Callable[..., tuple[bytes, ...]] = fetch_collection_pages
    normalize_pages: Callable[..., Any] = normalize_collection_pages
    normalize_file: Callable[..., Any] = normalize_collection_file


def add_finance_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    finance = subparsers.add_parser(
        "finance",
        help="Collect auditable provider aggregate usage and cost evidence",
    )
    commands = finance.add_subparsers(dest="finance_command", required=True)

    source = commands.add_parser("source", help="Manage immutable provider source bindings")
    source_commands = source.add_subparsers(dest="finance_source_command", required=True)
    bind = source_commands.add_parser(
        "bind",
        help="Append a strict source-binding version after administrator authorization",
    )
    bind.add_argument("file", help="Strict version-1 source-binding request JSON")
    _auth_arguments(bind)
    _fingerprint_key_argument(bind)

    collect = commands.add_parser(
        "collect",
        help="Collect one complete bounded provider API page chain",
    )
    _collection_arguments(collect)

    import_command = commands.add_parser(
        "import",
        help="Import one customer-supplied complete page bundle",
    )
    import_command.add_argument("file", help="Strict finance collection file bundle")
    _collection_arguments(import_command)


def _auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token-env",
        default="HORMUZ_PORTFOLIO_TOKEN",
        help="Environment variable holding an existing portfolio-admin bearer token",
    )


def _fingerprint_key_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fingerprint-key-env",
        default="HORMUZ_FINANCE_FINGERPRINT_KEY",
        help="Environment variable holding the tenant-fingerprint HMAC key",
    )


def _collection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("binding_id")
    parser.add_argument("binding_version", type=int)
    parser.add_argument("collection_profile")
    parser.add_argument("query_start_at")
    parser.add_argument("query_end_at")
    parser.add_argument("--bucket-width", default="1d", choices=("1m", "1h", "1d"))
    parser.add_argument("--page-size", dest="requested_page_size", type=int, default=7)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--fingerprint-key-version", required=True, type=int)
    _auth_arguments(parser)
    _fingerprint_key_argument(parser)


def run(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: FinanceCommandDependencies | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run only after token-derived tenant authorization; emit content-free errors."""

    dependencies = dependencies or FinanceCommandDependencies()
    environment = os.environ if environ is None else environ
    try:
        principal = _principal(config, args.token_env, environment, dependencies)
        repository = dependencies.create_repository(config, environ=environment)
        if args.finance_command == "source" and args.finance_source_command == "bind":
            key = _fingerprint_key(environment, args.fingerprint_key_env)
            request = _strict_json_object(_read_bounded(Path(args.file), _REQUEST_BYTES))
            result = repository.bind_source(
                principal,
                request,
                fingerprint_key=key,
            )
        elif args.finance_command in {"collect", "import"}:
            result = _collect_or_import(
                config,
                principal,
                repository,
                args,
                dependencies,
                environment,
            )
        else:
            raise FinanceCollectionError("invalid_request")
        print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
        return 0
    except AuthenticationError:
        return _failure("unauthenticated")
    except PortfolioError as error:
        return _failure(
            error.code if error.code in {"unauthenticated", "forbidden"} else "unavailable"
        )
    except FinanceCollectionError as error:
        return _failure(error.code)
    except CustodyError:
        return _failure("unavailable")
    except OSError:
        return _failure("invalid_request")


def _principal(
    config: GatewayConfig,
    token_env: object,
    environment: Mapping[str, str],
    dependencies: FinanceCommandDependencies,
) -> PortfolioPrincipal:
    if not isinstance(token_env, str) or not token_env:
        raise FinanceCollectionError("invalid_request")
    identity = dependencies.authenticator(config).authenticate(
        environment.get(token_env, "")
    )
    return authorize(config.portfolio_control, identity)


def _collect_or_import(
    config: GatewayConfig,
    principal: PortfolioPrincipal,
    repository: FinanceCollectionRepository,
    args: argparse.Namespace,
    dependencies: FinanceCommandDependencies,
    environment: Mapping[str, str],
):
    query = CollectionQuery(
        principal.organization_id,
        args.binding_id,
        args.binding_version,
        args.collection_profile,
        args.query_start_at,
        args.query_end_at,
        args.bucket_width,
        args.requested_page_size,
    )
    origin = "authenticated_api" if args.finance_command == "collect" else "customer_file"
    prepared = repository.prepare_collection(
        principal,
        query,
        idempotency_key=args.idempotency_key,
        evidence_origin=origin,
    )
    if prepared.state == "succeeded":
        return repository.receipt_for_prepared(principal, prepared)

    try:
        if (
            type(args.fingerprint_key_version) is not int
            or args.fingerprint_key_version != prepared.fingerprint_key_version
        ):
            raise _TerminalFailure(
                FinanceCollectionError("invalid_request"),
                "fingerprint_key_unavailable",
            )
        try:
            key = _fingerprint_key(environment, args.fingerprint_key_env)
        except FinanceCollectionError as error:
            raise _TerminalFailure(
                error,
                "fingerprint_key_unavailable",
            ) from error
        if args.finance_command == "collect":
            try:
                credentials = dependencies.resolve_credentials(
                    config,
                    environ=environment,
                    selection_allowed=lambda provider: provider == prepared.provider,
                )
            except CustodyError as error:
                raise _TerminalFailure(
                    FinanceCollectionError("unavailable"),
                    "credential_unavailable",
                ) from error
            credential = credentials.get(prepared.provider, "")
            if not credential:
                raise _TerminalFailure(
                    FinanceCollectionError("provider_unavailable"),
                    "credential_unavailable",
                )
            pages = dependencies.fetch_pages(
                query,
                credential=credential,
                base_url=f"https://{query.profile.host}",
            )
            collection = dependencies.normalize_pages(
                query,
                pages,
                fingerprint_key=key,
                fingerprint_key_version=args.fingerprint_key_version,
            )
        else:
            payload = _read_bounded(Path(args.file), 16_777_216)
            collection = dependencies.normalize_file(
                query,
                payload,
                fingerprint_key=key,
                fingerprint_key_version=args.fingerprint_key_version,
            )
        return repository.publish_collection(principal, prepared, collection)
    except _TerminalFailure as failure:
        _record_failure(repository, principal, prepared, failure.reason_code)
        raise failure.error
    except FinanceCollectionError as error:
        reason = {
            "provider_unauthorized": "provider_unauthorized",
            "provider_rate_limited": "provider_rate_limited",
            "provider_unavailable": "provider_unavailable",
            "collection_deadline": "collection_deadline",
        }.get(error.code, "normalization_failed")
        _record_failure(repository, principal, prepared, reason)
        raise
    except OSError:
        _record_failure(repository, principal, prepared, "normalization_failed")
        raise FinanceCollectionError("invalid_request") from None


class _TerminalFailure(RuntimeError):
    def __init__(self, error: FinanceCollectionError, reason_code: str):
        self.error = error
        self.reason_code = reason_code
        super().__init__(error.code)


def _record_failure(
    repository: FinanceCollectionRepository,
    principal: PortfolioPrincipal,
    prepared,
    reason_code: str,
) -> None:
    try:
        repository.fail_collection(
            principal,
            prepared,
            reason_code=reason_code,
        )
    except FinanceCollectionError:
        # Preserve the primary content-free failure. A concurrent terminal or
        # revoked administrator cannot be made less safe by a second write.
        pass


def _fingerprint_key(
    environment: Mapping[str, str],
    variable: object,
) -> bytes:
    if not isinstance(variable, str) or not variable:
        raise FinanceCollectionError("invalid_request")
    value = environment.get(variable, "")
    if (
        not isinstance(value, str)
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise FinanceCollectionError("invalid_request")
    key = value.encode("utf-8")
    if not 32 <= len(key) <= 4096:
        raise FinanceCollectionError("unavailable")
    return key


def _read_bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        payload = source.read(maximum + 1)
    if not 1 <= len(payload) <= maximum:
        raise FinanceCollectionError("invalid_request")
    return payload


def _strict_json_object(payload: bytes) -> dict[str, object]:
    def unique(pairs):
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise FinanceCollectionError("invalid_request")
            result[key] = value
        return result

    def nonfinite(_value):
        raise FinanceCollectionError("invalid_request")

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=nonfinite,
        )
    except FinanceCollectionError:
        raise
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise FinanceCollectionError("invalid_request") from None
    if not isinstance(value, dict):
        raise FinanceCollectionError("invalid_request")
    return value


def _failure(code: str) -> int:
    print(
        json.dumps(
            {"error": {"code": code}},
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return 2
