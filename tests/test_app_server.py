"""The local web server: what it serves, and above all what it refuses.

Every test talks to a real server on a real localhost port, because the
checks under test (Host, Origin, the token) only exist at that level.
"""

from __future__ import annotations

import http.client
import io
import json
import threading
import time

import pytest
from PIL import Image

from syllabus_cal.app.capture import ScreenCapture
from syllabus_cal.app.engine import DemoEngine
from syllabus_cal.app.server import TOKEN_HEADER, AppServer
from syllabus_cal.app.service import MAX_UPLOAD_BYTES, Service
from syllabus_cal.app.settings import Settings

GOOD_KEY = "sk-ant-api03-" + "A1b2C3d4" * 10 + "-hAAA"
CLIENT_FILE = json.dumps(
    {
        "installed": {
            "client_id": "1234-abc.apps.googleusercontent.com",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "not-a-real-secret",
        }
    }
).encode()
VIDEO = bytes(range(256)) * 4  # 1024 distinguishable bytes


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


class Client:
    """A tiny HTTP client that lets a test set (or omit) any header."""

    def __init__(self, server: AppServer):
        self.server = server
        self.port = server.port

    def request(
        self,
        method: str,
        path: str,
        body: bytes | dict | None = None,
        *,
        token: str | None = "default",
        host: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
        send = {"Host": host or f"127.0.0.1:{self.port}"}
        if token == "default":
            token = self.server.token
        if token is not None:
            send[TOKEN_HEADER] = token
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            send["Content-Type"] = "application/json"
        send.update(headers or {})
        connection.request(method, path, body=body, headers=send)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: bytes | dict | None = None, **kwargs):
        return self.request("POST", path, body if body is not None else {}, **kwargs)

    def json(self, method: str, path: str, body=None, **kwargs):
        response, data = self.request(method, path, body, **kwargs)
        return response.status, json.loads(data)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


@pytest.fixture
def stack(tmp_path):
    web = tmp_path / "web"
    (web / "media").mkdir(parents=True)
    (web / "index.html").write_text(
        '<!doctype html><meta name="schedru-token" content="__SCHEDRU_TOKEN__"><p>hi</p>'
    )
    (web / "app.js").write_text("console.log('app')")
    (web / "app.css").write_text("body{}")
    (web / "media" / "tutorial.mp4").write_bytes(VIDEO)
    (tmp_path / "secret.txt").write_text("outside the web folder")

    data = tmp_path / "data"
    data.mkdir()
    engine = DemoEngine(data, delay=0.01)
    # Never the real screenshot tool: on a Mac it would put a crosshair on
    # whatever screen the tests are running on.
    service = Service(
        engine,
        Settings(data, environ={}),
        workdir=tmp_path / "work",
        capture_tool=ScreenCapture(platform="test", binary=str(tmp_path / "missing")),
    )
    server = AppServer(service, web)
    server.start()
    yield Client(server), service, engine
    server.stop()
    service.close()


def make_ready(client: Client, service: Service) -> None:
    assert client.post("/api/key", {"key": GOOD_KEY})[0].status == 200
    assert client.post("/api/credentials", CLIENT_FILE)[0].status == 200
    assert client.post("/api/google/signin")[0].status == 200
    wait_until(lambda: service.status()["google"]["state"] == "connected")


# ---- the page and its files -----------------------------------------------


def test_the_page_is_served_with_the_token_filled_in(stack):
    client, _, _ = stack
    response, body = client.get("/", token=None)

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert f'content="{client.server.token}"'.encode() in body
    assert b"__SCHEDRU_TOKEN__" not in body
    assert response.getheader("Cache-Control") == "no-store"


