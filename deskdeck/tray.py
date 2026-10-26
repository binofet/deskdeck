"""Optional local preview tray/menu-bar launcher, with no hardware actions.

Install the optional ``deskdeck[tray]`` extra, then run ``deskdeck-tray`` in
the logged-in graphical session. This does not register automatic startup.
Linux needs a desktop supporting AppIndicator/Ayatana or GTK tray menus;
GNOME may need an indicator extension. A menu-less Xorg backend is refused.

pystray's public usage/backend contract:
https://pystray.readthedocs.io/en/latest/usage.html
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import Callable
from urllib.parse import urlencode
import webbrowser

from .core import get_profile, profiles
from .server import PreviewServer


class TrayUnavailable(RuntimeError):
    """Optional dependencies or the graphical tray backend are unavailable."""


def preview_url(port: int, profile_id: str | None = None) -> str:
    """Build only a loopback preview URL, never an action or remote URL."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("An actual local server port between 1 and 65535 is required")
    url = f"http://127.0.0.1:{port}/"
    if profile_id is not None:
        get_profile(profile_id)
        url += "#" + urlencode({"profile": profile_id})
    return url


def icon_pixels(size: int = 64) -> bytes:
    """A generic monochrome monitor/grid mark, RGBA with a transparent margin."""
    if type(size) is not int or not 32 <= size <= 256:
        raise ValueError("Icon size must be an integer between 32 and 256")
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            u, v = x * 64 // size, y * 64 // size
            monitor = 8 <= u < 56 and 8 <= v < 45
            border = monitor and (u < 11 or u >= 53 or v < 11 or v >= 42)
            divider = monitor and (30 <= u < 33 or (u >= 33 and 25 <= v < 28))
            stand = (29 <= u < 35 and 45 <= v < 52) or (21 <= u < 43 and 52 <= v < 56)
            if border or divider or stand:
                pixels.extend((0, 0, 0, 255))
            elif monitor:
                pixels.extend((255, 255, 255, 255))
            else:
                pixels.extend((0, 0, 0, 0))
    return bytes(pixels)


class TrayActions:
    """User-initiated navigation only; selecting a profile never applies it."""

    def __init__(self, port: int, opener: Callable[[str], bool] | None = None):
        self._port = port
        preview_url(port)
        self._opener = opener if opener is not None else webbrowser.open

    def open(self, profile_id: str | None = None) -> bool:
        url = preview_url(self._port, profile_id)
        opened = bool(self._opener(url))
        if not opened:
            print(f"Open DeskDeck in your browser: {url}", file=sys.stderr)
        return opened

    @staticmethod
    def quit(icon, _item=None):
        # The owning run_tray finally block closes its own preview server.
        icon.stop()


def build_menu(backend, actions: TrayActions):
    """Build callbacks without probing backends or running any callback."""
    def open_app(_icon, _item):
        actions.open()

    def select(profile_id):
        def callback(_icon, _item):
            actions.open(profile_id)
        return callback

    entries = [backend.MenuItem("Open DeskDeck", open_app, default=True), backend.Menu.SEPARATOR]
    entries.extend(backend.MenuItem("Preview " + item["name"], select(item["id"])) for item in profiles())
    entries.extend([backend.Menu.SEPARATOR, backend.MenuItem("Quit DeskDeck", actions.quit)])
    return backend.Menu(*entries)


def _load_backend():
    # Importing a tray backend can itself require a graphical session. Keep
    # normal CLI/server use independent of the optional native dependencies.
    try:
        import pystray
        from PIL import Image
    except Exception as error:
        # Native loaders may raise platform exceptions (for example an X11
        # display error) rather than ImportError. None should leave an owned
        # web server running or imply the native tray was accepted.
        raise TrayUnavailable(
            "Tray support is unavailable. Install the optional deskdeck[tray] extra "
            "and use a logged-in graphical session; the browser preview remains available."
        ) from error
    if not pystray.Icon.HAS_MENU:
        raise TrayUnavailable(
            "This tray backend cannot show profile menus. On Linux use an "
            "AppIndicator/Ayatana or GTK backend supported by your desktop."
        )
    return pystray, Image


def run_tray(port: int = 8765, setup=None, *, open_browser: bool = False):
    """Own one loopback preview server for this explicitly launched tray process.

    Does not attach to an existing server, install a service, watch other apps,
    start Deskflow or call the preview server's gated apply endpoint.
    """
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    if type(open_browser) is not bool:
        raise ValueError("open_browser must be an explicit boolean")
    if threading.current_thread() is not threading.main_thread():
        raise TrayUnavailable("The tray launcher must run on the main thread")
    backend, image = _load_backend()
    server = PreviewServer(("127.0.0.1", port), setup=setup)
    worker = None
    started = False
    try:
        actual_port = server.server_address[1]
        actions = TrayActions(actual_port)
        icon = backend.Icon("deskdeck", image.frombytes("RGBA", (64, 64), icon_pixels()),
                            "DeskDeck — preview only", menu=build_menu(backend, actions))
        worker = threading.Thread(target=server.serve_forever, name="deskdeck-preview", daemon=True)
        worker.start()
        started = True
        print(f"DeskDeck preview: {preview_url(actual_port)}", flush=True)
        print("Tray profiles preview layouts; they do not change monitors or input routing.", flush=True)
        if open_browser:
            actions.open()
        # Native menu loops must stay on the main thread, particularly on macOS.
        icon.run()
    finally:
        if started:
            server.shutdown()
        server.server_close()
        if started:
            worker.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="DeskDeck local preview tray/menu-bar launcher; no hardware actions",
        epilog="Linux requires a menu-capable AppIndicator/Ayatana or GTK tray backend. No automatic startup is installed.",
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Loopback preview port (default: 8765; 0 chooses an available port)")
    parser.add_argument("--setup", metavar="FILE", help="Explicit local planned-wiring JSON")
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument("--open", action="store_true", dest="open_browser",
                         help="Explicitly open the browser at launch (default: tray only)")
    opening.add_argument("--no-open", action="store_false", dest="open_browser",
                         help="Start only the tray (the default)")
    parser.set_defaults(open_browser=False)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    setup = None
    if args.setup is not None:
        from .setup import load_setup
        try:
            setup = load_setup(args.setup)
        except ValueError as error:
            parser.error(str(error))
    try:
        run_tray(args.port, setup=setup, open_browser=args.open_browser)
    except (TrayUnavailable, OSError) as error:
        print(f"DeskDeck tray: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
