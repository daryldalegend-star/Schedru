import io
import json
from datetime import date, time
from pathlib import Path

import pytest
from rich.console import Console

from syllabus_cal.confirm import ConfirmationAborted, confirm_events, render_summary
from syllabus_cal.schema import ExtractedEvent, ExtractionResult

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.json"


def quiet_console() -> Console:
    # force_terminal=False keeps rich from emitting ANSI codes into the buffer.
    return Console(file=io.StringIO(), width=120, force_terminal=False)


def scripted(*answers: str):
    """An `ask` that replays answers in order and fails loudly on an
    unexpected extra prompt (catches over-prompting regressions)."""
    queue = list(answers)

    def ask(prompt: str) -> str:
        if not queue:
            raise AssertionError(f"Unexpected extra prompt: {prompt!r}")
        return queue.pop(0)

    return ask


def clean_event(**overrides) -> ExtractedEvent:
    data = {
        "title": "General Biology Lecture",
        "event_type": "class",
        "start_date": date(2026, 9, 3),
        "start_time": time(9, 50),
        "end_time": time(11, 10),
        "location": "Busch SEC 118",
        "confidence": 0.94,
        "ambiguity_flags": [],
    }
    data.update(overrides)
    return ExtractedEvent(**data)


def flagged_event(**overrides) -> ExtractedEvent:
    data = {
        "title": "Midterm Exam",
        "event_type": "exam",
        "start_date": date(2026, 10, 15),
        "start_time": time(7, 0),
        "confidence": 0.45,
        "ambiguity_flags": ["AM/PM unclear"],
    }
    data.update(overrides)
    return ExtractedEvent(**data)


def test_empty_list_returns_nothing():
    assert confirm_events([], quiet_console(), scripted()) == []


def test_clean_event_defaults_to_add_on_enter():
    event = clean_event()
    confirmed = confirm_events([event], quiet_console(), scripted(""))
    assert confirmed == [event]


def test_flagged_event_defaults_to_skip_on_enter():
    # The core safety property: a low-confidence event is never added by
    # someone hitting Enter through the prompts.
    confirmed = confirm_events([flagged_event()], quiet_console(), scripted(""))
    assert confirmed == []


def test_flagged_event_can_be_added_explicitly():
    event = flagged_event()
    confirmed = confirm_events([event], quiet_console(), scripted("a"))
    assert confirmed == [event]


def test_clean_event_can_be_skipped_explicitly():
    assert confirm_events([clean_event()], quiet_console(), scripted("s")) == []


def test_invalid_answer_reprompts_then_accepts():
    event = clean_event()
    confirmed = confirm_events([event], quiet_console(), scripted("maybe", "?", "a"))
    assert confirmed == [event]


def test_mixed_batch_keeps_only_confirmed_events_in_order():
    first, second, third = clean_event(title="One"), clean_event(title="Two"), clean_event(title="Three")
    confirmed = confirm_events([first, second, third], quiet_console(), scripted("a", "s", "a"))
    assert [e.title for e in confirmed] == ["One", "Three"]


def test_edit_updates_title_and_date_then_adds():
    console = quiet_console()
    # e -> title, date, start time, end time, location -> confirm
    ask = scripted("e", "Bio Lecture", "2026-09-08", "", "", "", "a")
    confirmed = confirm_events([clean_event()], console, ask)

    assert len(confirmed) == 1
    assert confirmed[0].title == "Bio Lecture"
    assert confirmed[0].start_date == date(2026, 9, 8)
    assert confirmed[0].start_time == time(9, 50)


def test_editing_a_flagged_event_flips_the_default_to_add():
    # Having actively reviewed it, Enter should now mean add, not skip.
    ask = scripted("e", "", "", "14:00", "", "", "")
    confirmed = confirm_events([flagged_event()], quiet_console(), ask)

    assert len(confirmed) == 1
    assert confirmed[0].start_time == time(14, 0)


def test_clearing_start_time_makes_it_all_day_and_skips_end_time_prompt():
    # No end-time answer is queued; scripted() raises if it's asked for.
    ask = scripted("e", "", "", "-", "", "a")
    confirmed = confirm_events([clean_event()], quiet_console(), ask)

    assert confirmed[0].start_time is None
    assert confirmed[0].end_time is None


def test_clearing_location():
    ask = scripted("e", "", "", "", "", "-", "a")
    confirmed = confirm_events([clean_event()], quiet_console(), ask)
    assert confirmed[0].location is None


def test_invalid_date_reprompts():
    ask = scripted("e", "", "not-a-date", "2026-09-08", "", "", "", "a")
    confirmed = confirm_events([clean_event()], quiet_console(), ask)
    assert confirmed[0].start_date == date(2026, 9, 8)


def test_invalid_time_reprompts():
    ask = scripted("e", "", "", "25:99", "14:00", "", "", "a")
    confirmed = confirm_events([clean_event()], quiet_console(), ask)
    assert confirmed[0].start_time == time(14, 0)


def test_interrupt_aborts_the_whole_batch():
    def ask(prompt: str) -> str:
        raise KeyboardInterrupt

    with pytest.raises(ConfirmationAborted):
        confirm_events([clean_event()], quiet_console(), ask)


def test_eof_aborts_the_whole_batch():
    def ask(prompt: str) -> str:
        raise EOFError

    with pytest.raises(ConfirmationAborted):
        confirm_events([clean_event()], quiet_console(), ask)


def test_markup_in_model_text_renders_literally_instead_of_crashing():
    # "[/]" raises MarkupError unescaped; "[dim]" silently disappears.
    event = clean_event(title="Study Group [/]", location="Room [dim]", notes="See [b] handout")
    console = quiet_console()

    confirmed = confirm_events([event], console, scripted("a"))
    output = console.file.getvalue()

    assert confirmed == [event]
    assert "[/]" in output
    assert "[dim]" in output


def test_prompt_shows_which_default_enter_will_take():
    # Square brackets here would be eaten by rich's markup parser, hiding the
    # default from the one prompt where it matters most.
    seen: list[str] = []

    def ask(prompt: str) -> str:
        seen.append(prompt)
        return "s"

    confirm_events([clean_event()], quiet_console(), ask)
    assert "default: add" in seen[0]

    seen.clear()
    confirm_events([flagged_event()], quiet_console(), ask)
    assert "default: skip" in seen[0]


def test_summary_groups_clear_and_flagged_separately():
    console = quiet_console()
    render_summary([clean_event(), flagged_event()], console)
    output = console.file.getvalue()

    assert "Looks clear" in output
    assert "Needs review" in output


def test_fixture_parses_and_round_trips_through_the_ui():
    result = ExtractionResult.model_validate(json.loads(FIXTURE.read_text()))
    assert len(result.events) == 4

    # Enter through all four: the two clean ones are added, the two flagged
    # ones are skipped.
    confirmed = confirm_events(result.events, quiet_console(), scripted("", "", "", ""))
    assert [e.title for e in confirmed] == ["General Biology Lecture", "Problem Set 3 due"]
