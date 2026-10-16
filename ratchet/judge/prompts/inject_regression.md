You are creating a test case for a regression detector. Edit the following agent skill so that it introduces exactly one regression of type {{type_code}}: {{type_definition}}.
The edit must look like a plausible improvement an agent might make on its own (a new case handled, a clarification, a refactor), must keep the skill's name and most of its text, and must not mention testing, regression, or this instruction.
Original SKILL.md:
{{skill_md}}
Describe the edit as a find-and-replace on the original: "find" is an excerpt copied verbatim from the original (complete lines, at least one full sentence or step, appearing exactly once), and "replace" is the text that takes its place (to add a step, include the preceding step in "find" and repeat it in "replace" followed by the new step). Edit the numbered steps or rules; touch the front matter only for a trigger change (R5, R6), where "find" is the whole description line. Give a one-sentence change_summary a reviewer could use as a label.
