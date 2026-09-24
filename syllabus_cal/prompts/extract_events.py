"""Vision prompt for the screenshot -> calendar-event extraction call.

Kept as a standalone module (with only thin formatting helpers) so the wording
can be iterated on without touching vision.py's call/retry logic.
"""

from __future__ import annotations

from datetime import date

EXTRACT_EVENTS_PROMPT = """You are extracting calendar-worthy commitments from an image of a \
syllabus, flyer, or schedule screenshot.

Today's date is {today}.
{semester_context}

Return JSON ONLY. No prose, no explanation, no markdown code fences — your entire response \
must be a single JSON object that a JSON parser can load directly, matching exactly this shape:

{{
  "events": [
    {{
      "title": string,
      "event_type": "class" | "exam" | "assignment" | "office_hours" | "club" | "other",
      "start_date": "YYYY-MM-DD",
      "start_time": "HH:MM" | null,
      "end_time": "HH:MM" | null,
      "location": string | null,
      "recurrence": {{
        "freq": "DAILY" | "WEEKLY" | "BIWEEKLY" | "MONTHLY",
        "days_of_week": ["MO" | "TU" | "WE" | "TH" | "FR" | "SA" | "SU", ...],
        "until": "YYYY-MM-DD" | null,
        "exdates": ["YYYY-MM-DD", ...]
      }} | null,
      "notes": string | null,
      "confidence": float between 0.0 and 1.0,
      "ambiguity_flags": [string, ...],
      "assumptions": [string, ...]
    }}
  ]
}}

Rules:

1. RECURRENCE, NOT ENUMERATION. If the source describes a repeating pattern (e.g. \
"Tues/Thurs 9:50-11:10", "every other Monday", "MWF"), resolve it into a single event whose \
"start_date" is the FIRST occurrence on/after today (or the term start, if you can identify \
one) and whose "recurrence" field carries the pattern. Never emit one event per occurrence. \
A one-time deadline (an assignment due date, a single exam date) has "recurrence": null.

2. FILL IN OBVIOUS GAPS, AND SAY WHAT YOU FILLED IN. When something is unstated but has a \
clear answer from context, use it and record what you decided in "assumptions" — a short \
string like "7:30 read as PM (evening club meeting)". Do NOT leave these blank or flagged:

   - AM/PM on a bare time. Decide which one a college student would actually attend.
     Lectures, labs, classes and office hours: 8:00-11:59 is AM, 12:00-6:59 is PM.
     Club meetings, socials, performances, practices, review sessions: 4:00-11:59 is PM.
     A stated range usually settles it on its own ("7:30-9:00" is an evening).
   - A missing end time. Assume a sensible length and note it: about 1 hour for a club
     meeting or office hours, 1 hour 20 minutes for a lecture, 2-3 hours for an exam or lab.
   - A missing year on an otherwise complete date. Use the nearest matching date on or
     after today, and say so.

3. STILL DON'T INVENT WHAT YOU CANNOT INFER. "assumptions" is only for gaps with an obvious \
answer. When something has no reasonable default — no date at all, a date you can't read, \
conflicting information, a recurring event whose end is never stated — then lower "confidence" \
below 0.8 and put it in "ambiguity_flags" instead, leaving the field null. A confident wrong \
date is far worse than a flagged one.

   The difference in one line: "assumptions" means *I filled this in and I'm probably right*; \
"ambiguity_flags" means *I genuinely don't know, please look at this one*. Events with only \
assumptions are added by default; flagged events are skipped by default.

4. DISTINGUISH DEADLINES FROM MEETINGS. A one-time "due" item (assignment, paper, project) is \
"event_type": "assignment" with no recurrence. A recurring meeting block (lecture, lab, office \
hours, club meeting) gets the appropriate "event_type" and a "recurrence" rule.

5. LOCATION GOES IN "location", NOT IN "title". Room numbers, building names, and building \
abbreviations (e.g. "Busch SEC 118", "Zoom", "Room 204") belong in "location". Keep "title" to \
the human-readable name of the class/event/deadline only.

6. "confidence" reflects how sure you are about the event as a whole (dates, times, and \
recurrence all considered) — not just whether the text was legible.

If the image contains no dated commitments at all, return {{"events": []}}.

Respond with the JSON object and nothing else."""


def semester_context_line(semester_start: date | None, semester_end: date | None) -> str:
    if semester_start and semester_end:
        return (
            f"This image is from a course/term running from {semester_start.isoformat()} to "
            f"{semester_end.isoformat()}. Use this range to help resolve day-of-week patterns "
            f"and recurring 'until' dates, but do not invent an end date for an event if the "
            f"source doesn't state one — the term end is context, not a substitute for a "
            f"stated date."
        )
    return (
        "No semester start/end date was provided. If a recurring event's end date isn't "
        "stated in the image itself, leave 'until' as null and flag it in ambiguity_flags "
        "rather than guessing the term length."
    )


def render_prompt(
    today: date,
    semester_start: date | None = None,
    semester_end: date | None = None,
) -> str:
    return EXTRACT_EVENTS_PROMPT.format(
        today=today.isoformat(),
        semester_context=semester_context_line(semester_start, semester_end),
    )
