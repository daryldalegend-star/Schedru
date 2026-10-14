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

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

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
    """The user needs to sign in (again). Recoverable by running the sign-in
    flow, which the CLI offers inline -- never shown as a stack trace."""


class GoogleConnectionError(RuntimeError):
    """Google couldn't be reached or refused for a reason signing in again
    won't fix (no internet, Calendar API disabled)."""


def run_auth_flow() -> Credentials:
    """Run the InstalledAppFlow (opens a browser) and persist token.json."""
    if not CREDENTIALS_PATH.exists():
        raise GoogleConnectionError(
            f"'{CREDENTIALS_PATH.name}' isn't in {_PROJECT_ROOT}. It's the file you "
            "download at the end of SETUP.md step 4 -- put it in this folder, next to "
            "start.command."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    try:
        # select_account: always show the account picker, so signing in can't
        # silently reuse whichever Google account the browser happens to be on.
        # consent: Google otherwise issues a refresh token only on the first
        # grant, so signing in again could yield a token that dies an hour later.
        creds = flow.run_local_server(port=0, prompt="select_account consent")
    except OAuth2Error as exc:
        raise GoogleConnectionError(
            f"Google didn't grant access ({exc.description or exc.error}). If the browser "
            "said \"Access blocked\", the account you picked isn't on this app's test-user "
            "list: add it under Google Auth Platform -> Audience -> Test users (SETUP.md "
            "step 3), or pick the account that's already there."
        ) from exc
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def load_credentials() -> Credentials:
    """Load saved credentials, refreshing an expired access token if possible."""
    if not TOKEN_PATH.exists():
        raise AuthError("You're not signed in to Google yet.")

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (ValueError, OSError) as exc:
        raise AuthError("Your saved Google sign-in is unreadable.") from exc

    if creds.valid:
        return creds

    if not creds.refresh_token:
        raise AuthError("Your saved Google sign-in is incomplete.")

    try:
        creds.refresh(Request())
    except RefreshError as exc:
        # Google caps sign-ins for apps in "Testing" mode at 7 days, so this is
        # the expected weekly path, not a fault.
        raise AuthError(
            "Your Google sign-in has expired. (Google makes personal apps like this "
            "one sign in again every 7 days.)"
        ) from exc
    except TransportError as exc:
        raise GoogleConnectionError(
            "Couldn't reach Google to renew your sign-in -- check your internet connection."
        ) from exc

    TOKEN_PATH.write_text(creds.to_json())
    return creds


def connect() -> tuple[Any, str]:
    """Return (Calendar service, calendar name) for a connection that works.

    Makes one real Calendar request rather than trusting the token file, so a
    revoked grant or a disabled API is caught before the screenshot is sent to
    Claude. The primary calendar's name is normally the account's email, which
    tells the user which Google account events are going to.
    """
    creds = load_credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        listing = service.events().list(calendarId="primary", maxResults=1).execute()
    except HttpError as exc:
        if exc.resp.status == 401:
            raise AuthError("Google no longer accepts your saved sign-in.") from exc
        raise GoogleConnectionError(
            f"Google Calendar refused the connection: {_readable_error(exc)}"
        ) from exc
    except (HttpLib2Error, TransportError, OSError) as exc:
        raise GoogleConnectionError(
            "Couldn't reach Google Calendar -- check your internet connection."
        ) from exc
    return service, listing.get("summary", "your Google Calendar")


def _event_datetime(d: date, t: time) -> dict[str, str]:
    return {"dateTime": datetime.combine(d, t).isoformat(), "timeZone": TIMEZONE}


def _event_date(d: date) -> dict[str, str]:
    return {"date": d.isoformat()}


def calendar_location(event: ExtractedEvent) -> str | None:
    """The place plus its address, as one string Google Maps can resolve."""
    parts = [event.location, event.address]
    if event.location and event.address and event.address.lower() in event.location.lower():
        parts = [event.location]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _description(event: ExtractedEvent) -> str | None:
    lines = [event.notes] if event.notes else []
    if event.assumptions:
        if lines:
            lines.append("")
        # Kept on the event itself so a wrong guess is catchable later, in
        # Calendar, rather than only at the confirmation prompt.
        lines.append("Filled in by syllabus_cal (check these):")
        lines.extend(f"- {assumed}" for assumed in event.assumptions)
    return "\n".join(lines) or None


def build_event_body(event: ExtractedEvent) -> dict[str, Any]:
    """Turn a validated ExtractedEvent into a Google Calendar API event body.
    Pure data transformation -- no network calls, so this is unit-testable
    without touching Google at all."""
    body: dict[str, Any] = {
        "summary": event.title,
        "extendedProperties": {"private": dict(CREATED_BY_MARKER)},
    }

    location = calendar_location(event)
    if location:
        body["location"] = location

    description = _description(event)
    if description:
        body["description"] = description

    if event.start_time is None:
        body["start"] = _event_date(event.start_date)
        # All-day events are exclusive on end.date, so a single-day event's
        # end is the following calendar day.
        body["end"] = _event_date(event.start_date + timedelta(days=1))
    else:
        start = datetime.combine(event.start_date, event.start_time)
        # A missing end time would otherwise produce a zero-length event, which
        # Calendar renders as an unreadable sliver.
        end = (
            datetime.combine(event.start_date, event.end_time)
            if event.end_time
            else start + timedelta(hours=1)
        )
        if end <= start:
            # "10 PM - 1 AM": the end is on the next day. Google rejects an
            # end before the start outright.
            end += timedelta(days=1)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TIMEZONE}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": TIMEZONE}

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
        status = exc.resp.status
        return f"{reason} (HTTP {status})" if reason else f"HTTP {status}"
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
    OAuth + Calendar API write path works end to end. Used by `auth --test-event`."""
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