def test_the_page_forbids_inline_script_and_framing(stack):
    client, _, _ = stack
    response, _ = client.get("/", token=None)
    policy = response.getheader("Content-Security-Policy")

    assert "script-src 'self'" in policy and "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in policy
    assert "connect-src 'self'" in policy
    assert response.getheader("X-Frame-Options") == "DENY"
    assert response.getheader("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/app.js", "text/javascript; charset=utf-8"),
        ("/app.css", "text/css; charset=utf-8"),
        ("/media/tutorial.mp4", "video/mp4"),
        ("/index.html", "text/html; charset=utf-8"),
    ],
)
def test_static_files_have_the_right_types(stack, path, content_type):
    client, _, _ = stack
    response, _ = client.get(path, token=None)
    assert response.status == 200
    assert response.getheader("Content-Type") == content_type


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/media/../../secret.txt",
        "//etc/passwd",
        "/missing.js",
        "/media",
    ],
)
def test_nothing_outside_the_web_folder_is_served(stack, path):
    client, _, _ = stack
    response, body = client.get(path, token=None)
    assert response.status == 404
    assert b"outside the web folder" not in body


def test_files_cannot_be_posted_to(stack):
    client, _, _ = stack
    assert client.post("/app.js", b"x")[0].status == 405


def test_head_sends_headers_without_a_body(stack):
    client, _, _ = stack
    response, body = client.request("HEAD", "/media/tutorial.mp4", token=None)
    assert response.status == 200
    assert response.getheader("Content-Length") == str(len(VIDEO))
    assert body == b""


# ---- byte ranges, which video playback depends on -------------------------


def test_a_whole_file_advertises_range_support(stack):
    client, _, _ = stack
    response, body = client.get("/media/tutorial.mp4", token=None)
    assert response.status == 200 and body == VIDEO
    assert response.getheader("Accept-Ranges") == "bytes"


@pytest.mark.parametrize(
    "header, start, end",
    [
        ("bytes=0-1", 0, 1),
        ("bytes=0-99", 0, 99),
        ("bytes=100-", 100, 1023),
        ("bytes=1000-5000", 1000, 1023),  # past the end: clamp
        ("bytes=-24", 1000, 1023),  # the last 24 bytes
        ("bytes=1023-1023", 1023, 1023),
    ],
)
def test_ranges_return_exactly_the_bytes_asked_for(stack, header, start, end):
    client, _, _ = stack
    response, body = client.get("/media/tutorial.mp4", token=None, headers={"Range": header})

    assert response.status == 206
    assert body == VIDEO[start : end + 1]
    assert response.getheader("Content-Range") == f"bytes {start}-{end}/1024"
    assert response.getheader("Content-Length") == str(end - start + 1)


@pytest.mark.parametrize("header", ["bytes=2000-", "bytes=50-10"])
def test_impossible_ranges_are_refused(stack, header):
    client, _, _ = stack
    response, _ = client.get("/media/tutorial.mp4", token=None, headers={"Range": header})
    assert response.status == 416
    assert response.getheader("Content-Range") == "bytes */1024"


@pytest.mark.parametrize("header", ["bytes=-", "items=0-5", "bytes=a-b", "bytes=0-1,5-9"])
def test_ranges_it_does_not_understand_get_the_whole_file(stack, header):
    client, _, _ = stack
    response, body = client.get("/media/tutorial.mp4", token=None, headers={"Range": header})
    assert response.status == 200 and body == VIDEO


# ---- who may call the API -------------------------------------------------


def test_the_api_answers_the_apps_own_page(stack):
    client, _, _ = stack
    status, body = client.json("GET", "/api/status")
    assert status == 200
    assert body["app"]["name"] == "Schedru"


@pytest.mark.parametrize("token", [None, "", "wrong", "x" * 43])
def test_the_api_refuses_callers_without_the_token(stack, token):
    client, _, _ = stack
    status, body = client.json("GET", "/api/status", token=token)
    assert status == 403 and body["error"]["code"] == "bad_token"


def test_a_token_in_the_address_is_not_accepted_for_api_calls(stack):
    client, _, _ = stack
    status, _ = client.json("GET", f"/api/status?t={client.server.token}", token=None)
    assert status == 403
    status, _ = client.json(
        "POST", f"/api/tutorial/seen?t={client.server.token}", {}, token=None
    )
    assert status == 403


