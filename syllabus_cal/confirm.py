"""Terminal confirmation UI: confirm / edit / skip each extracted event.

Nothing here writes to Calendar -- it returns the events the user approved and
the caller does the writing. Events flagged for review (low confidence or any
ambiguity flag) default to skip, so adding a possibly-wrong event takes an
explicit yes rather than an absent no.

Every model-derived string goes through rich.markup.escape: a title or note
containing "[/]" raises MarkupError and takes down the whole render, and one
containing "[dim]" silently disappears.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .schema import ExtractedEvent

Ask = Callable[[str], str]

_KEEP = ""
_CLEAR = "-"


class ConfirmationAborted(RuntimeError):
    """Raised when the user Ctrl-C's or EOFs out of the confirmation loop."""


def format_when(event: ExtractedEvent) -> str:
    if event.start_time is None:
        return "all-day"
    if event.end_time:
        return f"{event.start_time:%H:%M}-{event.end_time:%H:%M}"
    return f"{event.start_time:%H:%M}"


def format_recurrence(event: ExtractedEvent) -> str:
    rule = event.recurrence
    if rule is None:
        return "one-time"
    days = ",".join(rule.days_of_week)
    text = f"{rule.freq}" + (f" {days}" if days else "")
    if rule.until:
        text += f" until {rule.until}"
    if rule.exdates:
        text += f" ({len(rule.exdates)} excluded)"
    return text


