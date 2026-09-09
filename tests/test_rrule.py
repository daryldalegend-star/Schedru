from datetime import date, time

from syllabus_cal.rrule import build_exdates, build_recurrence, build_rrule
from syllabus_cal.schema import RecurrenceRule


def test_weekly_two_days():
    rule = RecurrenceRule(freq="WEEKLY", days_of_week=["TU", "TH"], until=None, exdates=[])
    assert build_rrule(rule) == "RRULE:FREQ=WEEKLY;BYDAY=TU,TH"


def test_biweekly_maps_to_weekly_interval_2():
    # This is the case the spec calls out: BIWEEKLY isn't real RFC5545, it's
    # WEEKLY with INTERVAL=2.
    rule = RecurrenceRule(freq="BIWEEKLY", days_of_week=["MO"], until=None, exdates=[])
    assert build_rrule(rule) == "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO"


def test_daily_has_no_byday():
    rule = RecurrenceRule(freq="DAILY", days_of_week=[], until=None, exdates=[])
    assert build_rrule(rule) == "RRULE:FREQ=DAILY"


def test_monthly():
    rule = RecurrenceRule(freq="MONTHLY", days_of_week=["FR"], until=None, exdates=[])
    assert build_rrule(rule) == "RRULE:FREQ=MONTHLY;BYDAY=FR"


def test_all_day_until_is_a_bare_date():
    # DTSTART with no time -> RFC5545 requires UNTIL to also be a bare date,
    # not a UTC date-time.
    rule = RecurrenceRule(freq="WEEKLY", days_of_week=["MO"], until=date(2026, 12, 14), exdates=[])
    assert build_rrule(rule, start_time=None) == "RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261214"


def test_timed_until_converts_local_end_of_day_to_utc():
    # America/New_York is standard time (UTC-5) in December.
    rule = RecurrenceRule(freq="WEEKLY", days_of_week=["TU", "TH"], until=date(2026, 12, 10), exdates=[])
    line = build_rrule(rule, start_time=time(9, 50))
    assert line == "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261211T045959Z"


def test_all_day_exdates_use_value_date():
    rule = RecurrenceRule(
        freq="WEEKLY", days_of_week=["MO"], until=None,
        exdates=[date(2026, 11, 23), date(2026, 12, 25)],
    )
    assert build_exdates(rule, start_time=None) == [
        "EXDATE;VALUE=DATE:20261123",
        "EXDATE;VALUE=DATE:20261225",
    ]


def test_timed_exdates_convert_to_utc_across_a_dst_boundary():
    # This is the case the spec warns will break: multiple EXDATEs, one on
    # each side of the Nov 1, 2026 fall-back (EDT UTC-4 -> EST UTC-5). A
    # fixed-offset conversion gets one of these two wrong.
    rule = RecurrenceRule(
        freq="WEEKLY", days_of_week=["TU", "TH"], until=date(2026, 12, 10),
        exdates=[date(2026, 9, 22), date(2026, 11, 26)],
    )
    assert build_exdates(rule, start_time=time(9, 50)) == [
        "EXDATE:20260922T135000Z",  # EDT: 9:50 + 4h
        "EXDATE:20261126T145000Z",  # EST: 9:50 + 5h
    ]


def test_no_exdates_returns_empty_list():
    rule = RecurrenceRule(freq="WEEKLY", days_of_week=["FR"], until=None, exdates=[])
    assert build_exdates(rule) == []


def test_build_recurrence_combines_rrule_and_exdate_lines():
    rule = RecurrenceRule(
        freq="BIWEEKLY", days_of_week=["WE"], until=date(2026, 12, 9),
        exdates=[date(2026, 11, 25)],
    )
    lines = build_recurrence(rule, start_time=time(14, 0))
    assert lines == [
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=WE;UNTIL=20261210T045959Z",
        "EXDATE:20261125T190000Z",
    ]
