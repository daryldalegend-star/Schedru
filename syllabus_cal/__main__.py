"""CLI entry point: python -m syllabus_cal [parse|auth] ...

Running with no subcommand shows a short tutorial and, if you're not signed
in to Google yet, offers to run the sign-in flow right there.

Build status: `parse --dry-run` (image -> validated JSON) and `auth`
(Google sign-in, optionally writing one test event) are both live. Writing
real extracted events to Calendar needs the confirmation UI and end-to-end
wiring, which are later build steps.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .schema import CONFIDENCE_REVIEW_THRESHOLD, ExtractionResult

console = Console()
err_console = Console(stderr=True)

TUTORIAL_TEXT = """[bold]syllabus_cal[/bold] turns a screenshot of a syllabus, club flyer, or \
schedule email into Google Calendar events.

The intended flow:
  1. [bold]parse[/bold] an image -- Claude reads it and extracts every dated commitment
  2. you review what it found (anything uncertain is flagged, never guessed)
  3. confirmed events get written to your Google Calendar

[bold]Commands available right now:[/bold]
  [cyan]python -m syllabus_cal parse <image> --dry-run[/cyan]
      Extract events from a screenshot and print them. Never touches Calendar.
      Add --semester-start/--semester-end (YYYY-MM-DD) to help resolve recurring
      "until" dates.

  [cyan]python -m syllabus_cal auth[/cyan]
      Sign in with your Google account (opens a browser). Needed before Calendar
      writing will work. See SETUP.md if you haven't created credentials.json yet.

[bold]Still being built:[/bold] reviewing/editing each event before it's written, and
writing to Calendar itself. Until then, --dry-run is the way to try it."""


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

    auth_cmd = subparsers.add_parser("auth", help="Run the Google Calendar OAuth flow")
    auth_cmd.add_argument(
        "--test-event",
        action="store_true",
        help="After signing in, write one hardcoded test event to confirm write access",
    )

    return parser


def _render_events_table(result: ExtractionResult) -> None:
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
    table.add_column("Location")
    table.add_column("Confidence")
    table.add_column("Ambiguity flags")

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
            event.title,
            event.event_type,
            str(event.start_date),
            time_str,
            recurrence_str,
            event.location or "-",
            confidence_str,
            "; ".join(event.ambiguity_flags) or "-",
        )

    console.print(table)


def cmd_parse(args: argparse.Namespace) -> int:
    if not args.dry_run:
        err_console.print(
            "[red]Writing to Calendar isn't wired up yet (it needs the confirmation UI, "
            "a later build step). Re-run with --dry-run.[/red]"
        )
        return 2

    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        err_console.print(
            "[red]ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your "
            "key.[/red]"
        )
        return 2

    from anthropic import Anthropic

    from .vision import ExtractionError, ImageError, extract_events

    client = Anthropic(api_key=api_key)

    try:
        result = extract_events(
            args.image,
            client,
            today=date.today(),
            semester_start=args.semester_start,
            semester_end=args.semester_end,
        )
    except ImageError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1
    except ExtractionError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1

    _render_events_table(result)
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    from .calendar_client import AuthError, create_hardcoded_test_event, get_calendar_service, run_auth_flow

    console.print("Opening your browser to sign in with Google...")
    try:
        run_auth_flow()
    except AuthError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1

    console.print("[green]Signed in. token.json saved to the project root.[/green]")

    if getattr(args, "test_event", False):
        try:
            service = get_calendar_service()
            created = create_hardcoded_test_event(service)
        except AuthError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1
        except Exception as exc:  # googleapiclient.errors.HttpError, network errors, etc.
            err_console.print(f"[red]Signed in, but writing the test event failed: {exc}[/red]")
            return 1

        link = created.get("htmlLink", "(no link returned)")
        console.print(f"[green]Test event created:[/green] {link}")
        console.print("It's tomorrow at 9:00-9:30 AM, tagged as syllabus_cal test data. Safe to delete.")

    return 0


def cmd_tutorial(args: argparse.Namespace) -> int:
    from .calendar_client import is_authenticated

    console.print(Panel(TUTORIAL_TEXT, title="syllabus_cal", border_style="cyan", expand=False))

    if is_authenticated():
        console.print("\n[green]You're already signed in to Google.[/green]")
        return 0

    console.print(
        "\n[yellow]You're not signed in to Google yet.[/yellow] (--dry-run parsing works "
        "either way; signing in is only needed for the eventual Calendar writing.)"
    )
    try:
        answer = console.input("Sign in with Google now? (y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        answer = "n"

    if answer == "y":
        return cmd_auth(argparse.Namespace(test_event=False))

    console.print("No problem — run [bold]python -m syllabus_cal auth[/bold] whenever you're ready.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        return cmd_tutorial(args)
    if args.command == "parse":
        return cmd_parse(args)
    if args.command == "auth":
        return cmd_auth(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
