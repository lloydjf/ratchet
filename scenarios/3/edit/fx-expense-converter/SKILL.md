---
name: fx-expense-converter
description: Convert foreign-currency amounts into US dollars at the official exchange rate for a date, and log the converted amounts in the Expenses USD sheet.
---

# FX expense converter

Use this skill whenever the user wants amounts in a foreign currency (EUR, GBP, CHF, ...)
converted to USD and recorded.

## Steps

1. Take every item from the task in the form `<description> <amount> <currency>`; the items
   of one task always share a currency. Take the conversion date (YYYY-MM-DD) from the task;
   when the task gives none, use the date on the "Current date" line.
2. Call `finance.rate` once with `{"base": "<item currency>", "quote": "USD", "date": "<YYYY-MM-DD>"}`.
3. Convert every amount as amount × rate and round half up to two decimals. The USD total
   is the sum of those rounded amounts.
4. Call `sheets.append` once with `{"sheet": "Expenses USD", "rows": [...]}`: one row per item with the columns `date, description, amount, currency, usd, rate`.
5. Answer with one line per item, `<Description>: <amount> <currency> = <usd> USD`, followed
   by `Total: <sum> <currency> = <usd sum> USD`.
6. End with the line `Rate used: 1 <currency> = <rate> USD (<date>)`, showing the rate with 4 decimals and the date the rate is for.
