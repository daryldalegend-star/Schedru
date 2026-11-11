"""Starting the app: the server, then a window onto it.

On a Mac the window is a native one (WebKit, via pywebview) with a global
shortcut for capturing. Anywhere else -- or if the window can't be created --
the same UI opens in the default browser, where everything except the
on-screen capture works: screenshots are dropped, pasted or chosen instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

from ..paths import bundle_root, data_dir
from . import APP_NAME, __version__
from .engine import make_engine
from .server import AppServer
from .service import Hub, Service
from .settings import Settings

SHORTCUT_LABEL = "⌘⇧2"

# Set by tests and CI. None of these exist in normal use.
ENGINE_ENV = "SCHEDRU_ENGINE"  # "demo": canned results, no network or accounts
HOOK_FILE_ENV = "SCHEDRU_TEST_HOOK"  # write the address and token to this file
DEBUG_HOOKS_ENV = "SCHEDRU_DEBUG_HOOKS"  # expose /api/debug/* (still token-guarded)

WINDOW_SIZE = (1040, 760)
WINDOW_MIN_SIZE = (820, 620)
PAPER = "#F6F1E7"
INK = "#171614"


def web_dir() -> Path:
    return bundle_root() / "syllabus_cal" / "app" / "web"


def icon_path() -> Path:
    return web_dir() / "icon.png"


class App:
    """Everything that exists whichever kind of window is used."""

    def __init__(self, engine_kind: str | None = None, port: int = 0) -> None:
        folder = data_dir()
        self.engine = make_engine(folder, engine_kind or os.environ.get(ENGINE_ENV))
        self.settings = Settings(folder)
        self.hub = Hub()
        self.service = Service(self.engine, self.settings, hub=self.hub)
        self.server = AppServer(self.service, web_dir(), port=port)
        self._closed = False

        if os.environ.get(DEBUG_HOOKS_ENV) == "1":
            self.server.debug_hooks["demo"] = self._tune_demo
            self.server.debug_hooks["hotkey"] = lambda body: self.service.capture(origin="hotkey")

    def _tune_demo(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Let a test pick what the demo engine does next, and how slowly."""
        if not hasattr(self.engine, "scenario"):
            return None
        if "scenario" in body:
            self.engine.scenario = str(body["scenario"] or "default")
        if "delay" in body:
            self.engine.delay = float(body["delay"])
        return {"scenario": self.engine.scenario, "delay": self.engine.delay}

    def start(self) -> None:
        self.server.start()
        self.service.start()
        hook = os.environ.get(HOOK_FILE_ENV)
        if hook:
            Path(hook).write_text(
                json.dumps(
                    {
                        "url": self.server.url,
                        "port": self.server.port,
                        "token": self.server.token,
                        "pid": os.getpid(),
                    }
                )
            )

    def close(self) -> None:
        """Safe to call more than once: quitting can arrive by several routes."""
        if self._closed:
            return
        self._closed = True
        try:
            self.server.stop()
        finally:
            self.service.close()


# ---------------------------------------------------------------------------
# Browser


