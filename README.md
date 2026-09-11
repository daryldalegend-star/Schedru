# syllabus_cal

Screenshot → Google Calendar pipeline. Point it at an image of a syllabus,
club flyer, or schedule email; it extracts every dated commitment, shows you
what it found, and (once later build steps land) writes confirmed events to
your Google Calendar.

## Status: complete (all 5 build steps)

This is a **command-line tool**, not a windowed app — you run it from a
terminal. The launcher scripts below get you there with a double-click.

## Two things you must supply

Neither can be created for you; both are tied to your own accounts.

1. **An Anthropic API key** — from
   [console.anthropic.com](https://console.anthropic.com). This is a paid
   API, billed per use, separate from any Claude subscription. Goes in
   `.env`.
2. **`credentials.json`** — from Google Cloud Console. Follow
   [`SETUP.md`](./SETUP.md), which walks through it from zero.

Without #1 nothing runs at all. Without #2 you can still use `--dry-run` to
extract events; you just can't write them to a calendar.

## Getting started

**The easy way:** double-click `start.command` (macOS) or `start.bat`
(Windows). First run installs everything and creates `.env` for your API
key. Run it again after pasting the key in.

You need Python 3.11+ installed first — get it from
[python.org/downloads](https://www.python.org/downloads/). **On Windows,
tick "Add python.exe to PATH"** on the installer's first screen. If the
launcher says Python wasn't found even though you installed it, Windows'
Microsoft Store shortcut is shadowing it: turn off `python.exe` and
`python3.exe` under Settings → Apps → Advanced app settings → App
execution aliases.

**The manual way:**

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then paste your ANTHROPIC_API_KEY into it
python -m syllabus_cal    # tutorial + Google sign-in prompt
```

## Usage

```
python -m syllabus_cal                                    # tutorial, offers sign-in
python -m syllabus_cal parse ./screenshot.png             # the main flow
python -m syllabus_cal parse ./screenshot.png --dry-run   # extract only, never writes
python -m syllabus_cal auth                               # Google sign-in
python -m syllabus_cal auth --test-event                  # sign-in + one throwaway event
```

`parse` reads the image, shows what it found grouped by confidence, walks
you through each event with **(a)dd / (e)dit / (s)kip**, then writes only
what you approved and prints a link to each one.

`--semester-start` / `--semester-end` (YYYY-MM-DD) are optional; they help
the model resolve recurring "until" dates.

**Nothing is written without an explicit yes.** Events with confidence
below 0.8 or any ambiguity flag default to *skip*, so holding Enter through
the list never adds a guessed event.

If a write fails partway through a batch, the summary lists exactly which
events landed and which didn't — re-run and skip the ones already created
rather than getting duplicates.

Every event created carries `extendedProperties.private.created_by =
"syllabus_cal"`, so your test data is easy to find and bulk-delete later.

## What's verified, and what isn't

68 automated tests cover the schema, the RRULE builder (including the DST
boundary), event-body construction, the confirmation UI, first-run setup,
and the full parse → confirm → write path with the Anthropic and Google
calls faked.

**Never exercised against the real services**: the browser OAuth round-trip
and an actual write landing on a real Google calendar. Those need your
credentials, so the first real run is the first real test. Start with
`auth --test-event` before trusting it with a full syllabus.

**`start.bat` has never been run on Windows** — this was built on Linux,
where no `cmd.exe` exists. That's why setup logic lives in `bootstrap.py`
(tested) rather than in the launchers, leaving the batch file doing only
"find a working Python, run that file".

## What to check on a first real run

**Extraction.** Run `parse --dry-run` against a real screenshot of yours (a
syllabus page, a club flyer, a schedule email) and check:

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

**Google auth.** After completing `SETUP.md`:

- `python -m syllabus_cal auth` — should open your browser, let you pick
  your Google account, and finish with `Signed in. token.json saved...`.
- `python -m syllabus_cal auth --test-event` — should additionally print a
  link to a real event on your calendar, tomorrow at 9:00–9:30 AM. Open the
  link and confirm it's actually there, then delete it.
- Delete or rename `token.json` and re-run something that needs it — you
  should get a plain "run `auth` again" message, never a stack trace.

**The full flow.** `parse` a real screenshot without `--dry-run`, confirm
one or two events, and check they appear on your calendar correctly —
especially a recurring class: it should be **one** repeating event, not
fifteen separate ones.

## Development

```
pip install pytest && python -m pytest tests/ -q
```
