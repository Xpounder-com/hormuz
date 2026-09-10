"""Pinned official-client versions shared by release qualification tools."""

# Keep this tiny module self-contained: the protected pilot workflow executes
# tools with ``python3 -I``, where the installable ``hormuz`` package is not on
# the import path. Release-identity tests bind these values to the runtime copy.
SUPPORTED_CODEX_VERSION = "0.147.0"
SUPPORTED_CLAUDE_CODE_VERSION = "2.1.233"
