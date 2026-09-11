"""Google Calendar OAuth (desktop app flow) and event writing.

`credentials.json` (downloaded from Google Cloud Console, see SETUP.md) and
`token.json` (written here on first successful sign-in) both live in the
project root and are gitignored -- never commit either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .rrule import DEFAULT_TZ, TIMEZONE, build_recurrence
from .schema import ExtractedEvent

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"
TOKEN_PATH = _PROJECT_ROOT / "token.json"

# Written into every event this tool creates so they can be found and
# bulk-deleted later (e.g. test data) without touching anything else on the
# calendar.
CREATED_BY_MARKER = {"created_by": "syllabus_cal"}


class AuthError(RuntimeError):
    """Missing/expired/invalid Google auth. Always caught at the CLI boundary
    and shown as a short message pointing at `auth` -- never a raw stack trace."""


def is_authenticated() -> bool:
    return TOKEN_PATH.exists()


def run_auth_flow() -> Credentials:
    """Run the InstalledAppFlow (opens a browser) and persist token.json."""
    if not CREDENTIALS_PATH.exists():
        raise AuthError(
            f"'{CREDENTIALS_PATH.name}' not found in the project root. Follow SETUP.md to "
            "create OAuth Desktop App credentials in Google Cloud Console and download it "
            "there before running `auth`."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def load_credentials() -> Credentials:
    """Load saved credentials, refreshing an expired token if possible.

    Raises AuthError (never lets an oauth stack trace escape) when the user
    needs to re-run `auth`: no token yet, or refresh failed/impossible.
    """
    if not TOKEN_PATH.exists():
        raise AuthError("Not signed in yet. Run `python -m syllabus_cal auth` first.")

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (ValueError, OSError) as exc:
        raise AuthError(
            f"'{TOKEN_PATH.name}' is corrupted or unreadable ({exc}). Run "
            "`python -m syllabus_cal auth` again."
        ) from exc

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(
                "Your Google sign-in expired and couldn't be refreshed. Run "
                "`python -m syllabus_cal auth` again."
            ) from exc
        TOKEN_PATH.write_text(creds.to_json())
        return creds

    raise AuthError(
        "Your Google sign-in is invalid or incomplete. Run `python -m syllabus_cal auth` again."
    )


def get_calendar_service(creds: Credentials | None = None):
    creds = creds or load_credentials()
    return build("calendar", "v3", credentials=creds)


def _event_datetime(d: date, t: time) -> dict[str, str]:
    return {"dateTime": datetime.combine(d, t).isoformat(), "timeZone": TIMEZONE}


def _event_date(d: date) -> dict[str, str]:
    return {"date": d.isoformat()}


def build_event_body(event: ExtractedEvent) -> dict[str, Any]:
    """Turn a validated ExtractedEvent into a Google Calendar API event body.
    Pure data transformation -- no network calls, so this is unit-testable
    without touching Google at all."""
    body: dict[str, Any] = {
        "summary": event.title,
        "extendedProperties": {"private": dict(CREATED_BY_MARKER)},
    }

    if event.location:
        body["location"] = event.location

    notes = f"[{event.event_type}] {event.notes}" if event.notes else f"[{event.event_type}]"
    body["description"] = notes

    if event.start_time is None:
        body["start"] = _event_date(event.start_date)
        # All-day events are exclusive on end.date, so a single-day event's
        # end is the following calendar day.
        body["end"] = _event_date(event.start_date + timedelta(days=1))
    else:
        end_time = event.end_time or event.start_time
        body["start"] = _event_datetime(event.start_date, event.start_time)
        body["end"] = _event_datetime(event.start_date, end_time)

    if event.recurrence:
        body["recurrence"] = build_recurrence(
            event.recurrence, start_time=event.start_time, tz=DEFAULT_TZ
        )

    return body


def create_event(service, event_body: dict[str, Any], calendar_id: str = "primary") -> dict[str, Any]:
    """Write one event body to Calendar.

    Raises googleapiclient.errors.HttpError on failure -- callers writing
    multiple events should catch it per-event and report exactly which ones
    landed, so a re-run doesn't create duplicates.
    """
    return service.events().insert(calendarId=calendar_id, body=event_body).execute()


@dataclass(frozen=True)
class WriteOutcome:
    """What happened to one event. `link` is set on success, `error` on failure."""

    event: ExtractedEvent
    link: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _readable_error(exc: Exception) -> str:
    if isinstance(exc, HttpError):
        reason = getattr(exc, "reason", None)
        status = getattr(exc, "status_code", None)
        if reason:
            return f"{reason}" + (f" (HTTP {status})" if status else "")
    return f"{type(exc).__name__}: {exc}"


def write_events(
    service, events: list[ExtractedEvent], calendar_id: str = "primary"
) -> list[WriteOutcome]:
    """Write each event, continuing past failures.

    One rejected event shouldn't block the rest, and the caller needs to know
    exactly which ones landed so a re-run doesn't duplicate them -- so every
    event gets an outcome rather than the batch dying on the first error.
    """
    outcomes: list[WriteOutcome] = []
    for event in events:
        try:
            created = create_event(service, build_event_body(event), calendar_id)
        except Exception as exc:
            outcomes.append(WriteOutcome(event=event, error=_readable_error(exc)))
        else:
            outcomes.append(WriteOutcome(event=event, link=created.get("htmlLink")))
    return outcomes


def create_hardcoded_test_event(service, calendar_id: str = "primary") -> dict[str, Any]:
    """Write one fixed event, tomorrow at 9:00-9:30 local time, to prove the
    OAuth + Calendar API write path works end to end. Used by `auth --test-event`;
    not part of the real parse -> confirm -> write pipeline (that's step 5)."""
    tomorrow = date.today() + timedelta(days=1)
    body = {
        "summary": "syllabus_cal test event",
        "description": (
            "Created by `python -m syllabus_cal auth --test-event` to confirm Calendar "
            "write access works end to end. Safe to delete."
        ),
        "start": _event_datetime(tomorrow, time(9, 0)),
        "end": _event_datetime(tomorrow, time(9, 30)),
        "extendedProperties": {"private": dict(CREATED_BY_MARKER)},
    }
    return create_event(service, body, calendar_id)