def _events_table(events: list[ExtractedEvent], title: str, style: str) -> Table:
    table = Table(title=title, title_style=style, show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Date")
    table.add_column("Time")
    table.add_column("Repeats")
    table.add_column("Location")
    table.add_column("Conf.", justify="right")
    table.add_column("Assumed / flagged")

    for number, event in events:
        notes = [f"~ {a}" for a in event.assumptions] + [f"! {f}" for f in event.ambiguity_flags]
        table.add_row(
            str(number),
            escape(event.title),
            event.event_type,
            str(event.start_date),
            format_when(event),
            format_recurrence(event),
            escape(event.location or "-"),
            f"{event.confidence:.2f}",
            escape("; ".join(notes) or "-"),
        )
    return table


def render_summary(events: list[ExtractedEvent], console: Console) -> None:
    """Print the extracted events grouped by confidence, clear ones first."""
    numbered = list(enumerate(events, start=1))
    clear = [(n, e) for n, e in numbered if not e.needs_review]
    flagged = [(n, e) for n, e in numbered if e.needs_review]

    if clear:
        console.print(_events_table(clear, "Looks clear", "green"))
    if flagged:
        if clear:
            console.print()
        console.print(_events_table(flagged, "Needs review (defaults to skip)", "red"))


def render_event_detail(event: ExtractedEvent, console: Console) -> None:
    console.print(f"  [bold]{escape(event.title)}[/bold]  ({event.event_type})")
    console.print(f"  {event.start_date}  {format_when(event)}  |  {format_recurrence(event)}")
    if event.location:
        console.print(f"  Location: {escape(event.location)}")
    if event.notes:
        console.print(f"  Notes: {escape(event.notes)}")
    console.print(f"  Confidence: {event.confidence:.2f}")
    for assumed in event.assumptions:
        console.print(f"  [yellow]~ assumed: {escape(assumed)}[/yellow]")
    for flag in event.ambiguity_flags:
        console.print(f"  [red]! {escape(flag)}[/red]")


def _prompt(ask: Ask, text: str) -> str:
    try:
        return ask(text)
    except (EOFError, KeyboardInterrupt) as exc:
        raise ConfirmationAborted from exc


def _ask_text(ask: Ask, label: str, current: str) -> str:
    answer = _prompt(ask, f"  {label} (Enter = keep '{current}'): ").strip()
    return answer or current


def _ask_optional_text(ask: Ask, label: str, current: str | None) -> str | None:
    shown = current if current is not None else "none"
    answer = _prompt(
        ask, f"  {label} (Enter = keep '{shown}', '{_CLEAR}' = clear): "
    ).strip()
    if answer == _KEEP:
        return current
    if answer == _CLEAR:
        return None
    return answer


def _ask_date(ask: Ask, console: Console, label: str, current: date) -> date:
    while True:
        answer = _prompt(ask, f"  {label} (YYYY-MM-DD, Enter = keep {current}): ").strip()
        if answer == _KEEP:
            return current
        try:
            return datetime.strptime(answer, "%Y-%m-%d").date()
        except ValueError:
            console.print("  [red]Not a valid date. Use YYYY-MM-DD.[/red]")


def _ask_time(ask: Ask, console: Console, label: str, current: time | None) -> time | None:
    shown = f"{current:%H:%M}" if current else "none"
    while True:
        answer = _prompt(
            ask, f"  {label} (HH:MM, Enter = keep {shown}, '{_CLEAR}' = none): "
        ).strip()
        if answer == _KEEP:
            return current
        if answer == _CLEAR:
            return None
        try:
            return datetime.strptime(answer, "%H:%M").time()
        except ValueError:
            console.print("  [red]Not a valid time. Use 24-hour HH:MM, e.g. 14:30.[/red]")


def edit_event(event: ExtractedEvent, ask: Ask, console: Console) -> ExtractedEvent:
    """Return a copy of `event` with user-edited fields.

    Recurrence isn't editable here -- building a rule editor in the terminal
    costs more than it's worth when skipping and fixing it in Calendar works.
    """
    console.print("  [bold]Editing.[/bold] Press Enter to keep a value.")
    data = event.model_dump()

    data["title"] = _ask_text(ask, "Title", event.title)
    data["start_date"] = _ask_date(ask, console, "Start date", event.start_date)
    data["start_time"] = _ask_time(ask, console, "Start time", event.start_time)
    if data["start_time"] is None:
        data["end_time"] = None
    else:
        data["end_time"] = _ask_time(ask, console, "End time", event.end_time)
    data["location"] = _ask_optional_text(ask, "Location", event.location)

    return ExtractedEvent.model_validate(data)


def confirm_events(
    events: list[ExtractedEvent],
    console: Console | None = None,
    ask: Ask | None = None,
) -> list[ExtractedEvent]:
    """Walk the user through each event; return only the ones they approved.

    Raises ConfirmationAborted if the user interrupts, so the caller writes
    nothing rather than a half-reviewed batch.
    """
    console = console or Console()
    ask = ask or console.input

    if not events:
        console.print("[yellow]Nothing to confirm.[/yellow]")
        return []

    render_summary(events, console)

    confirmed: list[ExtractedEvent] = []
    total = len(events)

    for index, event in enumerate(events, start=1):
        console.print()
        console.rule(f"Event {index} of {total}", style="dim")
        render_event_detail(event, console)

        # An event the user has just edited defaults to "add" -- they've
        # actively reviewed it, so the model's original flags shouldn't keep
        # steering the default toward skip.
        edited = False

        while True:
            default = "add" if (edited or not event.needs_review) else "skip"
            answer = (
                _prompt(ask, f"  (a)dd / (e)dit / (s)kip  (default: {default}): ")
                .strip()
                .lower()
            )

            if answer == "":
                answer = "a" if default == "add" else "s"

            if answer in ("a", "add", "y", "yes"):
                confirmed.append(event)
                console.print("  [green]Added.[/green]")
                break
            if answer in ("s", "skip", "n", "no"):
                console.print("  [dim]Skipped.[/dim]")
                break
            if answer in ("e", "edit"):
                event = edit_event(event, ask, console)
                edited = True
                console.print()
                render_event_detail(event, console)
                continue

            console.print("  [red]Please enter a, e, or s.[/red]")

    console.print()
    console.print(f"[bold]{len(confirmed)} of {total} event(s) confirmed.[/bold]")
    return confirmed
