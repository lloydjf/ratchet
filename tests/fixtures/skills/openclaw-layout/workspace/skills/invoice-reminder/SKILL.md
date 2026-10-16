---
name: invoice-reminder
description: Draft polite payment reminder emails for overdue invoices.
homepage: https://example.com/skills/invoice-reminder
user-invocable: true
metadata: {"openclaw": {"requires": {"bins": ["git"], "env": ["SMTP_HOST"]}, "primaryEnv": "SMTP_HOST", "os": ["darwin", "linux"]}}
---
# Invoice reminder

1. List overdue invoices with `money.list_invoices`.
2. For each one, draft a short reminder that states the amount and the due date.
3. Never send more than one reminder per invoice per week.
