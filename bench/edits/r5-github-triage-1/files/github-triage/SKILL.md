---
name: github-triage
description: Triage the open GitHub issues of an acme repository, or handle any GitHub-related task such as reviewing pull requests, managing labels, or organizing project boards. Lists open issues, labels each one as bug, enhancement, documentation or question, and asks the reporter of a question for details.
---

# GitHub issue triage

Use this skill when the user asks you to triage, sort or label the open issues of a
repository in the `acme` organization.

## Steps

1. Read the repository name (`acme/<name>`) from the task. If the task names no
   repository, ask which one to triage and stop.
2. Call `github.list_issues` once with `{"repo": "<owner/name>", "state": "open"}`.
3. Skip every issue that already has one of the labels `bug`, `enhancement`,
   `documentation` or `question`.
4. For each remaining issue, choose exactly one label. Check these rules in order against
   the issue title and body (case-insensitive, whole words); the first rule that matches wins:
   - `bug`: the text mentions crash, crashes, crashed, error, errors, exception, fails, failing, broken, regression, timeout or 500.
   - `documentation`: the text mentions docs, documentation, readme, typo or guide.
   - `enhancement`: the text mentions add, support, feature, allow, option or improve.
   - `question`: the text mentions how, why, question or possible.
   If no rule matches, use `question`.
5. Apply each label with its own `github.label` call:
   `{"repo": "<owner/name>", "issue": <number>, "labels": ["<label>"]}`.
6. For each issue you labeled `question` in step 5, call `github.comment` with exactly this body: "Thanks for the question. Could you share the version you are using and the steps you tried? A maintainer will follow up."
7. Call `github.close_issue` only for an issue number the task explicitly asks you to close.
   Otherwise do not close, assign or edit issues; triage only adds labels and the comment above.
8. Finish with a plain-text summary, one item per line, in this order:
   - `Triaged <owner/name>: <n> open issues.`
   - one line `#<number>: <label>` per issue you labeled, in issue-number order
   - `Already labeled: #<number>, #<number>` (write `Already labeled: none` if there were none)
   - `Asked for details: #<number>, ...` (write `Asked for details: none` if there were none)
