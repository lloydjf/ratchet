Two runs of the same task, before and after a skill edit. Decide whether the new run still satisfies the success criteria.
Task input: {{task_input}}
Success criteria:
{{criteria}}
Golden final output:
{{golden_output}}
Replay final output:
{{replay_output}}
Golden tool calls (tool, side-effect class): {{golden_calls}}
Replay tool calls (tool, side-effect class): {{replay_calls}}
Verdict meanings: equivalent = criteria still met, differences are cosmetic. regressed = at least one criterion no longer met, or a required action missing. improved = criteria met and the output is strictly better on a criterion. uncertain = cannot tell from this evidence.
A criterion about meaning is met when the replay says the same thing in other words: synonyms (ship/deploy, send/email), reordered clauses and a different sentence structure all meet it. Only a changed value (amount, id, date, name, recipient), a missing item or a missing action fails a criterion.
Formatting is never a regression: bold or code spans, capitalization, punctuation, list markers, blank lines and markdown markup around the same value do not change whether a criterion is met.
