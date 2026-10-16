"""Verified offline llama.cpp lifetime; no downloads or PID-based adoption."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import shutil
import tempfile
from urllib.parse import urlsplit
import zlib


class ManagedLlamaError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def checksum_timeout(size_bytes):
    """A finite local-read budget: one minute plus one second per 8 MiB."""
    return 60 + (size_bytes + 8 * 1024**2 - 1) // (8 * 1024**2)


@dataclass(frozen=True)
class ManagedLlamaProfile:
    route: str
    executable: str
    runtime_archive: str
    runtime_sha256: str
    model_file: str
    model_sha256: str
    startup_timeout_seconds: int
    port: int
    alias: str
    output_tokens: int

    def verification_timeout_seconds(self):
        budget = 30  # Archive expansion and extracted-file verification.
        for path, limit in ((self.runtime_archive, 256 * 1024**2), (self.model_file, 32 * 1024**3)):
            try:
                size = min(limit, max(0, os.stat(path, follow_symlinks=False).st_size))
            except OSError:
                size = limit
            budget += checksum_timeout(size)
        return budget

    def verify(self, *, runtime_root=None):
        try:
            self._verify_files(runtime_root=runtime_root)
        except (OSError, ValueError, EOFError, tarfile.TarError, zlib.error):
            raise ManagedLlamaError("runtime_invalid") from None

    def _verify_files(self, *, runtime_root=None):
        if _digest(self.runtime_archive, 256 * 1024**2) != self.runtime_sha256:
            raise ManagedLlamaError("runtime_invalid")
        if _digest(self.model_file, 32 * 1024**3) != self.model_sha256:
            raise ManagedLlamaError("runtime_invalid")
        deadline = time.monotonic() + 30
        root = Path(self.executable).parent
        total = 0
        with gzip.open(self.runtime_archive, "rb") as expanded, tarfile.open(
                fileobj=_ArchiveBudget(expanded, deadline), mode="r|") as archive:
            members = []
            for member in archive:
                total += member.size
                if (len(members) >= 256 or member.size < 0 or total > 1024**3
                        or time.monotonic() > deadline):
                    raise ManagedLlamaError("runtime_invalid")
                expected = None
                if member.isfile():
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source:
                        while chunk := source.read(1024**2):
                            if time.monotonic() > deadline:
                                raise ManagedLlamaError("runtime_invalid")
                            digest.update(chunk)
                    expected = digest.hexdigest()
                elif not (member.isdir() or member.issym()):
                    raise ManagedLlamaError("runtime_invalid")
                members.append((member, expected))
            launchers = [m for m, _ in members if m.isfile() and PurePosixPath(m.name).name == Path(self.executable).name]
            if len(launchers) != 1:
                raise ManagedLlamaError("runtime_invalid")
            prefix = PurePosixPath(launchers[0].name).parent
            for member, expected in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise ManagedLlamaError("runtime_invalid")
                if member.isdir():
                    continue
                relative = name.relative_to(prefix)
                target = root.joinpath(*relative.parts)
                try:
                    resolved_target, resolved_root = target.resolve(strict=True), root.resolve(strict=True)
                except RuntimeError:
                    # Python 3.11/3.12 report a symlink cycle as RuntimeError.
                    raise ManagedLlamaError("runtime_invalid") from None
                if not resolved_target.is_relative_to(resolved_root):
                    raise ManagedLlamaError("runtime_invalid")
                if member.issym():
                    if not target.is_symlink() or os.readlink(target) != member.linkname:
                        raise ManagedLlamaError("runtime_invalid")
                elif member.isfile():
                    if _digest(str(target), 1024**3, deadline) != expected:
                        raise ManagedLlamaError("runtime_invalid")
                else:
                    raise ManagedLlamaError("runtime_invalid")
            if runtime_root is not None:
                runtime_root = Path(runtime_root)
                expected_paths = {}
                for member, _ in members:
                    name = PurePosixPath(member.name)
                    if name.is_absolute() or ".." in name.parts or len(name.parts) > 16:
                        raise ManagedLlamaError("runtime_invalid")
                    for parent in name.parents:
                        if parent.parts:
                            expected_paths[parent] = ("directory", 0o700)
                    expected_paths[name] = (("directory", 0o700) if member.isdir() else
                                            ("symlink", None) if member.issym() else
                                            ("file", 0o700 if member.mode & 0o111 else 0o600))
                observed_paths = {}
                for parent, directories, files in os.walk(runtime_root, followlinks=False):
                    for name in (*directories, *files):
                        path = Path(parent) / name
                        info = path.lstat()
                        kind = ("directory" if stat.S_ISDIR(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode)
                                else "file" if stat.S_ISREG(info.st_mode) else "unsafe")
                        if info.st_uid != os.getuid() or (kind != "directory" and info.st_nlink != 1):
                            raise ManagedLlamaError("runtime_invalid")
                        observed_paths[PurePosixPath(path.relative_to(runtime_root).as_posix())] = (
                            kind, None if kind == "symlink" else stat.S_IMODE(info.st_mode))
                        if len(observed_paths) > 256:
                            raise ManagedLlamaError("runtime_invalid")
                if observed_paths != expected_paths:
                    raise ManagedLlamaError("runtime_invalid")
        if not os.access(self.executable, os.X_OK):
            raise ManagedLlamaError("runtime_invalid")

    def require_free_endpoint(self):
        try:
            with socket.socket() as listener:
                # A completed owner's health connections may leave TIME_WAIT.
                # Reuse that closed socket state, while listen still excludes
                # any live external listener on the selected endpoint.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", self.port))
                listener.listen(1)
        except OSError:
            raise ManagedLlamaError("runtime_occupied") from None


class _ArchiveBudget:
    """Bound decompression, including metadata consumed before TarInfo yields."""
    def __init__(self, source, deadline):
        self.source, self.deadline, self.expanded = source, deadline, 0

    def read(self, size):
        if not 0 <= size <= 1024**2 or time.monotonic() > self.deadline:
            raise ManagedLlamaError("runtime_invalid")
        data = self.source.read(size)
        self.expanded += len(data)
        # File content plus bounded tar headers, padding and metadata.
        if self.expanded > 1024**3 + 1024**2:
            raise ManagedLlamaError("runtime_invalid")
        return data


def extract_runtime(archive_path, destination):
    """Extract a bounded verified archive into a new private runtime directory."""
    destination = Path(destination)
    staged = Path(tempfile.mkdtemp(prefix=".runtime-", dir=destination.parent))
    try:
        deadline = time.monotonic() + 30
        total, seen, links = 0, set(), []
        with gzip.open(archive_path, "rb") as expanded, tarfile.open(
                fileobj=_ArchiveBudget(expanded, deadline), mode="r|") as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or ".." in name.parts or not name.parts or len(name.parts) > 16
                        or len(member.name) > 4096 or any(ord(c) < 32 for c in member.name)
                        or name in seen or len(seen) >= 256 or member.size < 0
                        or time.monotonic() > deadline):
                    raise ManagedLlamaError("runtime_invalid")
                seen.add(name)
                total += member.size
                if total > 1024**3:
                    raise ManagedLlamaError("runtime_invalid")
                target = staged.joinpath(*name.parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(mode=0o700, exist_ok=True)
                elif member.isfile():
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o700 if member.mode & 0o111 else 0o600)
                    with os.fdopen(descriptor, "wb") as stream, archive.extractfile(member) as source:
                        remaining = member.size
                        while remaining:
                            if time.monotonic() > deadline:
                                raise ManagedLlamaError("runtime_invalid")
                            chunk = source.read(min(1024**2, remaining))
                            if not chunk:
                                raise ManagedLlamaError("runtime_invalid")
                            stream.write(chunk)
                            remaining -= len(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                elif member.issym():
                    if (not member.linkname or len(member.linkname) > 4096
                            or PurePosixPath(member.linkname).is_absolute()
                            or any(ord(c) < 32 for c in member.linkname)):
                        raise ManagedLlamaError("runtime_invalid")
                    links.append((target, member.linkname))
                else:
                    raise ManagedLlamaError("runtime_invalid")
        for parent, _, _ in os.walk(staged, followlinks=False):
            os.chmod(parent, 0o700)
        # Create links only after all writes so no archive path can redirect a write.
        for target, link in links:
            target.symlink_to(link)
        root = staged.resolve(strict=True)
        for target, _ in links:
            if not target.resolve(strict=True).is_relative_to(root):
                raise ManagedLlamaError("runtime_invalid")
        if destination.exists() or destination.is_symlink():
            raise ManagedLlamaError("runtime_invalid")
        staged.rename(destination)
    except (OSError, ValueError, RuntimeError, EOFError, tarfile.TarError, zlib.error):
        raise ManagedLlamaError("runtime_invalid") from None
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def _digest(path, limit, deadline=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o022 or before.st_size > limit):
            raise ManagedLlamaError("runtime_invalid")
        if deadline is None:
            deadline = time.monotonic() + checksum_timeout(before.st_size)
        digest = hashlib.sha256()
        while chunk := source.read(1024**2):
            if time.monotonic() > deadline:
                raise ManagedLlamaError("runtime_invalid")
            digest.update(chunk)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ManagedLlamaError("runtime_invalid")
        return digest.hexdigest()


def build_managed_llama(raw, routes, upstreams, admission):
    from .config import ConfigError
    value = raw.get("managed_llama")
    if value is None:
        return None
    fields = {"schema_version", "route", "executable", "runtime_archive", "runtime_sha256", "model_file", "model_sha256", "startup_timeout_seconds"}
    try:
        if type(value) is not dict or set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError()
        for key in ("executable", "runtime_archive", "model_file"):
            if type(value[key]) is not str or not 1 <= len(value[key]) <= 4096 or not Path(value[key]).is_absolute():
                raise ValueError()
        for key in ("runtime_sha256", "model_sha256"):
            if type(value[key]) is not str or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None:
                raise ValueError()
        if type(value["startup_timeout_seconds"]) is not int or not 1 <= value["startup_timeout_seconds"] <= 60:
            raise ValueError()
        route = routes[value["route"]]
        destination = upstreams[route.destination_id]
        endpoint = urlsplit(destination.base_url)
        qualification = dict(admission.local_routes)[value["route"]]
        if (admission.mode not in {"auto", "local_only"}
                or destination.locality != "local" or destination.authentication != "none" or route.protocol != "openai"
                or endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.port is None
                or endpoint.path not in {"", "/", "/v1"} or endpoint.query or endpoint.fragment or endpoint.username
                or route.model_revision != "sha256:" + value["model_sha256"] or qualification.max_output_tokens > 512):
            raise ValueError()
        return ManagedLlamaProfile(**{key: value[key] for key in fields - {"schema_version"}},
            port=endpoint.port, alias=route.upstream_model, output_tokens=qualification.max_output_tokens)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConfigError("managed_llama_invalid") from None


def _healthy(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = response.read(4097)
        return response.status == 200 and len(body) <= 4096 and json.loads(body) == {"status": "ok"}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


class OwnedLlama:
    """The pipe, inherited lease and process handle are ownership authority."""
    def __init__(self, profile, lease):
        self.profile, self.process, self.owner = profile, None, None
        read_fd, self.owner = os.pipe()
        try:
            environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ}
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(read_fd), str(lease), json.dumps(asdict(profile))],
                pass_fds=(read_fd, lease), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
        except BaseException:
            os.close(self.owner)
            self.owner = None
            raise
        finally:
            os.close(read_fd)
        deadline = time.monotonic() + profile.verification_timeout_seconds() + profile.startup_timeout_seconds + 2
        data = bytearray()
        try:
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise ManagedLlamaError("runtime_start_failed")
                chunk = os.read(self.process.stdout.fileno(), 1025 - len(data))
                if not chunk or len(data) + len(chunk) > 1024:
                    raise ManagedLlamaError("runtime_start_failed")
                data.extend(chunk)
            reply = json.loads(data)
            if reply != {"state": "ready"}:
                raise ManagedLlamaError(reply.get("error", "runtime_start_failed"))
        except BaseException:
            self.close()
            raise
        finally:
            self.process.stdout.close()

    def ready(self):
        return self.process.poll() is None and _healthy(self.profile.port)

    def close(self):
        if self.owner is not None:
            fd, self.owner = self.owner, None
            os.close(fd)
        if self.process is not None:
            # Do not signal a discovered PID or kill a guardian that may still
            # own an engine. Its inherited kernel lease remains authoritative.
            self.process.wait(timeout=7)


def _guardian(owner, lease, profile):
    process = None
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        profile.verify()
        profile.require_free_endpoint()
        if select.select([owner], [], [], 0)[0] and not os.read(owner, 1):
            return
        if stopping:
            return
        args = [profile.executable, "-m", profile.model_file, "--offline", "--host", "127.0.0.1", "--port", str(profile.port),
            "--alias", profile.alias, "--parallel", "1", "--threads", "4", "--threads-batch", "4", "--threads-http", "2",
            "--ctx-size", "32768", "--n-predict", str(profile.output_tokens), "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}',
            "--no-webui", "--no-agent", "--no-slots", "--temp", "0.7", "--top-p", "0.8", "--top-k", "20", "--min-p", "0", "--presence-penalty", "1.5"]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + profile.startup_timeout_seconds
        ready, bound, next_probe, observations, log = False, False, 0, 0, b""
        marker = f"llama_server: listening on http://127.0.0.1:{profile.port}".encode()
        with selectors.DefaultSelector() as selector:
            selector.register(owner, selectors.EVENT_READ, "owner")
            selector.register(process.stdout, selectors.EVENT_READ, "log")
            while not stopping:
                if process.poll() is not None:
                    raise ManagedLlamaError("runtime_start_failed")
                if not ready and time.monotonic() >= deadline:
                    raise ManagedLlamaError("runtime_start_failed")
                for key, _ in selector.select(timeout=0.2):
                    data = os.read(key.fd, 4096)
                    if key.data == "owner" and not data:
                        stopping = True
                    elif key.data == "log":
                        if not data:
                            selector.unregister(key.fileobj)
                        combined = log + data
                        bound = bound or re.search(re.escape(marker) + rb"\s", combined) is not None
                        log = combined[-4096:]
                if not ready and not stopping and bound and time.monotonic() >= next_probe:
                    observations += 1
                    if _healthy(profile.port) and process.poll() is None:
                        ready = True
                        print(json.dumps({"state": "ready"}), flush=True)
                    elif observations >= 30:
                        raise ManagedLlamaError("runtime_start_failed")
                    next_probe = time.monotonic() + 2
    except (ManagedLlamaError, OSError, ValueError, tarfile.TarError):
        print(json.dumps({"error": "runtime_start_failed"}), flush=True)
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            finally:
                process.stdout.close()
        os.close(owner)
        os.close(lease)


if __name__ == "__main__":
    _guardian(int(sys.argv[1]), int(sys.argv[2]), ManagedLlamaProfile(**json.loads(sys.argv[3])))
