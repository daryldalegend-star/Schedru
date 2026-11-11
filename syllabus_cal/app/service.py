"""What the app can do, as plain method calls.

One Service holds the app's state: whether there's an API key, where the
Google sign-in stands, and the screenshot currently being read or reviewed.
The HTTP server is a thin translation of web requests into these calls, the
window's shortcut calls `capture()` directly, and the tests drive it with no
server at all.

The rule this file enforces: **nothing reaches Google Calendar except through
`add_events`, and `add_events` only writes the events it is handed.** There is
no code path from "Claude found something" to "it's on the calendar" that
doesn't pass through a person pressing Add.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ..calendar_client import AuthError, GoogleConnectionError
from ..schema import ExtractedEvent
from ..vision import ClaudeAPIError, ExtractionError, ImageError
from . import APP_NAME, __version__
from .capture import NoWindow, ScreenCapture, WindowControl
from .describe import EventEditError, describe_when, event_from_json, event_to_json
from .google_signin import ClientFileError, SignInCancelled, SignInError
from .settings import KeyFormatError, Settings, clean_key, key_hint

MAX_UPLOAD_BYTES = 30 * 1024 * 1024

# Pillow format name -> the extension the engine expects for it.
_FORMAT_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "HEIF": ".heic"}

# Links the window may ask the system browser to open. Everything the UI links
# to is on this list; anything else is refused rather than trusted.
_OPENABLE_HOSTS = frozenset(
    {
        "calendar.google.com",
        "www.google.com",
        "console.cloud.google.com",
        "console.anthropic.com",
        "platform.claude.com",
        "support.google.com",
        "support.apple.com",
        "github.com",
    }
)

NOT_AN_IMAGE = (
    "Schedru couldn't open that as an image. It reads screenshots and photos: PNG, JPG, "
    "HEIC and WebP."
)
PDF_HINT = (
    "That's a PDF. Open it, then take a screenshot of the part with the dates and "
    "Schedru will read that."
)


class ApiError(Exception):
    """A request that can't be carried out. `status` is the HTTP status to
    answer with and `code` a stable name the UI can branch on."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


class Hub:
    """A numbered log of "something changed" notes that the window long-polls."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._seq = 0
        self._log: deque[dict[str, Any]] = deque(maxlen=200)

    @property
    def seq(self) -> int:
        with self._changed:
            return self._seq

    def publish(self, kind: str, **data: Any) -> int:
        with self._changed:
            self._seq += 1
            self._log.append({"seq": self._seq, "kind": kind, **data})
            self._changed.notify_all()
            return self._seq

    def wait(self, since: int, timeout: float) -> list[dict[str, Any]]:
        """Notes newer than `since`, waiting up to `timeout` for the first one."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._seq <= since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._changed.wait(remaining)
            return [note for note in self._log if note["seq"] > since]


@dataclass
class Job:
    """One screenshot on its way to the calendar."""

    id: str
    source: str  # "capture" or "upload"
    image: Path
    state: str = "reading"  # reading -> review -> adding -> done, or failed
    events: dict[str, ExtractedEvent] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    added: set[str] = field(default_factory=set)
    started: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "state": self.state,
            "image": f"/api/job/{self.id}/image",
            "events": [
                {**event_to_json(event, event_id), "added": event_id in self.added}
                for event_id, event in self.events.items()
            ],
            "error": self.error,
            "results": list(self.results),
        }


