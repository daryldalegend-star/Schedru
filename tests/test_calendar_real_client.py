"""Our events, sent through Google's real client library to a pretend Google.

The other calendar tests hand `write_events` a fake `service` object, so they
can't tell whether the real library accepts what we give it. Here the service
is the real one, built from the same bundled API description the app uses,
and only the network underneath is replaced: each test supplies the HTTP
replies Google would send and inspects the requests that went out.
"""

from __future__ import annotations

import json
from datetime import date, time
from urllib.parse import parse_qs, urlsplit

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from syllabus_cal import calendar_client
from syllabus_cal.calendar_client import AuthError, GoogleConnectionError, write_events
from syllabus_cal.schema import ExtractedEvent, RecurrenceRule


def service_answering(*replies):
    """A real Calendar client whose HTTP layer plays back `replies`."""
    http = HttpMockSequence(
        [({"status": str(status), "content-type": "application/json"}, json.dumps(body)) for status, body in replies]
    )
    return build("calendar", "v3", http=http, static_discovery=True), http


def sent(http, index=0):
    uri, method, body, headers = http.request_sequence[index]
    return urlsplit(uri), method, (json.loads(body) if body else None)


KICKOFF = ExtractedEvent(
    title="Scarlet Robotics Club: Kickoff Meeting",
    event_type="club",
    start_date=date(2026, 11, 19),
    start_time=time(19, 30),
    end_time=time(20, 30),
    location="Busch Student Center, Room 120C",
    address="Busch Campus, Rutgers University, Piscataway, NJ",
    notes="Free pizza.",
    confidence=0.94,
    assumptions=["7:30 read as PM (evening club meeting)"],
)


def test_an_event_is_posted_to_the_primary_calendar_and_its_link_comes_back():
    service, http = service_answering(
        (200, {"id": "abc", "htmlLink": "https://www.google.com/calendar/event?eid=abc"})
    )

    (outcome,) = write_events(service, [KICKOFF])

    assert outcome.ok and outcome.link == "https://www.google.com/calendar/event?eid=abc"
    url, method, body = sent(http)
    assert method == "POST"
    assert (url.netloc, url.path) == ("www.googleapis.com", "/calendar/v3/calendars/primary/events")
    assert body["summary"] == "Scarlet Robotics Club: Kickoff Meeting"
    assert body["start"] == {"dateTime": "2026-11-19T19:30:00", "timeZone": "America/New_York"}
    assert body["end"] == {"dateTime": "2026-11-19T20:30:00", "timeZone": "America/New_York"}
    assert body["location"] == (
        "Busch Student Center, Room 120C, Busch Campus, Rutgers University, Piscataway, NJ"
    )
    assert body["description"].startswith("Free pizza.")
    assert "Filled in by Schedru (check these):" in body["description"]
    assert body["extendedProperties"] == {"private": {"created_by": "syllabus_cal"}}


def test_a_repeating_class_goes_out_as_one_event_with_a_rule():
    lecture = ExtractedEvent(
        title="BIO 115: Lecture",
        event_type="class",
        start_date=date(2026, 11, 12),
        start_time=time(9, 50),
        end_time=time(11, 10),
        recurrence=RecurrenceRule(
            freq="WEEKLY", days_of_week=["TU", "TH"], until=date(2026, 12, 10), exdates=[date(2026, 11, 26)]
        ),
        confidence=0.9,
    )
    service, http = service_answering((200, {"id": "x", "htmlLink": "https://www.google.com/calendar/event?eid=x"}))

    write_events(service, [lecture])

    assert len(http.request_sequence) == 1  # one event, not one per lecture
    _, _, body = sent(http)
    assert body["recurrence"] == [
        "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261211T045959Z",
        "EXDATE:20261126T145000Z",
    ]


def test_an_all_day_deadline_uses_dates_not_times():
    due = ExtractedEvent(
        title="BIO 115: Problem set 9", event_type="assignment", start_date=date(2026, 11, 30), confidence=0.9
    )
    service, http = service_answering((200, {"id": "x"}))

    (outcome,) = write_events(service, [due])

    _, _, body = sent(http)
    assert body["start"] == {"date": "2026-11-30"} and body["end"] == {"date": "2026-12-01"}
    assert outcome.ok and outcome.link is None  # Google sent no link; that's not a failure


def test_one_refused_event_does_not_stop_the_others():
    service, http = service_answering(
        (200, {"id": "1", "htmlLink": "https://www.google.com/calendar/event?eid=1"}),
        (403, {"error": {"code": 403, "message": "Rate Limit Exceeded", "errors": [{"reason": "rateLimitExceeded"}]}}),
        (200, {"id": "3", "htmlLink": "https://www.google.com/calendar/event?eid=3"}),
    )
    events = [KICKOFF.model_copy(update={"title": name}) for name in ("first", "second", "third")]

    outcomes = write_events(service, events)

    assert [o.ok for o in outcomes] == [True, False, True]
    assert "403" in outcomes[1].error and "Rate Limit Exceeded" in outcomes[1].error
    assert [sent(http, i)[2]["summary"] for i in range(3)] == ["first", "second", "third"]


class StubCredentials:
    valid = True


@pytest.fixture
def connected_to(monkeypatch):
    """Make calendar_client.connect() use a real client over pretend replies."""

    def install(*replies):
        service, http = service_answering(*replies)
        monkeypatch.setattr(calendar_client, "load_credentials", lambda: StubCredentials())
        monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)
        return http

    return install


def test_connecting_asks_for_one_event_and_reports_whose_calendar_it_is(connected_to):
    http = connected_to((200, {"kind": "calendar#events", "summary": "student@example.com", "items": []}))

    _, name = calendar_client.connect()

    assert name == "student@example.com"
    url, method, body = sent(http)
    assert method == "GET" and url.path == "/calendar/v3/calendars/primary/events"
    assert parse_qs(url.query)["maxResults"] == ["1"]  # a check, not a download of the calendar


def test_a_sign_in_google_no_longer_accepts_asks_for_a_new_one(connected_to):
    connected_to((401, {"error": {"code": 401, "message": "Invalid Credentials"}}))
    with pytest.raises(AuthError):
        calendar_client.connect()


def test_a_disabled_calendar_api_is_explained_in_googles_own_words(connected_to):
    message = "Google Calendar API has not been used in project 123 before or it is disabled."
    connected_to((403, {"error": {"code": 403, "message": message}}))

    with pytest.raises(GoogleConnectionError) as caught:
        calendar_client.connect()
    assert message in str(caught.value) and "403" in str(caught.value)
