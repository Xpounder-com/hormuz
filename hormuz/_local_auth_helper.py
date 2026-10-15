"""Absolute entry point run with Python -I by the connected coding client."""
from pathlib import Path
import sys


if __name__ == "__main__":
    # Isolated Python excludes project imports and PYTHONPATH. Add only the
    # package containing this installed entry point, then invoke its CLI.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hormuz.cli import main
    raise SystemExit(main(["auth", "local-token", *sys.argv[1:]]))
