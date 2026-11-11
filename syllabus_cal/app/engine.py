"""The outside world behind one seam: Claude, Google sign-in, Google Calendar.

Everything above this file (the service, the server, the window) talks to an
engine and never to Anthropic or Google directly. `RealEngine` is the real
thing. `DemoEngine` answers the same calls from canned data with no network and
no accounts, which is what lets the whole app -- window, shortcut, capture,
review, "added" screen -- be exercised end to end on a build machine that has
no API key and no Google sign-in.
"""

from __future__ import annotations

import json
import threading
import time as time_module
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Callable

from .. import calendar_client
from ..calendar_client import AuthError, GoogleConnectionError, WriteOutcome
from ..schema import ExtractedEvent, ExtractionResult, RecurrenceRule
from ..vision import MODEL, ClaudeAPIError
from . import google_signin
from .google_signin import SignInCancelled, SignInError

# How long one read may take before the app gives up on it. A dense syllabus
# with Claude thinking it through can take the better part of a minute.
READ_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class KeyCheck:
    """The verdict on an API key. `message` explains a refusal, or carries a
    caveat when the key was accepted without being fully verified."""

    ok: bool
    message: str | None = None


class RealEngine:
    name = "real"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        calendar_client.CREDENTIALS_PATH = self.data_dir / "credentials.json"
        calendar_client.TOKEN_PATH = self.data_dir / "token.json"

    # ---- Claude --------------------------------------------------------

    def check_key(self, key: str) -> KeyCheck:
        """Ask Anthropic whether this key works. Costs nothing: it looks the
        model up rather than running it."""
        import anthropic

        client = anthropic.Anthropic(api_key=key, timeout=20.0, max_retries=1)
        try:
            client.models.retrieve(MODEL)
        except anthropic.AuthenticationError:
            return KeyCheck(
                False,
                "Anthropic doesn't recognise this key. It may have been copied only "
                "partly, deleted, or expired. Create a new key and paste it here.",
            )
        except anthropic.PermissionDeniedError:
            return KeyCheck(
                False,
                "Anthropic recognises this key but it isn't allowed to read images "
                "with Claude. Create a new key with the default settings.",
            )
        except anthropic.NotFoundError:
            return KeyCheck(
                True,
                f"Your key works, but the Claude model Schedru uses ({MODEL}) isn't "
                "available to your account, so reading may fail.",
            )
        except anthropic.APIStatusError:
            # Rate limits and outages say nothing about the key itself.
            return KeyCheck(True, "Saved. Anthropic was too busy to confirm the key right now.")
        except anthropic.APIConnectionError:
            return KeyCheck(
                False,
                "Couldn't reach Anthropic to check the key. Check your internet "
                "connection and try again.",
            )
        return KeyCheck(True)

    def read(self, image_path: Path, api_key: str) -> ExtractionResult:
        from anthropic import Anthropic

        from ..vision import extract_events

        client = Anthropic(api_key=api_key, timeout=READ_TIMEOUT_SECONDS, max_retries=1)
        return extract_events(image_path, client, today=date.today())

    # ---- Google --------------------------------------------------------

    def has_credentials(self) -> bool:
        return calendar_client.CREDENTIALS_PATH.exists()

    def save_credentials(self, raw: bytes) -> None:
        google_signin.validate_client_file(raw)
        path = calendar_client.CREDENTIALS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def has_saved_sign_in(self) -> bool:
        return calendar_client.TOKEN_PATH.exists()

    def connect(self) -> str:
        """The calendar's name, after proving the saved sign-in still works."""
        _, name = calendar_client.connect()
        return name

    def begin_sign_in(self, open_browser: Callable[[str], Any] | None = None) -> Any:
        if open_browser is None:
            return google_signin.begin()
        return google_signin.begin(open_browser)

    def complete_sign_in(self, attempt: Any) -> str:
        creds = attempt.wait()
        calendar_client.save_token(creds)
        return self.connect()

    def sign_out(self) -> None:
        try:
            calendar_client.TOKEN_PATH.unlink()
        except FileNotFoundError:
            pass

    def add(self, events: list[ExtractedEvent]) -> list[WriteOutcome]:
        # Connect afresh rather than reuse a client from earlier: Google ends
        # these sign-ins after a week, and an app left open can easily outlive
        # one. Checking first turns "expired" into a clear AuthError (sign in
        # again, your review is kept) instead of every event failing with a
        # token error. It costs one small request.
        service, _ = calendar_client.connect()
        return calendar_client.write_events(service, events)


# ---------------------------------------------------------------------------
# Demo


def _next_weekday(start: date, weekday: int) -> date:
    """The first `weekday` (Mon=0) strictly after `start`."""
    ahead = (weekday - start.weekday() - 1) % 7 + 1
    return start + timedelta(days=ahead)


