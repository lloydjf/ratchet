A user marked this agent task as done correctly.
Task input: {{task_input}}
Final output: {{final_output}}
Artifacts changed: {{artifact_list}}
Write 3 to 6 checkable criteria that any correct completion of this task must satisfy. Be concrete: name fields, recipients, files, counts. Do not describe how the agent did it; describe what must be true at the end.
Criteria are about meaning and required content, never exact wording. For a task like "tell the client the invoice is late", write "says the invoice will be late", not "contains the phrase 'invoice is delayed'". Require exact text only for values that must not change (amounts, ids, dates, names, recipients) or when the task itself asks for an exact format.
When the output repeats items (receipts, issues, rows, recipients), write criteria that cover every item, for example "states the merchant, date, total and VAT for each of the 2 receipts", rather than one criterion per field of the first item.
Never write criteria about formatting or presentation: no bold, code spans, markdown, capitalization, punctuation, line breaks or "the phrase ..." requirements. A criterion names a value, an item, an action or a meaning that must be present, not how it is typeset.
