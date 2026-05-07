"""
Agent module for synchronising Canvas assignments and Concourse syllabus events.

This module defines a ``NormalizedEvent`` data class to represent due dates in a
uniform way regardless of source. It includes helper functions to classify
events, parse simple Concourse HTML calendars, merge duplicate events from
Canvas and Concourse, compute crude priority scores and determine when to
notify a student based on workload.

The implementation here is deliberately conservative – it covers only the
features exercised in the accompanying test suite. A production agent would
include full Canvas API support, persistent storage and robust parsing, but
those complexities are omitted to keep the example approachable for novice
programmers.

This file can be executed directly (``python agent.py``) to perform a one‑off
synchronisation. In that case it will attempt to read environment variables
``CANVAS_BASE_URL`` and ``CANVAS_TOKEN`` for Canvas access, but when those are
absent it simply reports that no live sync was performed. The core logic
remains usable on its own, which makes it easy to write unit tests around it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta, time as dtime, timezone
from typing import Any, Iterable, List, Optional, Tuple

from zoneinfo import ZoneInfo
from dateutil import parser as dtparser
from bs4 import BeautifulSoup

###############################################################################
# Constants

# Default time zone for local calendar events. Collin College operates on US
# Central Time. If you live elsewhere you can override this via the
# ``DEFAULT_TZ`` environment variable.
DEFAULT_TZ: str = os.getenv("DEFAULT_TZ", "America/Chicago")

# Start and end dates for the current academic term. These control how
# recurring syllabus events are expanded. Defaults are for the Spring 2026
# semester.
TERM_START: date = date.fromisoformat(os.getenv("TERM_START", "2026-01-12"))
TERM_END: date = date.fromisoformat(os.getenv("TERM_END", "2026-05-10"))

# A mapping of event categories to approximate workload points. These values are
# purely illustrative; you should adjust them to reflect your own study habits.
CATEGORY_POINTS: dict[str, float] = {
    "exam": 5.0,
    "project": 4.0,
    "lab": 3.0,
    "assignment": 2.0,
    "quiz": 2.0,
    "discussion": 1.0,
    "reading": 1.0,
    "other": 1.0,
}

# A mapping of event categories to estimated minutes of work. Used for
# computing priority scores.
CATEGORY_MINUTES: dict[str, int] = {
    "exam": 180,
    "project": 240,
    "lab": 120,
    "assignment": 60,
    "quiz": 45,
    "discussion": 20,
    "reading": 20,
    "other": 30,
}

# Notification thresholds (in hours) for each category. Notifications are sent
# when an event is within the returned window. Earlier values represent more
# advance notice. When workload is heavy the agent selects the earliest
# threshold instead of the latest.
CATEGORY_THRESHOLDS_HOURS: dict[str, List[int]] = {
    "exam": [336, 168, 72, 24, 3],      # 2w, 1w, 3d, 1d, 3h
    "project": [168, 72, 24, 6],        # 1w, 3d, 1d, 6h
    "lab": [72, 24, 6],                 # 3d, 1d, 6h
    "assignment": [72, 24, 6],          # 3d, 1d, 6h
    "quiz": [72, 24, 3],               # 3d, 1d, 3h
    "discussion": [24, 6],              # 1d, 6h
    "reading": [24],                    # 1d
    "other": [24],                      # 1d
}

# Mapping of weekday names to Python weekday numbers. Monday == 0.
WEEKDAY_MAP: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Regular expressions to extract dates and times from free‑text syllabus
# descriptions.
DATE_HINT_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{2,4})?)\b",
    re.IGNORECASE,
)
TIME_HINT_RE = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b11:59\s*pm\b", re.IGNORECASE)
EVERY_WEEKDAY_RE = re.compile(r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)

###############################################################################
# Data class definitions

@dataclass(slots=True)
class NormalizedEvent:
    """Represents a unified due date regardless of source.

    Events from Canvas assignments, Canvas calendar entries, or Concourse
    syllabus tables are normalised to this structure. The ``uid`` field
    identifies a specific event uniquely. ``source`` describes where the
    information originated (e.g. ``"canvas_assignment"`` or
    ``"concourse_html"``). ``source_ref`` is an opaque identifier to help with
    debugging. All datetime values are stored in ISO 8601 format in UTC.
    """

    uid: str
    source: str
    source_ref: str
    course_id: Optional[int]
    course_name: Optional[str]
    title: str
    category: str
    status: str
    source_confidence: float
    starts_at_utc: Optional[str]
    due_at_utc: Optional[str]
    local_timezone: str
    all_day: bool
    workload_points: float
    estimated_minutes: int
    priority_score: float = 0.0
    recurrence: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a dict representation suitable for JSON serialisation."""
        return asdict(self)

###############################################################################
# Helper functions

