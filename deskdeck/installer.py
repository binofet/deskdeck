"""Explicit per-user preview installation; importing this module performs no I/O.

No services, autostart, OS packages, Deskflow or monitor actions are installed.
Use the maintained scripts from an independently reviewed ~/dev/deskdeck checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import sys


COMMON_PINS = ("pystray==0.19.5", "Pillow==13.0.0", "six==1.17.0")
PLATFORM_PINS = {
    "darwin": ("pyobjc-core==12.2.2", "pyobjc-framework-Cocoa==12.2.2", "pyobjc-framework-Quartz==12.2.2"),
    "linux": ("python-xlib==0.33",),
    "win32": (),
}
MANIFEST = ".deskdeck-install.json"
MAX_FILE = 128 * 1024 * 1024
MAX_FILES = 20000


class InstallError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_path(path: Path) -> Path:
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in str(path))):
        raise InstallError("An explicit safe absolute path is required")
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400):
            raise InstallError("Symlinked paths are not supported")
    return path


def prefix_for(home: Path, platform: str) -> Path:
    home = _safe_path(home)
    suffixes = {"linux": (".local", "share", "deskdeck"),
                "darwin": ("Library", "Application Support", "DeskDeck"),
                "win32": ("AppData", "Local", "DeskDeck")}
    if platform not in suffixes:
        raise InstallError("Unsupported platform")
    return home.joinpath(*suffixes[platform])


def _owned(path: Path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        raise InstallError("Unowned, linked or unsupported filesystem entry")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise InstallError("Filesystem entry belongs to another user")
    return info


def _owner(platform: str, windows_owner: str | None) -> str:
    if platform == "win32":
        # The PowerShell wrapper obtains and verifies this native user SID and
        # private-directory ACL. It is not inferred from USERNAME/environment.
        import re
        if not isinstance(windows_owner, str) or not re.fullmatch(r"S-1-(?:\d+-)+\d+", windows_owner):
            raise InstallError("Use the maintained PowerShell wrapper with its native SID/ACL checks")
        return windows_owner
    if not hasattr(os, "getuid") or os.getuid() == 0:
        raise InstallError("Run as a normal desktop user, without sudo")
    return "uid:" + str(os.getuid())


def _parents(home: Path, parent: Path):
    _safe_path(home)
    _owned(home, directory=True)
    current = home
    for component in parent.relative_to(home).parts:
        current = current / component
        if not current.exists():
            current.mkdir(mode=0o700)
        _safe_path(current)
        _owned(current, directory=True)


def _inventory(prefix: Path) -> tuple[dict, list[str]]:
    files, directories = {}, []
    for folder, names, filenames in os.walk(prefix, followlinks=False):
        parent = Path(folder)
        _owned(parent, directory=True)
        for name in names:
            entry = parent / name
            _owned(entry, directory=True)
            directories.append(entry.relative_to(prefix).as_posix())
        for name in filenames:
            entry = parent / name
            info = _owned(entry)
            relative = entry.relative_to(prefix).as_posix()
            if relative == MANIFEST:
                continue
            if info.st_size > MAX_FILE or len(files) >= MAX_FILES:
                raise InstallError("Installation inventory exceeds its bounds")
            files[relative] = {"sha256": _sha(entry.read_bytes()), "size": info.st_size}
    return files, sorted(directories)


def _write(path: Path, data: bytes, mode=0o600):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def _private_windows_prefix(prefix: Path, owner: str):
    """Restrict only the freshly created owned prefix, never an existing tree."""
    if os.name != "nt":
        raise InstallError("Windows installation requires its maintained native wrapper")
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise InstallError("Cannot identify the Windows system directory")
    subprocess.run([str(Path(buffer.value) / "icacls.exe"), str(prefix), "/inheritance:r",
                    "/grant:r", "*" + owner + ":(OI)(CI)F", "/grant:r", "*S-1-5-18:(OI)(CI)F"],
                   shell=False, check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def _clean_env() -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PIP_", "PYTHON", "VIRTUAL_ENV"))
           and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PYTHONDONTWRITEBYTECODE="1")
    return env


def _run(argv: list[str], *, prefix: Path):
    # No shell, package build hook, alternate index, OS installer or native app.
    log = prefix / "install.log"
    with log.open("ab") as stream:
        log.chmod(0o600)
        subprocess.run(argv, shell=False, check=True, stdin=subprocess.DEVNULL,
                       stdout=stream, stderr=subprocess.STDOUT, env=_clean_env(), timeout=600)


def install(*, home: Path, source: Path, platform: str, python: Path,
            runner=None, windows_owner=None, setup: Path | None = None,
            system_gtk: bool = False) -> dict:
    """Install only when explicitly called; tests inject a fake venv/pip runner."""
    # Resolve only the selected running interpreter (e.g. Homebrew's symlink).
    # Source, destination and configuration paths retain the no-link policy.
    home, source, python = _safe_path(home), _safe_path(source), _safe_path(Path(python).resolve(strict=True))
    if source != home / "dev" / "deskdeck":
        raise InstallError("The reviewed checkout must be ~/dev/deskdeck, outside AppData")
    _owned(source, directory=True)
    _safe_path(source / "deskdeck")
    _owned(source / "deskdeck", directory=True)
    if not (source / "deskdeck" / "tray.py").is_file():
        raise InstallError("A reviewed DeskDeck source checkout is required")
    if system_gtk and platform != "linux":
        raise InstallError("System GTK bindings are a Linux-only explicit opt-in")
    owner = _owner(platform, windows_owner)
    prefix = prefix_for(home, platform)
    _parents(home, prefix.parent)
    _safe_path(prefix)
    if prefix.exists():
        raise InstallError("Install prefix already exists; never overwrite it")
    setup_raw = None
    if setup is not None:
        setup = _safe_path(setup)
        if _owned(setup).st_size > 131072:
            raise InstallError("Setup file exceeds its bound")
        from .setup import load_setup
        load_setup(str(setup))
        setup_raw = setup.read_bytes()
    prefix.mkdir(mode=0o700)
    prefix.chmod(0o700)
    if platform == "win32":
        try:
            _private_windows_prefix(prefix, owner)
        except (InstallError, OSError, subprocess.SubprocessError):
            # Only our freshly created, still-empty leaf may be removed here.
            _owned(prefix, directory=True)
            prefix.rmdir()
            raise
    run = runner if runner is not None else _run
    state = "incomplete"
    try:
        argv = [str(python), "-I", "-m", "venv", "--copies"]
        if system_gtk:
            argv.append("--system-site-packages")
        run(argv + [str(prefix / "venv")], prefix=prefix)
        # CPython's POSIX venv may create this redundant fixed compatibility
        # link even with --copies. Remove only that freshly created exact link.
        compatibility = prefix / "venv" / "lib64"
        if compatibility.is_symlink() and os.readlink(compatibility) == "lib":
            compatibility.unlink()
        interpreter = prefix / "venv" / ("Scripts/python.exe" if platform == "win32" else "bin/python3")
        _owned(interpreter)
        pins = COMMON_PINS + PLATFORM_PINS[platform]
        run([str(interpreter), "-I", "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
             "--index-url", "https://pypi.org/simple", "--no-cache-dir", *pins], prefix=prefix)
        package = source / "deskdeck"
        _safe_path(package)
        _owned(package, directory=True)
        for folder, names, filenames in os.walk(package, followlinks=False):
            names[:] = [name for name in names if not name.startswith(".") and name != "__pycache__"]
            for name in names:
                _owned(Path(folder) / name, directory=True)
            for name in filenames:
                original = Path(folder) / name
                relative = original.relative_to(package)
                if original.suffix != ".py" and relative.parts[0] != "static":
                    continue
                info = _owned(original)
                if info.st_size > 4194304:
                    raise InstallError("Source payload file exceeds its bound")
                target = prefix / "app" / "deskdeck" / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write(target, original.read_bytes())
        config = prefix / "config"
        config.mkdir(mode=0o700)
        if setup_raw is not None:
            _write(config / "desk.json", setup_raw)
        arguments = ["--port", "0", "--no-open"]
        if setup_raw is not None:
            arguments.extend(["--setup", str(config / "desk.json")])
        launcher = ("import sys\nfrom pathlib import Path\nsys.dont_write_bytecode = True\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parent / 'app'))\n"
                    "from deskdeck.tray import main\nraise SystemExit(main(" + repr(arguments) + "))\n")
        _write(prefix / "launch.py", launcher.encode())
        state = "installed_preview_unqualified"
    finally:
        compatibility = prefix / "venv" / "lib64"
        if compatibility.is_symlink() and os.readlink(compatibility) == "lib":
            compatibility.unlink()
        files, directories = _inventory(prefix)
        record = {"schema": 1, "installation_id": secrets.token_hex(16), "state": state,
                  "home": str(home), "prefix": str(prefix), "source": str(source), "platform": platform,
                  "owner": owner, "pins": list(COMMON_PINS + PLATFORM_PINS[platform]),
                  "system_gtk": system_gtk, "files": files, "directories": directories,
                  "user_config": ["config/desk.json"] if setup_raw is not None else []}
        _write(prefix / MANIFEST, json.dumps(record, sort_keys=True, indent=2).encode() + b"\n")
    return record


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InstallError("Unsafe manifest member")
    path = PurePosixPath(value)
    if path.is_absolute() or value in (".", MANIFEST) or ".." in path.parts or path.as_posix() != value or ":" in value:
        raise InstallError("Unsafe manifest member")
    return value


def uninstall(*, home: Path, platform: str, windows_owner=None) -> dict:
    """Preflight every owned member, then unlink exact unchanged files only."""
    home = _safe_path(home)
    prefix = _safe_path(prefix_for(home, platform))
    owner = _owner(platform, windows_owner)
    _owned(prefix, directory=True)
    manifest = prefix / MANIFEST
    if _owned(manifest).st_size > 4194304:
        raise InstallError("Manifest exceeds its bound")
    record = json.loads(manifest.read_bytes())
    if (not isinstance(record, dict) or record.get("schema") != 1 or record.get("owner") != owner or record.get("home") != str(home)
            or record.get("prefix") != str(prefix) or record.get("platform") != platform
            or record.get("source") != str(home / "dev" / "deskdeck")
            or type(record.get("files")) is not dict or type(record.get("directories")) is not list
            or record.get("user_config") not in ([], ["config/desk.json"])):
        raise InstallError("Installation ownership manifest does not match")
    expected = {_relative(name): value for name, value in record["files"].items()}
    directories = [_relative(name) for name in record["directories"]]
    if len(set(directories)) != len(directories) or any(
            type(value) is not dict or set(value) != {"sha256", "size"}
            or type(value["size"]) is not int or not 0 <= value["size"] <= MAX_FILE
            or not isinstance(value["sha256"], str) or len(value["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in value["sha256"])
            for value in expected.values()):
        raise InstallError("Invalid owned-file inventory")
    actual, actual_directories = _inventory(prefix)
    if set(actual) != set(expected) or set(actual_directories) != set(directories):
        raise InstallError("Unowned, missing or unexpected entries; nothing removed")
    changed = {name for name in actual if actual[name] != expected[name]}
    if changed - set(record["user_config"]):
        raise InstallError("Installed program files changed; nothing removed")
    # Never delete the interpreter running this uninstall (notably Windows).
    if prefix in Path(sys.executable).resolve(strict=True).parents:
        raise InstallError("Run uninstall using the original external Python")
    preserved = set(record["user_config"])
    for name in sorted(set(expected) - preserved):
        target = _safe_path(prefix / name)
        info = _owned(target)
        if {"sha256": _sha(target.read_bytes()), "size": info.st_size} != expected[name]:
            raise InstallError("Entry changed during uninstall; stopped")
        target.unlink()
    manifest.unlink()
    for name in sorted(directories, key=lambda item: item.count("/"), reverse=True):
        target = _safe_path(prefix / name)
        if not any(target.iterdir()):
            target.rmdir()
    if not any(prefix.iterdir()):
        prefix.rmdir()
    return {"removed": sorted(set(expected) - preserved), "preserved_user_config": sorted(preserved),
            "prefix_retained": prefix.exists()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit per-user DeskDeck preview installer; no autostart")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--source", type=Path, default=Path.home() / "dev" / "deskdeck")
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--system-gtk", action="store_true", help="Explicitly expose existing Linux system GTK bindings")
    parser.add_argument("--windows-owner-sid", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 12):
            raise InstallError("Python 3.12 or newer with venv/ensurepip is required")
        if args.action == "install":
            record = install(home=Path.home(), source=args.source, platform=sys.platform,
                             python=Path(sys.executable).absolute(), setup=args.setup,
                             windows_owner=args.windows_owner_sid, system_gtk=args.system_gtk)
            prefix = Path(record["prefix"])
            interpreter = prefix / "venv" / ("Scripts/pythonw.exe" if sys.platform == "win32" else "bin/python3")
            print("Installed preview only. No app was started. Launch explicitly with:")
            print(json.dumps([str(interpreter), "-I", str(prefix / "launch.py")]))
        else:
            print(json.dumps(uninstall(home=Path.home(), platform=sys.platform,
                                       windows_owner=args.windows_owner_sid), sort_keys=True))
        return 0
    except (InstallError, OSError, subprocess.SubprocessError, ValueError):
        print("DeskDeck installation stopped. No automatic cleanup/retry was attempted; inspect the private prefix/manifest.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
