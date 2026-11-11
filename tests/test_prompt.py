from datetime import date

from syllabus_cal.prompts.extract_events import render_prompt


def test_render_prompt_includes_today_and_is_valid_format_string():
    prompt = render_prompt(date(2026, 9, 9))
    assert "2026-09-09" in prompt
    assert "No semester start/end date was provided" in prompt
    # The literal JSON example braces must survive .format() unescaped.
    assert '"events": [' in prompt


def test_render_prompt_includes_semester_range():
    prompt = render_prompt(date(2026, 9, 9), date(2026, 9, 2), date(2026, 12, 10))
    assert "2026-09-02" in prompt
    assert "2026-12-10" in prompt


def test_prompt_never_asks_for_a_null_start_date():
    """The schema requires a date, so the prompt mustn't invite leaving it out:
    that produced replies that failed validation and cost a second request."""
    prompt = render_prompt(date(2026, 9, 9))
    assert 'can never be null is "start_date"' in prompt
    assert '"start_date": "YYYY-MM-DD",' in prompt  # and the shape agrees: no "| null"