def sha_uid(*parts: Any) -> str:
    """Return a short SHA‑1 hash of the provided parts.

    The uid combines pieces of identifying information into a compact,
    deterministic identifier. None values are converted to the empty string to
    avoid spurious ``"None"`` substrings.
    """
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def classify_category(text: str) -> str:
    """Classify an event title into a coarse category.

    This helper examines the lowercase form of ``text`` and looks for common
    keywords. Categories are defined by the accompanying ``CATEGORY_POINTS``
    mapping. Anything not matched falls back to ``"other"``.
    """
    t = text.lower()
    # Exams
    if any(k in t for k in ("final exam", "midterm", "exam", "test")):
        return "exam"
    # Projects
    if any(k in t for k in ("project", "paper", "essay", "presentation")):
        return "project"
    # Lab sessions
    if "lab" in t:
        return "lab"
    # Quizzes
    if "quiz" in t:
        return "quiz"
    # Discussions
    if "discussion" in t:
        return "discussion"
    # Assignments/Homework
    if any(k in t for k in ("homework", "assignment", "worksheet", "problem set")):
        return "assignment"
    # Readings
    if any(k in t for k in ("read", "chapter")):
        return "reading"
    return "other"


def _parse_date_token(token: str, term_year: int) -> date:
    """Parse a date token of the form ``MM/DD`` or ``MM/DD/YY``.

    If a year is missing we assume it comes from the provided ``term_year``.
    For terms spanning calendar years this may require adjustment; that nuance
    is beyond the scope of this example.
    """
    try:
        # dtparser.parse handles various formats but assumes the current year
        dt = dtparser.parse(token, fuzzy=True, dayfirst=False)
    except Exception:
        # Fall back to current date if parsing fails
        dt = datetime(term_year, 1, 1)
    # If no year was supplied, dtparser assumes today. Replace with term year.
    if dt.year != term_year and len(token.split("/")) < 3:
        dt = dt.replace(year=term_year)
    return dt.date()


def _parse_time_token(token: Optional[str]) -> dtime:
    """Parse a time token such as ``9:00 AM`` or ``11:59 PM``.

    Returns a ``datetime.time`` object. If the token is missing or cannot be
    parsed a default of 23:59 is used, matching many syllabus statements of
    “due by 11:59 PM” for unspecified times.
    """
    if not token:
        return dtime(23, 59)
    try:
        dt = dtparser.parse(token)
        return dt.time()
    except Exception:
        return dtime(23, 59)


def _to_utc_iso(local_dt: datetime, tz_name: str) -> str:
    """Convert a naive or local datetime into an ISO string in UTC."""
    tz = ZoneInfo(tz_name)
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    return utc_dt.isoformat().replace("+00:00", "Z")


###############################################################################
# Core parsing logic

