# syllabus_cal

Screenshot → Google Calendar pipeline. Point it at an image of a syllabus,
club flyer, or schedule email; it extracts every dated commitment, shows you
what it found, and (once later build steps land) writes confirmed events to
your Google Calendar.

## Status: Step 1 of 5

Only the image → validated JSON pipeline is built and testable right now:

```
python -m syllabus_cal parse ./screenshot.png --dry-run
```

`--semester-start` / `--semester-end` are optional and only used to help the
model resolve recurring "until" dates — they're never required.

Everything else in the eventual CLI (`auth`, writing to Calendar,
per-event confirmation) is **not implemented yet** and will land in later
steps, per the build order in the project spec. Running `parse` without
`--dry-run`, or running `auth`, currently just prints a message saying so.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## What to test right now

Run `parse --dry-run` against a real screenshot of yours (a syllabus page,
a club flyer, a schedule email) and check:

- Does the table it prints match what's actually in the image?
- Are recurring class times resolved into one row with a recurrence
  pattern, instead of one row per class meeting?
- For anything the image doesn't state clearly (no stated end date, unclear
  AM/PM, etc.), does that row get flagged (`REVIEW`, red confidence, and a
  note in "Ambiguity flags") instead of Claude silently guessing?
- Are room numbers/buildings landing in "Location", not stuck in the title?

If something's wrong, the fix is almost always in
`syllabus_cal/prompts/extract_events.py` (the vision prompt) rather than in
the pipeline code — that's why the prompt was kept as a standalone module.

Google Calendar auth setup (`SETUP.md`) will be written when step 3 (the
OAuth flow) is built.