@pytest.mark.parametrize(
    "host",
    ["evil.example", "evil.example:80", "127.0.0.1", "127.0.0.1:1", "attacker.localhost:PORT"],
)
def test_requests_for_another_host_are_refused_even_with_the_token(stack, host):
    """DNS rebinding: a hostile site points its own name at 127.0.0.1."""
    client, _, _ = stack
    host = host.replace("PORT", str(client.port))
    status, body = client.json("GET", "/api/status", host=host)
    assert status == 403 and body["error"]["code"] == "bad_host"
    # ...and it can't fetch the page (which holds the token) either
    response, page = client.get("/", token=None, host=host)
    assert response.status == 403 and client.server.token.encode() not in page


def test_localhost_is_accepted_as_a_name_for_this_server(stack):
    client, _, _ = stack
    status, _ = client.json("GET", "/api/status", host=f"localhost:{client.port}")
    assert status == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:1", "null"])
def test_writes_from_another_site_are_refused_even_with_the_token(stack, origin):
    client, service, _ = stack
    status, body = client.json("POST", "/api/tutorial/seen", {}, headers={"Origin": origin})
    assert status == 403 and body["error"]["code"] == "bad_origin"
    assert service.status()["tutorial_seen"] is False


def test_writes_from_the_apps_own_origin_are_accepted(stack):
    client, service, _ = stack
    origin = f"http://127.0.0.1:{client.port}"
    status, _ = client.json("POST", "/api/tutorial/seen", {}, headers={"Origin": origin})
    assert status == 200
    assert service.status()["tutorial_seen"] is True


def test_cross_site_reads_are_never_invited(stack):
    client, _, _ = stack
    response, _ = client.get("/api/status", headers={"Origin": "https://evil.example"})
    assert response.getheader("Access-Control-Allow-Origin") is None


def test_each_launch_gets_its_own_token(stack, tmp_path):
    client, service, _ = stack
    other = AppServer(service, tmp_path / "web")
    try:
        assert other.token != client.server.token
        assert len(other.token) >= 32
    finally:
        other._httpd.server_close()


def test_it_listens_on_the_loopback_address_only(stack):
    client, _, _ = stack
    assert client.server._httpd.server_address[0] == "127.0.0.1"


# ---- the calls themselves -------------------------------------------------


def test_unknown_api_paths_are_not_found(stack):
    client, _, _ = stack
    assert client.json("GET", "/api/nope")[0] == 404
    assert client.json("POST", "/api/nope", {})[0] == 404
    assert client.json("GET", "/api/job/j99")[0] == 404


def test_saving_a_key_reports_problems_as_json(stack):
    client, service, _ = stack
    status, body = client.json("POST", "/api/key", {"key": "sk-ant-api03-Ggj...hAAA"})
    assert status == 400
    assert body["error"]["code"] == "bad_key" and "shortened preview" in body["error"]["message"]

    status, body = client.json("POST", "/api/key", {"key": GOOD_KEY})
    assert status == 200 and body["hint"] == "sk-ant-…hAAA"
    assert service.settings.api_key() == GOOD_KEY


def test_the_key_is_never_sent_back(stack):
    client, service, _ = stack
    _, body = client.post("/api/key", {"key": GOOD_KEY})
    assert GOOD_KEY.encode() not in body
    _, body = client.get("/api/status")
    assert GOOD_KEY.encode() not in body


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_malformed_json_is_a_400(stack, body):
    client, _, _ = stack
    status, payload = client.json("POST", "/api/key", body)
    assert status == 400 and payload["error"]["code"] == "bad_json"


def test_a_wrong_credentials_file_is_explained(stack):
    client, service, _ = stack
    status, body = client.json("POST", "/api/credentials", json.dumps({"web": {}}).encode())
    assert status == 400 and "Desktop app" in body["error"]["message"]
    assert service.status()["google"]["has_credentials"] is False