def run_in_browser(app: App, open_browser: bool = True) -> int:
    app.service.shell = "browser"
    app.start()
    print(f"{APP_NAME} is running at {app.server.url}")
    print("Leave this window open while you use it. Press Ctrl+C here to quit.")
    if open_browser:
        webbrowser.open(app.server.url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print()
    finally:
        app.close()
    return 0


# ---------------------------------------------------------------------------
# macOS window


def _on_main(function: Callable[[], Any], timeout: float = 5.0) -> Any:
    """Run `function` on the main (UI) thread and return what it returns.

    AppKit objects may only be touched from the main thread, and most of this
    app's work happens on others.
    """
    from Foundation import NSThread
    from PyObjCTools import AppHelper

    if NSThread.isMainThread():
        return function()

    done = threading.Event()
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = function()
        except Exception as exc:  # hand it back rather than lose it in the run loop
            box["error"] = exc
        finally:
            done.set()

    AppHelper.callAfter(run)
    if not done.wait(timeout):
        raise TimeoutError("the app's main thread didn't respond")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class MacWindow:
    """The capture code's handle on the real window (see capture.WindowControl)."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self.quitting = False
        self.keep_alive_on_close = False

    def _native(self) -> Any:
        from webview.platforms.cocoa import BrowserView

        return BrowserView.instances[self._window.uid].window

    def state(self) -> dict[str, bool]:
        import AppKit

        def read() -> dict[str, bool]:
            native = self._native()
            return {
                "active": bool(AppKit.NSApplication.sharedApplication().isActive()),
                "visible": bool(native.isVisible()),
                "key": bool(native.isKeyWindow()),
                "miniaturized": bool(native.isMiniaturized()),
            }

        return _on_main(read)

    def is_frontmost(self) -> bool:
        try:
            state = self.state()
        except Exception:
            return False
        return state["active"] and state["visible"]

    def hide(self) -> None:
        try:
            _on_main(lambda: self._native().orderOut_(None))
        except Exception:
            pass

    def show(self) -> None:
        import AppKit

        def bring_forward() -> None:
            native = self._native()
            if native.isMiniaturized():
                native.deminiaturize_(None)
            native.makeKeyAndOrderFront_(None)
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        try:
            _on_main(bring_forward)
        except Exception:
            pass

    def on_closing(self) -> bool | None:
        """The red button and Cmd-W hide the window instead of quitting, so the
        capture shortcut keeps working. Quit (Cmd-Q) really quits."""
        if self.quitting or not self.keep_alive_on_close:
            return None
        self._native().orderOut_(None)  # already on the main thread here
        return False


def _evaluate(window: Any, expression: str, timeout: float = 10.0) -> Any:
    """The value of a JavaScript expression in the window's page (tests only).

    Goes to WebKit directly rather than through pywebview's evaluate_js, which
    wraps the script in eval() -- and the page's security policy forbids eval.
    """
    from PyObjCTools import AppHelper
    from webview.platforms.cocoa import BrowserView

    done = threading.Event()
    box: dict[str, Any] = {}

    def finished(result: Any, error: Any) -> None:
        box["result"] = result
        box["error"] = None if error is None else str(error)
        done.set()

    script = f"JSON.stringify((function () {{ return (\n{expression}\n); }})())"

    def run() -> None:
        BrowserView.instances[window.uid].webview.evaluateJavaScript_completionHandler_(
            script, finished
        )

    AppHelper.callAfter(run)
    if not done.wait(timeout):
        raise TimeoutError("the page didn't answer")
    if box["error"]:
        raise RuntimeError(box["error"])
    return None if box["result"] is None else json.loads(box["result"])


def _focus_page(window: Any) -> bool:
    from webview.platforms.cocoa import BrowserView

    view = BrowserView.instances[window.uid]
    return bool(view.window.makeFirstResponder_(view.webview))


def _identity() -> dict[str, Any]:
    """How macOS sees this process. The tests check the packaged app really is
    "Schedru" rather than an anonymous Python."""
    import AppKit

    running = AppKit.NSRunningApplication.currentApplication()
    bundle = AppKit.NSBundle.mainBundle()
    return {
        "name": str(running.localizedName() or ""),
        "bundle_id": str(bundle.bundleIdentifier() or ""),
        "bundle_path": str(bundle.bundlePath() or ""),
        "pid": os.getpid(),
    }


def _prefers_dark() -> bool:
    try:
        from Foundation import NSUserDefaults

        style = NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle")
        return style == "Dark"
    except Exception:
        return False


def _install_app_delegate(controller: MacWindow, on_quit: Callable[[], None]) -> Any:
    """Teach the app two things pywebview's own delegate doesn't know: quitting
    means quit even though closing the window doesn't, and clicking the Dock
    icon brings a hidden window back.

    pywebview installs whatever delegate is already parked on BrowserView, so
    a subclass placed there before the window is created takes its place.
    """
    import Foundation
    from webview.platforms.cocoa import BrowserView

    class SchedruAppDelegate(BrowserView.AppDelegate):
        def applicationShouldTerminate_(self, application):  # noqa: N802
            controller.quitting = True
            on_quit()
            return Foundation.YES

        def applicationShouldHandleReopen_hasVisibleWindows_(  # noqa: N802
            self, application, has_visible_windows
        ):
            controller.show()
            return Foundation.YES

    delegate = SchedruAppDelegate.alloc().init()
    BrowserView._shared_app_delegate = delegate
    return delegate


def _register_shortcut(service: Service) -> bool:
    """Cmd-Shift-2, system-wide. Sits next to the Mac's own Cmd-Shift-3/4/5
    screenshot keys and is unused by macOS itself.

    Registered through Carbon's hot-key API (via quickmachotkey), the one way
    to get a global shortcut that needs no Accessibility permission.
    """
    from quickmachotkey import mask, quickHotKey
    from quickmachotkey.constants import cmdKey, kVK_ANSI_2, shiftKey

    @quickHotKey(virtualKey=kVK_ANSI_2, modifierMask=mask(cmdKey, shiftKey))
    def schedru_capture() -> None:
        # Called on the main thread; hand off so the UI never stalls.
        threading.Thread(
            target=_capture_from_shortcut, args=(service,), name="schedru-hotkey", daemon=True
        ).start()

    service._shortcut_handler = schedru_capture  # keep it alive  # type: ignore[attr-defined]
    return True


def _capture_from_shortcut(service: Service) -> None:
    try:
        service.capture(origin="hotkey")
    except Exception:
        pass  # capture() reports its own problems to the window


def run_in_window(app: App) -> int:
    import webview
    from PyObjCTools import AppHelper

    service = app.service
    service.shell = "window"

    dark = _prefers_dark()
    window = webview.create_window(
        APP_NAME,
        app.server.url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        background_color=INK if dark else PAPER,
        text_select=True,
    )
    controller = MacWindow(window)
    service.window = controller

    delegate = None
    try:
        delegate = _install_app_delegate(controller, app.close)
    except Exception:
        delegate = None  # closing the window will simply quit, as pywebview does by default

    window.events.closing += controller.on_closing

    if os.environ.get(DEBUG_HOOKS_ENV) == "1":
        app.server.debug_hooks["eval"] = lambda body: _evaluate(window, str(body.get("js", "")))
        app.server.debug_hooks["window"] = lambda body: controller.state()
        app.server.debug_hooks["show"] = lambda body: controller.show()
        app.server.debug_hooks["focus"] = lambda body: _on_main(lambda: _focus_page(window))
        app.server.debug_hooks["identity"] = lambda body: _on_main(_identity)

    def after_launch() -> None:
        # Runs on its own thread, started before the window exists.
        window.events.shown.wait(30)

        def on_main_thread() -> None:
            import AppKit

            # Only keep running without a window if Quit is sure to work.
            installed = AppKit.NSApplication.sharedApplication().delegate()
            controller.keep_alive_on_close = delegate is not None and installed is delegate
            try:
                if _register_shortcut(service):
                    service.shortcut = SHORTCUT_LABEL
            except Exception:
                service.shortcut = None  # the Capture button still works
            service.hub.publish("capture", outcome="ready")

        AppHelper.callAfter(on_main_thread)

    app.start()
    icon = icon_path()
    try:
        webview.start(after_launch, private_mode=True, icon=str(icon) if icon.exists() else None)
    finally:
        app.close()
    return 0


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="schedru", description=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--browser", action="store_true", help="Open in the default browser instead of a window"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="With --browser: start, but don't open a tab"
    )
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (default: any free one)")
    parser.add_argument("--demo", action="store_true", help="Canned results; no network or accounts")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Check that everything the app needs is present, print a report, and exit",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def self_check() -> int:
    """Exercise every outside dependency far enough to prove it's really here.

    The walk-through tests run on the demo engine, which never touches the
    Anthropic or Google libraries, so a packaged app could pass them while
    missing a file the real engine needs. This loads the real ones: it builds
    the Calendar client from its bundled API description, prepares a Google
    sign-in URL, round-trips images, and so on. Nothing here uses the network.
    """
    report: dict[str, Any] = {}

    def probe(name: str, function: Callable[[], Any]) -> None:
        try:
            report[name] = function()
        except Exception as exc:
            report[name] = f"FAILED: {type(exc).__name__}: {exc}"

    def web_files() -> list[str]:
        root = web_dir()
        wanted = ["index.html", "app.css", "app.js", "icon.png", "fonts/fraunces.woff2",
                  "media/tutorial.mp4", "media/tutorial-poster.jpg"]
        missing = [name for name in wanted if not (root / name).is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        return wanted

    def anthropic_client() -> str:
        import anthropic

        client = anthropic.Anthropic(api_key="sk-ant-self-check", max_retries=0)
        assert client.messages is not None and client.models is not None
        return anthropic.__version__

    def calendar_client() -> str:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=Credentials(token="self-check"))
        request = service.events().insert(calendarId="primary", body={"summary": "x"})
        return request.methodId

    def google_sign_in() -> str:
        from google_auth_oauthlib.flow import InstalledAppFlow

        from ..calendar_client import SCOPES

        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": "self-check.apps.googleusercontent.com",
                    "client_secret": "self-check",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            SCOPES,
        )
        flow.redirect_uri = "http://localhost:1/"
        url, _ = flow.authorization_url(prompt="select_account consent")
        return url.split("?")[0]

    def images() -> str:
        import io

        from PIL import Image

        from ..vision import load_and_prepare_image  # noqa: F401  (registers HEIC)

        source = Image.new("RGB", (64, 48), (200, 16, 46))
        formats = []
        for name in ("PNG", "JPEG", "WEBP", "HEIF"):
            buffer = io.BytesIO()
            source.save(buffer, format=name)
            with Image.open(io.BytesIO(buffer.getvalue())) as reopened:
                reopened.load()
                formats.append(reopened.format)
        return " ".join(formats)

    def schema() -> str:
        from .engine import demo_events
        from .describe import event_to_json

        return event_to_json(demo_events()[0], "e1")["when_text"]

    def time_zone() -> str:
        from datetime import datetime

        from ..rrule import DEFAULT_TZ

        return datetime(2026, 11, 19, 19, 30, tzinfo=DEFAULT_TZ).isoformat()

    def certificates() -> int:
        import ssl

        import certifi

        ssl.create_default_context(cafile=certifi.where())
        return Path(certifi.where()).stat().st_size

    def window_library() -> str:
        if sys.platform != "darwin":
            return "not a Mac: the browser is used instead"
        import quickmachotkey  # noqa: F401
        import webview
        import webview.platforms.cocoa  # noqa: F401

        return getattr(webview, "__version__", "present")

    probe("version", lambda: __version__)
    probe("python", lambda: sys.version.split()[0])
    probe("frozen", lambda: bool(getattr(sys, "frozen", False)))
    probe("web files", web_files)
    probe("anthropic", anthropic_client)
    probe("google calendar", calendar_client)
    probe("google sign-in", google_sign_in)
    probe("images", images)
    probe("events", schema)
    probe("time zone", time_zone)
    probe("certificates", certificates)
    probe("window", window_library)

    print(json.dumps(report, indent=2, default=str))
    failed = [name for name, value in report.items() if isinstance(value, str) and value.startswith("FAILED")]
    return 1 if failed else 0


def _log_problems_to_file() -> None:
    """The packaged app has no terminal to print to, so anything that goes
    wrong is written to schedru.log in the data folder instead."""
    import logging

    try:
        logging.basicConfig(
            filename=str(data_dir() / "schedru.log"),
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    except OSError:
        return

    def record(kind: type, value: BaseException, trace: Any) -> None:
        logging.getLogger("schedru").error("Unhandled error", exc_info=(kind, value, trace))

    sys.excepthook = record
    threading.excepthook = lambda args: record(args.exc_type, args.exc_value, args.exc_traceback)


def main(argv: list[str] | None = None) -> int:
    # parse_known_args: macOS can hand a double-clicked app arguments of its own.
    args, _ = build_parser().parse_known_args(argv)
    if args.self_check:
        return self_check()
    if getattr(sys, "frozen", False):
        _log_problems_to_file()
    app = App(engine_kind="demo" if args.demo else None, port=args.port)

    if not args.browser and sys.platform == "darwin":
        try:
            import webview  # noqa: F401
        except ImportError:
            pass  # not installed: fall through to the browser
        else:
            return run_in_window(app)

    return run_in_browser(app, open_browser=not args.no_open)
