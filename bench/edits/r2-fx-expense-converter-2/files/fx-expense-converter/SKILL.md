---
name: fx-expense-converter
description: Convert foreign-currency amounts into US dollars at the official exchange rate for a date, and log the converted amounts in the Expenses USD sheet.
---

# FX expense converter

Use this skill when the user asks you to convert amounts in another currency (EUR, GBP,
CHF, ...) to USD and log them.

## Steps

1. Read each item from the task as `<description> <amount> <currency>`; all items in one
   task share a currency. Read the conversion date (YYYY-MM-DD) from the task. If the task
   gives no date, use the date from the "Current date" line.
2. Call `finance.rate` once with `{"base": "<item currency>", "quote": "USD", "date": "<YYYY-MM-DD>"}`.
3. Convert each amount: amount × rate, rounded half up to two decimals. The USD total is the
   sum of the rounded converted amounts.
4. Call `sheets.append` once with `{"sheet": "Expenses USD", "rows": [...]}`: one row per item with the columns `date, description, amount, currency, usd, rate`.
5. Reply with one line per item as `<Description>: <amount> <currency> = <usd> USD`, then
   `Total: <sum> <currency> = <usd sum> USD`.
6. End with the line `Rate used: 1 <currency> = <rate> USD (<date>)`, showing the rate with 4 decimals and the date the rate is for, unless the conversion rate is exactly 1.0000, in which case omit this line to reduce clutter.
