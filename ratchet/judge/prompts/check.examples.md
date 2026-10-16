Worked examples (for calibration only; they are not the case you are judging).

Example 1 - answer: equivalent
Diff: one sentence of the skill reworded, no behavior change.
Before steps: t001 tool github.list_issues {"repo":"acme/widgets","state":"open"} -> 4 issues; t002 tool github.label {"issue":12,"labels":["bug"]}.
After steps: t001 tool github.list_issues {"repo":"acme/widgets","state":"open"} -> 4 issues; t002 tool github.label {"issue":12,"labels":["bug"]}.
Before answer text: "Labeled #12 as bug. 3 issues need no action."
After answer text: "Issue #12 labeled bug; nothing to do for the other 3."
Answer: {"verdict": "equivalent", "confidence": 0.9, "reasons": ["Same tool calls with the same arguments; wording differs only."], "evidence_steps": ["t001", "t002"], "criteria_met": [{"criterion": "Issue #12 carries the bug label", "met": true, "evidence_steps": ["t002"]}]}

Example 2 - answer: regressed
Diff: the skill now says "skip issues older than 30 days".
Before steps: t001 tool github.list_issues -> 4 issues; t002 tool github.label {"issue":7,"labels":["stale"]}; t003 tool github.close_issue {"issue":7}.
After steps: t001 tool github.list_issues -> 4 issues; no further calls.
Before answer text: "Closed stale issue #7."
After answer text: "No issues needed action."
Answer: {"verdict": "regressed", "confidence": 0.88, "reasons": ["Stale issue #7 is no longer labeled or closed.", "The new 30-day skip rule excludes it."], "evidence_steps": ["t002", "t003"], "criteria_met": [{"criterion": "Issue #7 is closed as stale", "met": false, "evidence_steps": ["t003"]}]}
