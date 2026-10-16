Worked examples (for calibration only; they are not the case you are judging).

Example 1 - answer: equivalent
Task: Summarize this week's merged PRs into CHANGELOG.md.
Criteria: CHANGELOG.md has a section for this week; every merged PR (#41, #42, #45) is listed.
Before: "Added ## Week 42 with #41 Fix login, #42 Add export, #45 Bump deps."
After: "Added ## Week 42 listing #42 Add export, #41 Fix login and #45 Bump deps."
Calls before: git.log (read), fs.write (write_reversible). Calls after: git.log (read), fs.write (write_reversible).
Answer: {"verdict": "equivalent", "confidence": 0.93, "reasons": ["Same three PRs listed; only the order changed."], "evidence_steps": ["t002"]}

Example 2 - answer: regressed
Task: File the October receipts into the expense sheet and email the total to finance.
Criteria: every receipt has a row with its VAT amount; finance@acme.example receives the total.
Before: "Added 3 rows (net, VAT, gross). Emailed finance: total EUR 412.80 incl. EUR 68.80 VAT."
After: "Added 3 rows (net, gross). Emailed finance: total EUR 412.80."
Calls before: receipts.parse (read), sheets.append (write_reversible), gmail.send (external_send). Calls after: the same.
Answer: {"verdict": "regressed", "confidence": 0.92, "reasons": ["The VAT amount is missing from the rows and the email."], "evidence_steps": ["t002", "t003"]}
