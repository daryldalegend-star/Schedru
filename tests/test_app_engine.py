"""The real engine's wiring, with Google and Anthropic replaced by fakes.

The walk-through tests use DemoEngine, so nothing else exercises the few
lines that connect the app to the real libraries.
"""

from __future__ import annotations

import json
from datetime import date, time

import pytest

from syllabus_cal import calendar_client
from syllabus_cal.app.engine import DemoEngine, KeyCheck, RealEngine, demo_events, make_engine
from syllabus_cal.calendar_client import AuthError, WriteOutcome
from syllabus_cal.schema import ExtractedEvent

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


@pytest.fixture
def engine(tmp_path, monkeypatch):
    # RealEngine points calendar_client at its folder; put that back afterwards.
    monkeypatch.setattr(calendar_client, "CREDENTIALS_PATH", calendar_client.CREDENTIALS_PATH)
    monkeypatch.setattr(calendar_client, "TOKEN_PATH", calendar_client.TOKEN_PATH)
    return RealEngine(tmp_path)


def event(title="Kickoff") -> ExtractedEvent:
    return ExtractedEvent(
        title=title, event_type="club", start_date=date(2026, 11, 19), start_time=time(19, 30), confidence=0.9
    )


def test_the_engine_keeps_google_files_in_its_data_folder(engine, tmp_path):
    assert calendar_client.CREDENTIALS_PATH == tmp_path / "credentials.json"
    assert calendar_client.TOKEN_PATH == tmp_path / "token.json"
    assert engine.has_credentials() is False and engine.has_saved_sign_in() is False


def test_saving_credentials_validates_then_writes_privately(engine, tmp_path):
    import os
    import stat

    from syllabus_cal.app.google_signin import ClientFileError

    with pytest.raises(ClientFileError):
        engine.save_credentials(b'{"web": {}}')
    assert not (tmp_path / "credentials.json").exists()

    engine.save_credentials(CLIENT_FILE)
    assert (tmp_path / "credentials.json").read_bytes() == CLIENT_FILE
    assert engine.has_credentials() is True
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode) == 0o600


def test_connect_reports_the_calendar_name(engine, monkeypatch):
    monkeypatch.setattr(calendar_client, "connect", lambda: (object(), "me@example.com"))
    assert engine.connect() == "me@example.com"


def test_adding_checks_the_sign_in_first_every_time(engine, monkeypatch):
    """An app left open for days outlives Google's week-long sign-in. Each add
    must find that out up front, not fail event by event."""
    connects = []
    written = []

    def connect():
        connects.append(True)
        return f"service-{len(connects)}", "me@example.com"

    def write_events(service, events):
        written.append((service, [e.title for e in events]))
        return [WriteOutcome(event=e, link="https://www.google.com/calendar/event?eid=1") for e in events]

    monkeypatch.setattr(calendar_client, "connect", connect)
    monkeypatch.setattr(calendar_client, "write_events", write_events)

    assert [o.ok for o in engine.add([event("A")])] == [True]
    assert [o.ok for o in engine.add([event("B"), event("C")])] == [True, True]
    assert written == [("service-1", ["A"]), ("service-2", ["B", "C"])]


def test_an_expired_sign_in_stops_an_add_before_anything_is_written(engine, monkeypatch):
    def expired():
        raise AuthError("Your Google sign-in has expired.")

    def write_events(service, events):
        raise AssertionError("nothing should be written without a working sign-in")

    monkeypatch.setattr(calendar_client, "connect", expired)
    monkeypatch.setattr(calendar_client, "write_events", write_events)
    with pytest.raises(AuthError):
        engine.add([event()])


def test_completing_a_sign_in_saves_it_then_proves_it_works(engine, tmp_path, monkeypatch):
    order = []

    class Attempt:
        def wait(self):
            order.append("wait")
            return "the-credentials"

    monkeypatch.setattr(calendar_client, "save_token", lambda creds: order.append(("save", creds)))
    monkeypatch.setattr(calendar_client, "connect", lambda: order.append("connect") or (object(), "me@example.com"))

    assert engine.complete_sign_in(Attempt()) == "me@example.com"
    assert order == ["wait", ("save", "the-credentials"), "connect"]


