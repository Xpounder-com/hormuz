"""Bound the whole native fetch, including non-cancellable system DNS calls."""
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ._native_catalog_worker import MAX_CATALOG_BYTES


@dataclass(frozen=True)
class NativeCatalogResponse:
    status: int
    retry_after: list[str]
    body: bytes


def fetch_native_catalog(*, authorization, account, query):
    deadline = time.monotonic() + 3
    worker = Path(__file__).with_name("_native_catalog_worker.py").resolve()
    # Native credentials travel only through the private stdin pipe, never argv,
    # environment, stderr, a file, a login cache or a persistent worker.
    environment = {key: os.environ[key] for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT") if key in os.environ}
    with subprocess.Popen([sys.executable, "-I", "-S", str(worker)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment) as process:
        try:
            output, _ = process.communicate(json.dumps({"authorization": authorization, "account": account,
                "query": query}).encode("utf-8"), timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode:
                raise OSError("native_catalog_unavailable")
        except subprocess.TimeoutExpired:
            raise TimeoutError("native_catalog_deadline") from None
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=1)
    value = json.loads(output)
    data = base64.b64decode(value["body"], validate=True)
    if len(data) > MAX_CATALOG_BYTES:
        raise ValueError("native_catalog_oversized")
    return NativeCatalogResponse(value["status"], value["retry_after"], data)
