---
name: invoice-parser
description: Log, record or extract supplier and vendor invoices from invoice text files into the Invoices sheet. Reads each invoice and logs its invoice number, vendor, dates and total.
---

# Invoice parser

Use this skill when the user asks you to log, extract, record or enter supplier invoices
from invoice files.

## Steps

1. Collect the invoice files named in the task (for example `invoices/inv-northwind.txt`). If
   the task names a folder instead, read the files the task lists for that folder.
2. Read each invoice file with `fs.read`.
3. From each invoice take: `Invoice number`, `Vendor`, `Invoice date`, the due date and
   `Total due` (amount and currency). If the invoice has no `Due date:` line but has
   `Payment terms: Net <n>`, the due date is the invoice date plus `<n>` days.
4. Write amounts with two decimals and no thousands separator (`1,240.00` becomes `1240.00`).
5. Call `sheets.append` once with `{"sheet": "Invoices", "rows": [...]}`: one row per invoice
   with the columns `invoice, vendor, date, due, total, currency, file`.
6. Reply with one block per invoice, in the task's order, each item on its own line:
   - `Invoice: <invoice number>`
   - `Vendor: <vendor>`
   - `Date: <YYYY-MM-DD>`
   - `Due date: <YYYY-MM-DD>`
   - `Total: <amount> <currency>`
7. Separate the blocks with a blank line and end with `Logged <n> invoices to the Invoices sheet.`
   If a file cannot be read, write `Not logged: <file> (could not read)` instead of its block.
