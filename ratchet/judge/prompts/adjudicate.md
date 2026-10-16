Two earlier judges disagreed or were unsure about whether a skill edit caused a regression. You have the full record. Decide.
Task input: {{task_input}}
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
Earlier opinions:
- nano: {{level1}}
- super: {{level2}}
For each criterion, state whether the replay meets it and cite the step ids that show it. Differences in wording, ordering of equivalent items, or timestamps are not regressions. Missing required side effects, wrong recipients, wrong amounts, dropped fields, and new unexpected actions are regressions.
Reason about causality: is the difference between the runs explained by the skill diff? Could it be explained by nondeterminism or by a changed external fact instead? If the replay violates a criterion and the diff plausibly causes it, the verdict is regressed. If the difference is cosmetic or an improvement, say so. If the difference is best explained by an external fact changing, say uncertain and name the fact in reasons.
If the verdict is regressed, also give failure_summary: what got worse as a short verb phrase that completes "the skill now ..." (for example "drops the VAT line" or "emails the wrong recipient"). Write reasons as final conclusions, one short sentence each, not as deliberation.
A criterion about meaning is met when the replay says the same thing in other words: synonyms (ship/deploy, send/email), reordered clauses and a different sentence structure all meet it. Only a changed value (amount, id, date, name, recipient), a missing item or a missing action fails a criterion.
Formatting is never a regression: bold or code spans, capitalization, punctuation, list markers, blank lines and markdown markup around the same value do not change whether a criterion is met.
Set caused_by_diff=true only when a specific change in the skill diff plausibly produces the difference you see. When the diff is empty, says "(no diff available)", or only rewords or reformats text without changing any step, tool, argument or rule, set caused_by_diff=false and answer uncertain: two runs of the same skill can differ by chance.
