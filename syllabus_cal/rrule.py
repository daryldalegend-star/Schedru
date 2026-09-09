"""Convert a RecurrenceRule into RFC5545 RRULE / EXDATE strings for the Google
Calendar API's `recurrence` field.

BIWEEKLY isn't a real RFC5545 FREQ value -- it's WEEKLY with INTERVAL=2, so
that's the one thing this module quietly translates. Everything else is close
to a direct serialization of the fields already validated by
schema.RecurrenceRule, except:

- UNTIL on a *timed* recurring event must be a UTC date-time (RFC5545 requires
  it match DTSTART's value type -- a date-time DTSTART needs a UTC UNTIL). An
  *all-day* event's UNTIL stays a bare date. Same split for EXDATE.
- That local-to-UTC conversion has to go through a real timezone, not a fixed
  offset, or it silently breaks across the DST boundary (see the Nov 1 test).
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .schema import RecurrenceRule

# calendar_client.py reads this same constant when it builds the event body,
# so there's exactly one place to change the timezone.
DEFAULT_TZ = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_FREQ_MAP = {
    "DAILY": "DAILY",
    "WEEKLY": "WEEKLY",
    "BIWEEKLY": "WEEKLY",
    "MONTHLY": "MONTHLY",
}


def _to_utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(_UTC)


def _format_until(until: date, start_time: time | None, tz: ZoneInfo) -> str:
    if start_time is None:
        return until.strftime("%Y%m%d")
    # Use the end of that local calendar day so the last stated occurrence,
    # whatever time it starts, is still inside the window.
    end_of_day_utc = _to_utc(until, time(23, 59, 59), tz)
    return end_of_day_utc.strftime("%Y%m%dT%H%M%SZ")


def _format_exdate(excluded: date, start_time: time | None, tz: ZoneInfo) -> str:
    if start_time is None:
        return f"EXDATE;VALUE=DATE:{excluded.strftime('%Y%m%d')}"
    dt_utc = _to_utc(excluded, start_time, tz)
    return f"EXDATE:{dt_utc.strftime('%Y%m%dT%H%M%SZ')}"


def build_rrule(
    rule: RecurrenceRule, start_time: time | None = None, tz: ZoneInfo = DEFAULT_TZ
) -> str:
    """Build the single ``RRULE:...`` line. EXDATEs are separate lines -- see
    build_exdates -- because that's how the Google Calendar API's `recurrence`
    list expects them: one string per RRULE/EXDATE/RDATE entry."""
    parts = [f"FREQ={_FREQ_MAP[rule.freq]}"]

    if rule.freq == "BIWEEKLY":
        parts.append("INTERVAL=2")

    if rule.days_of_week:
        parts.append(f"BYDAY={','.join(rule.days_of_week)}")

    if rule.until:
        parts.append(f"UNTIL={_format_until(rule.until, start_time, tz)}")

    return "RRULE:" + ";".join(parts)


def build_exdates(
    rule: RecurrenceRule, start_time: time | None = None, tz: ZoneInfo = DEFAULT_TZ
) -> list[str]:
    """One ``EXDATE:...`` line per excluded date (holidays, cancellations)."""
    return [_format_exdate(d, start_time, tz) for d in rule.exdates]


def build_recurrence(
    rule: RecurrenceRule, start_time: time | None = None, tz: ZoneInfo = DEFAULT_TZ
) -> list[str]:
    """Full ``recurrence`` list for the Google Calendar API: the RRULE line
    followed by one EXDATE line per excluded date."""
    return [build_rrule(rule, start_time, tz)] + build_exdates(rule, start_time, tz)
