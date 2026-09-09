"""CLI entry point: python -m syllabus_cal <parse|auth> ...

Stage 1 of the build: only `parse --dry-run` is wired up (image -> validated JSON,
printed as a table). `auth` and calendar writing land in later steps.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .schema import CONFIDENCE_REVIEW_THRESHOLD, ExtractionResult

console = Console()
err_console = Console(stderr=True)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid date, expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syllabus_cal")
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    subparsers.add_parser("auth", help="Run the Google Calendar OAuth flow")

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
            "[red]Only --dry-run is implemented so far (writing to Calendar is a later "
            "build step). Re-run with --dry-run.[/red]"
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
    err_console.print(
        "[yellow]Google Calendar auth isn't wired up yet — that lands in a later build "
        "step.[/yellow]"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        return cmd_parse(args)
    if args.command == "auth":
        return cmd_auth(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
