from datetime import date, time

import pytest
from pydantic import ValidationError

from syllabus_cal.schema import ExtractedEvent, ExtractionResult, RecurrenceRule


def test_one_time_assignment_needs_no_recurrence():
    event = ExtractedEvent(
        title="Problem Set 3",
        event_type="assignment",
        start_date=date(2026, 9, 20),
        start_time=None,
        end_time=None,
        location=None,
        recurrence=None,
        notes=None,
        confidence=0.95,
        ambiguity_flags=[],
    )
    assert event.recurrence is None
    assert event.needs_review is False


def test_recurring_class_with_rule():
    event = ExtractedEvent(
        title="Organic Chemistry Lecture",
        event_type="class",
        start_date=date(2026, 9, 3),
        start_time=time(9, 50),
        end_time=time(11, 10),
        location="Busch SEC 118",
        recurrence=RecurrenceRule(
            freq="WEEKLY",
            days_of_week=["TU", "TH"],
            until=date(2026, 12, 10),
            exdates=[date(2026, 11, 26)],
        ),
        notes=None,
        confidence=0.9,
        ambiguity_flags=[],
    )
    assert event.recurrence.freq == "WEEKLY"
    assert event.recurrence.exdates == [date(2026, 11, 26)]


def test_low_confidence_flags_for_review():
    event = ExtractedEvent(
        title="Midterm Exam",
        event_type="exam",
        start_date=date(2026, 10, 15),
        start_time=None,
        end_time=None,
        location=None,
        recurrence=None,
        notes=None,
        confidence=0.4,
        ambiguity_flags=["AM/PM unclear"],
    )
    assert event.needs_review is True


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        ExtractedEvent(
            title="Bad confidence",
            event_type="other",
            start_date=date(2026, 1, 1),
            confidence=1.5,
            ambiguity_flags=[],
        )


def test_extraction_result_defaults_to_empty_events():
    result = ExtractionResult.model_validate({"events": []})
    assert result.events == []


def test_invalid_event_type_rejected():
    with pytest.raises(ValidationError):
        ExtractedEvent(
            title="Mystery",
            event_type="not_a_real_type",
            start_date=date(2026, 1, 1),
            confidence=0.9,
            ambiguity_flags=[],
        )


# Assumptions vs ambiguity flags. An assumption is a gap the model filled with
# an obvious answer; a flag is something it genuinely couldn't determine. Only
# the latter should force the user to re-confirm.


def test_assumptions_alone_do_not_force_review():
    event = ExtractedEvent(
        title="Rutgers AI Club Weekly Meeting",
        event_type="club",
        start_date=date(2026, 9, 24),
        start_time=time(19, 30),
        confidence=0.9,
        ambiguity_flags=[],
        assumptions=["7:30 read as PM (evening club meeting)"],
    )
    assert event.needs_review is False


def test_ambiguity_flags_still_force_review_even_alongside_assumptions():
    event = ExtractedEvent(
        title="Midterm",
        event_type="exam",
        start_date=date(2026, 10, 15),
        start_time=time(9, 0),
        confidence=0.9,
        ambiguity_flags=["room not stated"],
        assumptions=["9:00 read as AM (morning exam)"],
    )
    assert event.needs_review is True


def test_low_confidence_still_forces_review_with_only_assumptions():
    event = ExtractedEvent(
        title="Something blurry",
        event_type="other",
        start_date=date(2026, 10, 15),
        confidence=0.4,
        ambiguity_flags=[],
        assumptions=["year assumed 2026"],
    )
    assert event.needs_review is True


def test_assumptions_default_to_empty():
    event = ExtractedEvent(
        title="Plain",
        event_type="other",
        start_date=date(2026, 1, 1),
        confidence=0.9,
    )
    assert event.assumptions == []
