# Per-user tray preview installation

These scripts prepare an explicitly launched, per-user tray preview. They do not
start the app, add login items, install OS packages or services, change monitor or
Deskflow settings, or enable profile Apply. Native icon/menu acceptance on each
host remains a separate test. Startup opens no browser and requests no GUI focus.
Choosing **Open DeskDeck** or a **Preview** menu item intentionally opens the local
browser. Quit stops this instance's loopback preview server.

Use an independently reviewed checkout at **`~/dev/deskdeck`** and Python 3.12 or
newer with `venv` and `ensurepip`. Run as the normal logged-in desktop user, never
with sudo or an elevated PowerShell. Installation downloads only pinned wheels
from the official PyPI index into a dedicated venv; no source build hooks or
unconstrained dependency resolution run. Python itself is a prerequisite.

## macOS

From the reviewed checkout, with the existing interpreter selected explicitly:

```sh
DESKDECK_PYTHON=/opt/homebrew/bin/python3.12 sh ~/dev/deskdeck/scripts/install.sh
```

Launch separately in the logged-in desktop session:

```sh
"$HOME/Library/Application Support/DeskDeck/venv/bin/python3" -I "$HOME/Library/Application Support/DeskDeck/launch.py"
```

The menu bar icon should appear without switching apps or opening a browser.
Use its menu to inspect the preview. No Accessibility or Input Monitoring grant
is requested by this preview. The command stays running until Quit; this first
version adds no launcher shortcut or automatic startup.

## Linux

A menu-capable GTK AppIndicator/Ayatana backend and visible indicator support in
the desktop session are prerequisites. `gi`/GTK alone and pip's `pystray`/Pillow
are insufficient. GNOME may additionally require a separately approved desktop
extension. No package/extension is installed automatically and a visible tray
icon is not promised until the host's backend is qualified.

When existing distro Python has those bindings, explicitly expose them:

```sh
DESKDECK_PYTHON=/usr/bin/python3 sh ~/dev/deskdeck/scripts/install.sh --system-gtk
"$HOME/.local/share/deskdeck/venv/bin/python3" -I "$HOME/.local/share/deskdeck/launch.py"
```

`--system-gtk` opts into existing system site packages; the pinned application
dependencies are still installed into the venv. Missing/headless/menu-less
backends return a clear unavailable-backend error; no fallback installs occur.
The current host's exact admin prerequisite must be qualified separately.

## Windows

Use a normal PowerShell with an existing Python 3.12+ and the reviewed checkout
at `$HOME\dev\deskdeck`. `-Python` accepts its already verified absolute
executable path; omitting it uses an existing `py -3` launcher. No new system
Python installation is required. Do not bypass an execution policy; if
it blocks a reviewed script, stop for the owner's normal policy decision.

```powershell
& "$HOME\dev\deskdeck\scripts\install.ps1"
# With an existing private Python instead of py:
# & "$HOME\dev\deskdeck\scripts\install.ps1" -Python 'C:\verified\python.exe'
& "$HOME\AppData\Local\DeskDeck\venv\Scripts\pythonw.exe" -I "$HOME\AppData\Local\DeskDeck\launch.py"
```

The wrapper obtains the current user's native SID and refuses elevation or
reparse ancestors. The installer restricts only the fresh dedicated prefix to
that user and SYSTEM, using the native system `icacls` executable. Installation
does not launch a program or change any other folder's permissions. Check the
notification area's overflow if the icon is hidden. Windows ACL/launcher behavior
has source review and fake-test coverage only until a native host test is recorded.

## Ownership, setup and removal

Prefixes are `~/Library/Application Support/DeskDeck` on macOS,
`~/.local/share/deskdeck` on Linux, and `~/AppData/Local/DeskDeck` on Windows.
An existing prefix is never overwritten. An optional reviewed `--setup /absolute/desk.json`
(PowerShell `-Setup`) is validated and copied privately into `config/desk.json`;
the default remains an unqualified preview. Do not put credentials into setup.
The source checkout is never installed under AppData or deleted by uninstall.

The private `.deskdeck-install.json` inventories exact files, hashes and
directories; install output contains an explicit launch command, never an HTTP
token. An ordinary package-install failure leaves an `incomplete` manifest so
the exact owned files can be removed. Unexpected links/unsafe entries stop the
operation; an unverifiable partial prefix is retained for inspection, never
recursively deleted. Pin versions are recorded in the manifest and source.

Quit the tray first. Use the original external Python, not the installed venv:

```sh
DESKDECK_PYTHON=/opt/homebrew/bin/python3.12 sh ~/dev/deskdeck/scripts/uninstall.sh
# Linux: use DESKDECK_PYTHON=/usr/bin/python3 instead.
```

```powershell
& "$HOME\dev\deskdeck\scripts\uninstall.ps1"
# Or supply the same external -Python 'C:\verified\python.exe'.
```

Uninstall preflights the entire prefix before removing any file. Modified program
files, unknown/missing entries, links and ownership changes cause refusal. User
setup is retained even if unchanged, so a prefix containing it remains. All other
unchanged, explicitly inventoried files and empty owned directories are removed;
there is no broad recursive cleanup. Reinstallation requires the owner to move
any retained configuration out of the way deliberately.

Source-only verification: `python3 -B -m unittest tests.test_installer tests.test_tray`.
These tests use fake subprocesses/backends and temporary files; passing them does
not mean installation, native tray visibility or any live profile is qualified.

Pinned dependency metadata: [pystray 0.19.5](https://pypi.org/project/pystray/0.19.5/),
[Pillow 13.0.0](https://pypi.org/project/pillow/13.0.0/),
[PyObjC Quartz 12.2.2](https://pypi.org/pypi/pyobjc-framework-Quartz/12.2.2/json),
[python-xlib 0.33](https://pypi.org/project/python-xlib/0.33/).