def test_the_whole_flow_over_http(stack):
    """Key, credentials, sign in, upload, review, add: the app's main path."""
    client, service, engine = stack
    make_ready(client, service)

    status, job = client.json("POST", "/api/read", png())
    assert status == 200 and job["state"] == "reading"
    wait_until(lambda: service.status()["job"]["state"] == "review")

    status, job = client.json("GET", f"/api/job/{job['id']}")
    assert status == 200 and len(job["events"]) == 2
    assert engine.added == []  # reviewed, not added

    status, done = client.json(
        "POST", f"/api/job/{job['id']}/add", {"events": [{"id": "e1", "title": "Renamed"}]}
    )
    assert status == 200 and done["state"] == "done"
    assert done["results"][0]["ok"] is True
    assert [event.title for event in engine.added] == ["Renamed"]

    status, _ = client.json("POST", f"/api/job/{job['id']}/clear", {})
    assert status == 200 and service.status()["job"] is None


def test_an_invalid_edit_names_the_event_and_field(stack):
    client, service, engine = stack
    make_ready(client, service)
    _, job = client.json("POST", "/api/read", png())
    wait_until(lambda: service.status()["job"]["state"] == "review")

    status, body = client.json(
        "POST", f"/api/job/{job['id']}/add", {"events": [{"id": "e2", "start_date": "soon"}]}
    )
    assert status == 400
    assert body["error"] == {
        "code": "invalid_event",
        "message": "The date isn't valid.",
        "event_id": "e2",
        "field": "start_date",
    }
    assert engine.added == []


def test_uploads_that_are_not_images_are_refused(stack):
    client, service, _ = stack
    make_ready(client, service)
    status, body = client.json("POST", "/api/read", b"%PDF-1.7 ...")
    assert status == 415 and body["error"]["code"] == "pdf"
    status, body = client.json("POST", "/api/read", b"hello")
    assert status == 415 and body["error"]["code"] == "not_an_image"


def test_an_oversized_upload_is_refused_without_being_read(stack):
    client, service, _ = stack
    make_ready(client, service)
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    connection.putrequest("POST", "/api/read")
    connection.putheader("Host", f"127.0.0.1:{client.port}")
    connection.putheader(TOKEN_HEADER, client.server.token)
    connection.putheader("Content-Length", str(MAX_UPLOAD_BYTES + 1))
    connection.endheaders()  # ...and never send the body
    response = connection.getresponse()
    assert response.status == 413
    assert json.loads(response.read())["error"]["code"] == "too_large"
    connection.close()


def test_a_post_without_a_length_is_refused(stack):
    client, _, _ = stack
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=10)
    connection.putrequest("POST", "/api/key")
    connection.putheader("Host", f"127.0.0.1:{client.port}")
    connection.putheader(TOKEN_HEADER, client.server.token)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 411
    response.read()
    connection.close()


def test_the_screenshot_can_be_shown_in_an_img_tag(stack):
    """<img> can't send headers, so this one route takes the token in the URL."""
    client, service, _ = stack
    make_ready(client, service)
    image = png()
    _, job = client.json("POST", "/api/read", image)

    response, body = client.get(f"{job['image']}?t={client.server.token}", token=None)
    assert response.status == 200 and body == image
    assert response.getheader("Content-Type") == "image/png"
    assert response.getheader("Cache-Control") == "no-store"

    assert client.get(job["image"], token=None)[0].status == 403
    assert client.get(f"{job['image']}?t=wrong", token=None)[0].status == 403


def test_capture_reports_when_it_is_unavailable(stack):
    client, service, _ = stack
    make_ready(client, service)
    status, body = client.json("POST", "/api/capture", {})
    assert status == 400 and body["error"]["code"] == "capture_unavailable"


