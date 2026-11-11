"""Turning events into what the window shows, and what the window sends back
into events.

The window never does date arithmetic or validation of its own: it displays
what `event_to_json` gives it and posts edits back through `event_from_json`,
so the rules live in one place (the pydantic schema) for the terminal and the
app alike.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

from pydantic import ValidationError

from ..schema import ExtractedEvent, RecurrenceRule

_DAY_NAMES = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}
_DAY_ORDER = list(_DAY_NAMES)
_FREQ_PHRASE = {
    "DAILY": "Every day",
    "WEEKLY": "Every week",
    "BIWEEKLY": "Every 2 weeks",
    "MONTHLY": "Every month",
}

# Fields the window may edit. Everything else on an event (confidence, flags,
# assumptions) is carried through from what Claude returned.
EDITABLE_FIELDS = (
    "title",
    "start_date",
    "start_time",
    "end_time",
    "location",
    "address",
    "notes",
)


class EventEditError(ValueError):
    """An edited event can't be saved. `field` names the input to point at."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def format_date(value: date) -> str:
    """'Thu, Nov 19' -- the year only when it isn't this one."""
    text = f"{value.strftime('%a')}, {value.strftime('%b')} {value.day}"
    if value.year != date.today().year:
        text += f", {value.year}"
    return text


def format_time(value: time) -> str:
    """'7:30 PM', and '7 PM' on the hour."""
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    if value.minute == 0:
        return f"{hour} {suffix}"
    return f"{hour}:{value.minute:02d} {suffix}"


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def describe_recurrence(rule: RecurrenceRule | None) -> str | None:
    """'Every week on Tuesday and Thursday until Dec 10', or None."""
    if rule is None:
        return None

    text = _FREQ_PHRASE[rule.freq]
    days = sorted(set(rule.days_of_week), key=_DAY_ORDER.index)
    if days and rule.freq != "DAILY":
        text += " on " + _join([_DAY_NAMES[day] for day in days])
    if rule.until:
        text += f" until {format_date(rule.until)}"
    else:
        text += ", with no end date"
    if rule.exdates:
        text += " (skipping " + _join([format_date(d) for d in sorted(rule.exdates)]) + ")"
    return text


def describe_when(event: ExtractedEvent) -> str:
    """One line for a list: 'Thu, Nov 19 · 7:30 – 8:30 PM'."""
    day = format_date(event.start_date)
    if event.start_time is None:
        return f"{day} · All day"
    start = format_time(event.start_time)
    if event.end_time is None:
        return f"{day} · {start}"
    end = format_time(event.end_time)
    # "7:30 – 8:30 PM" reads better than "7:30 PM – 8:30 PM".
    if start[-2:] == end[-2:]:
        start = start[:-3]
    return f"{day} · {start} – {end}"


def event_to_json(event: ExtractedEvent, event_id: str) -> dict[str, Any]:
    from ..calendar_client import calendar_location

    return {
        "id": event_id,
        "title": event.title,
        "event_type": event.event_type,
        "start_date": event.start_date.isoformat(),
        "start_time": event.start_time.strftime("%H:%M") if event.start_time else None,
        "end_time": event.end_time.strftime("%H:%M") if event.end_time else None,
        "location": event.location,
        "address": event.address,
        "notes": event.notes,
        "repeats": event.recurrence is not None,
        "recurrence_text": describe_recurrence(event.recurrence),
        "when_text": describe_when(event),
        "where_text": calendar_location(event),
        "confidence": event.confidence,
        "assumptions": list(event.assumptions),
        "ambiguity_flags": list(event.ambiguity_flags),
        "needs_review": event.needs_review,
    }


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


_FIELD_LABELS = {
    "title": "name",
    "start_date": "date",
    "start_time": "start time",
    "end_time": "end time",
}


def event_from_json(original: ExtractedEvent, edits: dict[str, Any]) -> ExtractedEvent:
    """Apply the window's edits to `original` and re-validate the result.

    Only EDITABLE_FIELDS are read from `edits`, plus `repeats: false` to turn a
    repeating event into a one-time one. Anything else the page sends is
    ignored, so it can't raise an event's confidence or clear its flags.
    """
    data = original.model_dump()

    for field in EDITABLE_FIELDS:
        if field in edits:
            data[field] = _blank_to_none(edits[field])

    if edits.get("repeats") is False:
        data["recurrence"] = None

    if not data.get("title"):
        raise EventEditError("Give this event a name.", field="title")
    if not data.get("start_date"):
        raise EventEditError("Pick a date for this event.", field="start_date")
    if data.get("start_time") is None and data.get("end_time") is not None:
        # An end without a start is an all-day event with a stray leftover.
        data["end_time"] = None

    try:
        return ExtractedEvent.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = str(first["loc"][0]) if first.get("loc") else None
        label = _FIELD_LABELS.get(field or "", field or "value")
        raise EventEditError(f"The {label} isn't valid.", field=field) from exc
