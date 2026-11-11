"""Use the real app on a real Mac, the way a person would, and check it works.

    python tools/mac_e2e.py --source                 run from this folder
    python tools/mac_e2e.py --app dist/Schedru.app   the packaged app

It starts Schedru with the demo engine (no network, no accounts) in a
throwaway data folder, then does what a person does: clicks through setup,
watches the tutorial, presses Cmd-Shift-2 from another app, drags a region,
presses the Capture button, cancels with Esc, pastes an image, closes and
reopens the window, quits. The keyboard and mouse events are real ones, posted
to the system, which is why this only runs where that is allowed: the macOS
machines GitHub provides.

Each check is independent and reports its own result; the exit status is 1 if
any failed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOD_KEY = "sk-ant-api03-" + "A1b2C3d4" * 10 + "-hAAA"
CLIENT_FILE = json.dumps(
    {
        "installed": {
            "client_id": "1234-abc.apps.googleusercontent.com",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "not-a-real-secret",
        }
    }
)

KEY_2, KEY_V, KEY_W, KEY_Q, KEY_ESC = 0x13, 0x09, 0x0D, 0x0C, 0x35

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    RESULTS.append((name, bool(ok), str(detail)))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail != "" else ""), flush=True)
    return bool(ok)


def note(name: str, value: object) -> None:
    print(f"  note  {name}: {value}", flush=True)


def step(title: str) -> None:
    print(f"\n{title}", flush=True)


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return struct.unpack(">II", data[16:24])


def make_png(width: int = 320, height: int = 200) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 16, 46)).save(buffer, format="PNG")
    return buffer.getvalue()


# ---- real input ---------------------------------------------------------------


def post_key(code: int, command: bool = False, shift: bool = False) -> None:
    import Quartz

    flags = 0
    if command:
        flags |= Quartz.kCGEventFlagMaskCommand
    if shift:
        flags |= Quartz.kCGEventFlagMaskShift
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.06)


def drag(x1: float, y1: float, x2: float, y2: float) -> None:
    import Quartz

    def mouse(kind: int, x: float, y: float) -> None:
        event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.03)

    mouse(Quartz.kCGEventMouseMoved, x1, y1)
    mouse(Quartz.kCGEventLeftMouseDown, x1, y1)
    for index in range(1, 11):
        mouse(
            Quartz.kCGEventLeftMouseDragged,
            x1 + (x2 - x1) * index / 10,
            y1 + (y2 - y1) * index / 10,
        )
    mouse(Quartz.kCGEventLeftMouseUp, x2, y2)


def copy_png_to_clipboard(data: bytes) -> bool:
    import AppKit
    import Foundation

    board = AppKit.NSPasteboard.generalPasteboard()
    board.clearContents()
    payload = Foundation.NSData.dataWithBytes_length_(data, len(data))
    return bool(board.setData_forType_(payload, AppKit.NSPasteboardTypePNG))


def activate_finder() -> None:
    subprocess.run(["osascript", "-e", 'tell application "Finder" to activate'], check=False, timeout=20)
    time.sleep(1.0)


# ---- the app ------------------------------------------------------------------


class Schedru:
    def __init__(self, work: Path, engine: str = "demo") -> None:
        self.work = work
        self.data = work / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.hook = work / "hook.json"
        self.env = {
            "SCHEDRU_DATA_DIR": str(self.data),
            "SCHEDRU_ENGINE": engine,
            "SCHEDRU_DEBUG_HOOKS": "1",
            "SCHEDRU_TEST_HOOK": str(self.hook),
        }
        self.process: subprocess.Popen | None = None
        self.url = ""
        self.token = ""
        self.pid = 0

    def launch(self, command: list[str]) -> None:
        self.hook.unlink(missing_ok=True)
        self.log = open(self.work / "app.log", "ab")
        self.process = subprocess.Popen(
            command, cwd=ROOT, env={**os.environ, **self.env}, stdout=self.log, stderr=self.log
        )

    def launch_through_finder(self, app: Path) -> None:
        """Start the app the way a double-click does (LaunchServices)."""
        self.hook.unlink(missing_ok=True)
        command = ["open", "-n", str(app)]
        for name, value in self.env.items():
            command += ["--env", f"{name}={value}"]
        subprocess.run(command, check=True, timeout=30)

    def wait_until_up(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.hook.exists():
                try:
                    info = json.loads(self.hook.read_text())
                except ValueError:
                    info = None
                if info:
                    self.url, self.token, self.pid = info["url"], info["token"], info["pid"]
                    return True
            if self.process is not None and self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return False

    def alive(self) -> bool:
        if self.process is not None:
            return self.process.poll() is None
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True

    def call(self, path: str, body: object = None, raw: bytes | None = None, timeout: float = 30.0):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(
            self.url + path.lstrip("/"),
            data=data,
            method="GET" if data is None else "POST",
            headers={"X-Schedru-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            payload = error.read()
        try:
            return json.loads(payload)
        except ValueError:
            return payload

    def status(self) -> dict:
        return self.call("/api/status")

    def js(self, expression: str):
        answer = self.call("/api/debug/eval", {"js": expression})
        if isinstance(answer, dict) and "error" in answer:
            return {"__error__": answer["error"].get("message")}
        return answer.get("result") if isinstance(answer, dict) else answer

    def route(self):
        return self.js("window.__schedru && window.__schedru.route()")

    def window(self) -> dict:
        answer = self.call("/api/debug/window", {})
        return answer.get("result", {}) if isinstance(answer, dict) else {}

    def click(self, selector: str) -> bool:
        return bool(
            self.js(
                f"(function () {{ var el = document.querySelector({json.dumps(selector)});"
                " if (!el) return false; el.click(); return true; })()"
            )
        )

    def text(self, selector: str):
        return self.js(
            f"(function () {{ var el = document.querySelector({json.dumps(selector)});"
            " return el ? el.textContent : null; })()"
        )

    def refresh_page(self) -> None:
        self.js("(window.dispatchEvent(new Event('focus')), true)")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        elif self.process is None and self.pid:
            try:
                os.kill(self.pid, 15)
            except OSError:
                pass


def wait_for(predicate, timeout: float = 15.0, every: float = 0.2):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception as error:  # the app may be mid-restart; keep polling
            value = None
            last = error  # noqa: F841
        if value:
            return value
        time.sleep(every)
    return value


def job_state(app: Schedru):
    job = app.status().get("job")
    return job["state"] if job else None


def capture_by_dragging(app: Schedru, name: str, expect_source: str = "capture") -> bool:
    """With the region picker up, drag a box and check Schedru reads the result."""
    time.sleep(1.6)  # the crosshair takes a moment to appear
    drag(260, 260, 660, 510)
    arrived = wait_for(lambda: (app.status().get("job") or {}).get("source") == expect_source, 12)
    if not check(f"{name}: dragging a region starts a read", arrived, app.status().get("capture")):
        post_key(KEY_ESC)  # leave no crosshair behind for the next check
        return False
    job = app.status()["job"]
    image = app.call(f"{job['image']}?t={app.token}")
    width, height = png_size(image) if isinstance(image, bytes) else (0, 0)
    check(f"{name}: the screenshot is the dragged region", width >= 300 and height >= 180, f"{width}x{height}")
    reviewed = wait_for(lambda: job_state(app) == "review", 15)
    check(f"{name}: the read finishes in review", reviewed, job_state(app))
    shown = wait_for(lambda: app.window().get("visible") and app.window().get("active"), 8)
    check(f"{name}: the window comes forward with the result", shown, app.window())
    check(f"{name}: the page shows the review", wait_for(lambda: app.route() == "review", 8), app.route())
    return True


# ---- the walk-through ---------------------------------------------------------


def walk_through(app: Schedru, packaged: bool) -> None:
    step("Starting")
    if not check("the app starts and reports its address", app.wait_until_up(90)):
        return
    status = app.status()
    check("it opened a real window, not a browser tab", status["app"]["shell"] == "window", status["app"])
    check("the page loads in the window", wait_for(lambda: app.route() == "welcome", 30), app.route())
    check("the name is on the first screen", app.text(".wordmark") == "Schedru", app.text(".wordmark"))
    check("the tagline is under it", app.text(".tagline") == "Your Schedule Optimizer", app.text(".tagline"))
    check("the display font loaded", app.js("document.fonts.check('600 40px Fraunces')") is True)
    check("the window is visible and in front", wait_for(lambda: app.window().get("visible") and app.window().get("active"), 10), app.window())
    ready = wait_for(lambda: app.status()["capture"]["shortcut"], 15)
    check("the global shortcut registered", ready == "⌘⇧2", app.status()["capture"])
    check("screen capture is available", app.status()["capture"]["available"] is True)
    identity = app.call("/api/debug/identity", {}).get("result", {})
    note("identity", identity)
    if packaged:
        check("macOS knows it as Schedru", identity.get("name") == "Schedru", identity.get("name"))
        check("it has its own bundle id", identity.get("bundle_id") == "com.schedru.app", identity.get("bundle_id"))

    step("Setting up")
    check("Continue with Google asks for the credentials file first", app.click("#google-button") and wait_for(lambda: app.route() == "google-setup", 8), app.route())
    dropped = app.js(
        "(function () { var data = new DataTransfer();"
        f" data.items.add(new File([{json.dumps(CLIENT_FILE)}], 'credentials.json', {{type: 'application/json'}}));"
        " window.dispatchEvent(new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true}));"
        " return true; })()"
    )
    check("dropping the credentials file is accepted", dropped is True, dropped)
    check("...and leads through sign-in to the tutorial", wait_for(lambda: app.route() == "tutorial", 20), app.route())
    check("the account shows as connected", app.status()["google"]["state"] == "connected", app.status()["google"])

    step("The tutorial video")
    video = "document.querySelector('.player video')"
    playing = wait_for(lambda: (app.js(f"{video} && {video}.currentTime") or 0) > 0.5, 20)
    facts = app.js(
        f"(function () {{ var v = {video}; if (!v) return null; return {{"
        " width: v.videoWidth, height: v.videoHeight, duration: v.duration, time: v.currentTime,"
        " paused: v.paused, ready: v.readyState, error: v.error ? v.error.code : null,"
        " hidden: v.parentElement.hidden }; })()"
    )
    note("video", facts)
    check("the tutorial video plays by itself", bool(playing), facts)
    if isinstance(facts, dict):
        check("it is the full-size video", facts.get("width") == 1920 and facts.get("height") == 1200, facts)
        check("it is the whole 38 seconds", 38 <= (facts.get("duration") or 0) <= 39, facts.get("duration"))
        check("it has no playback error", facts.get("error") is None and not facts.get("hidden"), facts)
    check("Skip moves on to the Claude key", app.click("#tutorial-skip") and wait_for(lambda: app.route() == "key", 8), app.route())
    check("the tutorial is remembered as seen", app.status()["tutorial_seen"] is True)

    app.js(f"(document.getElementById('key-input').value = {json.dumps(GOOD_KEY)}, true)")
    check("saving a key leads to the capture screen", app.click("#key-save") and wait_for(lambda: app.route() == "home", 12), app.route())
    check("the Capture button is offered with its shortcut", "Capture from screen" in (app.text("#capture-button") or ""), app.text("#capture-button"))

    step("Cmd-Shift-2 from another app")
    activate_finder()
    check("Schedru is in the background", wait_for(lambda: not app.window().get("active"), 6), app.window())
    post_key(KEY_2, command=True, shift=True)
    busy = wait_for(lambda: app.status()["capture"]["busy"], 5)
    check("the shortcut starts a capture", bool(busy), app.status()["capture"])
    if capture_by_dragging(app, "shortcut"):
        check("the button counts the ticked event", app.text("#add-button") == "Add 1 event to Google Calendar", app.text("#add-button"))
        check("nothing was added by reading", all(not e["added"] for e in app.status()["job"]["events"]))
        check("pressing Add finishes on the done screen", app.click("#add-button") and wait_for(lambda: app.route() == "done", 12), app.route())
        results = app.status()["job"]["results"]
        check("exactly the ticked event was added", len(results) == 1 and results[0]["ok"], results)
        check("Capture another returns to the start", app.click("#capture-another") and wait_for(lambda: app.route() == "home", 8), app.route())

    step("The Capture button")
    app.call("/api/debug/show", {})
    wait_for(lambda: app.window().get("active"), 6)
    check("pressing it is accepted", app.click("#capture-button"))
    check("the window steps aside", wait_for(lambda: app.window().get("visible") is False, 6), app.window())
    if capture_by_dragging(app, "button"):
        app.click("#start-over")
        check("Start over returns to the start", wait_for(lambda: app.route() == "home", 8), app.route())

    step("Cancelling with Esc")
    app.call("/api/debug/show", {})
    wait_for(lambda: app.window().get("active"), 6)
    app.click("#capture-button")
    wait_for(lambda: app.window().get("visible") is False, 6)
    time.sleep(1.6)
    post_key(KEY_ESC)
    check("the window comes back", wait_for(lambda: app.window().get("visible"), 10), app.window())
    check("no read was started", app.status().get("job") is None, app.status().get("job"))
    check("it says the capture was cancelled", wait_for(lambda: app.text(".toast") == "Capture cancelled", 5), app.text(".toast"))
    check("capturing is possible again afterwards", wait_for(lambda: not app.status()["capture"]["busy"], 6))

    step("Pasting a screenshot")
    check("an image is on the clipboard", copy_png_to_clipboard(make_png()))
    app.call("/api/debug/show", {})
    wait_for(lambda: app.window().get("active") and app.window().get("key"), 6)
    app.call("/api/debug/focus", {})
    app.js("(document.activeElement && document.activeElement.blur(), true)")
    time.sleep(0.4)
    post_key(KEY_V, command=True)
    pasted = wait_for(lambda: (app.status().get("job") or {}).get("source") == "upload", 10)
    check("Cmd-V starts a read", bool(pasted), app.status().get("job"))
    if pasted:
        check("...which ends in review", wait_for(lambda: app.route() == "review", 12), app.route())
        app.click("#start-over")
        wait_for(lambda: app.route() == "home", 8)

    step("Replaying the tutorial from the menu")
    app.click("#menu-button")
    opened = app.js(
        "(function () { var items = Array.from(document.querySelectorAll('#menu button'));"
        " var item = items.find(function (b) { return b.textContent.indexOf('Watch the tutorial') === 0; });"
        " if (!item) return false; item.click(); return true; })()"
    )
    check("the menu has Watch the tutorial", opened is True, opened)
    check("it opens the tutorial again", wait_for(lambda: app.route() == "tutorial", 8), app.route())
    app.click("#tutorial-done")
    check("Done returns to the capture screen", wait_for(lambda: app.route() == "home", 8), app.route())

    step("Closing the window keeps the shortcut alive")
    app.call("/api/debug/show", {})
    wait_for(lambda: app.window().get("active") and app.window().get("key"), 6)
    app.call("/api/debug/focus", {})
    time.sleep(0.3)
    post_key(KEY_W, command=True)
    check("Cmd-W hides the window", wait_for(lambda: app.window().get("visible") is False, 6), app.window())
    check("the app is still running", app.alive() and app.status()["app"]["name"] == "Schedru")
    activate_finder()
    post_key(KEY_2, command=True, shift=True)
    check("the shortcut still works with the window closed", bool(wait_for(lambda: app.status()["capture"]["busy"], 5)), app.status()["capture"])
    if capture_by_dragging(app, "closed window"):
        app.click("#start-over")
        wait_for(lambda: app.route() == "home", 8)


def quit_with_keyboard(app: Schedru) -> None:
    step("Quitting")
    app.call("/api/debug/show", {})
    wait_for(lambda: app.window().get("active") and app.window().get("key"), 6)
    post_key(KEY_Q, command=True)
    gone = wait_for(lambda: not app.alive(), 12)
    check("Cmd-Q quits the app", bool(gone))


def through_finder(app_path: Path, work: Path) -> None:
    """The packaged app, started the way a double-click starts it."""
    step("Opened like a double-click")
    app = Schedru(work)
    try:
        app.launch_through_finder(app_path)
        if not check("it starts when opened from Finder", app.wait_until_up(90)):
            return
        check("the page loads", wait_for(lambda: app.route() == "welcome", 30), app.route())
        identity = app.call("/api/debug/identity", {}).get("result", {})
        note("identity", identity)
        check("macOS knows it as Schedru", identity.get("name") == "Schedru", identity.get("name"))
        check("it runs from the bundle", str(identity.get("bundle_path", "")).endswith("Schedru.app"), identity.get("bundle_path"))

        # Past setup, by the back door: this pass is about the Mac, not the UI.
        app.call("/api/key", {"key": GOOD_KEY})
        app.call("/api/credentials", raw=CLIENT_FILE.encode())
        app.call("/api/google/signin", {})
        wait_for(lambda: app.status()["google"]["state"] == "connected", 15)
        app.call("/api/tutorial/seen", {})
        app.refresh_page()
        check("it reaches the capture screen", wait_for(lambda: app.route() == "home", 10), app.route())

        # Opened this way the app answers for itself to macOS's privacy system,
        # as it will on a real person's Mac, instead of inheriting this
        # script's permissions.
        app.call("/api/debug/show", {})
        wait_for(lambda: app.window().get("active"), 6)
        outcome = app.call("/api/capture", {})
        note("capture when opened from Finder", outcome)
        if outcome.get("started"):
            note("screen recording", "already allowed for the app on this machine")
            time.sleep(1.6)
            post_key(KEY_ESC)
            check("a cancelled capture brings the window back", wait_for(lambda: app.window().get("visible"), 10), app.window())
        else:
            check("without permission, it asks instead of capturing", outcome.get("reason") == "permission", outcome)
            app.refresh_page()
            check("...and explains what to do", wait_for(lambda: app.js("!!document.getElementById('permission-sheet')"), 6))
            app.click("#permission-later")

        step("Closing, then clicking the Dock icon")
        app.call("/api/debug/show", {})
        wait_for(lambda: app.window().get("active") and app.window().get("key"), 6)
        app.call("/api/debug/focus", {})
        time.sleep(0.3)
        post_key(KEY_W, command=True)
        check("Cmd-W hides the window", wait_for(lambda: app.window().get("visible") is False, 6), app.window())
        subprocess.run(["open", str(app_path)], check=False, timeout=30)  # what the Dock icon does
        check("opening it again shows the same window", wait_for(lambda: app.window().get("visible"), 10), app.window())
        running = subprocess.run(["pgrep", "-f", "Schedru.app/Contents/MacOS/Schedru"], capture_output=True, text=True)
        pids = [pid for pid in running.stdout.split() if pid]
        check("still one copy running", len(pids) == 1, pids)

        subprocess.run(["osascript", "-e", f'tell application "{app_path}" to quit'], check=False, timeout=30)
        check("Quit from the menu bar quits it", bool(wait_for(lambda: not app.alive(), 12)))
    finally:
        app.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", action="store_true")
    group.add_argument("--app", type=Path)
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("This drives the macOS app; it only runs on a Mac.")
        return 2

    work = Path(tempfile.mkdtemp(prefix="schedru-e2e-"))
    packaged = args.app is not None
    app = Schedru(work / "main")
    (work / "main").mkdir()
    try:
        if packaged:
            binary = args.app.resolve() / "Contents" / "MacOS" / "Schedru"
            app.launch([str(binary)])
        else:
            app.launch([sys.executable, "-W", "ignore", "-m", "syllabus_cal.app"])
        walk_through(app, packaged)
        if app.alive() and app.url:
            quit_with_keyboard(app)
    finally:
        app.stop()
        log = work / "main" / "app.log"
        if log.exists() and log.stat().st_size:
            print("\n--- app output ---\n" + log.read_text(errors="replace")[-4000:])
        crash = work / "main" / "data" / "schedru.log"
        if crash.exists() and crash.stat().st_size:
            print("\n--- schedru.log ---\n" + crash.read_text(errors="replace")[-4000:])

    if packaged:
        (work / "finder").mkdir()
        through_finder(args.app.resolve(), work / "finder")
        crash = work / "finder" / "data" / "schedru.log"
        if crash.exists() and crash.stat().st_size:
            print("\n--- schedru.log (Finder launch) ---\n" + crash.read_text(errors="replace")[-4000:])

    shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
    for name in failed:
        print(f"  failed: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