def demo_events(today: date | None = None) -> list[ExtractedEvent]:
    """What the demo "reads" from any image: one event that's ready to add and
    one that needs a look, so both sides of the review screen show up."""
    today = today or date.today()
    thursday = _next_weekday(today, 3)
    tuesday = _next_weekday(thursday, 1)
    return [
        ExtractedEvent(
            title="Scarlet Robotics Club: Kickoff Meeting",
            event_type="club",
            start_date=thursday,
            start_time=time(19, 30),
            end_time=time(20, 30),
            location="Busch Student Center, Room 120C",
            address="Busch Campus, Rutgers University, Piscataway, NJ",
            notes=(
                "Hosted by Scarlet Robotics Club. Free pizza. Open to all majors, no "
                "experience needed. Scan the QR code on the flyer to RSVP."
            ),
            confidence=0.94,
            assumptions=[
                "7:30 read as PM (evening club meeting)",
                "No end time given, so assumed 1 hour",
                "BSC = Busch Student Center (Busch campus)",
            ],
        ),
        ExtractedEvent(
            title="Scarlet Robotics Club: Build Night",
            event_type="club",
            start_date=tuesday,
            start_time=time(20, 0),
            end_time=time(22, 0),
            location="Richard Weeks Hall of Engineering, Makerspace",
            address="Busch Campus, Rutgers University, Piscataway, NJ",
            recurrence=RecurrenceRule(freq="WEEKLY", days_of_week=["TU"]),
            notes="Weekly build nights for the competition robot. Bring a laptop.",
            confidence=0.62,
            assumptions=["8-10 read as PM"],
            ambiguity_flags=["The flyer never says when the weekly build nights end"],
        ),
    ]


class _DemoSignIn:
    """Stands in for BrowserSignIn: "succeeds" after a short pause."""

    auth_url = None
    browser_opened = True

    def __init__(self, delay: float, fail: bool) -> None:
        self._delay = delay
        self._fail = fail
        self._cancelled = threading.Event()

    def open_in_browser(self) -> bool:
        return True

    def cancel(self) -> None:
        self._cancelled.set()

    def wait(self) -> None:
        if self._cancelled.wait(self._delay):
            raise SignInCancelled("Sign-in cancelled.")
        if self._fail:
            raise SignInError("Google stopped the sign-in (demo). Try again.")


class DemoEngine:
    """No network, no accounts. `scenario` picks what goes wrong, if anything:

    default        everything works
    empty          the read finds no events
    read_fail      the read fails as if offline
    read_bad_key   the read fails as if the key were rejected
    signin_fail    Google sign-in fails
    add_partial    the second event fails to save
    add_auth       saving finds the sign-in has expired
    """

    name = "demo"
    CALENDAR_NAME = "you@example.com"

    def __init__(
        self,
        data_dir: Path,
        delay: float = 1.2,
        scenario: str = "default",
        today: date | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.delay = delay
        self.scenario = scenario
        self.today = today  # None: the real date. Fixed for reproducible screenshots.
        self.added: list[ExtractedEvent] = []
        self.reads: list[Path] = []

    @property
    def _token_path(self) -> Path:
        return self.data_dir / "token.json"

    @property
    def _credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    def check_key(self, key: str) -> KeyCheck:
        time_module.sleep(min(self.delay, 0.4))
        if "bad" in key:
            return KeyCheck(False, "Anthropic doesn't recognise this key (demo).")
        return KeyCheck(True)

    def read(self, image_path: Path, api_key: str) -> ExtractionResult:
        self.reads.append(Path(image_path))
        time_module.sleep(self.delay)
        if self.scenario == "read_fail":
            raise ClaudeAPIError(
                "Couldn't reach Anthropic -- check your internet connection.", kind="connection"
            )
        if self.scenario == "read_bad_key":
            raise ClaudeAPIError("Anthropic rejected your API key.", kind="auth")
        if self.scenario == "empty":
            return ExtractionResult(events=[])
        return ExtractionResult(events=demo_events(self.today))

    def has_credentials(self) -> bool:
        return self._credentials_path.exists()

    def save_credentials(self, raw: bytes) -> None:
        google_signin.validate_client_file(raw)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._credentials_path.write_bytes(raw)

    def has_saved_sign_in(self) -> bool:
        return self._token_path.exists()

    def connect(self) -> str:
        if not self._token_path.exists():
            raise AuthError("You're not signed in to Google yet.")
        return self.CALENDAR_NAME

    def begin_sign_in(self, open_browser: Callable[[str], Any] | None = None) -> _DemoSignIn:
        if not self.has_credentials():
            raise SignInError("Schedru doesn't have your Google credentials file yet.")
        return _DemoSignIn(self.delay, fail=self.scenario == "signin_fail")

    def complete_sign_in(self, attempt: _DemoSignIn) -> str:
        attempt.wait()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps({"demo": True}))
        return self.connect()

    def sign_out(self) -> None:
        try:
            self._token_path.unlink()
        except FileNotFoundError:
            pass

    def add(self, events: list[ExtractedEvent]) -> list[WriteOutcome]:
        time_module.sleep(min(self.delay, 0.6))
        if self.scenario == "add_auth":
            raise AuthError("Your Google sign-in has expired.")
        outcomes = []
        for index, event in enumerate(events):
            if self.scenario == "add_partial" and index == 1:
                outcomes.append(WriteOutcome(event=event, error="Forbidden (HTTP 403)"))
                continue
            self.added.append(event)
            outcomes.append(
                WriteOutcome(
                    event=event,
                    link=f"https://www.google.com/calendar/event?eid=demo-{len(self.added)}",
                )
            )
        return outcomes


DEMO_TODAY_ENV = "SCHEDRU_DEMO_TODAY"  # YYYY-MM-DD: what "today" is to the demo engine


def make_engine(data_dir: Path, kind: str | None = None) -> Any:
    if (kind or "").strip().lower() == "demo":
        import os

        pinned = os.environ.get(DEMO_TODAY_ENV, "").strip()
        return DemoEngine(data_dir, today=date.fromisoformat(pinned) if pinned else None)
    return RealEngine(data_dir)


__all__ = [
    "AuthError",
    "ClaudeAPIError",
    "DemoEngine",
    "GoogleConnectionError",
    "KeyCheck",
    "RealEngine",
    "demo_events",
    "make_engine",
]
