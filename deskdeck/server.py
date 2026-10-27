"""Loopback-only preview server. No monitor or input-control process is started."""

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import get_profile, plan_profile, profiles, status
from .setup import setup_summary, validate_setup
from .desk_model import validate_desk_model, parse_desk_model, DeskModelError

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/static/style.css": ("style.css", "text/css; charset=utf-8"),
                "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/static/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
                "/static/icon.svg": ("icon.svg", "image/svg+xml")}


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, setup=None):
        if address[0] != "127.0.0.1":
            raise ValueError("This preview release binds only to 127.0.0.1")
        self.session_token = secrets.token_urlsafe(32)
        self.setup = validate_setup(setup) if setup is not None else None
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "DeskDeck"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass  # Do not write request bodies, local settings or session tokens into logs.

    def _allowed(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {"http://" + host for host in hosts}

    def _send(self, code, data, content_type="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "Untrusted host or origin"})
        path = urlsplit(self.path).path
        if path == "/api/session":
            return self._send(200, {"token": self.server.session_token})
        if path == "/api/profiles":
            data = {"profiles": profiles()}
            if self.server.setup is not None:
                data["setup"] = setup_summary(self.server.setup)
            return self._send(200, data)
        if path == "/api/status":
            return self._send(200, status())
        if path in STATIC_FILES:
            name, mime = STATIC_FILES[path]
            try:
                return self._send(200, (STATIC / name).read_bytes(), mime)
            except FileNotFoundError:
                return self._send(404, {"error": "Asset unavailable"})
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "Untrusted host or origin"})
        supplied = self.headers.get("X-DeskDeck-Token", "")
        if not hmac.compare_digest(supplied.encode(), self.server.session_token.encode()):
            return self._send(403, {"error": "Invalid session token"})
        if self.headers.get_content_type() != "application/json":
            return self._send(415, {"error": "Use application/json"})
        if self.headers.get("Transfer-Encoding"):
            return self._send(400, {"error": "Transfer encoding is not supported"})
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 262144 if path == "/api/desk-model" else 4096
            if not 0 < length <= limit:
                return self._send(413, {"error": "Invalid request size"})
            body = json.loads(self.rfile.read(length))
            if path == "/api/desk-model":
                if type(body) is not dict or set(body) not in ({"model"}, {"document"}):
                    raise ValueError("Expected one desk model")
                try:
                    model = (parse_desk_model(body["document"]) if "document" in body
                             else validate_desk_model(body["model"]))
                except DeskModelError as error:
                    return self._send(400, {"error": str(error)})
                return self._send(200, {"model": model, "applied": False})
            if not isinstance(body, dict) or set(body) != {"profile_id"}:
                raise ValueError("Expected profile_id only")
            if not isinstance(body["profile_id"], str):
                raise ValueError("Invalid profile_id")
            get_profile(body["profile_id"])
        except (ValueError, UnicodeError, RecursionError):
            return self._send(400, {"error": "Invalid profile request"})
        if path == "/api/plan":
            return self._send(200, plan_profile(body["profile_id"], setup=self.server.setup))
        if path == "/api/apply":
            return self._send(409, {"error": "Preview release: hardware and Deskflow activation are not connected", "ready": False})
        return self._send(404, {"error": "Not found"})


def serve(port=8765, setup=None):
    server = PreviewServer(("127.0.0.1", port), setup=setup)
    print(f"DeskDeck preview: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("Local preview only. No monitor, network KVM or Deskflow settings will change.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
