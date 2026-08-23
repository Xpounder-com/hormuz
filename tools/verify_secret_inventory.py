#!/usr/bin/env python3
"""Verify the active-core secret ownership and custody inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hormuz._secret_inventory import (
    SECRET_INVENTORY_SCHEMA_VERSION,
    load_secret_inventory,
    secret_inventory_sha256,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Repository or installed-package root containing the hormuz package",
    )
    args = parser.parse_args(argv)

    inventory = load_secret_inventory(source_root=args.source_root.resolve())
    print(
        "secret_inventory=verified "
        f"schema_version={SECRET_INVENTORY_SCHEMA_VERSION} "
        f"environment_reads={len(inventory['environment_reads'])} "
        f"ambient_credential_reads={len(inventory['ambient_credential_reads'])} "
        f"managed_materials={len(inventory['managed_materials'])} "
        f"sha256={secret_inventory_sha256()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