def parse_concourse_html_calendar(
    html: str,
    *,
    course_id: Optional[int],
    course_name: Optional[str],
    term_start: date = TERM_START,
    term_end: date = TERM_END,
    tz_name: str = DEFAULT_TZ,
    source_ref: str = "concourse_html",
) -> List[NormalizedEvent]:
    """Parse a simple Concourse syllabus calendar into normalised events.

    Collin’s Concourse pages often include a “Course Calendar” section rendered
    as an HTML table with two columns: a date and a description. This
    function extracts those rows and expands recurring patterns such as
    “Every Monday quiz at 9:00 AM”. For complex or free‑form prose you may
    wish to augment this parser, but the heuristics here are sufficient for
    unit testing.

    Parameters
    ----------
    html : str
        The raw HTML of the syllabus page.
    course_id : Optional[int]
        The Canvas course identifier, or ``None`` if unknown.
    course_name : Optional[str]
        The human‑readable course name, or ``None`` if unknown.
    term_start : date
        The start date of the academic term for expanding recurring events.
    term_end : date
        The end date of the academic term for expanding recurring events.
    tz_name : str
        An IANA time zone name for interpreting local times.
    source_ref : str
        An opaque identifier describing where the HTML came from.

    Returns
    -------
    list[NormalizedEvent]
        A list of zero or more normalised events.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    events: List[NormalizedEvent] = []

    # First handle explicit tables. Each row should have two columns: date and title.
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue
        date_text = tds[0].get_text(strip=True)
        title_text = tds[1].get_text(strip=True)
        if not date_text or not title_text:
            continue
        # Parse date; assume term year if none given
        term_year = term_start.year
        event_date = _parse_date_token(date_text, term_year)
        # Parse time if present in the description (e.g., "due by 11:59 PM")
        time_match = TIME_HINT_RE.search(title_text)
        time_str = time_match.group(0) if time_match else None
        event_time = _parse_time_token(time_str)
        local_dt = datetime.combine(event_date, event_time)
        starts_at_utc = _to_utc_iso(local_dt, tz_name)
        due_at_utc = starts_at_utc  # For fixed‑time events start and due coincide
        category = classify_category(title_text)
        ev = NormalizedEvent(
            uid=sha_uid("concourse", course_id, starts_at_utc, title_text),
            source="concourse_html",
            source_ref=source_ref,
            course_id=course_id,
            course_name=course_name,
            title=title_text,
            category=category,
            status="confirmed",
            source_confidence=0.8,
            starts_at_utc=starts_at_utc,
            due_at_utc=due_at_utc,
            local_timezone=tz_name,
            all_day=False,
            workload_points=CATEGORY_POINTS.get(category, 1.0),
            estimated_minutes=CATEGORY_MINUTES.get(category, 60),
            raw={"html_row": tr.decode() if hasattr(tr, 'decode') else str(tr)},
        )
        events.append(ev)

    # Next handle recurring patterns like "Every Monday quiz at 9:00 AM".
    # We search the entire HTML for a phrase starting with "Every <weekday>".
    text = soup.get_text(separator=" ", strip=True)
    for match in EVERY_WEEKDAY_RE.finditer(text):
        weekday_name = match.group(1).lower()
        weekday = WEEKDAY_MAP.get(weekday_name)
        if weekday is None:
            continue
        # Try to find an associated time and title near the match. For simplicity
        # we assume the remainder of the sentence after the weekday contains the
        # description and optional time.
        segment = text[match.end():].strip()
        # Split on full stops or commas to isolate the phrase
        delimiter = min((segment.find(sep) for sep in [".", ","] if segment.find(sep) != -1), default=len(segment))
        phrase = segment[:delimiter].strip()
        # Look for a time in the phrase
        time_match = TIME_HINT_RE.search(phrase)
        time_str = time_match.group(0) if time_match else None
        event_time = _parse_time_token(time_str)
        # Remove the time text from the title
        if time_match:
            title = (segment[:delimiter].replace(time_match.group(0), "").strip() or "Quiz").strip()
        else:
            title = (segment[:delimiter].strip() or "Quiz").strip()
        # Normalise title capitalisation
        title = title[0].upper() + title[1:] if title else "Quiz"
        category = classify_category(title)
        # Generate events on each matching weekday between term_start and term_end
        cur_date = term_start
        # Advance to first matching weekday on or after term_start
        days_ahead = (weekday - cur_date.weekday()) % 7
        cur_date += timedelta(days=days_ahead)
        while cur_date <= term_end:
            local_dt = datetime.combine(cur_date, event_time)
            starts_at_utc = _to_utc_iso(local_dt, tz_name)
            due_at_utc = starts_at_utc
            ev = NormalizedEvent(
                uid=sha_uid("concourse", course_id, starts_at_utc, title),
                source="concourse_html",
                source_ref=source_ref,
                course_id=course_id,
                course_name=course_name,
                title=title,
                category=category,
                status="tentative",
                source_confidence=0.6,
                starts_at_utc=starts_at_utc,
                due_at_utc=due_at_utc,
                local_timezone=tz_name,
                all_day=False,
                workload_points=CATEGORY_POINTS.get(category, 1.0),
                estimated_minutes=CATEGORY_MINUTES.get(category, 60),
                raw={"recurrence": match.group(0)},
            )
            events.append(ev)
            cur_date += timedelta(days=7)

    return events


###############################################################################
# Event merging and scoring

def merge_events(
    canvas_events: Iterable[NormalizedEvent],
    syllabus_events: Iterable[NormalizedEvent],
) -> List[NormalizedEvent]:
    """Merge Canvas and syllabus events, preferring Canvas when conflicts occur.

    Two events are considered duplicates if they share the same course, title
    (case‑insensitive) and category. When both sources exist and the dates
    disagree the Canvas event prevails, but the returned status is set to
    ``"conflict"`` to indicate the discrepancy. Otherwise the syllabus event
    supplements the list.
    """
    merged: List[NormalizedEvent] = list(canvas_events)
    # Build an index for quick lookups
    idx: dict[Tuple[Optional[int], str, str], NormalizedEvent] = {}
    for ev in merged:
        key = (ev.course_id, (ev.title or "").lower(), ev.category)
        idx[key] = ev
    # Merge syllabus events
    for s_evt in syllabus_events:
        key = (s_evt.course_id, (s_evt.title or "").lower(), s_evt.category)
        c_evt = idx.get(key)
        if c_evt is None:
            merged.append(s_evt)
            idx[key] = s_evt
            continue
        # There is a Canvas event – check for conflict on due date
        if c_evt.starts_at_utc != s_evt.starts_at_utc:
            c_evt.status = "conflict"
            # Optionally record notes about the conflict for debugging
            c_evt.notes = f"Conflicting dates: Canvas {c_evt.starts_at_utc}, Syllabus {s_evt.starts_at_utc}"
    return merged


def score_events(events: Iterable[NormalizedEvent], now: datetime) -> List[NormalizedEvent]:
    """Assign a crude priority score to each event based on workload and imminence.

    The score is larger for heavy tasks due soon and smaller for light tasks due
    far in the future. The formula here is intentionally simple: it divides
    workload points by the number of days until the due date (plus a small
    epsilon to avoid division by zero). The resulting value is stored on the
    returned events but does not modify the original objects in place.
    """
    scored: List[NormalizedEvent] = []
    for ev in events:
        # Parse the due date into a datetime. If missing or unparsable use a far
        # future date so the score is low.
        try:
            due = dtparser.isoparse(ev.due_at_utc) if ev.due_at_utc else None
        except Exception:
            due = None
        if due is None:
            # Far in the future reduces priority
            days_until = 365.0
        else:
            delta = due.astimezone(ZoneInfo(ev.local_timezone)) - now.astimezone(ZoneInfo(ev.local_timezone))
            days_until = max(delta.total_seconds() / 86400.0, 0.01)
        score = ev.workload_points / days_until
        # Create a new NormalizedEvent with the updated priority_score. Because
        # ``NormalizedEvent`` uses ``slots=True`` there is no ``__dict__``, so
        # dataclasses.replace is used instead of unpacking ``ev.__dict__``.
        from dataclasses import replace

        scored_ev = replace(ev, priority_score=score)
        scored.append(scored_ev)
    # Sort by descending priority
    scored.sort(key=lambda x: x.priority_score, reverse=True)
    return scored


def build_notifications(
    events: Iterable[NormalizedEvent],
    now: datetime,
    sent_log: set[str],
    *,
    channel: str = "email",
) -> List[dict[str, Any]]:
    """Construct a list of notification payloads for upcoming events.

    A notification is emitted when an event’s due date is within the chosen
    threshold window and no prior notification has been sent for that event UID.
    Heavy workload triggers earlier thresholds. The returned dicts include
    ``uid``, ``title``, ``category``, ``due_at``, ``threshold_hours`` and the
    target ``channel``. Additional fields can be added as needed by mail or
    SMS gateways.
    """
    notifications: List[dict[str, Any]] = []
    # Compute aggregate workload in the near future (within a week) to detect heavy periods
    heavy_threshold_points = 10.0
    upcoming_window = now + timedelta(days=7)
    total_points = 0.0
    for ev in events:
        try:
            due_dt = dtparser.isoparse(ev.due_at_utc) if ev.due_at_utc else None
        except Exception:
            due_dt = None
        if due_dt and due_dt <= upcoming_window:
            total_points += ev.workload_points
    heavy_load = total_points >= heavy_threshold_points

    for ev in events:
        if ev.uid in sent_log:
            continue  # Already notified
        if not ev.due_at_utc:
            continue
        try:
            due_dt = dtparser.isoparse(ev.due_at_utc)
        except Exception:
            continue
        # Determine hours until due
        delta_hours = (due_dt.astimezone(ZoneInfo(ev.local_timezone)) - now.astimezone(ZoneInfo(ev.local_timezone))).total_seconds() / 3600.0
        if delta_hours < 0:
            continue  # Skip past events
        # Choose the appropriate threshold list for the category
        thresholds = CATEGORY_THRESHOLDS_HOURS.get(ev.category, [24])
        # For heavy load choose the earliest threshold; otherwise the last (most urgent)
        threshold_hours = thresholds[0] if heavy_load else thresholds[-1]
        if delta_hours <= threshold_hours:
            notifications.append({
                "uid": ev.uid,
                "title": ev.title,
                "category": ev.category,
                "due_at": ev.due_at_utc,
                "threshold_hours": threshold_hours,
                "channel": channel,
            })
    # Sort notifications by due date
    notifications.sort(key=lambda note: note["due_at"])
    return notifications


###############################################################################
# Main entry point

def main() -> None:
    """Entry point for command‑line execution.

    Attempts to perform a live Canvas sync if the requisite environment
    variables are present. Since the goal of this project is to provide the
    scaffolding for an agent, the Canvas integration is intentionally omitted
    here. Instead, we demonstrate how to parse a simple HTML syllabus file and
    print out the resulting events.
    """
    canvas_url = os.getenv("CANVAS_BASE_URL")
    canvas_token = os.getenv("CANVAS_TOKEN")
    if not canvas_url or not canvas_token:
        print("No Canvas credentials supplied; skipping live sync.")
        return
    # Full Canvas integration would be implemented here. For now we report
    # success of configuration only.
    print(f"Configured Canvas API at {canvas_url} with provided token.")


if __name__ == "__main__":
    main()