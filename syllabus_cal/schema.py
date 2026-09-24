"""Pydantic schema for the events Claude extracts from a screenshot."""

from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field

DayOfWeek = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
EventType = Literal["class", "exam", "assignment", "office_hours", "club", "other"]
Frequency = Literal["DAILY", "WEEKLY", "BIWEEKLY", "MONTHLY"]

CONFIDENCE_REVIEW_THRESHOLD = 0.8


class RecurrenceRule(BaseModel):
    freq: Frequency
    days_of_week: list[DayOfWeek] = Field(default_factory=list)
    until: date | None = None
    exdates: list[date] = Field(default_factory=list)


class ExtractedEvent(BaseModel):
    title: str
    event_type: EventType
    start_date: date
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = None
    recurrence: RecurrenceRule | None = None
    notes: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguity_flags: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """Confidence below threshold or any stated ambiguity defaults to unselected.

        Deliberately ignores `assumptions`: those are gaps the model filled in
        with an obvious answer (a bare 7:30 on a club flyer is PM), which are
        shown to the user but shouldn't make them re-confirm every event.
        `ambiguity_flags` stays for things with no reasonable default.
        """
        return self.confidence < CONFIDENCE_REVIEW_THRESHOLD or bool(self.ambiguity_flags)


class ExtractionResult(BaseModel):
    events: list[ExtractedEvent] = Field(default_factory=list)
