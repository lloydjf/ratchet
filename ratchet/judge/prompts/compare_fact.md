A completed agent task relied on an external value that can move over time (a rate, a price, a version). Decide whether that value has moved since the task observed it.
Fact: {{statement}}
Observed value: {{old_value}}
Observed on: {{observed_on}}
Value the agent's own tool reported in the replay today: {{replay_value}}
Search results (title, url, published date, content):
{{search_results}}
Find the current value of the same quantity in the results; a "Tavily answer" line, when present, summarizes them. Compare it with the observed value:
- changed=true when the current value differs from the observed value; put the current value in new_value. Rounding and formatting are not changes (1.08 and 1.0800 are the same value).
- changed=false when the current value equals the observed value.
- If the results state no current value for this quantity, answer changed=false with confidence below 0.5.
When the results show a current value close to the replay's value (within about 1%), the value has moved: answer changed=true and give the value from the results.
Do not judge whether the fact was true on the date it was observed; only compare the observed value with today's value.
