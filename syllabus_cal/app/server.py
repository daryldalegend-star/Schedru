"""The window's link to the app: a web server that only this computer, and
only this app's own page, can talk to.

It serves the UI's files and turns `/api/...` requests into Service calls.

A local web server is reachable by anything running on the machine, and --
through the browser -- by any website the person happens to have open. The
calls here can save an API key and write to a calendar, so three checks stand
between a request and the Service:

1. It listens on 127.0.0.1 only. Nothing off this computer can connect.
2. The `Host` header must name this server. That defeats DNS rebinding, where a
   hostile page gets its own domain to resolve to 127.0.0.1.
3. Every API call must carry a random token minted at launch and handed only
   to the app's own page. Another site can make the browser *send* a request
   here, but can't read the page to learn the token, and can't attach the
   custom header without a permission (CORS) this server never grants.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .service import MAX_UPLOAD_BYTES, ApiError, Service

TOKEN_HEADER = "X-Schedru-Token"
TOKEN_PLACEHOLDER = "__SCHEDRU_TOKEN__"
MAX_JSON_BYTES = 1024 * 1024
LONG_POLL_SECONDS = 25.0

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)

_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".vtt": "text/vtt; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_JOB_ROUTE = re.compile(r"^/api/job/([A-Za-z0-9_-]+)(/[a-z]+)?$")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Schedru"
    sys_version = ""
    server: "_Httpd"

    # ---- plumbing ------------------------------------------------------

    def log_message(self, *args: Any) -> None:
        pass  # request lines can carry the token; never write them anywhere

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json", {"Cache-Control": "no-store"})

    def _error(self, status: int, code: str, message: str, **extra: Any) -> None:
        self._json({"error": {"code": code, "message": message, **extra}}, status)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin.strip().lower() in self.server.allowed_origins

    def _token_ok(self, query: dict[str, list[str]], allow_query: bool = False) -> bool:
        offered = self.headers.get(TOKEN_HEADER)
        if offered is None and allow_query:
            offered = (query.get("t") or [None])[0]
        return offered is not None and hmac.compare_digest(
            offered.encode("utf-8"), self.server.token.encode("utf-8")
        )

    def _body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            raise ApiError(411, "no_length", "The request had no length.") from None
        if length < 0:
            raise ApiError(400, "bad_length", "The request had a bad length.")
        if length > limit:
            # Refuse without reading it; the connection can't be reused after.
            self.close_connection = True
            raise ApiError(413, "too_large", "That's too large.")
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, Any]:
        raw = self._body(MAX_JSON_BYTES)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "bad_json", "The request wasn't valid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "The request wasn't a JSON object.")
        return data

    # ---- entry points --------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        try:
            if not self._host_ok():
                self._error(403, "bad_host", "Forbidden.")
                return
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            path = parts.path

            if not path.startswith("/api/"):
                if self.command == "POST":
                    self._error(405, "method", "Not allowed.")
                else:
                    self._static(path)
                return

            image_request = self.command == "GET" and path.endswith("/image")
            if not self._token_ok(query, allow_query=image_request):
                self._error(403, "bad_token", "Forbidden.")
                return
            if self.command == "POST" and not self._origin_ok():
                self._error(403, "bad_origin", "Forbidden.")
                return

            if self.command == "POST":
                self._api_post(path)
            else:
                self._api_get(path, query)
        except ApiError as exc:
            self._error(exc.status, exc.code, exc.message, **exc.extra)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # the page went away mid-answer
        except Exception as exc:  # a bug; answer rather than hang the page
            try:
                self._error(500, "internal", f"Something went wrong ({type(exc).__name__}).")
            except Exception:
                self.close_connection = True

    # ---- API -----------------------------------------------------------

    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        service = self.server.service

        if path == "/api/status":
            self._json(service.status())
            return

        if path == "/api/events":
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            current = service.hub.seq
            if since > current:
                # The page outlived a restart of the app; start it over.
                self._json({"events": [], "seq": current, "reset": True})
                return
            notes = service.hub.wait(since, LONG_POLL_SECONDS)
            self._json({"events": notes, "seq": service.hub.seq})
            return

        match = _JOB_ROUTE.match(path)
        if match:
            job = service.job(match.group(1))
            action = match.group(2)
            if action is None:
                self._json(job.to_json())
                return
            if action == "/image":
                try:
                    data = job.image.read_bytes()
                except OSError:
                    raise ApiError(404, "no_image", "That screenshot is gone.") from None
                content_type = _MIME_TYPES.get(job.image.suffix.lower(), "application/octet-stream")
                self._send(200, data, content_type, {"Cache-Control": "no-store"})
                return

        self._error(404, "not_found", "Not found.")

    def _api_post(self, path: str) -> None:
        service = self.server.service

        if path == "/api/read":
            job = service.accept_upload(self._body(MAX_UPLOAD_BYTES))
            self._json(job.to_json())
            return
        if path == "/api/credentials":
            service.save_credentials(self._body(MAX_JSON_BYTES))
            self._json({"ok": True})
            return

        body = self._json_body()

        if path == "/api/key":
            self._json(service.save_key(str(body.get("key", ""))))
        elif path == "/api/google/signin":
            self._json(service.start_sign_in())
        elif path == "/api/google/cancel":
            service.cancel_sign_in()
            self._json({"ok": True})
        elif path == "/api/google/signout":
            service.sign_out()
            self._json({"ok": True})
        elif path == "/api/google/retry":
            threading.Thread(target=service.refresh_google, daemon=True).start()
            self._json({"ok": True})
        elif path == "/api/tutorial/seen":
            service.mark_tutorial_seen()
            self._json({"ok": True})
        elif path == "/api/capture":
            self._json(service.capture(origin="button", force=bool(body.get("force"))))
        elif path == "/api/capture/settings":
            service.open_capture_settings()
            self._json({"ok": True})
        elif path == "/api/open":
            service.open_external(str(body.get("url", "")))
            self._json({"ok": True})
        elif path.startswith("/api/debug/"):
            self._debug(path, body)
        else:
            match = _JOB_ROUTE.match(path)
            if match and match.group(2) == "/add":
                self._json(service.add_events(match.group(1), body.get("events")))
            elif match and match.group(2) == "/clear":
                service.clear_job(match.group(1))
                self._json({"ok": True})
            else:
                self._error(404, "not_found", "Not found.")

    def _debug(self, path: str, body: dict[str, Any]) -> None:
        """Hooks for the automated end-to-end tests. Off unless the app was
        launched with them switched on, and still behind the token."""
        hooks = self.server.debug_hooks
        name = path[len("/api/debug/") :]
        if not hooks or name not in hooks:
            self._error(404, "not_found", "Not found.")
            return
        self._json({"result": hooks[name](body)})

    # ---- files ---------------------------------------------------------

    def _static(self, path: str) -> None:
        root = self.server.web_dir
        if path in ("", "/"):
            path = "/index.html"
        target = (root / path.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        suffix = target.suffix.lower()
        content_type = _MIME_TYPES.get(suffix) or mimetypes.guess_type(target.name)[0]
        content_type = content_type or "application/octet-stream"

        if target.name == "index.html":
            page = target.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, self.server.token)
            self._send(
                200,
                page.encode("utf-8"),
                content_type,
                {
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
                    "X-Frame-Options": "DENY",
                },
            )
            return

        data = target.read_bytes()
        cache = "no-store" if suffix in (".css", ".js", ".html") else "max-age=3600"
        headers = {"Cache-Control": cache, "Accept-Ranges": "bytes"}

        # Video elements ask for byte ranges, and WebKit won't play without them.
        requested = _RANGE.match(self.headers.get("Range") or "")
        if requested and (requested.group(1) or requested.group(2)):
            size = len(data)
            if requested.group(1):
                start = int(requested.group(1))
                end = int(requested.group(2)) if requested.group(2) else size - 1
            else:  # "bytes=-500": the last 500 bytes
                start = max(0, size - int(requested.group(2)))
                end = size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self._send(416, b"", content_type, {"Content-Range": f"bytes */{size}"})
                return
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send(206, data[start : end + 1], content_type, headers)
            return

        self._send(200, data, content_type, headers)


class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        # HTTPServer.server_bind() looks up the name of its own address
        # (socket.getfqdn), a reverse-DNS query that macOS can take half a
        # minute to give up on. That was the app's entire start-up time on the
        # first real Mac it ran on. The name is never used, so don't ask.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port

    service: Service
    web_dir: Path
    token: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    debug_hooks: dict[str, Callable[[dict[str, Any]], Any]]


class AppServer:
    def __init__(
        self,
        service: Service,
        web_dir: Path,
        *,
        port: int = 0,
        token: str | None = None,
        debug_hooks: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    ) -> None:
        self.service = service
        self.token = token or secrets.token_urlsafe(32)
        self._httpd = _Httpd(("127.0.0.1", port), _Handler)
        self.port: int = self._httpd.server_address[1]
        self._httpd.service = service
        self._httpd.web_dir = Path(web_dir).resolve()
        self._httpd.token = self.token
        self._httpd.allowed_hosts = frozenset(
            {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        )
        self._httpd.allowed_origins = frozenset(
            {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        )
        self._httpd.debug_hooks = dict(debug_hooks or {})
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def debug_hooks(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return self._httpd.debug_hooks

    def start(self) -> None:
        # The short poll interval is how quickly stop() is noticed: quitting
        # the app shouldn't hang for the default half second.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="schedru-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
