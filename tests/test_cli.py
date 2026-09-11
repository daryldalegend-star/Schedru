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
    monkeypatch.setattr(calendar_client, "is_authenticated", lambda: True)

    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://calendar.google.com/event?eid=xyz"
    }
    monkeypatch.setattr(calendar_client, "get_calendar_service", lambda *a, **k: service)
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


def test_unauthenticated_write_run_declines_gracefully(monkeypatch, capsys, events):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)

    import syllabus_cal.calendar_client as calendar_client

    monkeypatch.setattr(calendar_client, "is_authenticated", lambda: False)
    monkeypatch.setattr(cli.console, "input", lambda *a, **k: "n")

    exit_code = cli.main(["parse", "shot.png"])

    assert exit_code == 2
    assert "not signed in" in capsys.readouterr().err.lower()


def test_cancelling_confirmation_writes_nothing(wired, monkeypatch, capsys):
    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.console, "input", interrupt)

    exit_code = cli.main(["parse", "shot.png"])

    assert exit_code == 1
    wired.events.return_value.insert.assert_not_called()
    assert "cancelled" in capsys.readouterr().out.lower()
