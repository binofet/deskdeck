import argparse
import json

from . import __version__
from .core import plan_profile, profiles, render_deskflow


def main():
    parser = argparse.ArgumentParser(description="DeskDeck desk-profile preview and configuration drafts")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    web = sub.add_parser("serve", help="Open a loopback-only preview panel")
    web.add_argument("--direct-binding", metavar="FILE", help="Explicit private macOS monitor helper binding; enables two monitor transitions")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--setup", metavar="FILE", help="Explicit local planned-wiring JSON (no hardware verification)")
    sub.add_parser("profiles", help="List the complete desired layouts")
    plan = sub.add_parser("plan", help="Preview a complete profile restoration")
    plan.add_argument("profile_id", choices=[p["id"] for p in profiles()])
    plan.add_argument("--setup", metavar="FILE", help="Include local planned input assignments")
    export = sub.add_parser("deskflow-draft", help="Print logical routes for later physical calibration")
    export.add_argument("profile_id", choices=[p["id"] for p in profiles()])
    args = parser.parse_args()
    setup = None
    if getattr(args, "setup", None) is not None:
        from .setup import load_setup
        try:
            setup = load_setup(args.setup)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            parser.error("Port must be between 0 and 65535")
        from .server import serve
        direct_monitor = None
        if args.direct_binding:
            from .monitor_binding import load_binding
            from .direct_monitor import DirectMonitor
            try:
                direct_monitor = DirectMonitor(load_binding(args.direct_binding))
            except (ValueError, OSError):
                parser.error("Private direct monitor binding refused")
        serve(args.port, setup=setup, direct_monitor=direct_monitor)
    elif args.command == "profiles":
        print(json.dumps({"profiles": profiles()}, indent=2))
    elif args.command == "plan":
        print(json.dumps(plan_profile(args.profile_id, setup=setup), indent=2))
    elif args.command == "deskflow-draft":
        print(render_deskflow(args.profile_id), end="")
