"""What the window shows for an event, and what it may change about one."""

from __future__ import annotations

from datetime import date, time

import pytest

from syllabus_cal.app.describe import (
    EventEditError,
    describe_recurrence,
    describe_when,
    event_from_json,
    event_to_json,
    format_date,
    format_time,
)
from syllabus_cal.schema import ExtractedEvent, RecurrenceRule


def make_event(**overrides) -> ExtractedEvent:
    data = dict(
        title="Robotics Club: Kickoff",
        event_type="club",
        start_date=date(date.today().year, 11, 19),
        start_time=time(19, 30),
        end_time=time(20, 30),
        location="Busch Student Center, Room 120C",
        address="Busch Campus, Piscataway, NJ",
        notes="Free pizza.",
        confidence=0.9,
        assumptions=["7:30 read as PM"],
    )
    data.update(overrides)
    return ExtractedEvent(**data)


@pytest.mark.parametrize(
    "value, expected",
    [
        (time(19, 30), "7:30 PM"),
        (time(9, 5), "9:05 AM"),
        (time(0, 0), "12 AM"),
        (time(12, 0), "12 PM"),
        (time(13, 0), "1 PM"),
        (time(23, 59), "11:59 PM"),
    ],
)
def test_format_time(value, expected):
    assert format_time(value) == expected


def test_format_date_leaves_out_this_year_and_shows_another():
    this_year = date.today().year
    assert format_date(date(this_year, 3, 5)) == date(this_year, 3, 5).strftime("%a, Mar 5")
    assert format_date(date(this_year + 1, 3, 5)).endswith(f", {this_year + 1}")


def test_when_collapses_a_shared_am_pm():
    text = describe_when(make_event())
    assert text.endswith("· 7:30 – 8:30 PM")


def test_when_keeps_both_halves_when_they_differ():
    text = describe_when(make_event(start_time=time(11, 0), end_time=time(13, 0)))
    assert text.endswith("· 11 AM – 1 PM")


def test_when_for_all_day_and_open_ended():
    assert describe_when(make_event(start_time=None, end_time=None)).endswith("· All day")
    assert describe_when(make_event(end_time=None)).endswith("· 7:30 PM")


def test_recurrence_text():
    assert describe_recurrence(None) is None

    rule = RecurrenceRule(
        freq="WEEKLY", days_of_week=["TH", "TU"], until=date(date.today().year, 12, 10)
    )
    assert describe_recurrence(rule) == (
        "Every week on Tuesday and Thursday until "
        + date(date.today().year, 12, 10).strftime("%a, Dec 10")
    )

    assert describe_recurrence(RecurrenceRule(freq="BIWEEKLY", days_of_week=["MO"])) == (
        "Every 2 weeks on Monday, with no end date"
    )
    assert describe_recurrence(RecurrenceRule(freq="DAILY", days_of_week=["MO"])) == (
        "Every day, with no end date"
    )
    three = RecurrenceRule(freq="WEEKLY", days_of_week=["MO", "WE", "FR"])
    assert "on Monday, Wednesday and Friday" in describe_recurrence(three)


def test_recurrence_text_lists_skipped_dates():
    rule = RecurrenceRule(
        freq="WEEKLY", days_of_week=["TH"], exdates=[date(date.today().year, 11, 26)]
    )
    assert "(skipping Thu, Nov 26)" in describe_recurrence(rule)


def test_event_to_json_carries_everything_the_window_shows():
    payload = event_to_json(make_event(), "e1")

    assert payload["id"] == "e1"
    assert payload["start_date"] == f"{date.today().year}-11-19"
    assert payload["start_time"] == "19:30"
    assert payload["end_time"] == "20:30"
    assert payload["where_text"] == "Busch Student Center, Room 120C, Busch Campus, Piscataway, NJ"
    assert payload["repeats"] is False and payload["recurrence_text"] is None
    assert payload["assumptions"] == ["7:30 read as PM"]
    assert payload["needs_review"] is False


def test_event_to_json_marks_events_that_need_a_look():
    flagged = make_event(confidence=0.5, ambiguity_flags=["No end date given"])
    payload = event_to_json(flagged, "e2")
    assert payload["needs_review"] is True
    assert payload["ambiguity_flags"] == ["No end date given"]


def test_edits_are_applied():
    edited = event_from_json(
        make_event(),
        {
            "title": "  Renamed  ",
            "start_date": "2027-01-15",
            "start_time": "18:00",
            "end_time": "19:15",
            "location": "Somewhere else",
            "address": "",
            "notes": "   ",
        },
    )
    assert edited.title == "Renamed"
    assert edited.start_date == date(2027, 1, 15)
    assert edited.start_time == time(18, 0) and edited.end_time == time(19, 15)
    assert edited.location == "Somewhere else"
    assert edited.address is None  # blanked fields become "no value", not ""
    assert edited.notes is None


def test_fields_the_window_did_not_send_are_untouched():
    original = make_event()
    assert event_from_json(original, {"title": "Only this"}).location == original.location


def test_clearing_the_start_time_makes_it_all_day():
    edited = event_from_json(make_event(), {"start_time": "", "end_time": "20:30"})
    assert edited.start_time is None
    assert edited.end_time is None  # an end with no start means nothing


def test_repeats_false_makes_a_one_time_event():
    weekly = make_event(recurrence=RecurrenceRule(freq="WEEKLY", days_of_week=["TH"]))
    assert event_from_json(weekly, {"repeats": False}).recurrence is None
    assert event_from_json(weekly, {"repeats": True}).recurrence is not None
    assert event_from_json(weekly, {}).recurrence is not None


def test_the_window_cannot_vouch_for_an_event():
    """Confidence and flags come from the read, never from the page."""
    flagged = make_event(confidence=0.4, ambiguity_flags=["Date unreadable"])
    edited = event_from_json(
        flagged,
        {"confidence": 1.0, "ambiguity_flags": [], "assumptions": [], "event_type": "exam"},
    )
    assert edited.confidence == 0.4
    assert edited.ambiguity_flags == ["Date unreadable"]
    assert edited.assumptions == flagged.assumptions
    assert edited.event_type == "club"


@pytest.mark.parametrize("title", ["", "   ", None])
def test_a_nameless_event_is_refused(title):
    with pytest.raises(EventEditError) as caught:
        event_from_json(make_event(), {"title": title})
    assert caught.value.field == "title"
    assert str(caught.value) == "Give this event a name."


def test_a_dateless_event_is_refused():
    with pytest.raises(EventEditError) as caught:
        event_from_json(make_event(), {"start_date": ""})
    assert caught.value.field == "start_date"
    assert str(caught.value) == "Pick a date for this event."


@pytest.mark.parametrize(
    "field, value",
    [("start_date", "next thursday"), ("start_date", "2026-13-45"), ("start_time", "25:99")],
)
def test_nonsense_values_name_the_field_at_fault(field, value):
    with pytest.raises(EventEditError) as caught:
        event_from_json(make_event(), {field: value})
    assert caught.value.field == field
