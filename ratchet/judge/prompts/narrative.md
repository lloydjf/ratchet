Write the summary a busy engineer reads to decide whether to trust an automatic decision about their agent.
Decision: {{decision}}  Action taken: {{action}}
Skill(s) edited: {{skills}}  Edit summary: {{diff_summary}}
Pairs: {{n_pass}} pass, {{n_regression}} regression, {{n_drift}} drift, {{n_uncertain}} uncertain, {{n_improved}} improved, out of {{n_total}}.
Strongest evidence per non-pass pair:
{{evidence_lines}}
Cost: {{cost_by_tier}}
Write at most 180 words. First sentence: the decision and the single most important reason. Then what was checked. Then what the user should do next (nothing, approve, roll back, or re-bless). Plain language, no headings, no bullet points, no hedging words.
Start the first sentence with this headline, adjusted only if the evidence demands it: {{headline}}
End with exactly this sentence: {{closing}}
