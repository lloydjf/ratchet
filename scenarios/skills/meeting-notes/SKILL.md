---
name: meeting-notes
description: Turn raw meeting notes from the workspace into clean minutes with attendees, decisions and action items, written to the minutes folder.
---

# Meeting notes to minutes

Use this skill when the user asks you to clean up, write up or turn meeting notes into
minutes.

## Steps

1. Read the notes file named in the task with `fs.read`.
2. Decisions are the lines that start with `Decision:`. Action items are the lines that start with `TODO:` or `Action:`. Take the attendees from the `Attendees:` line.
3. Write the minutes with `fs.write` to `minutes/<notes file name>` using this layout, with a
   blank line between parts:
   - `# Minutes: <title>` (the notes' first `# ` heading, else the file name)
   - `## Attendees`, then the attendees (or `Not recorded`)
   - `## Decisions`, then one `- <decision>` line each (or `None recorded`)
   - `## Action items`, then one `- [ ] <action item>` line each (or `None recorded`)
4. Reply with three lines: `Minutes written to minutes/<file>`, `Decisions: <n>` and
   `Action items: <n>`.
