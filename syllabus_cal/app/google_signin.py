"""Signing in with Google from a window instead of a terminal.

`calendar_client.run_auth_flow()` blocks until Google redirects back, which is
fine in a terminal (Ctrl-C gets you out) and wrong in an app: closing the
browser tab would leave the app waiting forever. This does the same OAuth
desktop flow in steps so that it can be cancelled, times out on its own, and
reports each way it can fail in words a person can act on.

The steps mirror google-auth-oauthlib's own run_local_server(): listen on a
free localhost port, send the browser to Google with that port as the redirect
address, catch the redirect, swap the code for tokens.
"""

from __future__ import annotations

import html
import json
import os
import socketserver
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from .. import calendar_client
from ..calendar_client import MISSING_CALENDAR_PERMISSION, SCOPES, access_not_granted_message

DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_CLIENT_FILE_BYTES = 64 * 1024

SETUP_HINT = "It's the file from step 4 of the setup guide; its name starts with \"client_secret_\"."


class SignInError(RuntimeError):
    """Sign-in didn't complete. The message is written for the person using the app."""


class SignInCancelled(SignInError):
    """The person backed out, in the app or on Google's page. Not a fault."""


class ClientFileError(ValueError):
    """The chosen file isn't a usable Google client file. The message says why."""


def validate_client_file(raw: bytes) -> dict[str, Any]:
    """Check that `raw` is a Google "Desktop app" OAuth client file.

    People pick the wrong .json here often enough -- the sign-in token, a web
    client, a service-account key -- that each gets its own explanation.
    """
    if len(raw) > MAX_CLIENT_FILE_BYTES:
        raise ClientFileError(f"That file is too large to be Google's credentials file. {SETUP_HINT}")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClientFileError(
            f"That isn't Google's credentials file (it should be a .json file). {SETUP_HINT}"
        ) from exc
    if not isinstance(data, dict):
        raise ClientFileError(f"That isn't Google's credentials file. {SETUP_HINT}")

    if "installed" not in data:
        if "web" in data:
            raise ClientFileError(
                "This file is for a \"Web application\" client. Schedru needs the "
                "\"Desktop app\" kind: in Google Cloud, create a new OAuth client, choose "
                "Desktop app, and download that one."
            )
        if data.get("type") == "service_account":
            raise ClientFileError(
                "This is a service-account key. Schedru needs an OAuth client of the "
                "\"Desktop app\" kind instead."
            )
        if "refresh_token" in data or "token" in data:
            raise ClientFileError(
                "That's the saved sign-in (token.json), not the credentials file. "
                f"{SETUP_HINT}"
            )
        raise ClientFileError(f"That isn't Google's credentials file. {SETUP_HINT}")

    client = data["installed"]
    missing = [
        field
        for field in ("client_id", "auth_uri", "token_uri")
        if not (isinstance(client, dict) and client.get(field))
    ]
    if missing:
        raise ClientFileError(
            "This credentials file is incomplete (no " + ", ".join(missing) + "). "
            "Download it again from Google Cloud."
        )
    return data