class Service:
    def __init__(
        self,
        engine: Any,
        settings: Settings,
        *,
        hub: Hub | None = None,
        capture_tool: ScreenCapture | None = None,
        window: WindowControl | None = None,
        workdir: Path | None = None,
        open_url: Callable[[str], Any] = webbrowser.open,
        shell: str = "browser",
        platform: str | None = None,
    ) -> None:
        import sys

        self.engine = engine
        self.settings = settings
        self.hub = hub or Hub()
        self.capture_tool = capture_tool or ScreenCapture()
        self.window: WindowControl = window or NoWindow()
        self.shell = shell
        self.platform = platform or sys.platform
        self.shortcut: str | None = None  # set by the shell once the hotkey registers
        self._open_url = open_url

        self._owns_workdir = workdir is None
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="schedru-"))
        self.workdir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._counter = 0
        self._job: Job | None = None
        self._capturing = False
        self._google_state = "checking"
        self._google_message: str | None = None
        self._calendar: str | None = None
        self._attempt: Any = None

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Check the saved Google sign-in in the background."""
        threading.Thread(target=self.refresh_google, name="schedru-google", daemon=True).start()

    def close(self) -> None:
        with self._lock:
            attempt, self._attempt = self._attempt, None
        if attempt is not None:
            attempt.cancel()
        if self._owns_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    # ---- status --------------------------------------------------------

    def ready(self) -> bool:
        """True when a screenshot could be read and added right now."""
        with self._lock:
            return self._google_state == "connected" and self.settings.api_key() is not None

    def status(self) -> dict[str, Any]:
        key = self.settings.api_key()
        with self._lock:
            return {
                "app": {
                    "name": APP_NAME,
                    "version": __version__,
                    "engine": getattr(self.engine, "name", "real"),
                    "platform": self.platform,
                    "shell": self.shell,
                },
                "key": {"present": key is not None, "hint": key_hint(key) if key else None},
                "google": {
                    "state": self._google_state,
                    "has_credentials": self.engine.has_credentials(),
                    "calendar": self._calendar,
                    "message": self._google_message,
                    "browser_opened": bool(getattr(self._attempt, "browser_opened", True)),
                },
                "tutorial_seen": bool(self.settings.get("tutorial_seen", False)),
                "capture": {
                    "available": self.capture_tool.available,
                    "shortcut": self.shortcut,
                    "busy": self._capturing,
                },
                "job": self._job.to_json() if self._job else None,
                "seq": self.hub.seq,
            }

    def mark_tutorial_seen(self) -> None:
        self.settings.set("tutorial_seen", True)
        self.hub.publish("settings")

    # ---- API key -------------------------------------------------------

    def save_key(self, raw: str) -> dict[str, Any]:
        try:
            key = clean_key(raw)
        except KeyFormatError as exc:
            raise ApiError(400, "bad_key", str(exc)) from exc

        verdict = self.engine.check_key(key)
        if not verdict.ok:
            raise ApiError(400, "bad_key", verdict.message or "That key didn't work.")

        self.settings.save_api_key(key)
        self.hub.publish("key")
        return {"hint": key_hint(key), "note": verdict.message}

    # ---- Google --------------------------------------------------------

    def _set_google(self, state: str, message: str | None = None, calendar: str | None = None) -> None:
        with self._lock:
            self._google_state = state
            self._google_message = message
            self._calendar = calendar
        self.hub.publish("google", state=state)

    def refresh_google(self) -> None:
        """Find out whether the saved sign-in still works."""
        with self._lock:
            if self._google_state == "signing_in":
                return
            self._google_state = "checking"
        had_sign_in = self.engine.has_saved_sign_in()
        try:
            name = self.engine.connect()
        except AuthError as exc:
            # Only worth a message if there was a sign-in to lose: "you're not
            # signed in yet" on a first launch is not news.
            self._set_google("signed_out", str(exc) if had_sign_in else None)
        except GoogleConnectionError as exc:
            self._set_google("error", str(exc))
        except Exception as exc:  # never leave the window stuck on "checking"
            self._set_google("error", f"Couldn't check your Google sign-in ({type(exc).__name__}).")
        else:
            self._set_google("connected", calendar=name)

    def save_credentials(self, raw: bytes) -> None:
        try:
            self.engine.save_credentials(raw)
        except ClientFileError as exc:
            raise ApiError(400, "bad_credentials", str(exc)) from exc
        except OSError as exc:
            raise ApiError(500, "save_failed", f"Couldn't save that file ({exc.strerror or exc}).") from exc
        self.hub.publish("google")

    def start_sign_in(self) -> dict[str, Any]:
        with self._lock:
            if self._google_state == "signing_in" and self._attempt is not None:
                # Pressing the button again means "the browser didn't come up".
                self._attempt.open_in_browser()
                return {"state": "signing_in"}
            if not self.engine.has_credentials():
                raise ApiError(
                    409, "no_credentials", "Schedru needs your Google credentials file first."
                )
            try:
                attempt = self.engine.begin_sign_in()
            except SignInError as exc:
                raise ApiError(400, "signin_failed", str(exc)) from exc
            self._attempt = attempt
            self._google_state = "signing_in"
            self._google_message = None

        self.hub.publish("google", state="signing_in")
        threading.Thread(
            target=self._finish_sign_in, args=(attempt,), name="schedru-signin", daemon=True
        ).start()
        return {"state": "signing_in"}

    def _finish_sign_in(self, attempt: Any) -> None:
        try:
            name = self.engine.complete_sign_in(attempt)
        except SignInCancelled as exc:
            # Reached with this attempt still current only when the person
            # backed out on Google's page, which is worth saying -- otherwise
            # the app just seems to have done nothing. Backing out in the app
            # replaces the attempt first, and returns quietly below.
            outcome = ("signed_out", str(exc), None)
        except (SignInError, AuthError, GoogleConnectionError) as exc:
            outcome = ("signed_out", str(exc), None)
        except Exception as exc:
            outcome = ("signed_out", f"Sign-in failed unexpectedly ({type(exc).__name__}).", None)
        else:
            outcome = ("connected", None, name)

        stale: Any = None
        with self._lock:
            if self._attempt is attempt:
                self._attempt = None
            elif outcome[0] != "connected":
                return  # superseded by a cancel that already updated the state
            else:
                # An earlier browser tab finished after a newer attempt began.
                # The sign-in is good, so the newer attempt has nothing to wait for.
                stale, self._attempt = self._attempt, None
        if stale is not None:
            stale.cancel()
        self._set_google(*outcome)
        # The person is in their browser at this point; bring them back.
        self.window.show()

    def cancel_sign_in(self) -> None:
        with self._lock:
            attempt, self._attempt = self._attempt, None
            if self._google_state != "signing_in":
                return
        if attempt is not None:
            attempt.cancel()
        self._set_google("signed_out")

    def sign_out(self) -> None:
        self.cancel_sign_in()
        self.engine.sign_out()
        self._discard_job()
        self._set_google("signed_out")

    # ---- reading -------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}{self._counter}"

    def _discard_job(self) -> None:
        with self._lock:
            job, self._job = self._job, None
        if job is not None:
            try:
                job.image.unlink()
            except OSError:
                pass

    def clear_job(self, job_id: str) -> None:
        """Forget the current screenshot (after adding, or to start over)."""
        with self._lock:
            if self._job is None or self._job.id != job_id:
                return
            if self._job.state == "adding":
                raise ApiError(409, "busy", "Still adding events. Give it a moment.")
        self._discard_job()
        self.hub.publish("job")

    def accept_upload(self, data: bytes, filename: str | None = None) -> Job:
        """Start reading an image that was dropped, pasted or chosen."""
        if not data:
            raise ApiError(400, "empty", "That file is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "too_large", "That image is too large (over 30 MB).")
        if data[:5] == b"%PDF-":
            raise ApiError(415, "pdf", PDF_HINT)

        from PIL import Image

        from .. import vision  # noqa: F401  (registers the HEIC opener)

        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = (image.format or "").upper()
                extension = _FORMAT_EXTENSIONS.get(image_format)
                if extension is None:
                    # TIFF from the clipboard, GIF, BMP: fine once re-saved.
                    converted = io.BytesIO()
                    image.convert("RGB").save(converted, format="PNG")
                    data, extension = converted.getvalue(), ".png"
        except Exception as exc:
            raise ApiError(415, "not_an_image", NOT_AN_IMAGE) from exc

        path = self.workdir / f"{self._next_id('upload-')}{extension}"
        path.write_bytes(data)
        return self.start_read(path, source="upload")

    def start_read(self, image: Path, source: str) -> Job:
        key = self.settings.api_key()
        if key is None:
            raise ApiError(409, "no_key", "Add your Claude key first.")
        with self._lock:
            if self._google_state != "connected":
                raise ApiError(409, "not_signed_in", "Sign in with Google first.")
            if self._job is not None and self._job.state == "adding":
                raise ApiError(409, "busy", "Still adding events. Give it a moment.")

        self._discard_job()
        job = Job(id=self._next_id("j"), source=source, image=Path(image))
        with self._lock:
            self._job = job
        self.hub.publish("job", id=job.id, state=job.state)
        threading.Thread(
            target=self._read, args=(job, key), name="schedru-read", daemon=True
        ).start()
        return job

    def _read(self, job: Job, key: str) -> None:
        error: dict[str, Any] | None = None
        events: list[ExtractedEvent] = []
        try:
            events = list(self.engine.read(job.image, key).events)
        except ClaudeAPIError as exc:
            error = {"kind": exc.kind, "message": _app_wording(exc)}
        except ImageError as exc:
            error = {"kind": "image", "message": str(exc)}
        except ExtractionError as exc:
            # The raw model output that follows the first line helps nobody here.
            error = {"kind": "extraction", "message": str(exc).split("\n")[0]}
        except Exception as exc:
            error = {"kind": "other", "message": f"Something went wrong ({type(exc).__name__})."}

        with self._lock:
            if self._job is not job:
                return  # replaced or cancelled while Claude was thinking
            if error is not None:
                job.state, job.error = "failed", error
            else:
                job.events = {f"e{index}": event for index, event in enumerate(events, start=1)}
                job.state = "review"
        self.hub.publish("job", id=job.id, state=job.state)

    def job(self, job_id: str) -> Job:
        with self._lock:
            if self._job is None or self._job.id != job_id:
                raise ApiError(404, "no_job", "That screenshot is no longer open.")
            return self._job

    # ---- adding --------------------------------------------------------

    def add_events(self, job_id: str, chosen: list[dict[str, Any]]) -> dict[str, Any]:
        """Write the events the person ticked, as they edited them.

        Validates every one before writing any, so a typo in the third event
        doesn't leave the first two on the calendar and the rest not.
        """
        if not isinstance(chosen, list) or not chosen:
            raise ApiError(400, "nothing_chosen", "Tick at least one event to add.")

        with self._lock:
            job = self.job(job_id)
            if job.state == "adding":
                raise ApiError(409, "busy", "Already adding these.")
            if job.state not in ("review", "done"):
                raise ApiError(409, "not_ready", "This screenshot isn't ready to add yet.")
            if self._google_state != "connected":
                raise ApiError(401, "not_signed_in", "Sign in with Google first.")

            to_write: list[tuple[str, ExtractedEvent]] = []
            seen: set[str] = set()
            for entry in chosen:
                event_id = entry.get("id") if isinstance(entry, dict) else None
                if event_id not in job.events:
                    raise ApiError(400, "unknown_event", "That event isn't part of this screenshot.")
                if event_id in seen or event_id in job.added:
                    continue  # a double click must never add something twice
                seen.add(event_id)
                try:
                    edited = event_from_json(job.events[event_id], entry)
                except EventEditError as exc:
                    raise ApiError(
                        400, "invalid_event", str(exc), event_id=event_id, field=exc.field
                    ) from exc
                to_write.append((event_id, edited))

            if not to_write:
                raise ApiError(409, "already_added", "Those events are already on your calendar.")
            previous_state = job.state
            job.state = "adding"

        self.hub.publish("job", id=job.id, state="adding")
        try:
            outcomes = self.engine.add([event for _, event in to_write])
        except AuthError as exc:
            with self._lock:
                job.state = previous_state
            self._set_google("signed_out", str(exc))
            raise ApiError(401, "not_signed_in", str(exc)) from exc
        except Exception as exc:
            with self._lock:
                job.state = previous_state
            self.hub.publish("job", id=job.id, state=previous_state)
            message = str(exc) if isinstance(exc, GoogleConnectionError) else (
                "Couldn't reach Google Calendar. Check your internet connection and try again."
            )
            raise ApiError(502, "google_unreachable", message) from exc

        with self._lock:
            for (event_id, edited), outcome in zip(to_write, outcomes):
                job.events[event_id] = edited  # keep the edits if one has to be retried
                if outcome.ok:
                    job.added.add(event_id)
                job.results = [r for r in job.results if r["id"] != event_id]
                job.results.append(
                    {
                        "id": event_id,
                        "title": edited.title,
                        "when_text": describe_when(edited),
                        "ok": outcome.ok,
                        "link": outcome.link,
                        "error": outcome.error,
                    }
                )
            job.state = "done"
            result = job.to_json()
        self.hub.publish("job", id=job.id, state="done")
        return result

    # ---- capture -------------------------------------------------------

    def capture(self, origin: str = "button", force: bool = False) -> dict[str, Any]:
        """Start the on-screen region picker. Returns at once; the screenshot
        (or its cancellation) arrives as a hub note."""
        if not self.capture_tool.available:
            raise ApiError(400, "capture_unavailable", "Screen capture isn't available here.")

        with self._lock:
            if self._capturing:
                return {"started": False, "reason": "busy"}
            if self._job is not None and self._job.state == "adding":
                return {"started": False, "reason": "busy"}
            if not self.ready():
                reason = "setup"
            elif not force and self.capture_tool.permission() == "denied":
                reason = "permission"
            else:
                reason = None
                self._capturing = True

        if reason == "permission":
            self.capture_tool.request_permission()
        if reason is not None:
            # From the shortcut the window may be buried; say why nothing happened.
            self.window.show()
            self.hub.publish("capture", outcome=reason, origin=origin)
            return {"started": False, "reason": reason}

        self.hub.publish("capture", outcome="started", origin=origin)
        threading.Thread(
            target=self._capture, args=(origin,), name="schedru-capture", daemon=True
        ).start()
        return {"started": True}

    def _capture(self, origin: str) -> None:
        hidden = False
        image: Path | None = None
        try:
            # If Schedru is in front, it's covering what the person wants.
            if self.window.is_frontmost():
                self.window.hide()
                hidden = True
                time.sleep(0.35)  # let the window finish disappearing
            image = self.capture_tool.capture(self.workdir / f"{self._next_id('capture-')}.png")
        except Exception:
            image = None  # fall through: the window must come back either way
        finally:
            with self._lock:
                self._capturing = False

        if image is None:
            if hidden:
                self.window.show()
            self.hub.publish("capture", outcome="cancelled", origin=origin)
            return

        try:
            self.start_read(image, source="capture")
        except ApiError as exc:
            self.hub.publish("capture", outcome="failed", origin=origin, message=exc.message)
        self.window.show()

    def open_capture_settings(self) -> None:
        self.capture_tool.open_settings()

    # ---- misc ----------------------------------------------------------

    def open_external(self, url: str) -> None:
        parts = urlsplit(url or "")
        if parts.scheme != "https" or parts.hostname not in _OPENABLE_HOSTS:
            raise ApiError(400, "bad_link", "Schedru won't open that link.")
        self._open_url(url)


def _app_wording(exc: ClaudeAPIError) -> str:
    """The engine's messages were written for the terminal ("paste it into
    .env"). Say the same thing in the app's terms."""
    if exc.kind == "auth":
        return (
            "Anthropic rejected your Claude key. It may have expired or been deleted. "
            "Add a new one to keep going."
        )
    return str(exc).replace(" -- ", ". ").replace("check your", "Check your", 1)
