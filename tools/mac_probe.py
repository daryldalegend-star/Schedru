"""Find out, on a real Mac, how the pieces the app depends on actually behave.

Runs only in CI on a macOS runner. Every experiment is independent and
records its own failure, so one broken assumption doesn't hide the others;
the result is one JSON document between PROBE_JSON_BEGIN/END markers.

This is a measuring instrument, not part of the app.
"""

from __future__ import annotations

import json
import os
import platform
import struct
import subprocess
import sys
import threading
import time
import traceback
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "probe_assets"
OUT = Path(os.environ.get("PROBE_OUT", "probe-out"))
OUT.mkdir(parents=True, exist_ok=True)

REPORTS: list[dict] = []
RESULTS: dict = {}

PAGE = r"""<!doctype html><meta charset="utf-8"><title>probe</title>
<body style="font:16px -apple-system;padding:24px">
<h1 id="h">Schedru probe</h1>
<input id="d" type="date"><input id="t" type="time">
<textarea id="ta" style="position:absolute;left:-9999px"></textarea>
<div id="vids"></div>
<script>
const post = (kind, data) => fetch('/report', {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind, data})});

document.addEventListener('paste', (e) => {
  const cd = e.clipboardData;
  const items = cd ? [...cd.items].map(i => ({kind: i.kind, type: i.type})) : null;
  let fileSize = null, fileType = null;
  if (cd) for (const i of cd.items) if (i.kind === 'file') {
    const f = i.getAsFile(); if (f) { fileSize = f.size; fileType = f.type; }
  }
  post('paste', {active: document.activeElement && document.activeElement.tagName,
    items, files: cd ? cd.files.length : null, fileSize, fileType, tag: window.__pasteTag || null});
});

function videoTest(src) {
  return new Promise((resolve) => {
    const v = document.createElement('video');
    v.muted = true; v.playsInline = true; v.preload = 'auto'; v.width = 160;
    document.getElementById('vids').appendChild(v);
    let done = false;
    const finish = (r) => { if (!done) { done = true; resolve(r); } };
    v.addEventListener('error', () => finish({error: v.error && v.error.code,
      message: v.error && v.error.message}));
    v.addEventListener('canplay', async () => {
      const r = {canplay: true, w: v.videoWidth, h: v.videoHeight, duration: v.duration};
      try {
        await Promise.race([v.play(), new Promise((_, rej) => setTimeout(() => rej(new Error('play timeout')), 3000))]);
        await new Promise(res => setTimeout(res, 400));
        r.played = true; r.currentTime = v.currentTime;
      } catch (err) { r.played = false; r.playError = String(err); }
      finish(r);
    });
    setTimeout(() => finish({timeout: true, readyState: v.readyState, networkState: v.networkState}), 9000);
    v.src = src; v.load();
  });
}

(async () => {
  const sup = (p, v) => { try { return CSS.supports(p, v); } catch (e) { return 'err'; } };
  const sel = (s) => { try { return CSS.supports('selector(' + s + ')'); } catch (e) { return 'err'; } };
  await post('features', {
    ua: navigator.userAgent,
    dateInput: document.getElementById('d').type,
    timeInput: document.getElementById('t').type,
    dark: matchMedia('(prefers-color-scheme: dark)').matches,
    dpr: devicePixelRatio,
    css: {
      backdrop: sup('backdrop-filter', 'blur(2px)'),
      webkitBackdrop: sup('-webkit-backdrop-filter', 'blur(2px)'),
      colorMix: sup('color', 'color-mix(in srgb, red 50%, blue)'),
      has: sel(':has(a)'),
      nesting: sel('&'),
      dvh: sup('height', '100dvh'),
      gapFlex: sup('gap', '4px'),
      insetProp: sup('inset', '0'),
      aspectRatio: sup('aspect-ratio', '16 / 9'),
      accentColor: sup('accent-color', 'red'),
      textWrapBalance: sup('text-wrap', 'balance'),
      containerQ: sup('container-type', 'inline-size'),
      viewTransition: typeof document.startViewTransition,
    },
    js: {
      structuredClone: typeof structuredClone,
      clipboardRead: !!(navigator.clipboard && navigator.clipboard.read),
      abortTimeout: typeof AbortSignal.timeout,
      waapi: typeof Element.prototype.animate,
      canPlayMp4: document.createElement('video').canPlayType('video/mp4; codecs="avc1.640028"'),
      canPlayWebm: document.createElement('video').canPlayType('video/webm; codecs="vp9"'),
    },
  });
  const video = {};
  video.range = await videoTest('/range.mp4');
  video.norange = await videoTest('/norange.mp4');
  try {
    const blob = await (await fetch('/norange.mp4')).blob();
    video.blob = await videoTest(URL.createObjectURL(blob));
  } catch (e) { video.blob = {exception: String(e)}; }
  video.webm = await videoTest('/test.webm');
  await post('video', video);
})();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if path == "/test.webm":
            return self._send(200, (ASSETS / "test.webm").read_bytes(), "video/webm")
        if path == "/norange.mp4":
            return self._send(200, (ASSETS / "test.mp4").read_bytes(), "video/mp4")
        if path == "/range.mp4":
            data = (ASSETS / "test.mp4").read_bytes()
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start_s, _, end_s = rng[6:].partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start : end + 1]
                return self._send(
                    206, chunk, "video/mp4",
                    {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"},
                )
            return self._send(200, data, "video/mp4", {"Accept-Ranges": "bytes"})
        self._send(404, b"no", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        REPORTS.append(json.loads(self.rfile.read(length) or b"{}"))
        self._send(200, b"{}", "application/json")


def experiment(name: str):
    """Record an experiment's return value, or its exception, under `name`."""

    def wrap(fn):
        try:
            RESULTS[name] = fn()
        except BaseException as exc:  # noqa: BLE001 - a probe wants every failure
            RESULTS[name] = {"exception": f"{type(exc).__name__}: {exc}",
                             "trace": traceback.format_exc(limit=4)}
        return fn

    return wrap


