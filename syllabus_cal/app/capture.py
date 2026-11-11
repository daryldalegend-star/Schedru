"""Taking the screenshot.

macOS already has a good region picker -- the crosshair you get from
Cmd-Shift-4 -- and `screencapture -i` hands it to any program that asks. Using
it means the capture looks and behaves exactly like the screenshots people
already take: drag for a region, Space to pick a window, Esc to cancel.

The one cost is macOS's Screen Recording permission. Without it the picker
still appears but other apps' windows come out blank, so the permission is
checked first and explained, rather than discovered through a useless capture.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Protocol

SCREENCAPTURE = "/usr/sbin/screencapture"
SCREEN_RECORDING_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)
# Long enough to hunt for the right window; short enough that a forgotten
# crosshair doesn't block the shortcut for the rest of the day.
CAPTURE_TIMEOUT_SECONDS = 180.0


class WindowControl(Protocol):
    """What capturing needs from the app's window."""

    def is_frontmost(self) -> bool: ...

    def hide(self) -> None: ...

    def show(self) -> None: ...


class NoWindow:
    """For when the UI is a browser tab, which the app can't move around."""

    def is_frontmost(self) -> bool:
        return False

    def hide(self) -> None:
        pass

    def show(self) -> None:
        pass


class ScreenCapture:
    def __init__(
        self,
        *,
        platform: str = sys.platform,
        binary: str = SCREENCAPTURE,
        run: Callable[..., Any] = subprocess.run,
        quartz: Any = None,
    ) -> None:
        self._platform = platform
        self._binary = binary
        self._run = run
        self._quartz = quartz

    @property
    def available(self) -> bool:
        return self._platform == "darwin" and Path(self._binary).exists()

    def _load_quartz(self) -> Any:
        if self._quartz is None:
            import Quartz  # pyobjc; present wherever the window itself is

            self._quartz = Quartz
        return self._quartz

    def permission(self) -> str:
        """'granted', 'denied', or 'unknown' when macOS can't be asked."""
        if not self.available:
            return "unknown"
        try:
            granted = self._load_quartz().CGPreflightScreenCaptureAccess()
        except Exception:
            return "unknown"
        return "granted" if granted else "denied"

    def request_permission(self) -> None:
        """Have macOS show its own "allow screen recording" prompt. It only
        ever appears once per app; after that the switch is in System Settings."""
        try:
            self._load_quartz().CGRequestScreenCaptureAccess()
        except Exception:
            pass

    def open_settings(self) -> bool:
        if not self.available:
            return False
        try:
            result = self._run(["/usr/bin/open", SCREEN_RECORDING_SETTINGS_URL], check=False)
        except OSError:
            return False
        return getattr(result, "returncode", 1) == 0

    def capture(self, destination: Path) -> Path | None:
        """Show the region picker and save what's selected to `destination`.

        Returns None when nothing was captured. Cancelling with Esc exits 0
        just like a successful capture, so the file is the only evidence.
        """
        destination = Path(destination)
        try:
            destination.unlink()
        except FileNotFoundError:
            pass

        try:
            # -i pick a region or window, -x no shutter sound, -o no window shadow
            self._run(
                [self._binary, "-i", "-x", "-o", str(destination)],
                check=False,
                timeout=CAPTURE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

        try:
            if destination.stat().st_size > 0:
                return destination
        except OSError:
            pass
        return None
