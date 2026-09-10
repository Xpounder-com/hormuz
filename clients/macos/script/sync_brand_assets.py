#!/usr/bin/env python3
"""Copy the approved website mark and assemble its PNG sizes into a macOS icon."""
from pathlib import Path
import shutil
import struct

root = Path(__file__).resolve().parents[3]
source = root / "website/public/brand/icons"
destination = root / "clients/macos/Resources"
for size, name in [(1024, "HormuzMark"), (32, "HormuzMenuMark")]:
    shutil.copyfile(source / f"transparent/hormuz-transparent-{size}.png",
                    destination / f"{name}.png")

# Modern ICNS entries contain the original PNG bytes; no artwork is redrawn.
entries = [(16, b"icp4"), (32, b"icp5"), (64, b"icp6"), (128, b"ic07"),
           (256, b"ic08"), (512, b"ic09"), (1024, b"ic10")]
chunks = []
for size, kind in entries:
    data = (source / f"forest/hormuz-forest-{size}.png").read_bytes()
    chunks.append(kind + struct.pack(">I", len(data) + 8) + data)
body = b"".join(chunks)
(destination / "Hormuz.icns").write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
print("Synced the approved website mark, menu mark, and seven-size app icon.")
