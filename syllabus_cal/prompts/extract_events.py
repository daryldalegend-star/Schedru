"""Vision prompt for the screenshot -> calendar-event extraction call.

Kept as a standalone module (with only thin formatting helpers) so the wording
can be iterated on without touching vision.py's call/retry logic.
"""

from __future__ import annotations

from datetime import date

# Where events are assumed to be unless the image says otherwise. This is what
# lets "BSC 120 C" become a place Google Maps can find.
HOME_CONTEXT = "Rutgers University-New Brunswick (New Brunswick and Piscataway, New Jersey)"

EXTRACT_EVENTS_PROMPT = """You are turning an image of a syllabus, flyer, social-media post, or \
schedule screenshot into calendar events for a college student. Each event, read later in \
their calendar, should tell them everything they need: what it is, when, exactly where, and \
anything worth knowing before they go.

Today's date is {today}.
The student is at {home_context}. Unless the image says otherwise, assume events are there.
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
      "address": string | null,
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
A one-time deadline (an assignment due date, a single exam date) has "recurrence": null. \
A flyer that gives one date for something it calls "weekly" is announcing that one meeting: \
add that date as a one-time event and mention in "notes" that it meets weekly, rather than \
creating an open-ended series.

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

5. MAKE THE PLACE FINDABLE. Rooms, buildings, and abbreviations go in "location" and \
"address", never in "title".
   - "location": the place as a person would say it, with abbreviations expanded and the room
     kept, e.g. "BSC 120 C" -> "Busch Student Center, Room 120C". For an online event, the
     platform and link if one is shown ("Zoom: https://...").
   - "address": what a maps app needs to find the place: the street address when you know it
     reliably, otherwise the campus or venue with city and state, e.g. "Busch Campus, Rutgers
     University, Piscataway, NJ". Never invent a street number you aren't sure of — a correct
     city beats a wrong street. null for online events or when no place is given at all.
   - Any expansion or address you supplied that isn't printed on the image goes in
     "assumptions", e.g. "BSC = Busch Student Center (Busch campus)".

6. "notes" IS WHAT THEY'LL READ BEFORE GOING. It becomes the event's description, so put \
everything useful from the image there in a few short sentences: who is hosting (club, \
department, professor, course code), what it is if the title doesn't make that obvious \
(spell out acronyms like "AMSA"), cost, free food, what to bring, dress code, RSVP or sign-up \
links, who it's open to, and for coursework what's covered and how to submit. If there's a QR \
code, say so — you can't read it, but they can scan it. Only state what the image shows plus \
plainly safe expansions; never invent perks or requirements. null if there's nothing to add.

7. TITLES SHOULD STAND ON THEIR OWN in a calendar grid. Lead with the organizer or course \
when the image gives one: "Rutgers AI Club: Weekly Meeting", "BIO 101: Midterm Exam" — not \
just "Weekly Meeting" or "Midterm".

8. "confidence" reflects how sure you are about the event as a whole (dates, times, and \
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
        home_context=HOME_CONTEXT,
        semester_context=semester_context_line(semester_start, semester_end),
    )
