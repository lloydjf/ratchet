---
name: calendar-scheduler
description: Schedule, book or set up a meeting, call or sync with colleagues on a day. Finds the earliest common free slot for a 30-minute or one-hour meeting, books it and emails the invitation.
---

# Calendar scheduler

Use this skill when the user asks you to schedule, book or set up a meeting, call or sync
with one or more people.

## Steps

1. Read from the task: the attendees (a colleague named `Alice` is `alice@example.com`), the meeting length (default 30
   minutes), the day (`YYYY-MM-DD`; if the task gives none, use the next weekday after the
   "Current date" line) and a title (default `Meeting`). The organizer is always
   `jun@example.com`.
2. Call `calendar.freebusy` once with
   `{"attendees": ["jun@example.com", <attendees>], "start": "<day>T09:00:00Z", "end": "<day>T17:00:00Z"}`.
3. Find the earliest slot of the requested length that starts on the hour or half hour,
   lies between 09:00 and 17:00 UTC, and overlaps no busy interval of any attendee.
4. If there is no such slot, reply `No common free slot on <day>.` and stop: do not create
   an event and do not send email.
5. Call `calendar.create` with
   `{"title": "<title>", "start": "<slot start>", "end": "<slot end>", "attendees": [<attendees>], "calendar": "primary"}`.
6. Call `gmail.send` once with `{"to": [<attendees>], "subject": "Invitation: <title> on <day>", "body": "<title>, <day> <HH:MM>-<HH:MM> UTC. Organizer: jun@example.com"}`.
7. Reply with these lines:
   - `Scheduled: <title>`
   - `When: <day> <HH:MM>-<HH:MM> UTC`
   - `Attendees: <attendees, comma-separated>`
   - `Invitation sent to <n> attendees.`
