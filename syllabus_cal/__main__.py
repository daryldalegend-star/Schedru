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

TUTORIAL_TEXT = """[bold]syllabus_cal[/bold] turns a screenshot of a syllabus, club flyer, or \
schedule email into Google Calendar events.

The intended flow:
  1. [bold]parse[/bold] an image -- Claude reads it and extracts every dated commitment
  2. you review what it found (anything uncertain is flagged, never guessed)
  3. confirmed events get written to your Google Calendar

[bold]Commands:[/bold]
  [cyan]python -m syllabus_cal parse <image>[/cyan]
      The main one. Reads the image, shows you what it found, asks about each
      event (add / edit / skip), then writes the ones you approved.
      Add --semester-start/--semester-end (YYYY-MM-DD) to help it resolve
      recurring "until" dates.

  [cyan]python -m syllabus_cal parse <image> --dry-run[/cyan]
      Same extraction, but just prints the results and stops. Never touches
      your calendar. Good for checking what it sees before committing.

  [cyan]python -m syllabus_cal auth[/cyan]
      Sign in with your Google account (opens a browser). Add --test-event to
      also write one throwaway event confirming it works.

[bold]Nothing is ever written without you saying yes to it.[/bold] Anything the model
wasn't sure about is flagged and defaults to being skipped."""


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
            recurrence_str,
            escape(event.location or "-"),
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


def _ensure_signed_in() -> bool:
    """Offer to run sign-in if there's no token yet. True if we're good to write."""
    from .calendar_client import is_authenticated

    if is_authenticated():
        return True

    console.print(
        "[yellow]You're not signed in to Google yet[/yellow] — that's needed before "
        "anything can be written to your calendar."
    )
    try:
        answer = console.input("Sign in with Google now? (y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False

    if answer not in ("y", "yes"):
        return False

    return cmd_auth(argparse.Namespace(test_event=False)) == 0


def _render_write_summary(outcomes) -> None:
    created = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]

    if created:
        console.print()
        console.print(f"[green]Added {len(created)} event(s) to your calendar:[/green]")
        for outcome in created:
            link = outcome.link or "(no link returned)"
            console.print(f"  • {escape(outcome.event.title)}")
            console.print(f"    {link}")

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


def cmd_parse(args: argparse.Namespace) -> int:
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        err_console.print(
            "[red]ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your "
            "key.[/red]"
        )
        return 2

    # Check auth before the vision call so a sign-in problem doesn't cost an
    # API request, same reasoning as validating the image up front.
    if not args.dry_run and not _ensure_signed_in():
        err_console.print(
            "[red]Not signed in, so nothing can be written. Run "
            "`python -m syllabus_cal auth`, or use --dry-run to just see what's in "
            "the image.[/red]"
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

    if args.dry_run:
        _render_events_table(result)
        console.print("[dim]Dry run — nothing was written to your calendar.[/dim]")
        return 0

    if not result.events:
        console.print("[yellow]No dated commitments found in this image.[/yellow]")
        return 0

    from .calendar_client import AuthError, get_calendar_service, write_events
    from .confirm import ConfirmationAborted, confirm_events

    try:
        confirmed = confirm_events(result.events, console)
    except ConfirmationAborted:
        console.print("\n[yellow]Cancelled — nothing was written.[/yellow]")
        return 1

    if not confirmed:
        console.print("[yellow]Nothing confirmed, so nothing was written.[/yellow]")
        return 0

    try:
        service = get_calendar_service()
    except AuthError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1

    outcomes = write_events(service, confirmed)
    _render_write_summary(outcomes)

    return 0 if all(o.ok for o in outcomes) else 1


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
    console.print("[dim](Or just press Enter to quit.)[/dim]")

    try:
        answer = console.input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return 0

    if not answer:
        return 0

    return cmd_parse(
        argparse.Namespace(
            image=Path(clean_dropped_path(answer)),
            dry_run=False,
            semester_start=None,
            semester_end=None,
        )
    )


def cmd_tutorial(args: argparse.Namespace) -> int:
    from .calendar_client import is_authenticated

    console.print(Panel(TUTORIAL_TEXT, title="syllabus_cal", border_style="cyan", expand=False))

    if is_authenticated():
        console.print("\n[green]You're already signed in to Google.[/green]")
        return _prompt_for_screenshot()

    console.print(
        "\n[yellow]You're not signed in to Google yet.[/yellow] You'll need to be before "
        "anything can be written to your calendar. (--dry-run works either way.)"
    )
    try:
        answer = console.input("Sign in with Google now? (y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        answer = "n"

    if answer == "y":
        code = cmd_auth(argparse.Namespace(test_event=False))
        return _prompt_for_screenshot() if code == 0 else code

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
