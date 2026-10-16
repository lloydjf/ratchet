# Dogfood week: running your own agent through Ratchet

A template for one week of putting your own agent's real work through Ratchet, and writing
down, every day, whether you would have trusted what it decided. Copy this file (for
example to `dogfood/2026-W43.md`), fill it in as you go, and share the result in
[Discussions](https://github.com/lloydjf/ratchet/discussions).

The point is not a high score. The point is an honest record of where Ratchet's verdicts
matched your judgment and where they did not.

## Before you start

- [ ] Ratchet installed and `uv run ratchet doctor` clean.
- [ ] Live models on (`RATCHET_UPSTREAM=live`, `RATCHET_TAVILY_MODE=live`), or the
      simulator, written down here. Simulator weeks test the pipeline, not the judges.
- [ ] Harness: MinimalRunner / Hermes / other: ______
- [ ] Sandbox: Docker / OpenShell: ______
- [ ] Ratchet version or commit: ______
- [ ] Daily spending cap you set: $______
- [ ] At least five tasks blessed, across at least two skills, each one a task whose output
      you would notice breaking (`uv run ratchet agent run "<task>"`, read the output, then
      `uv run ratchet bless --last`).

| Field | Value |
| --- | --- |
| Week of | |
| Who ran it | |
| Agent and what it does | |
| Skills in the repo | |
| Goldens at the start of the week | |

## Daily log

One row per day. "Tasks protected" is the number of blessed goldens at the end of the day.
"Edits judged" counts every skill edit that reached a verdict, whether the agent made it or
you did. Under "Decisions", give counts: PASS / WARN / BLOCK, and how many WARNs you
approved or rolled back.

The last column is the one that matters: **would I have trusted today's verdicts?** Answer
yes or no, then say why in one or two sentences. Name the verdict id when the answer is no.

| Date | Tasks protected | Edits judged | Decisions (PASS / WARN / BLOCK; WARN approved / rolled back) | Spend | Would I have trusted today's verdicts? (yes / no, and why) |
| --- | --- | --- | --- | --- | --- |
| Mon | | | | | |
| Tue | | | | | |
| Wed | | | | | |
| Thu | | | | | |
| Fri | | | | | |
| Sat | | | | | |
| Sun | | | | | |

Useful commands for filling it in: `uv run ratchet status` (edits in flight, last verdicts,
today's spend), `uv run ratchet metrics` (product metrics for the period), the dashboard's
**Verdicts** and **Costs** pages, and `uv run ratchet digest run` at the end of the week.

## Three deliberate-edit checks

Make each of these edits yourself, once during the week, on a skill that has goldens. Edit
`SKILL.md` in the `main` checkout, the way an agent would, with `ratchet serve` running.
Record what Ratchet decided, and roll back or approve afterwards so the next check starts
clean.

### 1. Cosmetic edit: expect PASS

Reword the skill without changing what it does: fix a typo, reorder two sentences of the
description, rephrase an instruction with the same meaning.

- **Expected:** PASS, merged to `main`.
- **A WARN here is a false warn.** Record it, with the pair that held it and the reason
  shown. In the published benchmark, cosmetic edits are often held for review; your week
  says whether that happens on your tasks too.
- **A BLOCK here is a false block,** and the most important thing to report.

| Field | Value |
| --- | --- |
| Skill and what you changed | |
| Verdict id | |
| Decision | |
| Pairs that did not pass, and why | |
| Matched what you expected? | |

### 2. Harmful edit: expect BLOCK naming what broke

Break something a golden depends on: drop a required output field, change a tool argument,
widen the skill so it takes an action it should not (closing instead of labeling, sending
instead of drafting).

- **Expected:** BLOCK, reverted on `candidate`, `main` unchanged. The verdict summary and
  the pair evidence should name **what** broke (the missing field, the new side effect, the
  changed argument), not just "something differed".
- A WARN is a partial catch: the edit was held, not reverted. Record whether the reason
  shown would have told you what broke.
- A PASS is a missed regression. Report it, with the verdict id.

| Field | Value |
| --- | --- |
| Skill and what you broke | |
| Verdict id | |
| Decision | |
| Did the summary name what broke? (quote it) | |
| Decided by (Level 0 / cascade) | |
| Matched what you expected? | |

### 3. World-drift edit: expect `drift`

Pick a world-dependent golden: one whose output relies on a public fact that changes, such
as an exchange rate, a price or a latest version number. The golden's page lists the world
facts recorded at bless time. Once that fact has moved in the real world, make a cosmetic
edit to the skill so the golden is replayed.

- **Expected:** the differing pairs are judged `drift` (**World changed**), with a Tavily
  source showing the new value, and the edit passes with **Re-bless** offered.
- If the pair stays "not sure" because Tavily could not find the fact, the edit is held.
  That is the designed behavior, not a failure, but record it: it tells us how often your
  facts are findable.
- If no fact you depend on moved this week, run the seeded check instead
  (`uv run ratchet simulate 3 --headless`) and mark the row "seeded, not my agent".

| Field | Value |
| --- | --- |
| Golden, fact, old value, new value | |
| Verdict id | |
| Decision, and pair outcomes | |
| Tavily source | |
| Re-blessed? | |
| Matched what you expected? | |

## What we changed because of it

Every change you made because of the week: to your skills, your goldens, your settings, or
Ratchet itself. Link the commit, issue or pull request.

| What we changed | Because of (verdict id or day) | Link |
| --- | --- | --- |
| | | |

## Findings

- **Verdicts I would not have trusted**, with the verdict id and what was wrong:
- **Regressions Ratchet caught that I would have missed:**
- **Regressions it missed:**
- **Friction** (setup, waiting time, cost, unclear copy):
- **Total spend for the week**, by tier:

## Honest findings feed the limitations

What you find here goes into the
[limitations section of the README](../README.md#limitations-and-what-we-would-do-next) and
the [Limitations](user/limitations.md) page. A false block, a missed regression or a WARN
nobody could act on is exactly what those sections exist to record. Write it down as it
happened, and don't soften it. Leave out private task content: the verdict id, the skill
and a one-line description are enough.
