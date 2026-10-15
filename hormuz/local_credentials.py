"""Read one private, versioned local caller credential; never expose its value in errors."""
import os
from pathlib import Path
import re
import stat

from .compaction_formats import strict_json_loads


class LocalCredentialError(ValueError):
    pass


def read_local_credential(path):
    path = Path(path)
    try:
        parent = path.parent.lstat()
        if (not path.is_absolute() or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
            raise LocalCredentialError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 1024):
                raise LocalCredentialError()
            value = strict_json_loads(stream.read(1025).decode("utf-8"))
        if (type(value) is not dict or set(value) != {"schema_id", "schema_version", "token"}
                or value["schema_id"] != "hormuz.local-router-credential"
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["token"]) is not str or re.fullmatch(r"[a-f0-9]{64}", value["token"]) is None):
            raise LocalCredentialError()
        return value["token"]
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise LocalCredentialError("local_credential_unavailable") from None
