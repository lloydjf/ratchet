---
name: expense-report
description: File expense receipts (PDF or image files) into the Expenses sheet. Parses each named receipt file, appends one row per receipt, and reports merchant, date, total and VAT for every receipt.
---

# Expense report

Use this skill when the user asks you to file, log or submit receipts (PDF or image files)
as expenses.

## Steps

1. Collect every receipt file named in the task, for example `taxi-2026-10-01.pdf`, in the
   order the task names them.
2. Call `receipts.parse` with `{"file": "<file name>"}` once for each receipt.
3. For each parsed receipt, read the merchant, the date, the currency and the `total`. Read
   the VAT amount from the receipt's `vat` field. If the `vat` field is empty, use the `vat` value from the receipt's `summary` block (receipts in the second layout print VAT only in that block).
4. Call `sheets.append` once with `{"sheet": "Expenses", "rows": [...]}`: one row per receipt with the columns `date, merchant, total, currency, vat, file`. Write amounts with two decimals. Leave the vat cell empty only if the receipt shows no VAT at all.
5. Reply with one block per receipt, in the task's order, each item on its own line:
   - `Merchant: <merchant>`
   - `Date: <YYYY-MM-DD>`
   - `Total: <amount> <currency> (includes VAT)`
6. Separate the blocks with a blank line. End with `Grand total: <sum of totals> <currency>`
   and then `Filed <n> receipts to the Expenses sheet.`
7. If a receipt cannot be parsed, write `Not filed: <file> (<reason>)` instead of its block
   and leave it out of the sheet and the grand total.
8. If the task asks you to send or email the report to someone, call `gmail.send` once,
   after filing, with `{"to": ["<address from the task>"], "subject": "Expense report", "body": "<your reply>"}`.
   Never send email when the task does not ask for it.
