"""The app's behaviour, driven through Service with the demo engine.

No network, no window, no real screen: the engine is DemoEngine with its
pauses shortened, and the window and screenshot tool are recording fakes.
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from syllabus_cal.app.engine import DemoEngine
from syllabus_cal.app.service import (
    MAX_UPLOAD_BYTES,
    NOT_AN_IMAGE,
    PDF_HINT,
    ApiError,
    Hub,
    Service,
)
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


def image_bytes(fmt: str = "PNG", size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeWindow:
    def __init__(self, frontmost: bool = False):
        self.frontmost = frontmost
        self.log: list[str] = []

    def is_frontmost(self) -> bool:
        return self.frontmost

    def hide(self) -> None:
        self.log.append("hide")

    def show(self) -> None:
        self.log.append("show")


class FakeCapture:
    def __init__(self, available=True, permission="granted", result: bytes | None = None):
        self.available = available
        self._permission = permission
        self.result = image_bytes() if result is None else result
        self.cancel = False
        self.requested = 0
        self.settings_opened = 0
        self.captures = 0
        self.gate: threading.Event | None = None
        self.raises: Exception | None = None

    def permission(self) -> str:
        return self._permission

    def request_permission(self) -> None:
        self.requested += 1

    def open_settings(self) -> bool:
        self.settings_opened += 1
        return True

    def capture(self, destination: Path):
        self.captures += 1
        if self.gate is not None:
            assert self.gate.wait(5), "test never released the capture"
        if self.raises is not None:
            raise self.raises
        if self.cancel:
            return None
        Path(destination).write_bytes(self.result)
        return Path(destination)


def wait_until(predicate, timeout=5.0, message="condition never became true"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class App:
    """A Service wired to fakes, plus shortcuts the tests share."""

    def __init__(self, tmp_path, scenario="default", frontmost=False, **capture):
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.engine = DemoEngine(self.data, delay=0.01, scenario=scenario)
        self.settings = Settings(self.data, environ={})
        self.window = FakeWindow(frontmost)
        self.capture = FakeCapture(**capture)
        self.opened: list[str] = []
        self.service = Service(
            self.engine,
            self.settings,
            capture_tool=self.capture,
            window=self.window,
            workdir=tmp_path / "work",
            open_url=self.opened.append,
            platform="darwin",
        )

    def state(self) -> str:
        return self.service.status()["google"]["state"]

    def job_state(self):
        job = self.service.status()["job"]
        return job["state"] if job else None

    def ready(self) -> "App":
        """Past setup: key saved and signed in."""
        self.settings.save_api_key(GOOD_KEY)
        self.service.save_credentials(CLIENT_FILE)
        self.service.start_sign_in()
        wait_until(lambda: self.state() == "connected", message="demo sign-in never finished")
        self.window.log.clear()
        return self

    def read(self, data: bytes | None = None) -> dict:
        self.service.accept_upload(image_bytes() if data is None else data)
        wait_until(lambda: self.job_state() != "reading", message="read never finished")
        return self.service.status()["job"]

    def kinds(self) -> list[str]:
        return [note["kind"] for note in self.service.hub.wait(0, 0)]


@pytest.fixture
def app(tmp_path):
    made = App(tmp_path)
    yield made
    made.service.close()


def make(tmp_path, **kwargs) -> App:
    return App(tmp_path, **kwargs)


# ---- status and first launch ----------------------------------------------


def test_a_fresh_install_reports_nothing_set_up(app):
    status = app.service.status()
    assert status["app"] == {
        "name": "Schedru",
        "version": status["app"]["version"],
        "engine": "demo",
        "platform": "darwin",
        "shell": "browser",
    }
    assert status["key"] == {"present": False, "hint": None}
    assert status["google"]["state"] == "checking"
    assert status["google"]["has_credentials"] is False
    assert status["tutorial_seen"] is False
    assert status["job"] is None
    assert app.service.ready() is False


def test_first_launch_ends_signed_out_with_nothing_to_explain(app):
    app.service.refresh_google()
    google = app.service.status()["google"]
    assert google["state"] == "signed_out"
    assert google["message"] is None  # "you're not signed in" isn't news on day one


def test_a_sign_in_that_stopped_working_is_explained(app, monkeypatch):
    from syllabus_cal.calendar_client import AuthError

    (app.data / "token.json").write_text("{}")

    def expired():
        raise AuthError("Your Google sign-in has expired.")

    monkeypatch.setattr(app.engine, "connect", expired)
    app.service.refresh_google()

    google = app.service.status()["google"]
    assert google["state"] == "signed_out"
    assert google["message"] == "Your Google sign-in has expired."


def test_being_offline_is_an_error_not_a_sign_out(app, monkeypatch):
    from syllabus_cal.calendar_client import GoogleConnectionError

    def offline():
        raise GoogleConnectionError("Couldn't reach Google Calendar.")

    monkeypatch.setattr(app.engine, "connect", offline)
    app.service.refresh_google()

    google = app.service.status()["google"]
    assert google["state"] == "error"
    assert "Couldn't reach Google" in google["message"]


def test_an_unexpected_failure_never_leaves_the_window_checking(app, monkeypatch):
    def boom():
        raise RuntimeError("surprise")

    monkeypatch.setattr(app.engine, "connect", boom)
    app.service.refresh_google()
    assert app.state() == "error"


def test_start_checks_the_saved_sign_in_in_the_background(app):
    app.service.start()
    wait_until(lambda: app.state() == "signed_out")


def test_the_tutorial_is_remembered(app):
    app.service.mark_tutorial_seen()
    assert app.service.status()["tutorial_seen"] is True
    assert Settings(app.data, environ={}).get("tutorial_seen") is True


# ---- the API key ----------------------------------------------------------


def test_a_good_key_is_checked_then_saved(app):
    result = app.service.save_key(f"  {GOOD_KEY}  ")
    assert result == {"hint": "sk-ant-…hAAA", "note": None}
    assert app.settings.api_key() == GOOD_KEY
    assert app.service.status()["key"] == {"present": True, "hint": "sk-ant-…hAAA"}


def test_a_malformed_key_is_refused_before_asking_anthropic(app, monkeypatch):
    def unexpected(key):
        raise AssertionError("should not have been checked online")

    monkeypatch.setattr(app.engine, "check_key", unexpected)
    with pytest.raises(ApiError) as caught:
        app.service.save_key("sk-ant-api03-Ggj...hAAA")
    assert caught.value.status == 400 and caught.value.code == "bad_key"
    assert "shortened preview" in caught.value.message
    assert app.settings.api_key() is None


def test_a_key_anthropic_rejects_is_not_saved(app):
    with pytest.raises(ApiError) as caught:
        app.service.save_key("sk-ant-api03-bad-" + "x" * 60)
    assert caught.value.code == "bad_key"
    assert app.settings.api_key() is None


def test_the_key_never_appears_in_status(app):
    app.service.save_key(GOOD_KEY)
    assert GOOD_KEY not in json.dumps(app.service.status())


# ---- Google ---------------------------------------------------------------


def test_sign_in_needs_the_credentials_file_first(app):
    with pytest.raises(ApiError) as caught:
        app.service.start_sign_in()
    assert caught.value.status == 409 and caught.value.code == "no_credentials"


def test_a_wrong_credentials_file_is_explained_and_not_kept(app):
    with pytest.raises(ApiError) as caught:
        app.service.save_credentials(json.dumps({"web": {}}).encode())
    assert caught.value.code == "bad_credentials" and "Desktop app" in caught.value.message
    assert app.service.status()["google"]["has_credentials"] is False


def test_signing_in_connects_and_brings_the_window_back(app):
    app.service.save_credentials(CLIENT_FILE)
    assert app.service.start_sign_in() == {"state": "signing_in"}
    assert app.state() == "signing_in"

    wait_until(lambda: app.state() == "connected")
    google = app.service.status()["google"]
    assert google["calendar"] == DemoEngine.CALENDAR_NAME
    assert google["message"] is None
    assert app.window.log == ["show"]  # they were off in the browser


def test_pressing_the_button_again_does_not_start_a_second_sign_in(app, monkeypatch):
    app.engine.delay = 5
    app.service.save_credentials(CLIENT_FILE)
    app.service.start_sign_in()

    def unexpected(*args, **kwargs):
        raise AssertionError("a second attempt was started")

    monkeypatch.setattr(app.engine, "begin_sign_in", unexpected)
    assert app.service.start_sign_in() == {"state": "signing_in"}
    app.service.cancel_sign_in()


def test_cancelling_in_the_app_is_quiet(app):
    app.engine.delay = 5
    app.service.save_credentials(CLIENT_FILE)
    app.service.start_sign_in()
    app.service.cancel_sign_in()

    google = app.service.status()["google"]
    assert google["state"] == "signed_out" and google["message"] is None
    time.sleep(0.1)  # the waiting thread must not overwrite that
    assert app.state() == "signed_out"
    assert app.window.log == []


def test_a_failed_sign_in_says_why(tmp_path):
    app = make(tmp_path, scenario="signin_fail")
    app.service.save_credentials(CLIENT_FILE)
    app.service.start_sign_in()

    wait_until(lambda: app.state() == "signed_out")
    assert "Google stopped the sign-in" in app.service.status()["google"]["message"]
    assert app.window.log == ["show"]
    app.service.close()


def test_cancelling_on_googles_page_is_explained(app, monkeypatch):
    from syllabus_cal.app.google_signin import SignInCancelled

    def cancelled_on_page(attempt):
        raise SignInCancelled("You pressed Cancel on Google's page, so nothing was connected.")

    app.service.save_credentials(CLIENT_FILE)
    monkeypatch.setattr(app.engine, "complete_sign_in", cancelled_on_page)
    app.service.start_sign_in()

    wait_until(lambda: app.state() == "signed_out")
    assert "Cancel on Google's page" in app.service.status()["google"]["message"]


def test_signing_out_forgets_the_sign_in_and_the_open_screenshot(app):
    app.ready()
    app.read()
    image = Path(app.service._job.image)
    assert image.exists()

    app.service.sign_out()
    assert app.state() == "signed_out"
    assert app.service.status()["job"] is None
    assert not image.exists()
    assert not (app.data / "token.json").exists()


# ---- reading --------------------------------------------------------------


def test_reading_needs_a_key(app):
    with pytest.raises(ApiError) as caught:
        app.service.accept_upload(image_bytes())
    assert caught.value.status == 409 and caught.value.code == "no_key"


def test_reading_needs_a_sign_in(app):
    app.settings.save_api_key(GOOD_KEY)
    app.service.refresh_google()
    with pytest.raises(ApiError) as caught:
        app.service.accept_upload(image_bytes())
    assert caught.value.code == "not_signed_in"


def test_an_image_is_read_into_events_awaiting_review(app):
    app.ready()
    job = app.service.accept_upload(image_bytes())
    assert job.state == "reading" and job.source == "upload"

    wait_until(lambda: app.job_state() == "review")
    result = app.service.status()["job"]

    assert [event["id"] for event in result["events"]] == ["e1", "e2"]
    first, second = result["events"]
    assert first["title"] == "Scarlet Robotics Club: Kickoff Meeting"
    assert first["needs_review"] is False and first["added"] is False
    assert second["needs_review"] is True
    assert second["repeats"] is True
    assert result["image"] == f"/api/job/{result['id']}/image"


def test_reading_alone_puts_nothing_on_the_calendar(app):
    """The whole point: finding events is not adding them."""
    app.ready()
    app.read()
    assert app.engine.added == []


def test_the_engine_is_handed_the_saved_image(app):
    app.ready()
    data = image_bytes("JPEG")
    app.read(data)
    assert app.engine.reads[-1].suffix == ".jpg"
    assert app.engine.reads[-1].read_bytes() == data  # untouched


def test_unusual_image_formats_are_converted(app):
    app.ready()
    app.read(image_bytes("TIFF"))  # what copying an image on a Mac can produce
    saved = app.engine.reads[-1]
    assert saved.suffix == ".png"
    with Image.open(saved) as image:
        assert image.format == "PNG" and image.size == (40, 30)


@pytest.mark.parametrize(
    "data, status, code, message",
    [
        (b"", 400, "empty", None),
        (b"definitely not an image", 415, "not_an_image", NOT_AN_IMAGE),
        (b"%PDF-1.7\n...", 415, "pdf", PDF_HINT),
        (b"\x89PNG\r\n\x1a\n truncated", 415, "not_an_image", NOT_AN_IMAGE),
    ],
)
def test_things_that_are_not_images_are_turned_away(app, data, status, code, message):
    app.ready()
    with pytest.raises(ApiError) as caught:
        app.service.accept_upload(data)
    assert (caught.value.status, caught.value.code) == (status, code)
    if message:
        assert caught.value.message == message
    assert app.service.status()["job"] is None
    assert app.engine.reads == []


def test_an_oversized_upload_is_refused(app):
    app.ready()
    with pytest.raises(ApiError) as caught:
        app.service.accept_upload(b"\x00" * (MAX_UPLOAD_BYTES + 1))
    assert caught.value.status == 413


def test_an_image_with_no_events_reviews_as_empty(tmp_path):
    app = make(tmp_path, scenario="empty").ready()
    result = app.read()
    assert result["state"] == "review" and result["events"] == []
    app.service.close()


def test_a_failed_read_says_what_happened(tmp_path):
    app = make(tmp_path, scenario="read_fail").ready()
    result = app.read()
    assert result["state"] == "failed"
    assert result["error"]["kind"] == "connection"
    assert "internet connection" in result["error"]["message"]
    assert " -- " not in result["error"]["message"]
    app.service.close()


def test_a_rejected_key_is_described_in_the_apps_terms(tmp_path):
    app = make(tmp_path, scenario="read_bad_key").ready()
    result = app.read()
    assert result["error"]["kind"] == "auth"
    assert ".env" not in result["error"]["message"]  # that's the terminal's advice
    app.service.close()


def test_an_unexpected_read_failure_does_not_strand_the_job(app, monkeypatch):
    def boom(image, key):
        raise RuntimeError("surprise")

    app.ready()
    monkeypatch.setattr(app.engine, "read", boom)
    result = app.read()
    assert result["state"] == "failed" and result["error"]["kind"] == "other"


def test_a_new_screenshot_replaces_the_old_one(app):
    app.ready()
    first = app.read()
    first_image = Path(app.service._job.image)

    second = app.read()
    assert second["id"] != first["id"]
    assert not first_image.exists()
    with pytest.raises(ApiError) as caught:
        app.service.job(first["id"])
    assert caught.value.status == 404


def test_a_slow_read_that_was_replaced_is_ignored(app, monkeypatch):
    app.ready()
    release = threading.Event()
    original = app.engine.read
    calls = []

    def slow_first(image, key):
        calls.append(image)
        if len(calls) == 1:
            release.wait(5)
        return original(image, key)

    monkeypatch.setattr(app.engine, "read", slow_first)
    stale = app.service.accept_upload(image_bytes())
    fresh = app.service.accept_upload(image_bytes())
    wait_until(lambda: app.job_state() == "review")
    release.set()
    time.sleep(0.1)

    assert app.service.status()["job"]["id"] == fresh.id
    assert stale.state == "reading"  # its result was never applied


def test_clearing_forgets_the_screenshot(app):
    app.ready()
    job = app.read()
    image = Path(app.service._job.image)

    app.service.clear_job("some-other-id")  # not the open one: ignored
    assert app.service.status()["job"] is not None

    app.service.clear_job(job["id"])
    assert app.service.status()["job"] is None
    assert not image.exists()


# ---- adding ---------------------------------------------------------------


def test_adding_writes_exactly_the_ticked_events(app):
    app.ready()
    job = app.read()

    result = app.service.add_events(job["id"], [{"id": "e1"}])

    assert [event.title for event in app.engine.added] == ["Scarlet Robotics Club: Kickoff Meeting"]
    assert result["state"] == "done"
    assert result["results"] == [
        {
            "id": "e1",
            "title": "Scarlet Robotics Club: Kickoff Meeting",
            "when_text": result["results"][0]["when_text"],
            "ok": True,
            "link": "https://www.google.com/calendar/event?eid=demo-1",
            "error": None,
        }
    ]
    assert [event["added"] for event in result["events"]] == [True, False]


def test_edits_made_in_the_window_are_what_gets_written(app):
    app.ready()
    job = app.read()

    app.service.add_events(
        job["id"],
        [{"id": "e1", "title": "Robotics kickoff", "start_time": "18:00", "end_time": "19:00"}],
    )
    written = app.engine.added[0]
    assert written.title == "Robotics kickoff"
    assert written.start_time.strftime("%H:%M") == "18:00"
    # ...and what Claude said about it is still attached
    assert written.assumptions


@pytest.mark.parametrize("chosen", [[], None, "e1", {}])
def test_adding_nothing_is_refused(app, chosen):
    app.ready()
    job = app.read()
    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], chosen)
    assert caught.value.code == "nothing_chosen"
    assert app.engine.added == []


def test_an_event_from_nowhere_is_refused(app):
    app.ready()
    job = app.read()
    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}, {"id": "e99"}])
    assert caught.value.code == "unknown_event"
    assert app.engine.added == []


def test_one_invalid_edit_stops_the_whole_batch(app):
    app.ready()
    job = app.read()
    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}, {"id": "e2", "title": "  "}])

    assert caught.value.status == 400 and caught.value.code == "invalid_event"
    assert caught.value.extra == {"event_id": "e2", "field": "title"}
    assert app.engine.added == []  # e1 was fine, and still wasn't written
    assert app.job_state() == "review"


def test_the_same_event_is_never_added_twice(app):
    app.ready()
    job = app.read()
    app.service.add_events(job["id"], [{"id": "e1"}, {"id": "e1"}])
    assert len(app.engine.added) == 1

    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}])
    assert caught.value.code == "already_added"
    assert len(app.engine.added) == 1


def test_more_events_from_the_same_screenshot_can_be_added_later(app):
    app.ready()
    job = app.read()
    app.service.add_events(job["id"], [{"id": "e1"}])
    result = app.service.add_events(job["id"], [{"id": "e1"}, {"id": "e2"}])

    assert [event.title for event in app.engine.added] == [
        "Scarlet Robotics Club: Kickoff Meeting",
        "Scarlet Robotics Club: Build Night",
    ]
    assert [entry["id"] for entry in result["results"]] == ["e1", "e2"]


def test_a_partial_failure_reports_each_event_and_allows_a_retry(tmp_path):
    app = make(tmp_path, scenario="add_partial").ready()
    job = app.read()

    result = app.service.add_events(job["id"], [{"id": "e1"}, {"id": "e2", "title": "Edited"}])
    outcomes = {entry["id"]: entry for entry in result["results"]}
    assert outcomes["e1"]["ok"] is True
    assert outcomes["e2"]["ok"] is False and "403" in outcomes["e2"]["error"]
    assert [event["added"] for event in result["events"]] == [True, False]
    # the edit survives so the retry doesn't lose it
    assert result["events"][1]["title"] == "Edited"

    app.engine.scenario = "default"
    retried = app.service.add_events(job["id"], [{"id": "e2"}])
    outcomes = {entry["id"]: entry for entry in retried["results"]}
    assert outcomes["e2"]["ok"] is True and outcomes["e2"]["title"] == "Edited"
    assert len(retried["results"]) == 2  # replaced, not appended
    assert [event.title for event in app.engine.added] == [
        "Scarlet Robotics Club: Kickoff Meeting",
        "Edited",
    ]
    app.service.close()


def test_an_expired_sign_in_while_adding_keeps_the_review_for_later(tmp_path):
    app = make(tmp_path, scenario="add_auth").ready()
    job = app.read()

    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}])
    assert caught.value.status == 401 and caught.value.code == "not_signed_in"

    google = app.service.status()["google"]
    assert google["state"] == "signed_out" and "expired" in google["message"]
    assert app.job_state() == "review"  # nothing lost; sign in and press Add again
    app.service.close()


def test_google_being_unreachable_while_adding_is_recoverable(app, monkeypatch):
    def offline(events):
        raise OSError("network down")

    app.ready()
    job = app.read()
    monkeypatch.setattr(app.engine, "add", offline)

    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}])
    assert caught.value.status == 502 and "internet connection" in caught.value.message
    assert app.job_state() == "review"
    assert app.state() == "connected"


def test_adding_twice_at_once_is_refused(app, monkeypatch):
    app.ready()
    job = app.read()
    started, release = threading.Event(), threading.Event()
    original = app.engine.add

    def slow(events):
        started.set()
        release.wait(5)
        return original(events)

    monkeypatch.setattr(app.engine, "add", slow)
    worker = threading.Thread(
        target=lambda: app.service.add_events(job["id"], [{"id": "e1"}]), daemon=True
    )
    worker.start()
    assert started.wait(5)

    with pytest.raises(ApiError) as caught:
        app.service.add_events(job["id"], [{"id": "e1"}])
    assert caught.value.status == 409 and caught.value.code == "busy"
    with pytest.raises(ApiError):
        app.service.clear_job(job["id"])  # can't discard mid-write either

    release.set()
    worker.join(5)
    assert len(app.engine.added) == 1


def test_a_screenshot_still_being_read_cannot_be_added(app, monkeypatch):
    app.ready()
    release = threading.Event()
    original = app.engine.read

    def slow(image, key):
        release.wait(5)
        return original(image, key)

    monkeypatch.setattr(app.engine, "read", slow)
    job = app.service.accept_upload(image_bytes())
    with pytest.raises(ApiError) as caught:
        app.service.add_events(job.id, [{"id": "e1"}])
    assert caught.value.code == "not_ready"
    release.set()


# ---- capture --------------------------------------------------------------


def test_capture_is_refused_where_it_cannot_work(tmp_path):
    app = make(tmp_path, available=False).ready()
    with pytest.raises(ApiError) as caught:
        app.service.capture()
    assert caught.value.code == "capture_unavailable"
    assert app.service.status()["capture"]["available"] is False
    app.service.close()


def test_a_capture_becomes_a_read_and_the_window_comes_forward(app):
    app.ready()
    assert app.service.capture(origin="hotkey") == {"started": True}

    wait_until(lambda: app.job_state() == "review")
    job = app.service.status()["job"]
    assert job["source"] == "capture"
    assert app.capture.captures == 1
    assert app.window.log == ["show"]  # it was in the background: nothing to hide
    assert app.service.status()["capture"]["busy"] is False


def test_the_window_gets_out_of_the_way_when_it_is_in_front(tmp_path):
    app = make(tmp_path, frontmost=True).ready()
    app.service.capture(origin="button")
    wait_until(lambda: app.job_state() == "review")
    assert app.window.log == ["hide", "show"]
    app.service.close()


def test_cancelling_a_capture_puts_the_window_back_and_reads_nothing(tmp_path):
    app = make(tmp_path, frontmost=True).ready()
    app.capture.cancel = True
    app.service.capture(origin="button")

    wait_until(lambda: "cancelled" in [n.get("outcome") for n in app.service.hub.wait(0, 0)])
    assert app.window.log == ["hide", "show"]
    assert app.service.status()["job"] is None
    assert app.engine.reads == []
    app.service.close()


def test_cancelling_from_the_background_does_not_steal_focus(app):
    app.ready()
    app.capture.cancel = True
    app.service.capture(origin="hotkey")
    wait_until(lambda: "cancelled" in [n.get("outcome") for n in app.service.hub.wait(0, 0)])
    assert app.window.log == []


def test_a_capture_tool_that_blows_up_still_returns_the_window(tmp_path):
    app = make(tmp_path, frontmost=True).ready()
    app.capture.raises = RuntimeError("screencapture vanished")
    app.service.capture()
    wait_until(lambda: app.window.log == ["hide", "show"])
    assert app.service.status()["capture"]["busy"] is False
    app.service.close()


def test_the_shortcut_before_setup_shows_the_window_instead(app):
    app.service.refresh_google()
    assert app.service.capture(origin="hotkey") == {"started": False, "reason": "setup"}
    assert app.capture.captures == 0
    assert app.window.log == ["show"]
    assert app.service.hub.wait(0, 0)[-1]["outcome"] == "setup"


def test_missing_screen_permission_is_asked_for_not_worked_around(tmp_path):
    app = make(tmp_path, permission="denied").ready()
    assert app.service.capture() == {"started": False, "reason": "permission"}
    assert app.capture.requested == 1
    assert app.capture.captures == 0
    assert app.window.log == ["show"]
    assert app.service.hub.wait(0, 0)[-1]["outcome"] == "permission"
    app.service.close()


def test_try_anyway_skips_the_permission_check(tmp_path):
    app = make(tmp_path, permission="denied").ready()
    assert app.service.capture(force=True) == {"started": True}
    wait_until(lambda: app.job_state() == "review")
    assert app.capture.requested == 0
    app.service.close()


def test_unknown_permission_does_not_block_capturing(tmp_path):
    app = make(tmp_path, permission="unknown").ready()
    assert app.service.capture() == {"started": True}
    wait_until(lambda: app.job_state() == "review")
    app.service.close()


def test_a_second_shortcut_press_during_a_capture_is_ignored(app):
    app.ready()
    app.capture.gate = threading.Event()
    assert app.service.capture() == {"started": True}
    wait_until(lambda: app.capture.captures == 1)
    assert app.service.status()["capture"]["busy"] is True

    assert app.service.capture() == {"started": False, "reason": "busy"}
    assert app.capture.captures == 1

    app.capture.gate.set()
    wait_until(lambda: app.job_state() == "review")


def test_opening_the_permission_settings(app):
    app.service.open_capture_settings()
    assert app.capture.settings_opened == 1


# ---- links and notes ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/calendar/event?eid=abc",
        "https://calendar.google.com/calendar/u/0/r",
        "https://console.anthropic.com/settings/keys",
        "https://console.cloud.google.com/apis/credentials",
    ],
)
def test_links_the_app_itself_uses_are_opened(app, url):
    app.service.open_external(url)
    assert app.opened == [url]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.google.com/",  # not https
        "https://evil.example/",
        "https://www.google.com.evil.example/",
        "https://evil.example/?https://www.google.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "x-apple.systempreferences:com.apple.preference.security",
        "",
    ],
)
def test_other_links_are_refused(app, url):
    with pytest.raises(ApiError) as caught:
        app.service.open_external(url)
    assert caught.value.code == "bad_link"
    assert app.opened == []


def test_hub_returns_only_newer_notes_and_waits_for_the_next():
    hub = Hub()
    assert hub.wait(0, 0) == []
    first = hub.publish("a")
    second = hub.publish("b", detail=1)
    assert [note["kind"] for note in hub.wait(0, 0)] == ["a", "b"]
    assert hub.wait(first, 0) == [{"seq": second, "kind": "b", "detail": 1}]
    assert hub.wait(second, 0) == []

    threading.Timer(0.1, hub.publish, args=("c",)).start()
    began = time.monotonic()
    notes = hub.wait(second, 5)
    assert [note["kind"] for note in notes] == ["c"]
    assert time.monotonic() - began < 2


def test_state_changes_are_announced(app):
    app.ready()
    app.read()
    kinds = app.kinds()
    assert "google" in kinds and "job" in kinds
