"""End-to-end tests of the parse command's wiring.

The vision call and the Calendar service are faked; everything between them
(confirmation, event building, write, summary) is the real code path.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from syllabus_cal import __main__ as cli
from syllabus_cal.schema import ExtractionResult

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.json"
CALENDAR_NAME = "daryl.lacuesta@gmail.com"


@pytest.fixture
def events():
    return ExtractionResult.model_validate(json.loads(FIXTURE.read_text()))


@pytest.fixture
def wired(monkeypatch, events):
    """API key present, signed in, extraction returns the fixture, Calendar faked."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)

    import syllabus_cal.calendar_client as calendar_client
    import syllabus_cal.vision as vision

    monkeypatch.setattr(vision, "extract_events", lambda *a, **k: events)

    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://calendar.google.com/event?eid=xyz"
    }
    monkeypatch.setattr(calendar_client, "connect", lambda: (service, CALENDAR_NAME))
    return service


def answer_with(monkeypatch, *answers):
    queue = list(answers)
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: queue.pop(0))


def test_dry_run_never_touches_the_calendar(wired, monkeypatch, capsys):
    exit_code = cli.main(["parse", "shot.png", "--dry-run"])

    assert exit_code == 0
    wired.events.return_value.insert.assert_not_called()
    assert "nothing was written" in capsys.readouterr().out.lower()


