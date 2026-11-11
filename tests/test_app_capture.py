"""The screenshot tool, with macOS's parts replaced by fakes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from syllabus_cal.app.capture import (
    SCREEN_RECORDING_SETTINGS_URL,
    NoWindow,
    ScreenCapture,
)


class FakeRun:
    """Stands in for subprocess.run. `writes` is what "screencapture" saves."""

    def __init__(self, writes: bytes | None = b"PNG", raises: BaseException | None = None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.writes = writes
        self.raises = raises

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        self.kwargs.append(kwargs)
        if self.raises is not None:
            raise self.raises
        if self.writes is not None and command[0].endswith("screencapture"):
            Path(command[-1]).write_bytes(self.writes)
        return SimpleNamespace(returncode=0)


def mac_tool(tmp_path, run=None, quartz=None) -> ScreenCapture:
    binary = tmp_path / "screencapture"
    binary.write_text("")
    return ScreenCapture(platform="darwin", binary=str(binary), run=run or FakeRun(), quartz=quartz)


def test_unavailable_away_from_a_mac(tmp_path):
    binary = tmp_path / "screencapture"
    binary.write_text("")
    for platform in ("linux", "win32"):
        tool = ScreenCapture(platform=platform, binary=str(binary), run=FakeRun())
        assert tool.available is False
        assert tool.permission() == "unknown"
        assert tool.open_settings() is False


def test_unavailable_on_a_mac_without_the_tool(tmp_path):
    tool = ScreenCapture(platform="darwin", binary=str(tmp_path / "missing"), run=FakeRun())
    assert tool.available is False


def test_capture_runs_the_region_picker_silently(tmp_path):
    run = FakeRun()
    tool = mac_tool(tmp_path, run)
    destination = tmp_path / "shot.png"

    assert tool.capture(destination) == destination
    command = run.calls[0]
    assert command[0].endswith("screencapture")
    assert command[1:] == ["-i", "-x", "-o", str(destination)]
    assert run.kwargs[0]["timeout"] > 0  # a forgotten crosshair can't hang the app


def test_esc_leaves_no_file_and_that_means_cancelled(tmp_path):
    tool = mac_tool(tmp_path, FakeRun(writes=None))
    assert tool.capture(tmp_path / "shot.png") is None


def test_an_empty_file_is_not_a_screenshot(tmp_path):
    tool = mac_tool(tmp_path, FakeRun(writes=b""))
    assert tool.capture(tmp_path / "shot.png") is None


def test_a_stale_file_is_not_mistaken_for_a_new_capture(tmp_path):
    destination = tmp_path / "shot.png"
    destination.write_bytes(b"from last time")

    tool = mac_tool(tmp_path, FakeRun(writes=None))
    assert tool.capture(destination) is None
    assert not destination.exists()


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired(cmd="screencapture", timeout=1), OSError("gone")],
)
def test_a_capture_that_cannot_run_is_just_no_capture(tmp_path, failure):
    tool = mac_tool(tmp_path, FakeRun(raises=failure))
    assert tool.capture(tmp_path / "shot.png") is None


def test_permission_reflects_what_macos_says(tmp_path):
    granted = SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: True)
    denied = SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False)
    assert mac_tool(tmp_path, quartz=granted).permission() == "granted"
    assert mac_tool(tmp_path, quartz=denied).permission() == "denied"


def test_permission_is_unknown_when_macos_cannot_be_asked(tmp_path):
    def broken():
        raise RuntimeError("no such symbol")

    tool = mac_tool(tmp_path, quartz=SimpleNamespace(CGPreflightScreenCaptureAccess=broken))
    assert tool.permission() == "unknown"


def test_requesting_permission_asks_macos_and_survives_failure(tmp_path):
    asked = []
    quartz = SimpleNamespace(CGRequestScreenCaptureAccess=lambda: asked.append(True))
    mac_tool(tmp_path, quartz=quartz).request_permission()
    assert asked == [True]

    mac_tool(tmp_path, quartz=SimpleNamespace()).request_permission()  # must not raise


def test_open_settings_goes_to_the_screen_recording_pane(tmp_path):
    run = FakeRun()
    assert mac_tool(tmp_path, run).open_settings() is True
    assert run.calls == [["/usr/bin/open", SCREEN_RECORDING_SETTINGS_URL]]


def test_no_window_does_nothing_and_is_never_in_front():
    window = NoWindow()
    assert window.is_frontmost() is False
    window.hide()
    window.show()
