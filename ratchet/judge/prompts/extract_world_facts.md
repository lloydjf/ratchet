You are indexing facts a completed agent task depended on.
Task input: {{task_input}}
Final output: {{final_output}}
Tool results (tool name, first 600 chars each):
{{tool_results}}

List every external fact the output relies on that could change over time: prices, rates, dates, availability, versions, counts, names of current office holders, weather, anything read from the web or a live API. For each fact give a one-line statement with the value and the date it was observed, mark whether it is volatile, and write a short web search query that would re-check it.
Only list public facts about the outside world that came from a web page, a search result or a public data API (for example an exchange rate, a market price, a published software version). Never list the user's own data (their issues, labels, emails, receipts, invoices, files, calendars or spreadsheets), amounts or labels the agent computed or assigned, arithmetic, or anything the agent wrote itself. If the output relies on no such public fact, return an empty list.
Write the statement as "<what> was <value> on <date>", for example "The EUR/USD exchange rate on 2026-10-14 was 1.0800".
Also give the bare observed value on its own in "value" (for example "1.0800"), and the step id of the tool result it came from in "source_step".
