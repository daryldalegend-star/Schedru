"""The cancellable browser sign-in, driven by a fake Google.

A real HTTP request is made to the redirect listener in each test, because
that listener *is* the thing under test: what it accepts, what it ignores, and
that it lets go of its port.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, InvalidGrantError

from syllabus_cal.app.google_signin import (
    BrowserSignIn,
    ClientFileError,
    SignInCancelled,
    SignInError,
    build_flow,
    validate_client_file,
)
from syllabus_cal.calendar_client import MISSING_CALENDAR_PERMISSION, SCOPES

CLIENT_FILE = {
    "installed": {
        "client_id": "1234-abc.apps.googleusercontent.com",
        "project_id": "schedru-test",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "not-a-real-secret",
        "redirect_uris": ["http://localhost"],
    }
}


class FakeCredentials:
    def __init__(self, granted=None):
        self.granted_scopes = granted


class FakeFlow:
    """Records what the sign-in asked of it; `fail` is raised by fetch_token."""

    def __init__(self, granted=tuple(SCOPES), fail=None):
        self.redirect_uri = None
        self.authorization_kwargs = None
        self.fetched = None
        self.credentials = FakeCredentials(list(granted) if granted is not None else None)
        self._fail = fail

    def authorization_url(self, **kwargs):
        self.authorization_kwargs = kwargs
        return f"https://accounts.example/auth?redirect_uri={self.redirect_uri}", "state-123"

    def fetch_token(self, authorization_response):
        self.fetched = authorization_response
        if self._fail is not None:
            raise self._fail


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def run(attempt: BrowserSignIn):
    """wait() on a thread; returns a function that joins and gives the outcome."""
    box = {}

    def target():
        try:
            box["value"] = attempt.wait()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def outcome(timeout=5):
        thread.join(timeout)
        assert not thread.is_alive(), "wait() never returned"
        if "error" in box:
            raise box["error"]
        return box["value"]

    return outcome


def start(flow=None, **kwargs):
    opened = []
    flow = flow or FakeFlow()
    attempt = BrowserSignIn(flow, open_browser=lambda url: opened.append(url) or True, **kwargs)
    attempt.start()
    return attempt, flow, opened


# ---- the happy path -------------------------------------------------------


def test_start_opens_the_browser_at_googles_page_with_a_localhost_redirect():
    attempt, flow, opened = start()
    try:
        redirect = urlsplit(flow.redirect_uri)
        assert redirect.scheme == "http" and redirect.hostname == "localhost"
        assert redirect.port and redirect.path == "/"
        assert opened == [attempt.auth_url]
        assert attempt.browser_opened is True
        # Always the account picker, always a fresh refresh token.
        assert flow.authorization_kwargs == {"prompt": "select_account consent"}
    finally:
        attempt.cancel()
        with pytest.raises(SignInCancelled):
            attempt.wait()


def test_a_redirect_with_a_code_completes_the_sign_in():
    attempt, flow, _ = start()
    outcome = run(attempt)

    status, page = get(flow.redirect_uri + "?state=state-123&code=4/abc&scope=x")
    assert status == 200
    assert "Schedru is connected" in page

    assert outcome() is flow.credentials
    fetched = urlsplit(flow.fetched)
    assert fetched.scheme == "https"  # oauthlib refuses plain http
    assert fetched.netloc == urlsplit(flow.redirect_uri).netloc
    assert parse_qs(fetched.query)["code"] == ["4/abc"]
    assert parse_qs(fetched.query)["state"] == ["state-123"]


def test_other_requests_do_not_end_the_wait():
    attempt, flow, _ = start()
    outcome = run(attempt)

    assert get(flow.redirect_uri + "favicon.ico")[0] == 404
    assert get(flow.redirect_uri)[0] == 404
    time.sleep(0.1)
    assert get(flow.redirect_uri + "?code=real")[0] == 200

    outcome()
    assert parse_qs(urlsplit(flow.fetched).query)["code"] == ["real"]


def test_the_port_is_released_afterwards():
    attempt, flow, _ = start()
    port = urlsplit(flow.redirect_uri).port
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")
    outcome()

    with pytest.raises(OSError):
        socket.create_connection(("localhost", port), timeout=1).close()


def test_unknown_granted_scopes_are_not_treated_as_missing():
    attempt, flow, _ = start(FakeFlow(granted=None))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")
    assert outcome() is flow.credentials


# ---- the ways it doesn't finish -------------------------------------------


def test_cancel_stops_the_wait_promptly():
    attempt, _, _ = start()
    outcome = run(attempt)
    began = time.monotonic()
    attempt.cancel()

    with pytest.raises(SignInCancelled):
        outcome()
    assert time.monotonic() - began < 2


def test_it_gives_up_on_its_own():
    attempt, _, _ = start(timeout=0.4)
    with pytest.raises(SignInError) as caught:
        run(attempt)()
    assert not isinstance(caught.value, SignInCancelled)
    assert "wasn't finished" in str(caught.value)


def test_pressing_cancel_on_googles_page_is_a_cancellation():
    attempt, flow, _ = start()
    outcome = run(attempt)

    status, page = get(flow.redirect_uri + "?error=access_denied&state=state-123")
    assert status == 200 and "Nothing was connected" in page

    with pytest.raises(SignInCancelled) as caught:
        outcome()
    assert "Cancel on Google's page" in str(caught.value)
    assert flow.fetched is None  # nothing to exchange


def test_another_google_error_is_reported_by_name():
    attempt, flow, _ = start()
    outcome = run(attempt)
    get(flow.redirect_uri + "?error=server_error")

    with pytest.raises(SignInError) as caught:
        outcome()
    assert not isinstance(caught.value, SignInCancelled)
    assert "server_error" in str(caught.value)


def test_leaving_the_calendar_box_unticked_is_explained():
    attempt, flow, _ = start(FakeFlow(granted=["openid"]))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")

    with pytest.raises(SignInError) as caught:
        outcome()
    assert str(caught.value) == MISSING_CALENDAR_PERMISSION


def test_oauthlibs_scope_warning_is_explained_the_same_way():
    attempt, flow, _ = start(FakeFlow(fail=Warning("Scope has changed from ... to ...")))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")

    with pytest.raises(SignInError) as caught:
        outcome()
    assert str(caught.value) == MISSING_CALENDAR_PERMISSION


def test_a_rejected_code_mentions_the_test_user_list():
    attempt, flow, _ = start(FakeFlow(fail=InvalidGrantError("Bad Request")))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")

    with pytest.raises(SignInError) as caught:
        outcome()
    assert "Test users" in str(caught.value)


def test_access_denied_from_the_token_exchange_is_a_sign_in_error():
    attempt, flow, _ = start(FakeFlow(fail=AccessDeniedError()))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")
    with pytest.raises(SignInError):
        outcome()


def test_losing_the_connection_mid_exchange_blames_the_internet():
    attempt, flow, _ = start(FakeFlow(fail=ConnectionError("no route")))
    outcome = run(attempt)
    get(flow.redirect_uri + "?code=abc")

    with pytest.raises(SignInError) as caught:
        outcome()
    assert "internet" in str(caught.value)


def test_a_browser_that_will_not_open_is_noticed_and_can_be_retried():
    calls = []

    def opener(url):
        calls.append(url)
        return len(calls) > 1  # fails the first time

    attempt = BrowserSignIn(FakeFlow(), open_browser=opener)
    attempt.start()
    try:
        assert attempt.browser_opened is False
        assert attempt.open_in_browser() is True
        assert calls == [attempt.auth_url, attempt.auth_url]
    finally:
        attempt.cancel()
        with pytest.raises(SignInCancelled):
            attempt.wait()


def test_an_opener_that_raises_does_not_break_the_sign_in():
    def opener(url):
        raise RuntimeError("no display")

    attempt = BrowserSignIn(FakeFlow(), open_browser=opener)
    attempt.start()
    assert attempt.browser_opened is False
    attempt.cancel()
    with pytest.raises(SignInCancelled):
        attempt.wait()


# ---- the credentials file -------------------------------------------------


def test_a_desktop_client_file_is_accepted():
    assert validate_client_file(json.dumps(CLIENT_FILE).encode()) == CLIENT_FILE


def test_a_file_saved_with_a_byte_order_mark_is_accepted():
    raw = b"\xef\xbb\xbf" + json.dumps(CLIENT_FILE).encode()
    assert validate_client_file(raw) == CLIENT_FILE


@pytest.mark.parametrize(
    "content, expected",
    [
        (b"this is not json", "isn't Google's credentials file"),
        (b"\xff\xfe\x00binary", "isn't Google's credentials file"),
        (b"[1, 2]", "isn't Google's credentials file"),
        (b"{}", "isn't Google's credentials file"),
        (json.dumps({"web": CLIENT_FILE["installed"]}).encode(), "Desktop app"),
        (json.dumps({"type": "service_account", "private_key": "x"}).encode(), "service-account"),
        (json.dumps({"token": "ya29", "refresh_token": "1//x"}).encode(), "token.json"),
        (json.dumps({"installed": {"client_id": "x"}}).encode(), "incomplete"),
        (json.dumps({"installed": "nope"}).encode(), "incomplete"),
    ],
)
def test_the_wrong_file_gets_a_specific_explanation(content, expected):
    with pytest.raises(ClientFileError) as caught:
        validate_client_file(content)
    assert expected in str(caught.value)


def test_an_enormous_file_is_refused_without_parsing():
    with pytest.raises(ClientFileError) as caught:
        validate_client_file(b"{" + b" " * (70 * 1024) + b"}")
    assert "too large" in str(caught.value)


def test_build_flow_reads_a_valid_file(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(CLIENT_FILE))

    flow = build_flow(path)
    flow.redirect_uri = "http://localhost:1/"
    url, _ = flow.authorization_url(prompt="select_account consent")
    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == [CLIENT_FILE["installed"]["client_id"]]
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["prompt"] == ["select_account consent"]


def test_build_flow_explains_a_missing_or_wrong_file(tmp_path):
    with pytest.raises(SignInError) as caught:
        build_flow(tmp_path / "credentials.json")
    assert "doesn't have your Google credentials file" in str(caught.value)

    (tmp_path / "credentials.json").write_text(json.dumps({"web": {}}))
    with pytest.raises(SignInError) as caught:
        build_flow(tmp_path / "credentials.json")
    assert "Desktop app" in str(caught.value)


def test_starting_a_sign_in_never_looks_up_its_own_hostname(monkeypatch):
    def too_slow(*args, **kwargs):
        raise AssertionError("the redirect listener asked DNS for its own name")

    monkeypatch.setattr(socket, "getfqdn", too_slow)
    monkeypatch.setattr(socket, "gethostbyaddr", too_slow)

    attempt, flow, _ = start()
    outcome = run(attempt)
    assert get(flow.redirect_uri + "?code=abc")[0] == 200
    assert outcome() is flow.credentials
