---
name: expense-report
description: Build a monthly expense report from receipts and append the rows to the expenses sheet.
---
# Expense report

Parse every receipt in `receipts/` with `receipts.parse`, convert to USD with
`finance.rate`, and append one row per receipt to the `expenses` sheet.
