"""Private digest-bound asset generations feeding the existing shared setup owner."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import tempfile
import time

from .asset_download import AssetDownloader, AssetDownloadError
from .compaction_formats import strict_json_loads
from .contracts import ContractValidationError, validate_contract
from .managed_llama import ManagedLlamaError, ManagedLlamaProfile, checksum_timeout, extract_runtime


DEFAULT_CATALOG = "local-acquisition-codex-0147-llama-b11617-qwen3-4b-v1.json"
EXPANSION_LIMIT = 1024**3


class LocalAcquisitionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _safe_file(path, limit, *, private=True):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        _file_info(stream, limit, private=private)
        return stream.read(limit + 1)


def _file_info(stream, limit, *, private=True):
    info = os.fstat(stream.fileno())
    owners = {os.getuid()} if private else {os.getuid(), 0}
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022) or info.st_size > limit):
        raise LocalAcquisitionError("installation_changed")
    return info


def _directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise LocalAcquisitionError("directory_unsafe")


def _layout(generation):
    allowed = {"catalog.json", "runtime.tar.gz", "runtime.tar.gz.part", "model.gguf", "model.gguf.part",
               "runtime", "qualification.json", "receipt.json"}
    with os.scandir(generation) as entries:
        for index, entry in enumerate(entries):
            if index >= len(allowed) or entry.name not in allowed:
                raise LocalAcquisitionError("installation_changed")


def _write_new(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _sync(path.parent)
    finally:
        os.unlink(temporary)


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _catalog(path):
    try:
        source = Path(path).expanduser() if path else Path(__file__).with_name(DEFAULT_CATALOG)
        value = strict_json_loads(_safe_file(source, 65536, private=False))
        if type(value) is not dict:
            raise ValueError()
        validate_contract(value)
        if value["schema_id"] != "hormuz.local-acquisition-catalog":
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ContractValidationError, LocalAcquisitionError):
        raise LocalAcquisitionError("catalog_invalid") from None


@contextmanager
def _lock(directory, digest, *, create):
    import fcntl

    try:
        try:
            descriptor = os.open(directory / (".acquire-" + digest + ".lock"),
                (os.O_CREAT if create else 0) | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        except FileNotFoundError:
            if create:
                raise
            yield None
            return
        with os.fdopen(descriptor, "r+b") as stream:
            _file_info(stream, 0)
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LocalAcquisitionError("busy") from None
            yield stream.fileno()
    except OSError:
        raise LocalAcquisitionError("directory_unsafe") from None


def _managed(catalog, generation, executable):
    return ManagedLlamaProfile(route=catalog["profile"]["model"], executable=str(executable),
        runtime_archive=str(generation / "runtime.tar.gz"), runtime_sha256=catalog["runtime"]["sha256"],
        model_file=str(generation / "model.gguf"), model_sha256=catalog["model"]["sha256"],
        startup_timeout_seconds=catalog["profile"]["startup_timeout_seconds"], port=1,
        alias=catalog["profile"]["model"], output_tokens=catalog["profile"]["max_output_tokens"])


def _verify(catalog, generation):
    executable = []
    runtime = generation / "runtime"
    _directory(runtime)
    count = 0
    for parent, directories, files in os.walk(runtime, followlinks=False):
        count += len(directories) + len(files)
        if count > 256:
            raise LocalAcquisitionError("runtime_invalid")
        executable.extend(Path(parent) / name for name in files if name == "llama-server")
    if len(executable) != 1:
        raise LocalAcquisitionError("runtime_invalid")
    for role, name in (("runtime", "runtime.tar.gz"), ("model", "model.gguf")):
        with os.fdopen(os.open(generation / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            if _file_info(stream, catalog[role]["size_bytes"]).st_size != catalog[role]["size_bytes"]:
                raise LocalAcquisitionError("installation_changed")
    _managed(catalog, generation, executable[0]).verify(runtime_root=runtime)
    if _safe_file(generation / "qualification.json", 65536) != _bytes(catalog["profile"]):
        raise LocalAcquisitionError("installation_changed")
    return ["--qualification", str(generation / "qualification.json"),
            "--runtime-archive", str(generation / "runtime.tar.gz"),
            "--llama-executable", str(executable[0]), "--model-file", str(generation / "model.gguf")]


def _download(downloader, artifact, path):
    if path.exists() or path.is_symlink():
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            _file_info(stream, artifact["size_bytes"])
            if not _matches(stream, artifact):
                raise LocalAcquisitionError("installation_changed")
        return
    partial = path.with_name(path.name + ".part")
    descriptor = os.open(partial, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        info = _file_info(stream, artifact["size_bytes"])
        try:
            if info.st_size < artifact["size_bytes"]:
                downloader.fetch(artifact, stream)
        finally:
            stream.flush()
            os.fsync(stream.fileno())
        if not _matches(stream, artifact):
            stream.seek(0)
            stream.truncate()
            raise LocalAcquisitionError("download_invalid")
        os.link(partial, path)
    partial.unlink()
    _sync(path.parent)


def _matches(stream, artifact):
    stream.seek(0)
    digest = hashlib.sha256()
    deadline = time.monotonic() + checksum_timeout(artifact["size_bytes"])
    total = 0
    while chunk := stream.read(1024**2):
        total += len(chunk)
        if time.monotonic() > deadline:
            raise LocalAcquisitionError("download_unavailable")
        if total > artifact["size_bytes"]:
            return False
        digest.update(chunk)
    return total == artifact["size_bytes"] and digest.hexdigest() == artifact["sha256"]


def _remaining_bytes(catalog, generation):
    remaining = 0
    for role, name in (("runtime", "runtime.tar.gz"), ("model", "model.gguf")):
        size = catalog[role]["size_bytes"]
        retained = 0
        for path in (generation / name, generation / (name + ".part")):
            if path.exists() or path.is_symlink():
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                    retained = max(retained, _file_info(stream, size).st_size)
        remaining += size - retained
    return remaining


def acquire_local(args):
    if os.name != "posix":
        raise LocalAcquisitionError("platform_unsupported")
    catalog = _catalog(args.catalog)
    if (catalog["profile"]["system"], catalog["profile"]["machine"]) != (platform.system(), platform.machine()):
        raise LocalAcquisitionError("platform_unsupported")
    if not 1 <= len(args.directory) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in args.directory):
        raise LocalAcquisitionError("directory_unsafe")
    try:
        directory = Path(args.directory).expanduser().absolute()
        directory = directory.parent.resolve(strict=True) / directory.name
    except (OSError, RuntimeError, ValueError):
        raise LocalAcquisitionError("directory_unsafe") from None
    digest = hashlib.sha256(_bytes(catalog)).hexdigest()
    generation = directory / digest
    if directory.exists() or directory.is_symlink():
        _directory(directory)
    generation_present = generation.exists() or generation.is_symlink()
    if args.download:
        _directory(directory, create=True)
    with _lock(directory, digest, create=args.download and not generation_present) as lock_descriptor:
        if lock_descriptor is None and generation_present:
            raise LocalAcquisitionError("installation_changed")
        return _acquire_locked(args, catalog, directory, digest, lock_descriptor)


def _acquire_locked(args, catalog, directory, digest, lock_descriptor):
    generation = directory / digest
    # A lockless preview is a missing-state snapshot; a writer may start after
    # the lock lookup, so do not inspect any generation that then appears.
    inspect = lock_descriptor is not None
    if inspect and (generation.exists() or generation.is_symlink()):
        _directory(generation)
        _layout(generation)
        if _safe_file(generation / "catalog.json", 65536) != _bytes(catalog):
            raise LocalAcquisitionError("installation_changed")
    installed = generation / "receipt.json"
    setup = []
    if inspect and (installed.exists() or installed.is_symlink()):
        previous = strict_json_loads(_safe_file(installed, 65536))
        if type(previous) is not dict:
            raise LocalAcquisitionError("installation_changed")
        validate_contract(previous)
        if previous.get("catalog_sha256") != digest or previous.get("directory") != str(generation):
            raise LocalAcquisitionError("installation_changed")
        setup = _verify(catalog, generation)
    total = (_remaining_bytes(catalog, generation) if inspect else
             sum(catalog[role]["size_bytes"] for role in ("runtime", "model")))
    available = shutil.disk_usage(directory if directory.exists() else directory.parent).free
    required = 0 if setup else total + EXPANSION_LIMIT + 64 * 1024**2
    result = {"schema_id": "hormuz.local-acquisition", "schema_version": 1,
              "catalog_id": catalog["catalog_id"], "catalog_sha256": digest,
              "directory": str(generation), "operation": "preview", "state": "ready" if setup else "planned",
              "artifacts": {role: {**catalog[role], "path": str(generation / name)}
                            for role, name in (("runtime", "runtime.tar.gz"), ("model", "model.gguf"))},
              "storage": {"download_bytes": 0 if setup else total, "required_free_bytes": required,
                          "available_bytes": available}, "setup_arguments": setup}
    if not args.download and not args.offline:
        validate_contract(result)
        return result
    if setup:
        result["operation"] = "reuse"
        validate_contract(result)
        return result
    if args.offline:
        raise LocalAcquisitionError("offline_missing")
    if available < required:
        raise LocalAcquisitionError("space_unavailable")
    if not generation.exists():
        generation.mkdir(mode=0o700)
        _write_new(generation / "catalog.json", _bytes(catalog))
    downloader = AssetDownloader(args.timeout_seconds, lock_descriptor=lock_descriptor)
    try:
        for role, name in (("runtime", "runtime.tar.gz"), ("model", "model.gguf")):
            _download(downloader, catalog[role], generation / name)
    finally:
        downloader.close()
    runtime = generation / "runtime"
    if not runtime.exists() and not runtime.is_symlink():
        extract_runtime(generation / "runtime.tar.gz", runtime)
    qualification = generation / "qualification.json"
    if not qualification.exists() and not qualification.is_symlink():
        _write_new(qualification, _bytes(catalog["profile"]))
    result.update(operation="acquire", state="ready", setup_arguments=_verify(catalog, generation))
    result["storage"] = {"download_bytes": 0, "required_free_bytes": 0,
                         "available_bytes": shutil.disk_usage(directory).free}
    validate_contract(result)
    if not installed.exists() and not installed.is_symlink():
        _write_new(installed, _bytes(result))
    return result
