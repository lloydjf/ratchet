You are checking whether a skill edit broke a task that used to work.
Skill diff (unified):
{{skill_diff}}
Success criteria:
{{criteria}}
Golden run, step by step (step_id, tool or model, normalized args, result preview):
{{golden_steps}}
Replay run, step by step:
{{replay_steps}}
Golden final output:
{{golden_output}}
Replay final output:
{{replay_output}}
A first-pass judge said: {{level1_verdict}} ({{level1_confidence}}).
For each criterion, state whether the replay meets it and cite the step ids that show it. Then give the overall verdict. Differences in wording, ordering of equivalent items, or timestamps are not regressions. Missing required side effects, wrong recipients, wrong amounts, dropped fields, and new unexpected actions are regressions.
If the verdict is regressed, also give failure_summary: what got worse as a short verb phrase that completes "the skill now ..." (for example "drops the VAT line" or "emails the wrong recipient"). Write reasons as final conclusions, one short sentence each, not as deliberation.
A criterion about meaning is met when the replay says the same thing in other words: synonyms (ship/deploy, send/email), reordered clauses and a different sentence structure all meet it. Only a changed value (amount, id, date, name, recipient), a missing item or a missing action fails a criterion.
Formatting is never a regression: bold or code spans, capitalization, punctuation, list markers, blank lines and markdown markup around the same value do not change whether a criterion is met.
