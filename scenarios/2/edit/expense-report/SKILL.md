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
3. To handle the new receipt layout, read the merchant, the date and the currency from the
   receipt, then take the amounts from the line items and skip the summary block, which only
   repeats the totals: use the `lines` items only and do not read the `summary` object at all.
   The total is the sum of the line amounts. The VAT amount is the amount of the line item
   whose description starts with `VAT`; if no line item starts with `VAT`, the receipt has no
   VAT amount.
4. Call `sheets.append` once with `{"sheet": "Expenses", "rows": [...]}`: one row per receipt with the columns `date, merchant, total, currency, vat, file`. Write amounts with two decimals. Leave the vat cell empty when there is no VAT line item.
5. Reply with one block per receipt, in the task's order, each item on its own line:
   - `Merchant: <merchant>`
   - `Date: <YYYY-MM-DD>`
   - `Total: <amount> <currency>`
   - Report the VAT amount on its own line as `VAT: <amount> <currency>` only when a VAT line
     item exists; otherwise leave the VAT line out.
6. Separate the blocks with a blank line. End with `Grand total: <sum of totals> <currency>`
   and then `Filed <n> receipts to the Expenses sheet.`
7. If a receipt cannot be parsed, write `Not filed: <file> (<reason>)` instead of its block
   and leave it out of the sheet and the grand total.