def test_signing_out_removes_the_saved_sign_in_and_is_repeatable(engine, tmp_path):
    (tmp_path / "token.json").write_text("{}")
    engine.sign_out()
    assert not (tmp_path / "token.json").exists()
    engine.sign_out()  # already signed out: nothing to do, no error


def test_reading_hands_the_image_and_todays_date_to_the_extractor(engine, tmp_path, monkeypatch):
    import anthropic

    from syllabus_cal import vision
    from syllabus_cal.schema import ExtractionResult

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs

    def extract_events(path, client, today):
        seen["path"], seen["today"] = path, today
        return ExtractionResult(events=[event()])

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(vision, "extract_events", extract_events)

    result = engine.read(tmp_path / "shot.png", "sk-ant-the-key")
    assert [e.title for e in result.events] == ["Kickoff"]
    assert seen["path"] == tmp_path / "shot.png" and seen["today"] == date.today()
    assert seen["client"]["api_key"] == "sk-ant-the-key"
    assert seen["client"]["timeout"] > 0  # a stuck request must give up eventually


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.headers = {}
        self.request = None


def _status_error(cls, status):
    import httpx2

    request = httpx2.Request("GET", "https://api.anthropic.com/v1/models/x")
    response = httpx2.Response(status, request=request, json={"error": {"message": "nope"}})
    return cls(message="nope", response=response, body={"error": {"message": "nope"}})


@pytest.mark.parametrize(
    "error_name, status, ok, phrase",
    [
        ("AuthenticationError", 401, False, "doesn't recognise this key"),
        ("PermissionDeniedError", 403, False, "isn't allowed"),
        ("NotFoundError", 404, True, "isn't available to your account"),
        ("RateLimitError", 429, True, "too busy to confirm"),
        ("InternalServerError", 500, True, "too busy to confirm"),
    ],
)
def test_checking_a_key_explains_each_answer_anthropic_can_give(
    engine, monkeypatch, error_name, status, ok, phrase
):
    import anthropic

    error = _status_error(getattr(anthropic, error_name), status)

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def retrieve(self, model):
            raise error

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    verdict = engine.check_key("sk-ant-whatever")
    assert verdict.ok is ok
    assert phrase in verdict.message


def test_checking_a_key_offline_does_not_save_or_condemn_it(engine, monkeypatch):
    import anthropic
    import httpx2

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def retrieve(self, model):
            raise anthropic.APIConnectionError(request=httpx2.Request("GET", "https://api.anthropic.com"))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    verdict = engine.check_key("sk-ant-whatever")
    assert verdict.ok is False and "internet" in verdict.message


def test_a_working_key_is_simply_accepted(engine, monkeypatch):
    import anthropic

    asked = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def retrieve(self, model):
            asked.append(model)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    assert engine.check_key("sk-ant-whatever") == KeyCheck(True)
    assert asked == ["claude-sonnet-5"]  # looks the model up; never runs it


def test_demo_events_are_always_in_the_future_and_valid():
    for today in (date(2026, 11, 11), date(2026, 11, 12), date(2026, 12, 31), date(2028, 2, 29)):
        first, second = demo_events(today)
        assert first.start_date > today and first.start_date.weekday() == 3  # a Thursday
        assert second.start_date > first.start_date and second.start_date.weekday() == 1
        assert first.needs_review is False and second.needs_review is True


def test_make_engine_picks_demo_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_client, "CREDENTIALS_PATH", calendar_client.CREDENTIALS_PATH)
    monkeypatch.setattr(calendar_client, "TOKEN_PATH", calendar_client.TOKEN_PATH)
    assert isinstance(make_engine(tmp_path, "demo"), DemoEngine)
    assert isinstance(make_engine(tmp_path, " Demo "), DemoEngine)
    assert isinstance(make_engine(tmp_path, None), RealEngine)
    assert isinstance(make_engine(tmp_path, ""), RealEngine)
