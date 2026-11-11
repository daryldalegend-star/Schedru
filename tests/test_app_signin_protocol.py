"""The whole Google sign-in exchange, with a pretend Google on localhost.

test_app_google_signin.py uses a fake `flow` object, so it can't tell whether
the real libraries (google-auth-oauthlib, requests-oauthlib, oauthlib) are
being driven correctly. Here the flow is the real one, and "Google" is a
small local server that behaves the way Google's endpoints do: it issues a
code only for a well-formed request, and exchanges it only if the secret
half of the PKCE pair matches. A real Google account is the one thing this
can't stand in for.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socketserver
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from google_auth_oauthlib.flow import InstalledAppFlow

from syllabus_cal import calendar_client
from syllabus_cal.app.google_signin import BrowserSignIn, SignInCancelled, SignInError
from syllabus_cal.calendar_client import MISSING_CALENDAR_PERMISSION, SCOPES

CLIENT_ID = "1234-abc.apps.googleusercontent.com"
CLIENT_SECRET = "not-a-real-secret"


class _NoLookupServer(HTTPServer):
    def server_bind(self):
        # The stock HTTPServer asks DNS for its own name here, which costs
        # about 35 seconds on a Mac (the very thing the app's servers avoid).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class PretendGoogle:
    """The token endpoint, plus a record of everything it was sent."""

    def __init__(self, granted_scope: str = " ".join(SCOPES), token_error: str | None = None):
        self.granted_scope = granted_scope
        self.token_error = token_error
        self.token_requests: list[dict[str, str]] = []
        self.challenges: dict[str, str] = {}  # code -> PKCE challenge it was issued for
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                # Like Google, accept the client's id and secret either in the
                # form or as HTTP Basic credentials (which is what gets sent).
                scheme, _, encoded = (self.headers.get("Authorization") or "").partition(" ")
                if scheme == "Basic":
                    client_id, _, secret = base64.b64decode(encoded).decode().partition(":")
                    form.setdefault("client_id", client_id)
                    form.setdefault("client_secret", secret)
                outer.token_requests.append(form)

                status, payload = outer.answer(form)
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = _NoLookupServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def answer(self, form: dict[str, str]) -> tuple[int, dict]:
        if self.token_error:
            return 400, {"error": self.token_error, "error_description": "Bad Request"}
        challenge = self.challenges.get(form.get("code", ""))
        if challenge is None:
            return 400, {"error": "invalid_grant", "error_description": "unknown code"}
        verifier = form.get("code_verifier", "")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if digest != challenge:
            return 400, {"error": "invalid_grant", "error_description": "PKCE mismatch"}
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return 401, {"error": "invalid_client"}
        return 200, {
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "expires_in": 3599,
            "scope": self.granted_scope,
            "token_type": "Bearer",
        }

    def client_config(self) -> dict:
        return {
            "installed": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": f"http://127.0.0.1:{self.port}/auth",
                "token_uri": f"http://127.0.0.1:{self.port}/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class PretendBrowser:
    """Stands in for the person: receives the sign-in address, "approves", and
    follows the redirect back to the app, as a browser would."""

    def __init__(self, google: PretendGoogle, *, approve: bool = True, tamper_state: bool = False):
        self.google = google
        self.approve = approve
        self.tamper_state = tamper_state
        self.asked: dict[str, str] = {}
        self.page: str | None = None
        self.done = threading.Event()

    def __call__(self, url: str) -> bool:
        self.asked = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        threading.Thread(target=self._redirect, daemon=True).start()
        return True

    def _redirect(self) -> None:
        asked = self.asked
        if self.approve:
            self.google.challenges["one-time-code"] = asked["code_challenge"]
            answer = {
                "state": "tampered" if self.tamper_state else asked["state"],
                "code": "one-time-code",
                "scope": asked["scope"],
            }
        else:
            answer = {"state": asked["state"], "error": "access_denied"}
        try:
            with urllib.request.urlopen(f"{asked['redirect_uri']}?{urlencode(answer)}", timeout=10) as response:
                self.page = response.read().decode()
        except urllib.error.URLError as error:
            self.page = f"failed: {error}"
        self.done.set()


@pytest.fixture
def google(monkeypatch):
    # The pretend endpoints are plain http on localhost; the real ones are https.
    monkeypatch.setenv("OAUTHLIB_INSECURE_TRANSPORT", "1")
    made: list[PretendGoogle] = []

    def make(**kwargs) -> PretendGoogle:
        made.append(PretendGoogle(**kwargs))
        return made[-1]

    yield make
    for server in made:
        server.close()


def sign_in(google: PretendGoogle, browser: PretendBrowser):
    flow = InstalledAppFlow.from_client_config(google.client_config(), SCOPES)
    attempt = BrowserSignIn(flow, open_browser=browser, timeout=15)
    attempt.start()
    return attempt.wait()


def test_a_full_sign_in_yields_working_credentials(google):
    server = google()
    browser = PretendBrowser(server)

    credentials = sign_in(server, browser)

    assert credentials.token == "access-123"
    assert credentials.refresh_token == "refresh-456"  # what keeps you signed in for the week
    assert set(SCOPES) <= set(credentials.granted_scopes)
    assert browser.done.wait(5) and "Schedru is connected" in browser.page


def test_the_request_to_google_is_what_google_requires(google):
    server = google()
    browser = PretendBrowser(server)
    sign_in(server, browser)

    asked = browser.asked
    assert asked["client_id"] == CLIENT_ID
    assert asked["response_type"] == "code"
    assert asked["scope"] == " ".join(SCOPES)  # calendar events, and nothing more
    assert asked["access_type"] == "offline"  # without this there is no refresh token
    assert asked["prompt"] == "select_account consent"
    assert asked["code_challenge_method"] == "S256" and len(asked["code_challenge"]) >= 43
    redirect = urlsplit(asked["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == ("http", "localhost", "/")

    (exchange,) = server.token_requests
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "one-time-code"
    assert exchange["redirect_uri"] == asked["redirect_uri"]  # must match exactly
    assert exchange["client_secret"] == CLIENT_SECRET


def test_the_saved_sign_in_can_be_loaded_again(google, tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_client, "TOKEN_PATH", tmp_path / "token.json")
    server = google()
    credentials = sign_in(server, PretendBrowser(server))

    calendar_client.save_token(credentials)
    reloaded = calendar_client.load_credentials()

    assert reloaded.token == "access-123" and reloaded.refresh_token == "refresh-456"
    assert reloaded.client_id == CLIENT_ID
    assert reloaded.valid


def test_a_reply_meant_for_another_sign_in_is_refused(google):
    """The `state` value ties Google's answer to the request that asked for
    it. An answer carrying a different one must never be exchanged."""
    server = google()
    with pytest.raises(SignInError):
        sign_in(server, PretendBrowser(server, tamper_state=True))
    assert server.token_requests == []


def test_pressing_cancel_at_google_is_a_cancellation_not_an_error(google):
    server = google()
    with pytest.raises(SignInCancelled):
        sign_in(server, PretendBrowser(server, approve=False))
    assert server.token_requests == []


def test_leaving_the_calendar_box_unticked_is_explained(google):
    server = google(granted_scope="openid")
    with pytest.raises(SignInError) as caught:
        sign_in(server, PretendBrowser(server))
    assert str(caught.value) == MISSING_CALENDAR_PERMISSION


def test_google_rejecting_the_code_is_reported(google):
    server = google(token_error="invalid_grant")
    with pytest.raises(SignInError) as caught:
        sign_in(server, PretendBrowser(server))
    assert "Google didn't grant access" in str(caught.value)
