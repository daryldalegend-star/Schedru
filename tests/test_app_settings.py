"""The API key file and the app's preferences."""

from __future__ import annotations

import os
import stat

import pytest

from syllabus_cal.app.settings import (
    EMPTY_KEY_MESSAGE,
    MASKED_KEY_MESSAGE,
    NOT_A_KEY_MESSAGE,
    KeyFormatError,
    Settings,
    clean_key,
    key_hint,
)

GOOD_KEY = "sk-ant-api03-" + "A1b2C3d4" * 10 + "-hAAA"


def test_clean_key_accepts_a_real_looking_key_unchanged():
    assert clean_key(GOOD_KEY) == GOOD_KEY


@pytest.mark.parametrize(
    "pasted",
    [
        f"  {GOOD_KEY}\n",
        f'"{GOOD_KEY}"',
        f"'{GOOD_KEY}'",
        f"ANTHROPIC_API_KEY={GOOD_KEY}",
        f'ANTHROPIC_API_KEY="{GOOD_KEY}"',
    ],
)
def test_clean_key_strips_what_people_paste_around_a_key(pasted):
    assert clean_key(pasted) == GOOD_KEY


@pytest.mark.parametrize("pasted", ["sk-ant-api03-Ggj...hAAA", "sk-ant-api03-Ggj…hAAA"])
def test_clean_key_explains_the_masked_preview(pasted):
    with pytest.raises(KeyFormatError) as caught:
        clean_key(pasted)
    assert str(caught.value) == MASKED_KEY_MESSAGE


@pytest.mark.parametrize("pasted", ["", "   ", '""', "ANTHROPIC_API_KEY="])
def test_clean_key_rejects_nothing(pasted):
    with pytest.raises(KeyFormatError) as caught:
        clean_key(pasted)
    assert str(caught.value) == EMPTY_KEY_MESSAGE


@pytest.mark.parametrize(
    "pasted",
    [
        "hello",
        "sk-ant-short",
        "sk-proj-" + "x" * 60,  # another vendor's key
        GOOD_KEY[:30] + " " + GOOD_KEY[30:],  # a line break in the middle
    ],
)
def test_clean_key_rejects_things_that_cannot_be_a_key(pasted):
    with pytest.raises(KeyFormatError) as caught:
        clean_key(pasted)
    assert str(caught.value) == NOT_A_KEY_MESSAGE


def test_key_hint_never_contains_enough_to_use():
    hint = key_hint(GOOD_KEY)
    assert hint == "sk-ant-…hAAA"
    assert GOOD_KEY[10:40] not in hint


def test_no_key_anywhere_is_none(tmp_path):
    assert Settings(tmp_path, environ={}).api_key() is None


def test_saved_key_round_trips_and_is_private(tmp_path):
    settings = Settings(tmp_path, environ={})
    settings.save_api_key(GOOD_KEY)

    assert settings.api_key() == GOOD_KEY
    assert (tmp_path / ".env").read_text() == f"ANTHROPIC_API_KEY={GOOD_KEY}\n"
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_saving_replaces_the_old_key_and_keeps_other_lines(tmp_path):
    (tmp_path / ".env").write_text(
        "# my settings\nOTHER=1\nANTHROPIC_API_KEY=sk-ant-old\nLAST=2\n"
    )
    settings = Settings(tmp_path, environ={})
    settings.save_api_key(GOOD_KEY)

    assert (tmp_path / ".env").read_text() == (
        f"# my settings\nOTHER=1\nANTHROPIC_API_KEY={GOOD_KEY}\nLAST=2\n"
    )
    assert settings.api_key() == GOOD_KEY


def test_saving_collapses_duplicate_key_lines(tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two\n")
    settings = Settings(tmp_path, environ={})
    settings.save_api_key(GOOD_KEY)

    assert (tmp_path / ".env").read_text().count("ANTHROPIC_API_KEY") == 1
    assert settings.api_key() == GOOD_KEY


def test_the_file_wins_over_the_environment(tmp_path):
    settings = Settings(tmp_path, environ={"ANTHROPIC_API_KEY": "from-environment"})
    assert settings.api_key() == "from-environment"

    settings.save_api_key(GOOD_KEY)
    assert settings.api_key() == GOOD_KEY


def test_an_empty_key_line_falls_back_to_the_environment(tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")
    assert Settings(tmp_path, environ={"ANTHROPIC_API_KEY": "fallback"}).api_key() == "fallback"
    assert Settings(tmp_path, environ={}).api_key() is None


def test_saving_does_not_leave_a_temporary_file_behind(tmp_path):
    Settings(tmp_path, environ={}).save_api_key(GOOD_KEY)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_preferences_persist_across_instances(tmp_path):
    first = Settings(tmp_path, environ={})
    assert first.get("tutorial_seen", False) is False
    first.set("tutorial_seen", True)
    first.set("other", {"a": 1})

    second = Settings(tmp_path, environ={})
    assert second.get("tutorial_seen") is True
    assert second.get("other") == {"a": 1}
    assert second.get("missing", "default") == "default"


@pytest.mark.parametrize("garbage", ["", "not json", "[1, 2, 3]", '"a string"'])
def test_a_corrupt_settings_file_reads_as_empty_and_recovers(tmp_path, garbage):
    (tmp_path / "settings.json").write_text(garbage)
    settings = Settings(tmp_path, environ={})

    assert settings.get("tutorial_seen", False) is False
    settings.set("tutorial_seen", True)
    assert Settings(tmp_path, environ={}).get("tutorial_seen") is True


def test_the_data_folder_is_created_on_first_write(tmp_path):
    settings = Settings(tmp_path / "not" / "there" / "yet", environ={})
    settings.set("x", 1)
    assert settings.get("x") == 1
