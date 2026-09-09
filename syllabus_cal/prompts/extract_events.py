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
      "ambiguity_flags": [string, ...]
    }}
  ]
}}

Rules:

1. RECURRENCE, NOT ENUMERATION. If the source describes a repeating pattern (e.g. \
"Tues/Thurs 9:50-11:10", "every other Monday", "MWF"), resolve it into a single event whose \
"start_date" is the FIRST occurrence on/after today (or the term start, if you can identify \
one) and whose "recurrence" field carries the pattern. Never emit one event per occurrence. \
A one-time deadline (an assignment due date, a single exam date) has "recurrence": null.

2. NEVER GUESS A PLAUSIBLE DATE OR TIME. This is the most important rule. If a date, a time, \
an AM/PM, or an end-of-term / "until" date is not clearly stated in the image, do NOT infer a \
"reasonable" value for it. Instead:
   - lower "confidence" (below 0.8) for that event, and
   - add a short human-readable string to "ambiguity_flags" describing exactly what was \
unstated or unclear (e.g. "end date not stated", "AM/PM unclear", "year assumed from context").
   A confident-looking wrong date is much worse than one flagged as uncertain — when in doubt, \
flag it, don't guess it.

3. DISTINGUISH DEADLINES FROM MEETINGS. A one-time "due" item (assignment, paper, project) is \
"event_type": "assignment" with no recurrence. A recurring meeting block (lecture, lab, office \
hours, club meeting) gets the appropriate "event_type" and a "recurrence" rule.

4. LOCATION GOES IN "location", NOT IN "title". Room numbers, building names, and building \
abbreviations (e.g. "Busch SEC 118", "Zoom", "Room 204") belong in "location". Keep "title" to \
the human-readable name of the class/event/deadline only.

5. "confidence" reflects how sure you are about the event as a whole (dates, times, and \
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