def test_only_known_links_are_opened(stack, monkeypatch):
    client, service, _ = stack
    opened = []
    monkeypatch.setattr(service, "_open_url", opened.append)

    assert client.json("POST", "/api/open", {"url": "https://evil.example/"})[0] == 400
    assert client.json("POST", "/api/open", {"url": "https://calendar.google.com/"})[0] == 200
    assert opened == ["https://calendar.google.com/"]


def test_sign_out_and_retry(stack):
    client, service, _ = stack
    make_ready(client, service)
    assert client.json("POST", "/api/google/signout", {})[0] == 200
    assert service.status()["google"]["state"] == "signed_out"

    assert client.json("POST", "/api/google/retry", {})[0] == 200
    wait_until(lambda: service.status()["google"]["state"] == "signed_out")


def test_cancelling_a_sign_in_over_http(stack):
    client, service, engine = stack
    engine.delay = 5
    client.post("/api/credentials", CLIENT_FILE)
    assert client.json("POST", "/api/google/signin", {})[1] == {"state": "signing_in"}
    assert client.json("POST", "/api/google/cancel", {})[0] == 200
    assert service.status()["google"]["state"] == "signed_out"


# ---- the long poll --------------------------------------------------------


def test_events_returns_at_once_when_there_is_news(stack):
    client, service, _ = stack
    service.hub.publish("job", id="j1")
    status, body = client.json("GET", "/api/events?since=0")
    assert status == 200
    assert [note["kind"] for note in body["events"]] == ["job"]
    assert body["seq"] == service.hub.seq


def test_events_waits_for_the_next_change(stack):
    client, service, _ = stack
    since = service.hub.seq
    threading.Timer(0.15, service.hub.publish, args=("google",)).start()

    began = time.monotonic()
    status, body = client.json("GET", f"/api/events?since={since}")
    elapsed = time.monotonic() - began

    assert status == 200 and [note["kind"] for note in body["events"]] == ["google"]
    assert 0.1 < elapsed < 5


def test_a_page_ahead_of_the_server_is_told_to_start_over(stack):
    client, service, _ = stack
    status, body = client.json("GET", f"/api/events?since={service.hub.seq + 50}")
    assert status == 200 and body["reset"] is True and body["events"] == []


def test_a_garbled_since_is_treated_as_zero(stack):
    client, service, _ = stack
    service.hub.publish("job")
    status, body = client.json("GET", "/api/events?since=abc")
    assert status == 200 and len(body["events"]) == 1


def test_other_requests_are_served_while_one_is_waiting(stack):
    client, service, _ = stack
    waiter = threading.Thread(
        target=lambda: client.get(f"/api/events?since={service.hub.seq}"), daemon=True
    )
    waiter.start()
    time.sleep(0.1)

    began = time.monotonic()
    assert client.json("GET", "/api/status")[0] == 200
    assert time.monotonic() - began < 2
    service.hub.publish("wake")
    waiter.join(5)


# ---- test hooks -----------------------------------------------------------


def test_debug_hooks_do_not_exist_unless_switched_on(stack):
    client, _, _ = stack
    status, _ = client.json("POST", "/api/debug/eval", {"js": "1+1"})
    assert status == 404


def test_debug_hooks_work_when_switched_on_and_still_need_the_token(stack):
    client, _, _ = stack
    client.server.debug_hooks["echo"] = lambda body: body.get("say")

    status, body = client.json("POST", "/api/debug/echo", {"say": "hi"})
    assert (status, body) == (200, {"result": "hi"})
    assert client.json("POST", "/api/debug/echo", {"say": "hi"}, token=None)[0] == 403
    assert client.json("POST", "/api/debug/other", {})[0] == 404


def test_a_crash_in_a_handler_is_a_500_not_a_hung_request(stack):
    client, service, _ = stack

    def boom(body):
        raise RuntimeError("surprise")

    client.server.debug_hooks["boom"] = boom
    status, body = client.json("POST", "/api/debug/boom", {})
    assert status == 500 and body["error"]["code"] == "internal"
    assert client.json("GET", "/api/status")[0] == 200  # still serving
