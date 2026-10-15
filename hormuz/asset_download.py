"""Sequential pinned downloads; no ambient credentials, proxies or automatic retries."""
import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit


class AssetDownloadError(RuntimeError):
    pass


class AssetDownloader:
    def __init__(self, timeout_seconds):
        self.timeout_seconds = timeout_seconds
        self.connections = {}

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def fetch(self, artifact, stream):
        # A single owned process makes DNS, address attempts and TLS cancellable.
        # Isolated Python and an allowlisted environment exclude ambient auth.
        environment = {key: value for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL")
                       if (value := os.environ.get(key)) is not None}
        stream.flush()
        process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()),
                                    str(stream.fileno()), str(self.timeout_seconds)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, pass_fds=(stream.fileno(),))
        try:
            try:
                process.communicate(_request_bytes(artifact), timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                raise AssetDownloadError("download_unavailable") from None
            if process.returncode:
                raise AssetDownloadError("download_invalid" if process.returncode == 2 else "download_unavailable")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=0.25)
            if process.stdin is not None:
                process.stdin.close()

    def _fetch(self, artifact, stream):
        deadline = time.monotonic() + self.timeout_seconds
        url = artifact["url"]
        origin = urlsplit(url)
        offset = stream.seek(0, 2)
        try:
            for _ in range(5):
                parsed = urlsplit(url)
                if (parsed.hostname not in artifact["allowed_hosts"] or parsed.username or parsed.password
                        or parsed.fragment or parsed.scheme != origin.scheme
                        or any(ord(c) < 32 or ord(c) == 127 for c in url)
                        or (parsed.scheme == "http" and (parsed.hostname, parsed.port) != (origin.hostname, origin.port))):
                    raise AssetDownloadError("download_invalid")
                key = parsed.scheme, parsed.hostname, parsed.port
                connection = self.connections.get(key)
                if connection is None:
                    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
                    connection = connection_type(parsed.hostname, parsed.port, timeout=min(10, self._remaining(deadline)))
                    self.connections[key] = connection
                connection.timeout = min(10, self._remaining(deadline))
                if connection.sock is not None:
                    connection.sock.settimeout(min(15, self._remaining(deadline)))
                headers = {"Accept-Encoding": "identity", "User-Agent": "Hormuz-local-acquisition/1"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                target = parsed.path or "/"
                if parsed.query:
                    target += "?" + parsed.query
                connection.request("GET", target, headers=headers)
                response = connection.getresponse()
                try:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if not location or len(location) > 8192:
                            raise AssetDownloadError("download_invalid")
                        url = urljoin(url, location)
                        connection.close()
                        continue
                    if response.status not in {200, 206}:
                        raise AssetDownloadError("download_unavailable")
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise AssetDownloadError("download_invalid")
                    if response.status == 206:
                        expected_range = f"bytes {offset}-{artifact['size_bytes']-1}/{artifact['size_bytes']}"
                        if not offset or response.getheader("Content-Range") != expected_range:
                            raise AssetDownloadError("download_invalid")
                    elif offset:
                        stream.seek(0)
                        stream.truncate()
                        offset = 0
                    length = response.getheader("Content-Length")
                    if length is not None and (re.fullmatch(r"[0-9]{1,12}", length) is None
                                               or int(length) != artifact["size_bytes"] - offset):
                        raise AssetDownloadError("download_invalid")
                    total = offset
                    while True:
                        if connection.sock is not None:
                            connection.sock.settimeout(min(15, self._remaining(deadline)))
                        else:
                            self._remaining(deadline)
                        chunk = response.read1(min(1024**2, artifact["size_bytes"] - total + 1))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > artifact["size_bytes"]:
                            raise AssetDownloadError("download_invalid")
                        stream.write(chunk)
                    if total != artifact["size_bytes"]:
                        raise AssetDownloadError("download_unavailable")
                    self._remaining(deadline)
                    return
                finally:
                    response.close()
            raise AssetDownloadError("download_invalid")
        except (OSError, ValueError, http.client.HTTPException):
            raise AssetDownloadError("download_unavailable") from None

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetDownloadError("download_unavailable")
        return remaining


def _request_bytes(artifact):
    return json.dumps(artifact, separators=(",", ":")).encode()


def _worker():
    def interrupt(_signum, _frame):
        raise AssetDownloadError("download_unavailable")

    signal.signal(signal.SIGTERM, interrupt)
    downloader = AssetDownloader(int(sys.argv[2]))
    try:
        artifact = json.loads(sys.stdin.buffer.read(65537))
        with os.fdopen(int(sys.argv[1]), "r+b") as stream:
            try:
                downloader._fetch(artifact, stream)
            finally:
                stream.flush()
                os.fsync(stream.fileno())
        return 0
    except AssetDownloadError as error:
        return 2 if str(error) == "download_invalid" else 3
    except (OSError, ValueError, KeyError, TypeError):
        return 3
    finally:
        downloader.close()


if __name__ == "__main__":
    raise SystemExit(_worker())
