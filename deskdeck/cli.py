import argparse
import json

from . import __version__
from .core import plan_profile, profiles, render_deskflow


def main():
    parser = argparse.ArgumentParser(description="DeskDeck desk-profile preview and configuration drafts")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    web = sub.add_parser("serve", help="Open a loopback-only preview panel")
    web.add_argument("--port", type=int, default=8765)
    sub.add_parser("profiles", help="List the five complete desired layouts")
    plan = sub.add_parser("plan", help="Preview a complete profile restoration")
    plan.add_argument("profile_id", choices=[p["id"] for p in profiles()])
    export = sub.add_parser("deskflow-draft", help="Print logical routes for later physical calibration")
    export.add_argument("profile_id", choices=[p["id"] for p in profiles()])
    args = parser.parse_args()
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            parser.error("Port must be between 0 and 65535")
        from .server import serve
        serve(args.port)
    elif args.command == "profiles":
        print(json.dumps({"profiles": profiles()}, indent=2))
    elif args.command == "plan":
        print(json.dumps(plan_profile(args.profile_id), indent=2))
    elif args.command == "deskflow-draft":
        print(render_deskflow(args.profile_id), end="")
