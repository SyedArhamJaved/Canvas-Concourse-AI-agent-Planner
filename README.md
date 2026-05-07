# Canvas Concourse Assignment Tracking Agent

Canvas Concourse Assignment Tracking Agent is a Python-based academic productivity tool designed to help students track class assignments, quizzes, exams, projects, and syllabus-based deadlines from Canvas and Concourse course syllabi.

The goal of this project is to create a student-owned agent that does more than send basic deadline reminders. Instead of treating every assignment equally, the agent is designed to evaluate future workload, identify busy academic periods, and notify the student earlier when multiple high-effort tasks are approaching simultaneously.

## Project Purpose

Many students rely on Canvas calendars, syllabus PDFs, course announcements, and manual reminders to stay organized. The problem is that deadlines are often scattered across multiple places. Some assignments appear directly in Canvas, while exams, readings, projects, and major course milestones may appear only in the Concourse syllabus.

This project attempts to solve that problem by building a deterministic Python agent that can:

- Parse course calendar information from Concourse syllabus content
- Normalize assignments, exams, quizzes, discussions, labs, projects, and readings into one event format
- Estimate workload using category-based scoring
- Detect when the upcoming week is light, busy, or overloaded
- Shift reminders earlier when workload density increases
- Preserve uncertainty by marking syllabus-only events as tentative when needed
- Detect conflicts when Canvas and syllabus dates appear to disagree
- Provide a foundation for daily scheduled updates

## Current Features

The current version includes the foundational logic for the assignment tracking agent:

- `NormalizedEvent` data model for storing course deadlines in a consistent format
- Event category classification for exams, projects, labs, quizzes, assignments, discussions, readings, and other tasks
- Concourse HTML calendar parsing for simple syllabus calendar tables
- Recurring event expansion for patterns such as weekly quizzes
- Workload point estimation based on assignment type
- Priority scoring based on urgency and workload
- Notification threshold logic that shifts reminders earlier during heavy academic weeks
- Conflict detection logic for cases where two sources disagree about a deadline
- Environment-variable-based configuration for future Canvas API integration
- Unit tests for parser behavior, recurrence handling, conflict detection, and heavy-load notification shifting

## Long-Term Vision

The long-term goal is to turn this into an autonomous academic planning agent that connects to a student's Canvas account, syncs course assignments, enriches missing deadlines from Concourse syllabi, and generates daily or weekly planning alerts.

The intended workflow is:

1. Authenticate with Canvas using a secure access token.
2. Pull active courses, assignments, calendar events, and upcoming events.
3. Detect Concourse syllabus links or accept uploaded syllabus content.
4. Parse syllabus calendar sections for exams, projects, quizzes, and other milestones.
5. Merge Canvas and syllabus events into a single normalized schedule.
6. Score upcoming workload using urgency, effort, category weight, and workload crowding.
7. Send alerts earlier when the student has a heavy academic week.
8. Store snapshots and notification history for future review.

## Why This Project Matters

Most reminder tools only notify users at fixed intervals, such as 24 hours before a deadline. That approach fails during midterms, finals, and project-heavy weeks because it ignores total workload.

This project is designed around a better planning model:

- An exam due in one week should become urgent earlier if three other assignments are also due soon.
- A syllabus-only exam should be tracked even if it has not been added to Canvas yet.
- Conflicting dates should be flagged instead of silently overwritten.
- Heavy workload should trigger earlier reminders, not just more notifications.
- The system should prioritize trust, transparency, and auditability over guesswork.

## Tech Stack

- Python
- BeautifulSoup
- python-dateutil
- pytest
- Canvas API-ready architecture
- Concourse syllabus parsing
- Local environment variable configuration

## Current Status

This repository is currently an early-stage prototype. The parser, event model, scoring logic, and notification decision logic are implemented as a foundation. Full live Canvas API syncing, persistent storage, email delivery, and automated scheduling are planned as the next steps.

## Planned Improvements

Future improvements may include:

- Full Canvas API client integration
- Daily scheduled sync using cron or a background job
- Local JSON or SQLite storage
- Email notification support
- SMS notification support for high-priority exams or conflicts
- Support for uploaded PDF syllabi
- Better syllabus parsing for different course calendar formats
- Conflict dashboard for manually reviewing uncertain deadlines
- GitHub Actions testing workflow
- Codex-assisted development and maintenance workflow

## Security Notes

Canvas access tokens should never be committed to GitHub. Tokens should be stored only in environment variables or a secure local secret manager. This project is designed so that sensitive credentials remain outside the source code.
