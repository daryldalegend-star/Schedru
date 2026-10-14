"""CLI entry point: python -m syllabus_cal [parse|auth] ...

Running with no subcommand (what start.command does) shows a short tutorial,
makes sure Google Calendar is connected -- signing in if needed -- and then
asks for a screenshot by drag-and-drop.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .schema import CONFIDENCE_REVIEW_THRESHOLD, ExtractionResult

console = Console()
err_console = Console(stderr=True)

TUTORIAL_TEXT = """[bold]syllabus_cal[/bold] turns a screenshot of a syllabus, club flyer, \
Instagram post, or schedule email into Google Calendar events.

  1. Drag a screenshot into this window and press Enter.
  2. Claude reads it and fills in what you'd want to know: the full place and
     address (so Maps can find it), the time, and the useful details.
  3. You check each event -- [bold]a[/bold] add, [bold]e[/bold] edit, [bold]s[/bold] skip -- and it goes on
     your calendar.

Anything it filled in itself is marked [yellow]~[/yellow] so you can check it. Anything it
couldn't work out is marked [red]![/red] and skipped unless you say otherwise.
[bold]Nothing is added without your yes.[/bold]"""


def _error(message: object) -> None:
    # Messages can carry text from Claude or Google, which rich would
    # otherwise try to read as markup (and crash on, for "[/]").
    err_console.print(f"[red]{escape(str(message))}[/red]")


def _ask_yes(question: str, default: bool) -> bool:
    hint = "(Y/n)" if default else "(y/N)"
    try:
        answer = console.input(f"{question} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid date, expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syllabus_cal")
    subparsers = parser.add_subparsers(dest="command")

    parse_cmd = subparsers.add_parser("parse", help="Extract events from a screenshot")
    parse_cmd.add_argument("image", type=Path, help="Path to the screenshot image")
    parse_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print extracted events and exit; never write to Calendar",
    )
    parse_cmd.add_argument(
        "--semester-start", type=_parse_date, default=None, metavar="YYYY-MM-DD"
    )
    parse_cmd.add_argument(
        "--semester-end", type=_parse_date, default=None, metavar="YYYY-MM-DD"
    )

    auth_cmd = subparsers.add_parser("auth", help="Sign in with Google (or switch accounts)")
    auth_cmd.add_argument(
        "--test-event",
        action="store_true",
        help="After signing in, write one hardcoded test event to confirm write access",
    )

    return parser


def _render_events_table(result: ExtractionResult) -> None:
    from .calendar_client import calendar_location

    if not result.events:
        console.print("[yellow]No dated commitments found in this image.[/yellow]")
        return

    table = Table(title="Extracted events", show_lines=True)
    table.add_column("Flag")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Start")
    table.add_column("Time")
    table.add_column("Recurrence")
    table.add_column("Where")
    table.add_column("Confidence")
    table.add_column("Assumed / flagged")

    for event in result.events:
        flag = "[bold red]REVIEW[/bold red]" if event.needs_review else "[green]ok[/green]"

        if event.start_time is None:
            time_str = "all-day"
        elif event.end_time:
            time_str = f"{event.start_time}–{event.end_time}"
        else:
            time_str = str(event.start_time)

        recurrence_str = "-"
        if event.recurrence:
            days = ",".join(event.recurrence.days_of_week) or "-"
            recurrence_str = f"{event.recurrence.freq} [{days}]"
            if event.recurrence.until:
                recurrence_str += f" until {event.recurrence.until}"

        confidence_str = f"{event.confidence:.2f}"
        if event.confidence < CONFIDENCE_REVIEW_THRESHOLD:
            confidence_str = f"[red]{confidence_str}[/red]"

        table.add_row(
            flag,
            escape(event.title),
            event.event_type,
            str(event.start_date),
            time_str,
            escape(recurrence_str),
            escape(calendar_location(event) or "-"),
            confidence_str,
            escape(
                "; ".join(
                    [f"~ {a}" for a in event.assumptions]
                    + [f"! {f}" for f in event.ambiguity_flags]
                )
                or "-"
            ),
        )

    console.print(table)


def _sign_in():
    """Run the browser sign-in, then connect. (service, calendar name) or None."""
    from .calendar_client import AuthError, GoogleConnectionError, connect, run_auth_flow

    console.print("Opening your browser to sign in with Google...")
    try:
        run_auth_flow()
        return connect()
    except (AuthError, GoogleConnectionError) as exc:
        _error(exc)
        return None


def _connect_calendar():
    """A working (service, calendar name), signing in first if needed. None if not."""
    from .calendar_client import AuthError, GoogleConnectionError, connect

    try:
        return connect()
    except GoogleConnectionError as exc:
        _error(exc)
        return None
    except AuthError as exc:
        console.print(f"[yellow]{escape(str(exc))}[/yellow]")

    if not _ask_yes("Sign in with Google now?", default=True):
        console.print("No problem — you'll be asked to sign in again next time.")
        return None
    return _sign_in()


def _show_connected(calendar_name: str) -> None:
    console.print(f"[green]Connected to Google Calendar: {escape(calendar_name)}[/green]")


def _render_write_summary(outcomes) -> None:
    created = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]

    if created:
        console.print()
        console.print(f"[green]Added {len(created)} event(s) to your calendar:[/green]")
        for outcome in created:
            link = outcome.link or "(no link returned)"
            console.print(f"  • {escape(outcome.event.title)}")
            console.print(f"    {escape(link)}")

    if failed:
        console.print()
        err_console.print(f"[red]{len(failed)} event(s) could NOT be written:[/red]")
        for outcome in failed:
            err_console.print(f"  • {escape(outcome.event.title)} — {escape(outcome.error or '')}")
        err_console.print(
            "\n[yellow]The events listed above were not created. Everything under "
            "'Added' already exists — if you re-run this image, skip those at the "
            "confirmation step so you don't get duplicates.[/yellow]"
        )


def _load_api_key() -> str | None:
    """The Anthropic key from .env, or None after explaining what's wrong with it."""
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _error("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
        return None
    if "..." in api_key or "…" in api_key:
        # After creation the console only ever shows a preview like
        # "sk-ant-api03-Ggj...hAAA", and copying that is an easy mistake.
        _error(
            "Your .env has the shortened preview of your API key (the one with '...' in "
            "the middle), not the key itself. Anthropic shows the full key only once, "
            "when you create it: make a new key at console.anthropic.com -> API keys, "
            "copy it from that dialog, and paste it into .env."
        )
        return None
    return api_key


def cmd_parse(args: argparse.Namespace) -> int:
    api_key = _load_api_key()
    if api_key is None:
        return 2

    calendar = None
    if not args.dry_run:
        # Before the vision call, so a sign-in problem doesn't cost a request.
        calendar = _connect_calendar()
        if calendar is None:
            _error("Not connected to Google Calendar, so nothing can be added.")
            return 2

    from anthropic import Anthropic

    from .vision import ClaudeAPIError, ExtractionError, ImageError, extract_events

    client = Anthropic(api_key=api_key)

    try:
        with console.status("Reading the screenshot..."):
            result = extract_events(
                args.image,
                client,
                today=date.today(),
                semester_start=args.semester_start,
                semester_end=args.semester_end,
            )
    except (ImageError, ExtractionError, ClaudeAPIError) as exc:
        _error(exc)
        return 1

    if args.dry_run:
        _render_events_table(result)
        console.print("[dim]Dry run — nothing was written to your calendar.[/dim]")
        return 0

    if not result.events:
        console.print("[yellow]No dated commitments found in this image.[/yellow]")
        return 0

    from .calendar_client import write_events
    from .confirm import ConfirmationAborted, confirm_events

    try:
        confirmed = confirm_events(result.events, console)
    except ConfirmationAborted:
        console.print("\n[yellow]Cancelled — nothing was written.[/yellow]")
        return 1

    if not confirmed:
        console.print("[yellow]Nothing confirmed, so nothing was written.[/yellow]")
        return 0

    service, _ = calendar
    outcomes = write_events(service, confirmed)
    _render_write_summary(outcomes)

    return 0 if all(o.ok for o in outcomes) else 1


def cmd_auth(args: argparse.Namespace) -> int:
    calendar = _sign_in()
    if calendar is None:
        return 1
    service, calendar_name = calendar
    _show_connected(calendar_name)

    if args.test_event:
        from .calendar_client import create_hardcoded_test_event

        try:
            created = create_hardcoded_test_event(service)
        except Exception as exc:  # googleapiclient.errors.HttpError, network errors, etc.
            _error(f"Signed in, but writing the test event failed: {exc}")
            return 1

        link = created.get("htmlLink", "(no link returned)")
        console.print(f"[green]Test event created:[/green] {escape(link)}")
        console.print("It's tomorrow at 9:00-9:30 AM, tagged as syllabus_cal test data. Safe to delete.")

    return 0


def clean_dropped_path(raw: str) -> str:
    """Turn whatever dragging a file into a terminal produces into a real path.

    Terminal.app backslash-escapes spaces and other shell metacharacters;
    some terminals wrap the path in quotes instead.
    """
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return re.sub(r"\\(.)", r"\1", text)


def _prompt_for_screenshot() -> int:
    """Ask for an image by drag-and-drop, then run the normal parse flow."""
    console.print()
    console.print("[bold]Drag your screenshot into this window, then press Enter.[/bold]")
    console.print(
        "[dim](Type 'switch' to use a different Google account, or press Enter to quit.)[/dim]"
    )

    try:
        answer = console.input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return 0

    if not answer:
        return 0

    if answer.lower() == "switch":
        calendar = _sign_in()
        if calendar is None:
            return 1
        _show_connected(calendar[1])
        return _prompt_for_screenshot()

    return cmd_parse(
        argparse.Namespace(
            image=Path(clean_dropped_path(answer)),
            dry_run=False,
            semester_start=None,
            semester_end=None,
        )
    )


def cmd_tutorial(args: argparse.Namespace) -> int:
    console.print(Panel(TUTORIAL_TEXT, title="syllabus_cal", border_style="cyan", expand=False))
    console.print()

    # Checked up front: otherwise a bad key only surfaces after signing in to
    # Google and dragging a screenshot in, which then has to be done again.
    if _load_api_key() is None:
        return 2

    calendar = _connect_calendar()
    if calendar is None:
        return 0

    _show_connected(calendar[1])
    return _prompt_for_screenshot()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    commands = {None: cmd_tutorial, "parse": cmd_parse, "auth": cmd_auth}
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        # Ctrl-C while waiting on Claude or the browser sign-in.
        console.print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
