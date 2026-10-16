---
name: weekly-research-brief
description: Write a weekly research brief on a topic from this week's web news. Searches the web, reads the top articles, and writes a short brief with dated key facts and sources to the briefs folder.
---

# Weekly research brief

Use this skill when the user asks for a research brief, a weekly digest or a news summary
on a topic.

## Steps

1. Read the topic from the task and make a slug from it (lower case, words joined with `-`).
2. Call `web.search` once with `{"query": "<topic> news"}`.
3. If the search returns no results, reply `No sources found for <topic> this week.` and stop:
   do not write a file.
4. Call `web.fetch` with `{"url": "<url>"}` for each of the first three results, in order.
5. From each page, take one key fact: the sentence that states a number or a version,
   together with its date (`YYYY-MM-DD`).
6. Write the brief with `fs.write` to `briefs/<slug>.md`:
   - `# Weekly brief: <topic>`
   - `## Key facts`, then one `- <fact> (<date>, <url>)` line per page
   - `## Sources`, then one `- <page title>: <url>` line per page
7. Reply with `Brief written to briefs/<slug>.md`, then `Sources: <n>`, then one
   `- <fact> (<date>)` line per key fact.
