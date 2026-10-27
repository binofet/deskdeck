"""Read only an explicitly selected private local binding; never run its helper."""
import json
import os
from pathlib import Path
import stat
import sys
from .direct_monitor import DirectMonitorBinding


def load_binding(filename):
    if sys.platform != "darwin":
        raise ValueError("Direct monitor binding requires macOS")
    path = Path(filename)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Absolute private binding required")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Symlink binding refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077 or not 0 < info.st_size <= 4096:
            raise ValueError("Private owned regular binding required")
        raw = os.read(fd, 4097)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate key")
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or set(value) != {"executable", "sha256", "monitor_uuid"}:
            raise ValueError("Invalid binding")
        return DirectMonitorBinding(**value)
    finally:
        os.close(fd)
