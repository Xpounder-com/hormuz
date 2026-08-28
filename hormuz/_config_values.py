"""Shared scalar parsers for typed Hormuz configuration construction."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import ConfigError


_ENVIRONMENT_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_POSTGRES_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value.strip()


def _url(value: Any, path: str) -> str:
    result = _string(value, path)
    parsed = urlparse(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ConfigError(f"{path} must be an HTTP(S) URL without a query or fragment")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _environment_name(value: Any, path: str) -> str:
    result = _string(value, path)
    if _ENVIRONMENT_NAME_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe environment variable name")
    return result


def _postgres_identifier(value: Any, path: str) -> str:
    result = _string(value, path)
    if _POSTGRES_IDENTIFIER_PATTERN.fullmatch(result) is None:
        raise ConfigError(f"{path} must be a safe PostgreSQL identifier")
    return result


def _configured_path(value: Any, path: str, base_directory: Path) -> Path:
    result = _string(value, path)
    if any(character in result for character in "\x00\r\n"):
        raise ConfigError(f"{path} must be a safe file path")
    configured = Path(result).expanduser()
    return configured.resolve() if configured.is_absolute() else (base_directory / configured).resolve()


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be an array of strings")
    result = tuple(_string(item, f"{path}[]") for item in value)
    if len(result) != len(set(result)):
        raise ConfigError(f"{path} cannot contain duplicates")
    return result


def _optional_string_tuple(value: Any, path: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, path)


def _optional_string_map(value: Any, path: str) -> dict[str, str] | None:
    if value is None:
        return None
    item = _object(value, path)
    result: dict[str, str] = {}
    for key, mapped_value in item.items():
        protocol = _string(key, f"{path} key")
        if protocol not in {"openai", "anthropic"}:
            raise ConfigError(f"{path} keys must be openai or anthropic")
        result[protocol] = _string(mapped_value, f"{path}.{protocol}")
    return result


def _integer(value: Any, path: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise ConfigError(f"{path} must be at least {minimum}{suffix}")
    return value


def _optional_integer(value: Any, path: str, *, minimum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, path, minimum=minimum)


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{path} must be a non-negative number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ConfigError(f"{path} must be a non-negative number") from error
    if not math.isfinite(result):
        raise ConfigError(f"{path} must be a non-negative number")
    return result


def _optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path)
