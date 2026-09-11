from datetime import date, time

from syllabus_cal.calendar_client import CREATED_BY_MARKER, build_event_body
from syllabus_cal.rrule import TIMEZONE
from syllabus_cal.schema import ExtractedEvent, RecurrenceRule


def test_all_day_assignment_uses_exclusive_end_date():
    event = ExtractedEvent(
        title="Problem Set 3",
        event_type="assignment",
        start_date=date(2026, 9, 20),
        confidence=0.95,
        ambiguity_flags=[],
    )
    body = build_event_body(event)

    assert body["start"] == {"date": "2026-09-20"}
    assert body["end"] == {"date": "2026-09-21"}
    assert "recurrence" not in body
    assert "dateTime" not in body["start"]


def test_timed_event_carries_timezone_and_marker():
    event = ExtractedEvent(
        title="Office Hours",
        event_type="office_hours",
        start_date=date(2026, 9, 22),
        start_time=time(14, 0),
        end_time=time(15, 0),
        location="Busch SEC 118",
        confidence=0.9,
        ambiguity_flags=[],
    )
    body = build_event_body(event)

    assert body["start"] == {"dateTime": "2026-09-22T14:00:00", "timeZone": TIMEZONE}
    assert body["end"] == {"dateTime": "2026-09-22T15:00:00", "timeZone": TIMEZONE}
    assert body["location"] == "Busch SEC 118"
    assert body["extendedProperties"]["private"] == CREATED_BY_MARKER


def test_missing_end_time_defaults_to_start_time():
    event = ExtractedEvent(
        title="Quick check-in",
        event_type="other",
        start_date=date(2026, 9, 22),
        start_time=time(9, 0),
        confidence=0.7,
        ambiguity_flags=["end time not stated"],
    )
    body = build_event_body(event)
    assert body["start"]["dateTime"] == body["end"]["dateTime"]


def test_recurring_class_includes_rrule_in_body():
    event = ExtractedEvent(
        title="Organic Chemistry Lecture",
        event_type="class",
        start_date=date(2026, 9, 3),
        start_time=time(9, 50),
        end_time=time(11, 10),
        recurrence=RecurrenceRule(
            freq="WEEKLY", days_of_week=["TU", "TH"], until=date(2026, 12, 10), exdates=[]
        ),
        confidence=0.9,
        ambiguity_flags=[],
    )
    body = build_event_body(event)

    assert body["recurrence"][0].startswith("RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=")


def test_notes_and_event_type_land_in_description_not_title():
    event = ExtractedEvent(
        title="Robotics Club",
        event_type="club",
        start_date=date(2026, 9, 25),
        notes="Bring laptop",
        confidence=0.85,
        ambiguity_flags=[],
    )
    body = build_event_body(event)

    assert body["summary"] == "Robotics Club"
    assert "Bring laptop" in body["description"]
    assert "[club]" in body["description"]


def test_every_event_gets_the_marker_for_bulk_delete():
    event = ExtractedEvent(
        title="Anything",
        event_type="other",
        start_date=date(2026, 1, 1),
        confidence=0.5,
        ambiguity_flags=[],
    )
    body = build_event_body(event)
    assert body["extendedProperties"]["private"]["created_by"] == "syllabus_cal"