def wait_for(predicate, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def png_size(path: Path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return {"bytes": len(data), "png": False}
    w, h = struct.unpack(">II", data[16:24])
    return {"bytes": len(data), "w": w, "h": h}


def make_png(path: Path, size: int = 64) -> None:
    raw = b"".join(b"\x00" + bytes([200, 40, 40, 255]) * size for _ in range(size))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    RESULTS["platform"] = {
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "mac_ver": platform.mac_ver()[0],
        "executable": sys.executable,
    }

    import AppKit
    import Foundation
    import Quartz
    import webview
    from PyObjCTools import AppHelper

    NSAPP = AppKit.NSApplication.sharedApplication()

    RESULTS["versions"] = {
        "pywebview": getattr(webview, "__version__", "?"),
        "pyobjc": __import__("objc").__version__,
    }

    def on_main(fn, timeout: float = 10.0):
        box: dict = {}
        done = threading.Event()

        def run():
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
            done.set()

        AppHelper.callAfter(run)
        if not done.wait(timeout):
            raise TimeoutError("main thread did not run the call")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Does renaming the process this way change what the system calls the app?
    @experiment("rename_bundle")
    def _():
        info = Foundation.NSBundle.mainBundle().infoDictionary()
        before = info.get("CFBundleName")
        info["CFBundleName"] = "Schedru"
        return {"before": before, "after": info.get("CFBundleName")}

    icon = OUT / "icon.png"
    make_png(icon, 128)
    window = webview.create_window(
        "Schedru", f"http://127.0.0.1:{port}/", width=900, height=640, min_size=(640, 480)
    )

    key_flags = {"cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift}

    def post_key(code: int, flags: int = 0) -> None:
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.06)

    def post_mouse(kind: int, x: float, y: float) -> None:
        event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.03)

    def driver():
        from webview.platforms.cocoa import BrowserView

        try:
            RESULTS["loaded"] = window.events.loaded.wait(30)
            bv = BrowserView.instances[window.uid]

            @experiment("app_identity")
            def _():
                app = AppKit.NSRunningApplication.currentApplication()
                return {
                    "localizedName": str(app.localizedName()),
                    "bundleIdentifier": str(app.bundleIdentifier()),
                    "activationPolicy": int(app.activationPolicy()),
                    "menuTitle": on_main(lambda: str(NSAPP.mainMenu().itemAtIndex_(0).submenu().title())),
                }

            RESULTS["page_reported"] = wait_for(
                lambda: any(r["kind"] == "video" for r in REPORTS), 70
            )
            for report in REPORTS:
                if report["kind"] in ("features", "video"):
                    RESULTS[report["kind"]] = report["data"]

            @experiment("permissions")
            def _():
                out = {}
                for name in ("CGPreflightScreenCaptureAccess", "CGPreflightPostEventAccess",
                             "CGPreflightListenEventAccess"):
                    fn = getattr(Quartz, name, None)
                    out[name] = bool(fn()) if fn else "missing"
                try:
                    from ApplicationServices import AXIsProcessTrusted

                    out["AXIsProcessTrusted"] = bool(AXIsProcessTrusted())
                except Exception as exc:  # noqa: BLE001
                    out["AXIsProcessTrusted"] = f"unavailable: {exc}"
                out["hasRequestFn"] = hasattr(Quartz, "CGRequestScreenCaptureAccess")
                return out

            fired = threading.Event()

            @experiment("hotkey_register")
            def _():
                def register():
                    from quickmachotkey import mask, quickHotKey
                    from quickmachotkey.constants import cmdKey, kVK_ANSI_2, shiftKey

                    @quickHotKey(virtualKey=kVK_ANSI_2, modifierMask=mask(cmdKey, shiftKey))
                    def handler() -> None:
                        fired.set()

                    RESULTS["_keep_handler"] = None  # handler stays referenced by the library
                    return "registered"

                return on_main(register)

            @experiment("hotkey_raw_status")
            def _():
                def raw():
                    from quickmachotkey._MinimalHIToolbox import (
                        GetEventDispatcherTarget,
                        RegisterEventHotKey,
                    )

                    target = GetEventDispatcherTarget()
                    combo = 0x100 | 0x800 | 0x1000  # cmd+option+control
                    first = RegisterEventHotKey(0x19, combo, (0x53434844, 901), target, 0, None)
                    again = RegisterEventHotKey(0x19, combo, (0x53434844, 902), target, 0, None)
                    return {"fresh": int(first[0]), "duplicate_same_app": int(again[0])}

                return on_main(raw)

            @experiment("hotkey_fires")
            def _():
                out = {}
                post_key(0x13, key_flags["cmd"] | key_flags["shift"])
                out["via_cgevent"] = fired.wait(2.5)
                if not out["via_cgevent"]:
                    script = 'tell application "System Events" to keystroke "2" using {command down, shift down}'
                    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
                    out["osascript_rc"] = proc.returncode
                    out["osascript_err"] = proc.stderr.strip()[:200]
                    out["via_osascript"] = fired.wait(2.5)
                return out

            @experiment("hotkey_fires_when_app_inactive")
            def _():
                fired.clear()
                subprocess.run(["open", "-a", "Finder"], timeout=10)
                time.sleep(1.2)
                active_before = on_main(lambda: bool(NSAPP.isActive()))
                post_key(0x13, key_flags["cmd"] | key_flags["shift"])
                got = fired.wait(2.5)
                return {"app_active_before_press": active_before, "fired": got}

            @experiment("show_brings_app_forward")
            def _():
                window.show()
                time.sleep(1.0)
                return {
                    "active": on_main(lambda: bool(NSAPP.isActive())),
                    "visible": on_main(lambda: bool(bv.window.isVisible())),
                    "key": on_main(lambda: bool(bv.window.isKeyWindow())),
                }

            @experiment("hide_then_show")
            def _():
                window.hide()
                time.sleep(0.7)
                hidden = on_main(lambda: bool(bv.window.isVisible()))
                window.show()
                time.sleep(0.7)
                return {"visible_after_hide": hidden,
                        "visible_after_show": on_main(lambda: bool(bv.window.isVisible()))}

            @experiment("screencapture_full")
            def _():
                path = OUT / "full.png"
                proc = subprocess.run(["/usr/sbin/screencapture", "-x", str(path)],
                                      capture_output=True, text=True, timeout=30)
                info = {"rc": proc.returncode, "stderr": proc.stderr.strip()[:200]}
                if path.exists():
                    info.update(png_size(path))
                return info

            @experiment("screencapture_interactive_drag")
            def _():
                path = OUT / "region.png"
                proc = subprocess.Popen(["/usr/sbin/screencapture", "-i", "-x", "-o", str(path)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                time.sleep(1.5)
                post_mouse(Quartz.kCGEventMouseMoved, 200, 200)
                post_mouse(Quartz.kCGEventLeftMouseDown, 200, 200)
                for step in range(1, 11):
                    post_mouse(Quartz.kCGEventLeftMouseDragged, 200 + step * 40, 200 + step * 25)
                post_mouse(Quartz.kCGEventLeftMouseUp, 600, 450)
                try:
                    proc.wait(timeout=8)
                    info = {"rc": proc.returncode, "exists": path.exists()}
                except subprocess.TimeoutExpired:
                    proc.kill()
                    info = {"timeout": True, "exists": path.exists()}
                if path.exists():
                    info.update(png_size(path))
                return info

            @experiment("screencapture_interactive_escape")
            def _():
                path = OUT / "cancelled.png"
                proc = subprocess.Popen(["/usr/sbin/screencapture", "-i", "-x", "-o", str(path)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                time.sleep(1.5)
                post_key(0x35)
                try:
                    proc.wait(timeout=8)
                    return {"rc": proc.returncode, "exists": path.exists(),
                            "stderr": proc.stderr.read().decode()[:200]}
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return {"timeout": True, "exists": path.exists()}

            clip = OUT / "clip.png"
            make_png(clip, 64)

            def put_png_on_clipboard():
                pb = AppKit.NSPasteboard.generalPasteboard()
                pb.clearContents()
                data = Foundation.NSData.dataWithContentsOfFile_(str(clip))
                return bool(pb.setData_forType_(data, AppKit.NSPasteboardTypePNG))

            def paste_reports(tag: str):
                return [r["data"] for r in REPORTS if r["kind"] == "paste" and r["data"].get("tag") == tag]

            def try_paste(tag: str, how):
                window.evaluate_js(f"window.__pasteTag = {json.dumps(tag)}")
                how()
                wait_for(lambda: bool(paste_reports(tag)), 2.5)
                return paste_reports(tag) or "no paste event"

            @experiment("paste_image")
            def _():
                out = {"clipboard_set": on_main(put_png_on_clipboard)}
                window.show()
                time.sleep(0.6)
                on_main(lambda: bv.window.makeFirstResponder_(bv.webview))
                window.evaluate_js("document.activeElement && document.activeElement.blur()")
                out["action_body_focused"] = try_paste(
                    "action-body", lambda: on_main(lambda: bv.webview.paste_(None)))
                out["cmdv_body_focused"] = try_paste(
                    "cmdv-body", lambda: post_key(0x09, key_flags["cmd"]))
                window.evaluate_js("document.getElementById('ta').focus()")
                time.sleep(0.2)
                out["action_textarea_focused"] = try_paste(
                    "action-ta", lambda: on_main(lambda: bv.webview.paste_(None)))
                out["cmdv_textarea_focused"] = try_paste(
                    "cmdv-ta", lambda: post_key(0x09, key_flags["cmd"]))
                return out

            @experiment("window_screenshot")
            def _():
                window.show()
                time.sleep(0.8)
                path = OUT / "window.png"
                subprocess.run(["/usr/sbin/screencapture", "-x", str(path)], timeout=30)
                return png_size(path) if path.exists() else "missing"

        except BaseException as exc:  # noqa: BLE001
            RESULTS["driver_crash"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"
        finally:
            RESULTS.pop("_keep_handler", None)
            (OUT / "report.json").write_text(json.dumps(RESULTS, indent=1, default=str))
            print("PROBE_JSON_BEGIN")
            print(json.dumps(RESULTS, indent=1, default=str))
            print("PROBE_JSON_END", flush=True)
            window.destroy()

    def watchdog():
        time.sleep(240)
        RESULTS["watchdog"] = "probe did not finish in 240s"
        print("PROBE_JSON_BEGIN")
        print(json.dumps(RESULTS, indent=1, default=str))
        print("PROBE_JSON_END", flush=True)
        os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(driver, icon=str(icon))
    return 0


if __name__ == "__main__":
    sys.exit(main())
