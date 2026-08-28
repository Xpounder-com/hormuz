"""Custody, lifecycle, and immutable-audit configuration construction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._config_values import (
    _boolean,
    _configured_path,
    _environment_name,
    _integer,
    _object,
    _postgres_identifier,
    _string,
    _url,
)
from .config import (
    AuditAnchorConfig,
    AuditChainConfig,
    ConfigError,
    CustodyControlConfig,
    CustodyExecutorConfig,
    CustodyRetentionConfig,
    KeyCustodyConfig,
    PolicyControlConfig,
    UpstreamConfig,
    UsageStorageConfig,
)
from .custody import KEY_PURPOSES, KEY_PURPOSE_DATA_ENCRYPTION, KEY_PURPOSE_PROVIDER_CREDENTIAL
from .custody_lifecycle import (
    CUSTODY_ASSET_TYPES,
    CustodyAsset,
    CustodyAssetCatalog,
    CustodyLifecycleConfig,
    CustodyLifecycleError,
    binding_fingerprint,
)


_AWS_REGION_PATTERN = re.compile(r"[a-z]{2}(?:-gov)?-[a-z0-9-]+-\d+\Z")
_S3_BUCKET_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_OPENBAO_PATH_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CUSTODY_ASSET_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z")


@dataclass(frozen=True)
class ExternalCustodyConstruction:
    key_custody: KeyCustodyConfig | None
    audit_anchor: AuditAnchorConfig | None
    audit_chain: AuditChainConfig | None


@dataclass(frozen=True)
class CustodyControlConstruction:
    control: CustodyControlConfig
    executor: CustodyExecutorConfig
    retention: CustodyRetentionConfig | None
    bootstrap_administrators_raw: tuple[Any, ...]


def build_external_custody_domain(raw: dict[str, Any]) -> ExternalCustodyConstruction:
    key_custody = _key_custody(raw.get("key_custody"))
    audit_anchor = _audit_anchor(raw.get("audit_anchor"), key_custody=key_custody)
    return ExternalCustodyConstruction(
        key_custody=key_custody,
        audit_anchor=audit_anchor,
        audit_chain=_audit_chain(raw.get("audit_chain"), audit_anchor=audit_anchor),
    )


def build_custody_control_domain(
    raw: dict[str, Any],
    *,
    usage_storage: UsageStorageConfig,
    policy_control: PolicyControlConfig,
    key_custody: KeyCustodyConfig | None,
) -> CustodyControlConstruction:
    custody_control_raw = _object(raw.get("custody_control", {}), "custody_control")
    unsupported_custody_control_fields = set(custody_control_raw).difference(
        {
            "mode",
            "bootstrap_administrators",
            "postgres_control_dsn_env",
            "postgres_control_role",
            "authorization_ttl_seconds",
        }
    )
    if unsupported_custody_control_fields:
        raise ConfigError(
            "custody_control contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported_custody_control_fields))
        )
    custody_control_mode = _string(custody_control_raw.get("mode", "local"), "custody_control.mode")
    if custody_control_mode not in {"local", "postgresql"}:
        raise ConfigError("custody_control.mode must be local or postgresql")
    custody_control_dsn_env = _environment_name(
        custody_control_raw.get("postgres_control_dsn_env", "HORMUZ_CUSTODY_CONTROL_DSN"),
        "custody_control.postgres_control_dsn_env",
    )
    custody_control_role = _postgres_identifier(
        custody_control_raw.get("postgres_control_role", "hormuz_custody_control"),
        "custody_control.postgres_control_role",
    )
    custody_authorization_ttl_seconds = _integer(
        custody_control_raw.get("authorization_ttl_seconds", 900),
        "custody_control.authorization_ttl_seconds",
        minimum=60,
        maximum=24 * 60 * 60,
    )
    custody_bootstrap_administrators_raw = custody_control_raw.get("bootstrap_administrators", [])
    if not isinstance(custody_bootstrap_administrators_raw, list):
        raise ConfigError("custody_control.bootstrap_administrators must be an array")
    custody_retention = _custody_retention(raw.get("custody_retention"))
    if custody_control_mode == "postgresql":
        if usage_storage.backend != "postgresql":
            raise ConfigError("custody_control.mode postgresql requires usage_storage.backend postgresql")
        if key_custody is None:
            raise ConfigError("custody_control.mode postgresql requires key_custody")
        if custody_retention is None:
            raise ConfigError("custody_control.mode postgresql requires custody_retention")
        active_dsn_envs = {
            usage_storage.postgres_dsn_env,
            usage_storage.postgres_migration_dsn_env,
        }
        active_roles = {usage_storage.postgres_runtime_role}
        if policy_control.mode == "postgresql":
            active_dsn_envs.add(policy_control.postgres_control_dsn_env)
            active_roles.add(policy_control.postgres_control_role)
        if custody_control_dsn_env in active_dsn_envs:
            raise ConfigError(
                "custody_control.postgres_control_dsn_env must name a credential distinct from "
                "runtime, migration, and policy-control credentials"
            )
        if custody_control_role in active_roles:
            raise ConfigError(
                "custody_control.postgres_control_role must differ from runtime and policy-control roles"
            )
        if not custody_bootstrap_administrators_raw:
            raise ConfigError("custody_control.bootstrap_administrators must contain at least one administrator")
    else:
        if custody_bootstrap_administrators_raw:
            raise ConfigError(
                "custody_control.bootstrap_administrators require custody_control.mode postgresql"
            )
        if custody_retention is not None:
            raise ConfigError("custody_retention requires custody_control.mode postgresql")

    custody_executor_raw = _object(raw.get("custody_executor", {}), "custody_executor")
    unsupported_custody_executor_fields = set(custody_executor_raw).difference(
        {
            "postgres_executor_dsn_env",
            "postgres_executor_role",
            "pending_attempt_ttl_seconds",
        }
    )
    if unsupported_custody_executor_fields:
        raise ConfigError(
            "custody_executor contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported_custody_executor_fields))
        )
    custody_executor_dsn_env = _environment_name(
        custody_executor_raw.get("postgres_executor_dsn_env", "HORMUZ_CUSTODY_EXECUTOR_DSN"),
        "custody_executor.postgres_executor_dsn_env",
    )
    custody_executor_role = _postgres_identifier(
        custody_executor_raw.get("postgres_executor_role", "hormuz_custody_executor"),
        "custody_executor.postgres_executor_role",
    )
    custody_executor_pending_ttl_seconds = _integer(
        custody_executor_raw.get("pending_attempt_ttl_seconds", 900),
        "custody_executor.pending_attempt_ttl_seconds",
        minimum=60,
        maximum=24 * 60 * 60,
    )
    if custody_control_mode != "postgresql" and custody_executor_raw:
        raise ConfigError("custody_executor requires custody_control.mode postgresql")
    if custody_control_mode == "postgresql":
        active_dsn_envs = {
            usage_storage.postgres_dsn_env,
            usage_storage.postgres_migration_dsn_env,
            custody_control_dsn_env,
        }
        active_roles = {usage_storage.postgres_runtime_role, custody_control_role}
        if policy_control.mode == "postgresql":
            active_dsn_envs.add(policy_control.postgres_control_dsn_env)
            active_roles.add(policy_control.postgres_control_role)
        if custody_executor_dsn_env in active_dsn_envs:
            raise ConfigError(
                "custody_executor.postgres_executor_dsn_env must name a credential distinct from "
                "runtime, migration, policy-control, and custody-control credentials"
            )
        if custody_executor_role in active_roles:
            raise ConfigError(
                "custody_executor.postgres_executor_role must differ from runtime, policy-control, and custody-control roles"
            )

    return CustodyControlConstruction(
        control=CustodyControlConfig(
            mode=custody_control_mode,
            postgres_control_dsn_env=custody_control_dsn_env,
            postgres_control_role=custody_control_role,
            authorization_ttl_seconds=custody_authorization_ttl_seconds,
        ),
        executor=CustodyExecutorConfig(
            postgres_executor_dsn_env=custody_executor_dsn_env,
            postgres_executor_role=custody_executor_role,
            pending_attempt_ttl_seconds=custody_executor_pending_ttl_seconds,
        ),
        retention=custody_retention,
        bootstrap_administrators_raw=tuple(custody_bootstrap_administrators_raw),
    )


def _key_custody(value: Any) -> KeyCustodyConfig | None:
    if value is None:
        return None
    item = _object(value, "key_custody")
    backend = _string(item.get("backend"), "key_custody.backend")
    raw_references = _object(item.get("key_references"), "key_custody.key_references")
    if not raw_references:
        raise ConfigError("key_custody.key_references must contain at least one purpose")
    key_references: dict[str, str] = {}
    for purpose, raw_reference in raw_references.items():
        if not isinstance(purpose, str) or purpose not in KEY_PURPOSES:
            raise ConfigError(
                "key_custody.key_references keys must be one of: " + ", ".join(sorted(KEY_PURPOSES))
            )
        reference = _string(raw_reference, f"key_custody.key_references.{purpose}")
        if len(reference) > 2048 or any(character in reference for character in "\x00\r\n"):
            raise ConfigError(f"key_custody.key_references.{purpose} must be a safe KMS key reference")
        key_references[purpose] = reference
    if len(key_references) != len(set(key_references.values())):
        raise ConfigError("key_custody.key_references must use distinct keys for distinct purposes")
    if backend == "aws-kms":
        unsupported = set(item).difference({"backend", "region", "key_references"})
        if unsupported:
            raise ConfigError("key_custody contains unsupported fields: " + ", ".join(sorted(unsupported)))
        return KeyCustodyConfig(
            backend=backend,
            region=_aws_region(item.get("region"), "key_custody.region"),
            key_references=key_references,
        )
    if backend == "openbao-transit":
        unsupported = set(item).difference({"backend", "endpoint_url", "token_env", "transit_mount", "key_references"})
        if unsupported:
            raise ConfigError("key_custody contains unsupported fields: " + ", ".join(sorted(unsupported)))
        for purpose, reference in key_references.items():
            if _OPENBAO_PATH_NAME_PATTERN.fullmatch(reference) is None:
                raise ConfigError(
                    f"key_custody.key_references.{purpose} must be a safe OpenBao Transit key name"
                )
        return KeyCustodyConfig(
            backend=backend,
            region=None,
            key_references=key_references,
            endpoint_url=_self_hosted_service_url(item.get("endpoint_url"), "key_custody.endpoint_url"),
            token_env=_environment_name(item.get("token_env"), "key_custody.token_env"),
            transit_mount=_openbao_path_name(item.get("transit_mount", "transit"), "key_custody.transit_mount"),
        )
    raise ConfigError("key_custody.backend must be aws-kms or openbao-transit")


def _custody_retention(value: Any) -> CustodyRetentionConfig | None:
    if value is None:
        return None
    item = _object(value, "custody_retention")
    unsupported = set(item).difference({"retention_days", "legal_hold"})
    if unsupported:
        raise ConfigError("custody_retention contains unsupported fields: " + ", ".join(sorted(unsupported)))
    return CustodyRetentionConfig(
        retention_days=_integer(
            item.get("retention_days"),
            "custody_retention.retention_days",
            minimum=1,
            maximum=36500,
        ),
        legal_hold=_boolean(item.get("legal_hold", False), "custody_retention.legal_hold"),
    )


def build_custody_lifecycle(
    value: Any,
    *,
    organization_ids: tuple[str, ...],
    upstreams: dict[str, UpstreamConfig],
    key_custody: KeyCustodyConfig | None,
    base_directory: Path,
) -> CustodyLifecycleConfig | None:
    """Build the private asset catalog used by governed lifecycle operations."""

    if value is None:
        return None
    if len(organization_ids) != 1:
        raise ConfigError(
            "custody_lifecycle requires exactly one configured organization; use a tenant-scoped gateway configuration"
        )
    if key_custody is None:
        raise ConfigError("custody_lifecycle requires key_custody")
    item = _object(value, "custody_lifecycle")
    unsupported = set(item).difference({"freshness_lease_seconds", "assets"})
    if unsupported:
        raise ConfigError("custody_lifecycle contains unsupported fields: " + ", ".join(sorted(unsupported)))
    lease_seconds = _integer(
        item.get("freshness_lease_seconds", 5),
        "custody_lifecycle.freshness_lease_seconds",
        minimum=5,
        maximum=5,
    )
    raw_assets = item.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ConfigError("custody_lifecycle.assets must contain at least one asset")
    organization_id = organization_ids[0]
    assets: list[CustodyAsset] = []
    envelope_bindings: list[tuple[CustodyAsset, str, str, int, str, int]] = []
    key_assets: list[tuple[CustodyAsset, str, str]] = []
    provider_assets: dict[str, CustodyAsset] = {}

    for index, raw_asset in enumerate(raw_assets):
        prefix = f"custody_lifecycle.assets[{index}]"
        asset = _object(raw_asset, prefix)
        unsupported_asset = set(asset).difference({"asset_type", "asset_id", "generation", "binding"})
        if unsupported_asset:
            raise ConfigError(prefix + " contains unsupported fields: " + ", ".join(sorted(unsupported_asset)))
        asset_type = _string(asset.get("asset_type"), f"{prefix}.asset_type")
        if asset_type not in CUSTODY_ASSET_TYPES:
            raise ConfigError(f"{prefix}.asset_type is unsupported")
        asset_id = _asset_identifier(asset.get("asset_id"), f"{prefix}.asset_id")
        generation = _integer(asset.get("generation"), f"{prefix}.generation", minimum=1)
        binding_raw = _object(asset.get("binding"), f"{prefix}.binding")
        if asset_type == "provider_credential":
            unsupported_binding = set(binding_raw).difference({"protocol"})
            if unsupported_binding:
                raise ConfigError(f"{prefix}.binding contains unsupported fields: " + ", ".join(sorted(unsupported_binding)))
            protocol = _string(binding_raw.get("protocol"), f"{prefix}.binding.protocol")
            upstream = upstreams.get(protocol)
            if upstream is None:
                raise ConfigError(f"{prefix}.binding.protocol must identify a configured provider")
            if protocol in provider_assets:
                raise ConfigError(f"custody_lifecycle has more than one provider credential for {protocol}")
            source = (
                f"env:{upstream.api_key_env}"
                if upstream.api_key_env is not None
                else f"envelope:{upstream.api_key_envelope_path}"
            )
            binding = {"protocol": protocol, "source": source}
        elif asset_type == "envelope":
            required = {
                "path",
                "provider_credential_asset_id",
                "provider_credential_generation",
                "key_reference_asset_id",
                "key_reference_generation",
            }
            if set(binding_raw) != required:
                raise ConfigError(f"{prefix}.binding must define the configured envelope and its asset links")
            path = _configured_path(binding_raw.get("path"), f"{prefix}.binding.path", base_directory)
            provider_asset_id = _asset_identifier(
                binding_raw.get("provider_credential_asset_id"),
                f"{prefix}.binding.provider_credential_asset_id",
            )
            provider_generation = _integer(
                binding_raw.get("provider_credential_generation"),
                f"{prefix}.binding.provider_credential_generation",
                minimum=1,
            )
            key_asset_id = _asset_identifier(
                binding_raw.get("key_reference_asset_id"),
                f"{prefix}.binding.key_reference_asset_id",
            )
            key_generation = _integer(
                binding_raw.get("key_reference_generation"),
                f"{prefix}.binding.key_reference_generation",
                minimum=1,
            )
            binding = {
                "path": str(path),
                "provider_credential_asset": f"{provider_asset_id}@{provider_generation}",
                "key_reference_asset": f"{key_asset_id}@{key_generation}",
            }
        else:
            required = {"purpose", "key_reference"}
            if set(binding_raw) != required:
                raise ConfigError(f"{prefix}.binding must define the key purpose and customer key reference")
            purpose = _string(binding_raw.get("purpose"), f"{prefix}.binding.purpose")
            if purpose not in KEY_PURPOSES:
                raise ConfigError(f"{prefix}.binding.purpose is unsupported")
            reference = _string(binding_raw.get("key_reference"), f"{prefix}.binding.key_reference")
            if len(reference) > 2048 or any(character in reference for character in "\x00\r\n"):
                raise ConfigError(f"{prefix}.binding.key_reference must be a safe KMS key reference")
            binding = {"purpose": purpose, "key_reference": reference}

        fingerprint = binding_fingerprint(
            organization_id=organization_id,
            asset_type=asset_type,
            asset_id=asset_id,
            generation=generation,
            binding=binding,
        )
        try:
            constructed = CustodyAsset(
                organization_id=organization_id,
                asset_type=asset_type,
                asset_id=asset_id,
                generation=generation,
                binding_fingerprint=fingerprint,
                binding=binding,
            )
        except ValueError as error:
            raise ConfigError(f"{prefix} is invalid") from error
        assets.append(constructed)
        if asset_type == "provider_credential":
            provider_assets[binding["protocol"]] = constructed
        elif asset_type == "envelope":
            envelope_bindings.append(
                (
                    constructed,
                    binding["path"],
                    provider_asset_id,
                    provider_generation,
                    key_asset_id,
                    key_generation,
                )
            )
        else:
            key_assets.append((constructed, binding["purpose"], binding["key_reference"]))

    try:
        catalog = CustodyAssetCatalog(tuple(assets))
    except ValueError as error:
        raise ConfigError("custody_lifecycle asset identities or bindings are duplicated") from error
    if set(provider_assets) != set(upstreams):
        raise ConfigError("custody_lifecycle requires exactly one provider credential asset per configured upstream")
    envelopes_by_path = {path: asset for asset, path, _pid, _pgen, _kid, _kgen in envelope_bindings}
    if len(envelopes_by_path) != len(envelope_bindings):
        raise ConfigError("custody_lifecycle envelope paths must be unique")
    for protocol, upstream in upstreams.items():
        credential = provider_assets[protocol]
        if upstream.api_key_envelope_path is None:
            continue
        envelope = envelopes_by_path.get(str(upstream.api_key_envelope_path))
        if envelope is None:
            raise ConfigError(f"custody_lifecycle requires an envelope asset for upstreams.{protocol}")
        linked = next(
            links
            for links in envelope_bindings
            if links[0].key == envelope.key
        )
        _asset, _path, provider_asset_id, provider_generation, key_asset_id, key_generation = linked
        if credential.asset_id != provider_asset_id or credential.generation != provider_generation:
            raise ConfigError(f"custody_lifecycle envelope link for upstreams.{protocol} is invalid")
        try:
            key_asset = catalog.asset(
                organization_id=organization_id,
                asset_type="key_reference",
                asset_id=key_asset_id,
                generation=key_generation,
            )
        except CustodyLifecycleError as error:
            raise ConfigError(f"custody_lifecycle envelope link for upstreams.{protocol} is invalid") from error
        if key_asset.binding.get("purpose") != KEY_PURPOSE_PROVIDER_CREDENTIAL:
            raise ConfigError(f"custody_lifecycle envelope link for upstreams.{protocol} must use provider_credential")
    for purpose, configured_reference in key_custody.key_references.items():
        active = [
            asset
            for asset, candidate_purpose, reference in key_assets
            if candidate_purpose == purpose and reference == configured_reference
        ]
        if len(active) != 1:
            raise ConfigError("custody_lifecycle requires one current key reference asset for " + purpose)
    return CustodyLifecycleConfig(freshness_lease_seconds=lease_seconds, assets=catalog)


def _audit_anchor(value: Any, *, key_custody: KeyCustodyConfig | None) -> AuditAnchorConfig | None:
    if value is None:
        return None
    item = _object(value, "audit_anchor")
    backend = _string(item.get("backend"), "audit_anchor.backend")
    if key_custody is None:
        raise ConfigError("audit_anchor requires key_custody")
    key_custody.key_reference_for(KEY_PURPOSE_DATA_ENCRYPTION)
    bucket = _string(item.get("bucket"), "audit_anchor.bucket")
    if _S3_BUCKET_PATTERN.fullmatch(bucket) is None or ".." in bucket or ".-" in bucket or "-." in bucket:
        raise ConfigError("audit_anchor.bucket must be a valid lower-case S3 bucket name")
    prefix = _string(item.get("prefix", "hormuz/audit"), "audit_anchor.prefix").strip("/")
    if (
        not prefix
        or len(prefix) > 512
        or any(character in prefix for character in "\x00\r\n")
        or any(part in {"", ".", ".."} for part in prefix.split("/"))
    ):
        raise ConfigError("audit_anchor.prefix must be a safe non-empty object-key prefix")
    retention_days = _integer(item.get("retention_days"), "audit_anchor.retention_days", minimum=1, maximum=36500)
    legal_hold = _boolean(item.get("legal_hold", False), "audit_anchor.legal_hold")
    if backend == "aws-s3-object-lock":
        unsupported = set(item).difference({"backend", "region", "bucket", "prefix", "retention_days", "legal_hold"})
        if unsupported:
            raise ConfigError("audit_anchor contains unsupported fields: " + ", ".join(sorted(unsupported)))
        if key_custody.backend != "aws-kms" or key_custody.region is None:
            raise ConfigError("audit_anchor.backend aws-s3-object-lock requires key_custody.backend aws-kms")
        region = _aws_region(item.get("region"), "audit_anchor.region")
        if region != key_custody.region:
            raise ConfigError("audit_anchor.region must equal key_custody.region for SSE-KMS")
        return AuditAnchorConfig(
            backend=backend,
            region=region,
            bucket=bucket,
            prefix=prefix,
            retention_days=retention_days,
            legal_hold=legal_hold,
        )
    if backend == "s3-compatible-object-lock":
        unsupported = set(item).difference(
            {
                "backend",
                "region",
                "bucket",
                "prefix",
                "retention_days",
                "legal_hold",
                "endpoint_url",
                "access_key_env",
                "secret_key_env",
            }
        )
        if unsupported:
            raise ConfigError("audit_anchor contains unsupported fields: " + ", ".join(sorted(unsupported)))
        if key_custody.backend != "openbao-transit":
            raise ConfigError("audit_anchor.backend s3-compatible-object-lock requires key_custody.backend openbao-transit")
        access_key_env = _environment_name(item.get("access_key_env"), "audit_anchor.access_key_env")
        secret_key_env = _environment_name(item.get("secret_key_env"), "audit_anchor.secret_key_env")
        if access_key_env == secret_key_env:
            raise ConfigError("audit_anchor access_key_env and secret_key_env must differ")
        return AuditAnchorConfig(
            backend=backend,
            region=_s3_compatible_region(item.get("region"), "audit_anchor.region"),
            bucket=bucket,
            prefix=prefix,
            retention_days=retention_days,
            legal_hold=legal_hold,
            endpoint_url=_self_hosted_service_url(item.get("endpoint_url"), "audit_anchor.endpoint_url"),
            access_key_env=access_key_env,
            secret_key_env=secret_key_env,
        )
    raise ConfigError("audit_anchor.backend must be aws-s3-object-lock or s3-compatible-object-lock")


def _audit_chain(value: Any, *, audit_anchor: AuditAnchorConfig | None) -> AuditChainConfig | None:
    if value is None:
        return None
    item = _object(value, "audit_chain")
    unsupported = set(item).difference({"maximum_anchor_age_seconds"})
    if unsupported:
        raise ConfigError("audit_chain contains unsupported fields: " + ", ".join(sorted(unsupported)))
    if audit_anchor is None:
        raise ConfigError("audit_chain requires audit_anchor")
    return AuditChainConfig(
        maximum_anchor_age_seconds=_integer(
            item.get("maximum_anchor_age_seconds"),
            "audit_chain.maximum_anchor_age_seconds",
            minimum=60,
            maximum=31 * 24 * 60 * 60,
        )
    )


def _asset_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if not _CUSTODY_ASSET_IDENTIFIER_PATTERN.fullmatch(result):
        raise ConfigError(f"{path} must be a safe immutable asset identifier")
    return result


def _self_hosted_service_url(value: Any, path: str) -> str:
    result = _url(value, path)
    parsed = urlparse(result)
    if parsed.path not in {"", "/"}:
        raise ConfigError(f"{path} must be a service origin without a path")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ConfigError(f"{path} requires HTTPS outside loopback development")
    return result.rstrip("/")


def _openbao_path_name(value: Any, path: str) -> str:
    result = _string(value, path)
    if _OPENBAO_PATH_NAME_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe OpenBao path name")
    return result


def _aws_region(value: Any, path: str) -> str:
    result = _string(value, path)
    if _AWS_REGION_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a valid AWS region identifier")
    return result


def _s3_compatible_region(value: Any, path: str) -> str:
    result = _string(value, path)
    if result == "default" or _AWS_REGION_PATTERN.fullmatch(result) is not None:
        return result
    raise ConfigError(f"{path} must be a valid S3-compatible region identifier")