def _page(title: str, body: str, ok: bool) -> bytes:
    accent = "#1f7a4d" if ok else "#b3261e"
    mark = "&#10003;" if ok else "&#33;"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Schedru</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         font: 17px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif;
         background: #f6f1e7; color: #1d1b18; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #171614; color: #f1ece2; }} }}
  main {{ max-width: 30rem; padding: 2rem; text-align: center; }}
  .mark {{ width: 3.5rem; height: 3.5rem; border-radius: 50%; margin: 0 auto 1.25rem;
           display: grid; place-items: center; font-size: 1.75rem; font-weight: 700;
           color: #fff; background: {accent}; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 .5rem; }}
  p {{ margin: 0; opacity: .8; }}
</style></head>
<body><main><div class="mark">{mark}</div><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p></main></body></html>
""".encode("utf-8")


SUCCESS_PAGE = _page(
    "Schedru is connected",
    "You can close this tab and go back to Schedru.",
    ok=True,
)
DENIED_PAGE = _page(
    "Sign-in wasn't finished",
    "Nothing was connected. Go back to Schedru to try again.",
    ok=False,
)


class _RedirectHandler(BaseHTTPRequestHandler):
    server_version = "Schedru"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        query = parse_qs(urlsplit(self.path).query)
        if "code" in query or "error" in query:
            # First answer wins: a reload of the redirect page mustn't replace it.
            if self.server.redirect_path is None:  # type: ignore[attr-defined]
                self.server.redirect_path = self.path  # type: ignore[attr-defined]
            body = SUCCESS_PAGE if "code" in query else DENIED_PAGE
            status = 200
        else:
            body = b"Not found"  # favicon and friends
            status = 404

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if status == 200 else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass  # the redirect carries a one-time code; keep it out of any log


class _RedirectServer(HTTPServer):
    redirect_path: str | None = None

    def server_bind(self) -> None:
        # Skip HTTPServer's reverse-DNS lookup of its own address, which can
        # stall for many seconds on macOS (see app/server.py). Here it would
        # be a long pause between pressing the button and the browser opening.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class BrowserSignIn:
    """One attempt at signing in. start(), then wait() (from another thread if
    you like); cancel() makes a pending wait() give up."""

    def __init__(
        self,
        flow: Any,
        *,
        open_browser: Callable[[str], Any] = webbrowser.open,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        host: str = "localhost",
    ) -> None:
        self._flow = flow
        self._open_browser = open_browser
        self._timeout = timeout
        self._host = host
        self._server: _RedirectServer | None = None
        self._cancelled = threading.Event()
        self.auth_url: str | None = None
        self.browser_opened = False

    def start(self) -> str:
        try:
            server = _RedirectServer((self._host, 0), _RedirectHandler)
        except OSError as exc:
            raise SignInError(
                "Schedru couldn't start the sign-in on this computer "
                f"({exc.strerror or exc}). Close other apps that might be blocking it "
                "and try again."
            ) from exc
        server.timeout = 0.25
        self._server = server

        self._flow.redirect_uri = f"http://{self._host}:{server.server_port}/"
        # select_account: always show the account picker, so signing in can't
        # silently reuse whichever Google account the browser happens to be on.
        # consent: Google otherwise issues a refresh token only on the first
        # grant, so signing in again could yield a token that dies an hour later.
        self.auth_url, _ = self._flow.authorization_url(prompt="select_account consent")
        self.open_in_browser()
        return self.auth_url

    def open_in_browser(self) -> bool:
        """(Re)open Google's page. False when no browser could be launched."""
        if not self.auth_url:
            return False
        try:
            self.browser_opened = bool(self._open_browser(self.auth_url))
        except Exception:
            self.browser_opened = False
        return self.browser_opened

    def cancel(self) -> None:
        self._cancelled.set()

    def wait(self) -> Any:
        """Block until Google redirects back; return the new credentials.

        Raises SignInCancelled or SignInError. Always releases the port.
        """
        if self._server is None:
            raise RuntimeError("start() must be called before wait()")
        server = self._server
        deadline = time.monotonic() + self._timeout
        try:
            while server.redirect_path is None:
                if self._cancelled.is_set():
                    raise SignInCancelled("Sign-in cancelled.")
                if time.monotonic() >= deadline:
                    raise SignInError(
                        "The Google sign-in wasn't finished, so Schedru stopped waiting. "
                        "Press Continue with Google to try again."
                    )
                server.handle_request()  # returns after server.timeout if nothing arrives
            path: str = server.redirect_path
        finally:
            server.server_close()
            self._server = None

        return self._exchange(path, server.server_port)

    def _exchange(self, path: str, port: int) -> Any:
        query = parse_qs(urlsplit(path).query)
        if "error" in query:
            code = query["error"][0]
            if code == "access_denied":
                raise SignInCancelled(
                    "You pressed Cancel on Google's page, so nothing was connected."
                )
            raise SignInError(f"Google stopped the sign-in ({code}). Try again.")

        # oauthlib insists the redirect was over https, which a localhost
        # redirect never is. The library's own flow rewrites the scheme too.
        response_uri = f"https://{self._host}:{port}{path}"
        # Without this oauthlib raises a bare Warning when the granted scopes
        # differ from the requested ones; the check below explains it instead.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        try:
            self._flow.fetch_token(authorization_response=response_uri)
        except OAuth2Error as exc:
            raise SignInError(access_not_granted_message(exc)) from exc
        except Warning as exc:
            raise SignInError(MISSING_CALENDAR_PERMISSION) from exc
        except Exception as exc:  # requests/urllib3 connection failures
            raise SignInError(
                "Couldn't reach Google to finish signing in. Check your internet "
                "connection and try again."
            ) from exc

        creds = self._flow.credentials
        granted = getattr(creds, "granted_scopes", None)
        if granted and not set(SCOPES) <= set(granted):
            raise SignInError(MISSING_CALENDAR_PERMISSION)
        return creds


def build_flow(credentials_path: Path) -> Any:
    """The OAuth flow for the saved client file, or a SignInError saying what's
    wrong with it."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    try:
        validate_client_file(credentials_path.read_bytes())
        return InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    except FileNotFoundError as exc:
        raise SignInError("Schedru doesn't have your Google credentials file yet.") from exc
    except (ClientFileError, ValueError, OSError) as exc:
        raise SignInError(str(exc)) from exc


def begin(open_browser: Callable[[str], Any] = webbrowser.open) -> BrowserSignIn:
    """Start a sign-in against the saved client file and open the browser."""
    attempt = BrowserSignIn(build_flow(calendar_client.CREDENTIALS_PATH), open_browser=open_browser)
    attempt.start()
    return attempt
