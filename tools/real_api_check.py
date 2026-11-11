"""Read the sample screenshots with the real Claude API and show what comes back.

    ANTHROPIC_API_KEY=... python tools/real_api_check.py

Everything else in this repository is tested against stand-ins. This is the
one check that spends real money (a few cents) to answer the question the
stand-ins can't: given a real image, does Claude return events that are right
and that fit the schema? It prints every event in full, what the reads cost,
and whether a handful of facts a person would insist on came out correctly.

Run by hand from the "Real API check" workflow on GitHub, which supplies the
key from the repository's secrets so it never has to be pasted anywhere else.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, time as clock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCREENS = ROOT / "tests" / "fixtures" / "screens"
# The samples are dated around here; a fixed "today" keeps "this Monday" and
# "the next Thursday" meaning the same thing whenever this is run.
TODAY = date(2026, 11, 11)

# Dollars per million tokens for the model in vision.MODEL (Claude Sonnet 5).
INPUT_PRICE, OUTPUT_PRICE = 2.00, 10.00


class Recorder:
    """Looks like an Anthropic client to extract_events, and keeps the receipts."""

    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        started = time.monotonic()
        response = self._client.messages.create(**kwargs)
        self.calls.append(
            {
                "seconds": time.monotonic() - started,
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "stop": response.stop_reason,
                "model": response.model,
            }
        )
        return response


def cost(calls: list[dict]) -> float:
    return sum(c["input"] * INPUT_PRICE + c["output"] * OUTPUT_PRICE for c in calls) / 1_000_000


def find(events, **wanted):
    """The first event matching every given attribute (callables are predicates)."""
    for event in events:
        if all(
            (check(getattr(event, name)) if callable(check) else getattr(event, name) == check)
            for name, check in wanted.items()
        ):
            return event
    return None


def mentions(*words):
    return lambda text: bool(text) and all(word.lower() in text.lower() for word in words)


def expectations(name: str, events) -> list[tuple[str, bool]]:
    if name == "flyer":
        event = events[0] if events else None
        return [
            ("one event, not several", len(events) == 1),
            ("on Thursday Nov 19, 2026", bool(event) and event.start_date == date(2026, 11, 19)),
            ("the bare 7:30 is read as evening", bool(event) and event.start_time == clock(19, 30)),
            ("an end time is filled in", bool(event) and event.end_time is not None),
            ("BSC is expanded to Busch Student Center", bool(event) and mentions("Busch Student Center")(event.location)),
            ("the room is kept", bool(event) and "120" in (event.location or "")),
            ("an address a map can find", bool(event) and mentions("Piscataway")(event.address)),
            ("the pizza made it into the notes", bool(event) and mentions("pizza")(event.notes)),
            ("it says what it assumed", bool(event) and len(event.assumptions) >= 1),
            ("it's sure enough to be ticked by default", bool(event) and not event.needs_review),
            ("it's a one-time event", bool(event) and event.recurrence is None),
        ]

    if name == "syllabus":
        lecture = find(events, event_type="class", recurrence=lambda r: r is not None)
        midterm = find(events, start_date=date(2026, 11, 19), event_type="exam")
        final = find(events, start_date=date(2026, 12, 15))
        lab = find(events, start_date=date(2026, 11, 20))
        hours = find(events, event_type="office_hours")
        return [
            ("the lecture is one repeating event", lecture is not None),
            ("...on Tuesdays and Thursdays", bool(lecture) and set(lecture.recurrence.days_of_week) == {"TU", "TH"}),
            ("...weekly", bool(lecture) and lecture.recurrence.freq == "WEEKLY"),
            ("...9:50 read as morning", bool(lecture) and lecture.start_time == clock(9, 50)),
            ("...ending Dec 10", bool(lecture) and lecture.recurrence.until == date(2026, 12, 10)),
            ("...skipping Thanksgiving", bool(lecture) and date(2026, 11, 26) in lecture.recurrence.exdates),
            ("...starting on or after today", bool(lecture) and lecture.start_date >= TODAY),
            ("Midterm 2 is Nov 19 at 7:40 PM", bool(midterm) and midterm.start_time == clock(19, 40)),
            ("the final is Dec 15 at 8:00 AM", bool(final) and final.start_time == clock(8, 0)),
            ("the lab report is due Nov 20", bool(lab) and lab.event_type == "assignment"),
            ("office hours are found", hours is not None),
            ("...2:00 read as afternoon", bool(hours) and hours.start_time == clock(14, 0)),
            ("no event per lecture", len(events) <= 8),
        ]

    if name == "email":
        event = events[0] if events else None
        return [
            ("one event", len(events) == 1),
            ("\"this Monday\" is Nov 16", bool(event) and event.start_date == date(2026, 11, 16)),
            ("\"6 to 7:30\" is the evening", bool(event) and event.start_time == clock(18, 0) and event.end_time == clock(19, 30)),
            ("the room is kept", bool(event) and "111" in (event.location or "")),
            ("the Zoom link is kept", bool(event) and "zoom" in f"{event.location} {event.notes}".lower()),
        ]

    return []


def show(event) -> None:
    when = f"{event.start_date}  {event.start_time or 'all day'}"
    if event.end_time:
        when += f" - {event.end_time}"
    print(f"    {event.title}   [{event.event_type}, confidence {event.confidence:.2f}]")
    print(f"      when      {when}")
    if event.recurrence:
        rule = event.recurrence
        print(f"      repeats   {rule.freq} {','.join(rule.days_of_week)} until {rule.until} except {[str(d) for d in rule.exdates]}")
    print(f"      place     {event.location}")
    print(f"      address   {event.address}")
    print(f"      notes     {event.notes}")
    for assumed in event.assumptions:
        print(f"      ~ {assumed}")
    for flag in event.ambiguity_flags:
        print(f"      ! {flag}")


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("No ANTHROPIC_API_KEY was provided, so nothing was sent to Claude.")
        print()
        print("To run this on GitHub: open the repository's Settings, then")
        print("Secrets and variables, then Actions, press New repository secret,")
        print("name it ANTHROPIC_API_KEY, paste the key, and run this workflow again.")
        return 2

    from anthropic import Anthropic

    from syllabus_cal.vision import MODEL, ClaudeAPIError, ExtractionError, ImageError, extract_events

    client = Anthropic(api_key=key, timeout=180.0, max_retries=1)
    print(f"Model: {MODEL}    Today is taken to be {TODAY}")

    total_calls: list[dict] = []
    failed: list[str] = []
    for name in ("flyer", "syllabus", "email"):
        print(f"\n=== {name}.png ===")
        recorder = Recorder(client)
        try:
            result = extract_events(SCREENS / f"{name}.png", recorder, today=TODAY)
        except (ClaudeAPIError, ExtractionError, ImageError) as error:
            print(f"  FAILED: {str(error)[:600]}")
            failed.append(f"{name}: the read failed")
            total_calls += recorder.calls
            if isinstance(error, ClaudeAPIError) and error.kind in ("auth", "permission", "bad_request"):
                break  # the key or the account is the problem; more reads won't help
            continue

        total_calls += recorder.calls
        for event in result.events:
            show(event)
        for call in recorder.calls:
            print(
                f"  {call['seconds']:.1f}s, {call['input']} tokens in, {call['output']} out, "
                f"stopped on {call['stop']}, served by {call['model']}"
            )
        print(f"  cost of this read: {cost(recorder.calls) * 100:.2f} cents ({len(recorder.calls)} request)")
        for label, ok in expectations(name, result.events):
            print(f"  {'ok  ' if ok else 'MISS'}  {label}")
            if not ok:
                failed.append(f"{name}: {label}")

    print("\n=== summary ===")
    print(f"{len(total_calls)} requests, {cost(total_calls) * 100:.2f} cents in total")
    if failed:
        print(f"{len(failed)} expectation(s) missed:")
        for item in failed:
            print(f"  - {item}")
        return 1
    print("Every expectation met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