def test_confirmed_events_are_written_and_linked(wired, monkeypatch, capsys):
    # Enter through all four: two clean ones added, two flagged ones skipped.
    answer_with(monkeypatch, "", "", "", "")

    exit_code = cli.main(["parse", "shot.png"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert wired.events.return_value.insert.call_count == 2
    assert "Added 2 event(s)" in out
    assert "https://calendar.google.com/event?eid=xyz" in out


def test_skipping_everything_writes_nothing(wired, monkeypatch, capsys):
    answer_with(monkeypatch, "s", "s", "s", "s")

    exit_code = cli.main(["parse", "shot.png"])

    assert exit_code == 0
    wired.events.return_value.insert.assert_not_called()
    assert "nothing was written" in capsys.readouterr().out.lower()


def test_partial_failure_reports_both_sides_and_exits_nonzero(wired, monkeypatch, capsys):
    wired.events.return_value.insert.return_value.execute.side_effect = [
        {"htmlLink": "https://calendar.google.com/event?eid=ok"},
        RuntimeError("backend error"),
    ]
    answer_with(monkeypatch, "", "", "", "")

    exit_code = cli.main(["parse", "shot.png"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Added 1 event(s)" in captured.out
    assert "could NOT be written" in captured.err
    assert "backend error" in captured.err
    # The user needs to know which ones already exist before re-running.
    assert "duplicates" in captured.err


def test_missing_api_key_fails_before_anything_else(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)

    assert cli.main(["parse", "shot.png", "--dry-run"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def signed_out(monkeypatch, service=None, reason="Your Google sign-in has expired."):
    """connect() fails as if the weekly Google expiry hit; returns a list that
    records each time the browser sign-in is launched."""
    import syllabus_cal.calendar_client as calendar_client

    launched = []

    def connect():
        if not launched:
            raise calendar_client.AuthError(reason)
        return (service if service is not None else MagicMock()), CALENDAR_NAME

    monkeypatch.setattr(calendar_client, "connect", connect)
    monkeypatch.setattr(calendar_client, "run_auth_flow", lambda: launched.append(True))
    return launched


def test_declining_sign_in_writes_nothing_and_skips_the_claude_call(
    monkeypatch, capsys, events
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    import syllabus_cal.vision as vision

    called = []
    monkeypatch.setattr(vision, "extract_events", lambda *a, **k: called.append(1))
    launched = signed_out(monkeypatch)
    answer_with(monkeypatch, "n")

    exit_code = cli.main(["parse", "shot.png"])

    assert exit_code == 2
    assert launched == []
    assert called == []  # no API request spent on a run that can't write
    assert "not connected" in capsys.readouterr().err.lower()


def test_expired_sign_in_is_renewed_by_pressing_enter(wired, monkeypatch, capsys):
    launched = signed_out(monkeypatch, service=wired)
    # Enter at "Sign in with Google now? (Y/n)", then Enter through the events.
    answer_with(monkeypatch, "", "", "", "", "")

    exit_code = cli.main(["parse", "shot.png"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert launched == [True]
    assert "expired" in out
    assert "Added 2 event(s)" in out


def test_cancelling_confirmation_writes_nothing(wired, monkeypatch, capsys):
    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.console, "input", interrupt)

    exit_code = cli.main(["parse", "shot.png"])

    assert exit_code == 1
    wired.events.return_value.insert.assert_not_called()
    assert "cancelled" in capsys.readouterr().out.lower()


# Dragging a file into a terminal doesn't produce a clean path: Terminal.app
# backslash-escapes spaces and metacharacters, other terminals quote instead.


def test_plain_path_is_unchanged():
    assert cli.clean_dropped_path("/Users/me/shot.png") == "/Users/me/shot.png"


def test_surrounding_whitespace_is_trimmed():
    # Terminal leaves a trailing space after a drag.
    assert cli.clean_dropped_path("  /Users/me/shot.png ") == "/Users/me/shot.png"


def test_backslash_escaped_spaces_are_unescaped():
    assert (
        cli.clean_dropped_path("/Users/me/Screen\\ Shot\\ 2026.png")
        == "/Users/me/Screen Shot 2026.png"
    )


def test_single_quoted_path_is_unwrapped():
    assert cli.clean_dropped_path("'/Users/me/my shot.png'") == "/Users/me/my shot.png"


def test_double_quoted_path_is_unwrapped():
    assert cli.clean_dropped_path('"/Users/me/my shot.png"') == "/Users/me/my shot.png"


def test_other_escaped_metacharacters_are_unescaped():
    assert (
        cli.clean_dropped_path("/Users/me/CHEM\\&BIO\\ \\(1\\).png")
        == "/Users/me/CHEM&BIO (1).png"
    )


def test_dropped_screenshot_runs_the_full_parse_flow(wired, monkeypatch, capsys):
    # Path with an escaped space, then Enter through the four events.
    answer_with(monkeypatch, "/tmp/Screen\\ Shot.png", "", "", "", "")

    exit_code = cli.main([])

    assert exit_code == 0
    assert wired.events.return_value.insert.call_count == 2


def test_empty_drop_prompt_quits_without_parsing(wired, monkeypatch):
    answer_with(monkeypatch, "")

    assert cli.main([]) == 0
    wired.events.return_value.insert.assert_not_called()


def test_launch_shows_which_google_account_events_go_to(wired, monkeypatch, capsys):
    # The user has two Gmail accounts; seeing the name is how they'd notice
    # events heading to the wrong one.
    answer_with(monkeypatch, "")

    assert cli.main([]) == 0
    assert f"Connected to Google Calendar: {CALENDAR_NAME}" in capsys.readouterr().out


def test_typing_switch_signs_in_again_then_asks_for_the_screenshot(wired, monkeypatch, capsys):
    import syllabus_cal.calendar_client as calendar_client

    launched = []
    monkeypatch.setattr(calendar_client, "run_auth_flow", lambda: launched.append(True))
    answer_with(monkeypatch, "switch", "")

    assert cli.main([]) == 0
    assert launched == [True]
    assert capsys.readouterr().out.count("Drag your screenshot") == 2


def test_masked_key_preview_is_caught_before_any_request(monkeypatch, capsys):
    # What the Anthropic console shows after creation, and what the user
    # actually tried to paste.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-Ggj...hAAA")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)

    assert cli.main(["parse", "shot.png", "--dry-run"]) == 2
    assert "shortened preview" in capsys.readouterr().err


def test_claude_api_errors_are_a_message_not_a_stack_trace(wired, monkeypatch, capsys):
    import syllabus_cal.vision as vision

    def rejected(*a, **k):
        raise vision.ClaudeAPIError("Anthropic rejected your API key.")

    monkeypatch.setattr(vision, "extract_events", rejected)

    assert cli.main(["parse", "shot.png", "--dry-run"]) == 1
    assert "Anthropic rejected your API key." in capsys.readouterr().err


def test_error_text_from_claude_cannot_crash_the_error_display(wired, monkeypatch, capsys):
    # ExtractionError embeds Claude's raw reply; "[/]" in it used to be read
    # as rich markup by the error printer and raise MarkupError.
    import syllabus_cal.vision as vision

    def unparseable(*a, **k):
        raise vision.ExtractionError('Raw response: {"title": "Notes [/] here"')

    monkeypatch.setattr(vision, "extract_events", unparseable)

    assert cli.main(["parse", "shot.png", "--dry-run"]) == 1
    assert "Notes [/] here" in capsys.readouterr().err


def test_failed_sign_in_is_not_reported_as_the_user_declining(monkeypatch, capsys):
    import syllabus_cal.calendar_client as calendar_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    signed_out(monkeypatch)

    def blocked():
        raise calendar_client.GoogleConnectionError("Google didn't grant access.")

    monkeypatch.setattr(calendar_client, "run_auth_flow", blocked)
    answer_with(monkeypatch, "")  # Enter = yes, sign in

    assert cli.main([]) == 0
    captured = capsys.readouterr()
    assert "didn't grant access" in captured.err
    assert "No problem" not in captured.out


def test_ctrl_c_anywhere_exits_quietly(wired, monkeypatch, capsys):
    import syllabus_cal.vision as vision

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(vision, "extract_events", interrupted)

    assert cli.main(["parse", "shot.png", "--dry-run"]) == 130
    assert "Cancelled." in capsys.readouterr().out


def test_bad_key_is_caught_before_google_sign_in_on_launch(monkeypatch, capsys):
    import syllabus_cal.calendar_client as calendar_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-Ggj...hAAA")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    connects = []
    monkeypatch.setattr(calendar_client, "connect", lambda: connects.append(1))

    assert cli.main([]) == 2
    assert connects == []
    assert "shortened preview" in capsys.readouterr().err
