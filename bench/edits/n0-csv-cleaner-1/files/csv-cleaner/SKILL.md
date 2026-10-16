---
name: csv-cleaner
description: Clean a CSV file in the workspace. Trims whitespace, normalizes dates and emails, removes duplicate rows and rows without an id, and writes a cleaned copy next to the original file.
---

# CSV cleaner

<!-- Reviewed for clarity; the steps are unchanged. -->

Use this skill when the user asks you to clean, tidy, deduplicate or normalize a CSV file.

## Steps

1. Read the CSV file named in the task with `fs.read`. If it cannot be read, reply
   `Could not read <path>.` and stop.
2. Call `shell.run` with `{"cmd": "wc -l <path>"}` to count the lines of the original file.
3. Clean the rows (the first line is the header; keep it unchanged):
   - trim spaces at the start and end of every cell;
   - in a column named `date`, rewrite `DD/MM/YYYY` and `YYYY/MM/DD` as `YYYY-MM-DD`;
   - in a column named `email`, write the address in lower case;
   - drop every row whose first column (`id`) is empty;
   - after the steps above, drop exact duplicate rows, keeping the first one.
4. Write the result with `fs.write` to `<same folder>/<name>.clean.csv` (for `exports/orders.csv`
   that is `exports/orders.clean.csv`). Never overwrite the original file.
5. Reply with these lines:
   - `Cleaned <path> -> <output path>`
   - `Rows in: <data rows in the original, header excluded>`
   - `Rows out: <data rows written>`
   - `Duplicates removed: <n>`
   - `Rows without id removed: <n>`
