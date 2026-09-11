# syllabus_cal

Screenshot → Google Calendar pipeline. Point it at an image of a syllabus,
club flyer, or schedule email; it extracts every dated commitment, shows you
what it found, and (once later build steps land) writes confirmed events to
your Google Calendar.

## Status: Step 3 of 5

Running `python -m syllabus_cal` with no arguments shows a short tutorial
and, if you're not signed in yet, offers to run Google sign-in right there.

What's live:

```
python -m syllabus_cal parse ./screenshot.png --dry-run   # step 1: image -> validated JSON
python -m syllabus_cal auth                                # step 3: Google sign-in
python -m syllabus_cal auth --test-event                   # step 3: + write one test event
```

`--semester-start` / `--semester-end` on `parse` are optional and only used
to help the model resolve recurring "until" dates — never required.

**Not yet wired**: `parse` without `--dry-run` doesn't write anything —
that needs the per-event confirmation UI (step 4) and end-to-end wiring
(step 5), still to come. `auth` and `auth --test-event` are real, but
they're for proving the OAuth + Calendar API path works, not the actual
parse → confirm → write pipeline.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

For Google Calendar auth, follow [`SETUP.md`](./SETUP.md) first (Google
Cloud Console steps, from zero) — you need `credentials.json` in the
project root before `auth` will work.

## What to test right now

**Step 1 — extraction.** Run `parse --dry-run` against a real screenshot of
yours (a syllabus page, a club flyer, a schedule email) and check:

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

**Step 3 — Google auth.** After completing `SETUP.md`:

- `python -m syllabus_cal auth` — should open your browser, let you pick
  your Google account, and finish with `Signed in. token.json saved...`.
- `python -m syllabus_cal auth --test-event` — should additionally print a
  link to a real event on your calendar, tomorrow at 9:00–9:30 AM. Open the
  link and confirm it's actually there, then feel free to delete it.
- `python -m syllabus_cal` with no arguments — should show the tutorial and
  correctly report whether you're already signed in.
- Delete or rename `token.json` and re-run something that needs it — you
  should get a plain "run `auth` again" message, never a stack trace.
