# Concepts

This page explains what Ratchet does to a skill edit, in the order it happens. The
behavior described here is the product's contract: the design notes (`docs/design/`), the
decision records ([ADRs](reference/adr/index.md)), and the code in `ratchet/judge/` and
`ratchet/orchestrator/` agree on it.

## Skills and skill edits

A **skill** is a directory holding a `SKILL.md`: YAML frontmatter between `---` lines, then
Markdown instructions. This is the [agentskills.io](https://agentskills.io) format that
Hermes and OpenClaw use.

```markdown
---
name: expense-report
description: File expense receipts (PDF or image files) into the Expenses sheet. …
---

# Expense report

1. Collect every receipt file named in the task …
2. Call `receipts.parse` with `{"file": "<file name>"}` once for each receipt.
```

- `name` is 1–64 characters of `a-z0-9-`, with no leading, trailing or doubled hyphens, and
  must match the directory name. Ratchet identifies a skill by its directory name everywhere.
- `description` (1–1024 characters) is the **trigger text**: the harness decides from it
  whether the skill applies to a task. Widening it can make the skill fire on tasks it
  shouldn't; narrowing it can stop it from firing on its own tasks.

A **skill edit** is any create, modify or delete in the skills directory. Ratchet captures
each one as a commit.

## The skills repo: `main` and `candidate`

The skills directory is a git repo with two checkouts (ADR 0004):

- **`main`** (`RATCHET_SKILLS_REPO`, default `var/skills`) is what the harness reads. It
  changes only when Ratchet merges.
- **`candidate`** (a worktree at `<skills repo>-candidate`) is where edits wait while
  Ratchet checks them.

The watcher sees a write in the `main` checkout, waits 750 ms for the write to settle,
moves it onto a new `candidate` commit, and restores `main`. Writes to `main` are attributed
to the **agent**. Writes in the candidate worktree are attributed to the **user**. Seeded
scenario edits are attributed to **seeded scenario**.

Every action is a normal git operation, so ordinary review tools work. History is never
rewritten:

| Action | Git operation |
| --- | --- |
| Merge (PASS or Approve) | fast-forward `main` to the candidate commit (`--ff-only`) |
| Hold (WARN) | leave `candidate` ahead of `main` |
| Revert (BLOCK or Rollback) | `git revert` the edit's commit on `candidate`, then fast-forward `main` |

## Golden traces and blessing

A **golden trace** is a finished task you marked as correct. It holds the task input, every
model call, every tool call with its result, the final output, an environment snapshot
(workspace, frozen time, seed, agent tier, harness) and what Ratchet extracted at bless time.

You **bless** a live run from the trace page, or with `ratchet bless <run_id>` or
`ratchet bless --last`. Blessing:

1. **PII pass.** Emails, phone numbers and secrets in the task input and output become
   tokens (`<EMAIL_1>`, `<PHONE_1>`, `<SECRET>`). The token map is stored encrypted, with its
   key outside the data directory.
2. **World facts** (Nano). The facts about the outside world that the output relied on: an
   exchange rate, a price, a version. Each fact is marked volatile or stable and carries a
   search hint. A trace with facts, or one that used a world tool (`web.*`, `finance.*`,
   `time.now`), is **world-dependent**.
3. **Side-effect classes.** Each tool call gets a class from the rule table, or from Nano
   for tools no rule covers (cached per tool name).
4. **Success criteria** (Nano). Short statements the output must keep meeting, for example
   "The output includes the VAT line". You can edit them on the trace page.

**Unbless** stops Ratchet from replaying a task. The run and its steps are kept.
**Re-bless** makes a replay the new reference and archives the old golden with the reason
"world changed" (see [drift](#drift-is-not-regression)).

### Side-effect classes

| Class | Examples in the tool catalog | Hard? |
| --- | --- | --- |
| `read` | `github.list_issues`, `receipts.parse`, `finance.rate`, `web.search`, `fs.read`, `shell.run` (read-only) | no |
| `write_reversible` | `github.label`, `sheets.append`, `fs.write` | no |
| `write_irreversible` | `github.comment`, `github.close_issue`, `github.open_pr`, `github.merge_pr`, `calendar.create` | **yes** |
| `destructive` | `fs.delete` | **yes** |
| `external_send` | `gmail.send`, `slack.post` | **yes** |
| `spend` | `payments.pay` | **yes** |

A **new** call of a hard class in a replay is a Level 0 hard fail.

## Which goldens are replayed

For an edit to skill *S*, Ratchet selects, in priority order:

1. **Direct:** every golden that invoked *S*.
2. **Trigger neighborhood:** goldens that did not invoke *S*, but whose task input is close
   to *S*'s new description. These catch a skill that now fires where it shouldn't.
3. **Canaries:** 3 fixed goldens that invoke no skill, replayed every time, to catch global
   breakage.

The selection is capped at `RATCHET_MAX_TRACES_PER_EDIT` (40) and split into batches of
`RATCHET_BATCH_SIZE` (10; `RATCHET_SIMULATE_BATCH_SIZE`, 4, for Simulate). If no golden uses
the skill, the edit passes with "Passed: no blessed tasks use *S* yet. Bless one to protect
it."

## Replay and sandboxes

A **replay** re-runs a golden's task input with the candidate skills, in a fresh sandbox:

- **Same model tier** as the original run (ADR 0003). The proxy refuses an agent call on any
  other tier with `409 tier_mismatch`. A difference in the output is then the skill's, not
  the model's.
- **Same environment:** the workspace snapshot, frozen time, the golden's seed and
  temperature 0.
- **Side effects come from cassettes** (below). Pure local tools (`fs.*`, the read-only
  `shell.run`) execute for real inside the sandbox's workspace.
- **The key stays outside.** The sandbox gets a dummy key. Only the sidecar proxy outside it
  holds the real Token Factory key.

Where the worker runs (local runner or Nebius Serverless Jobs) and what isolates it
(`process`, `docker` or `openshell`) are settings. See the
[matrix](configuration.md#runners-sandboxes-and-harnesses).

!!! note "Replays are not byte-identical"
    Token Factory does not honor `seed` (verified live), so even an unchanged skill can
    produce different wording. That is why Ratchet judges meaning, not bytes. The
    benchmark's E6 run measures this noise floor.

## Cassettes

A **cassette** is a recorded tool request and response, keyed by the tool and its
normalized arguments (ADR 0005). When you record a run, the mock tool server saves one per
side-effecting call. When Ratchet replays a run, the mock tool server answers from the
cassettes:

- A **read** call matches tolerantly. A miss gets a synthetic empty response and is logged;
  it is not a side effect.
- A **hard-class** call must match its cassette exactly. A reversible write must also match
  on its recipient, channel, amount and id-like arguments (ADR 0022).
- A hard-class call with **no cassette** is refused with `409 ratchet_blocked`, so the agent
  can't succeed through a forbidden action. The call is still recorded as an unexpected side
  effect, which is Level 0's evidence.

World-dependent tools can be served from an **overlay**: the same arguments answered from a
changed world. Scenario 3 uses one to move the EUR/USD rate.

## Level 0: the check with no model

Level 0 is a deterministic diff of side-effect calls between the golden and the replay
(ADR 0006). It runs before any model and can't be talked out of its answer by text.

- **New calls:** calls in the replay that the golden never made.
- **Missing calls:** calls in the golden that the replay no longer makes.
- **Changed args:** the same tool called with different arguments. These are paired up and
  removed from new and missing (ADR 0020), so an email whose body changed is a changed
  call, not a new send.
- **Hard fail:** any *new* call of a hard class. The pair is a `regression`, decided by
  Level 0. No tier is called for it, and it is enough to BLOCK the edit.

A missing, new or changed call that is not a hard fail is passed to the cascade as a
signal, and it removes Nano's shortcut to "pass" (ADR 0024). So does a **materially
different final output**: Level 0 also records `output_similarity`, a model-free text
similarity between the golden's reply and the replay's (whitespace and case ignored), and
below 0.90 the pair must be seen by Super. On the first live benchmark sweep every
tool-argument regression Nano cleared alone carried a "changed args" finding, and no
cosmetic edit did.

## The judge cascade

Every pair Level 0 did not decide goes through the cascade (ADR 0007, ADR 0020). Each
judge returns an opinion (`equivalent`, `regressed`, `improved` or `uncertain`) with a
confidence and the step ids it cites.

| Level | Judge | Sees | Settles the pair when |
| --- | --- | --- | --- |
| 1 | <span class="tier nano">N</span> Nano screen | criteria, both outputs, tool-call summaries | `equivalent` ≥ 0.85 → **pass**, unless Level 0 found a structural difference or the outputs differ materially (ADR 0024). Anything else goes to Super. Nano never blocks |
| 2 | <span class="tier super">S</span> Super check | full step summaries, both outputs, the skill diff | `equivalent` ≥ 0.80 → **pass**; `improved` ≥ 0.80 → **improved**. Everything else, including `regressed`, goes to Ultra |
| 3 | <span class="tier ultra">U</span> Ultra adjudication | everything Super saw, plus full outputs; thinking on, 4,000-token reasoning budget | Ultra's verdict is final, except that Ultra `equivalent`/`improved` against Super `regressed` ≥ 0.80 makes the pair **uncertain** |

Rules that keep the cascade honest:

- A pair becomes `regression` through the cascade only when **Super and Ultra both say
  regressed** (or Level 0 found a missing hard-class call and Ultra says regressed).
- A failed judge call, an unreadable answer (`schema_failure`, after 2 retries) or a model
  outage gives an `uncertain` opinion. **Unknown never passes.**
- An Ultra timeout makes the pair `uncertain` with `judge_timeout`, and the edit is held.
- Judge inputs are treated as data: untrusted text is wrapped in nonce-tagged delimiters,
  and only one JSON object is parsed from a reply (ADR 0022).
- When evidence was cut to fit a judge's context, the opinion is marked **Truncated**.

### Pair outcomes

| Outcome | Dashboard label | Meaning |
| --- | --- | --- |
| `pass` | <span class="chip pass">✓ Same result</span> | Output meets the criteria; differences are cosmetic. |
| `improved` | <span class="chip pass">↑ Better</span> | Meets the criteria and improves on one. |
| `drift` | <span class="chip drift">~ World changed</span> | A fact changed since the original run; the new output follows from it. |
| `regression` | <span class="chip block">✕ Got worse</span> | A criterion is no longer met, or Level 0 found a new hard side effect. |
| `uncertain` | <span class="chip warn">? Not sure</span> | The judges disagreed or could not decide. Held for a human. |
| `failed` | <span class="chip neutral">– Could not replay</span> | The sandbox or harness failed for this task. |

## Drift is not regression

Some tasks depend on the world: an exchange rate, a price, a package version. When the world
moves, their output changes even though the skill didn't. A naive gate would block that. In
Ratchet, drift is its own outcome (ADR 0008).

The drift check runs only for **world-dependent** goldens whose pair ended `regression` or
`uncertain` after the cascade. It never runs on a Level 0 hard fail, because a changed fact
can't explain a new destructive call. For up to four volatile facts:

1. **Search.** Tavily is asked for the fact's current value: the query is rewritten to ask
   for "… today". The search is filtered by the claim's source domain (`include_domains`,
   retried unrestricted if that finds nothing) and its observation date (`time_range` for a
   recent claim, `start_date` for an older one), with `include_answer`.
2. **Compare.** Nano compares the old value with the evidence and with the value the replay's
   own tool call observed. Tavily has to corroborate that value. An unsure comparison
   escalates to Super.
3. **Attribute.** For a fact that changed with confidence ≥ 0.75, Super decides whether the
   difference between the outputs follows from the new value. The pair becomes `drift` only
   if Super says attributable with confidence ≥ 0.75.
4. Otherwise the outcome stands, with the checks attached as evidence that the world did not
   move.

If Tavily fails, the check is marked unavailable, and the pair keeps its outcome with a
<span class="badge warn">Drift check unavailable</span> badge. The dashboard says
"Couldn't check whether the world changed. Treating this difference as a possible
regression." A fact that isn't findable on the public web stays "not sure", and the edit
is held. It never passes silently.

`RATCHET_FEATURE_DRIFT=false` turns the drift check off. `RATCHET_TAVILY_MODE=fixtures`
answers from recorded Tavily responses (the default; the seeded scenarios ship them).

## Noise is not regression either: control replays

Models vary from run to run. On Nebius Token Factory, `seed` is not honored, so two runs
of the *same* skill on the same task can differ: one run reports "Rows in: 5", the next
"Rows in: 4". A single replay cannot tell that kind of variance from a regression.

When a pair is still `regression` or `uncertain` after the cascade and the drift check,
and Level 0 did not decide it, Ratchet replays the golden once more with the **unchanged**
skill (the edit's parent commit). This is the *control replay*, and it is judged with
Level 0 and the Nano screen only:

- **Control agrees** with the golden: the task is stable, so the original outcome stands
  and carries more weight.
- **Control diverges** too: the task varies without the edit. The pair counts as
  `pass` with `decided_by: control`, the dashboard marks it "Same result (noisy task)",
  and the reason reads: *The unchanged skill also produced a different result on this
  task, so the difference is nondeterminism, not the edit.*
- **Control could not run** (sandbox or harness failure): nothing changes.

The stage shows up as "Replaying controls (k/n)" on the progress strip, between the
Tavily check and the summary. It costs one extra replay per contested pair and nothing
when nothing is contested. The benchmark reports the share of control-diverged pairs as
the measured noise floor (ADR 0023).

## Decisions and actions

The pair outcomes become one decision for the edit. The rules are evaluated top to bottom
(`ratchet/judge/decide.py`, ADR 0007), and the first rule that holds decides:

| # | Condition | Decision | Action | Reason code |
| --- | --- | --- | --- | --- |
| 1 | Any pair has a Level 0 hard fail | <span class="chip block">✕ BLOCK</span> | reverted | `level0_hard_fail` |
| 2 | A `regression` pair where Super and Ultra both said regressed (or a missing hard call + Ultra regressed) | <span class="chip block">✕ BLOCK</span> | reverted | `agreed_regression` |
| 3 | More than 20 % of pairs failed to replay | <span class="chip warn">! WARN</span> | held | `replay_infra` |
| 4 | The final judge timed out | <span class="chip warn">! WARN</span> | held | `judge_timeout` |
| 5 | Super and Ultra disagreed on a pair | <span class="chip warn">! WARN</span> | held | `judges_disagreed` |
| 6 | A `regression` on a single judge | <span class="chip warn">! WARN</span> | held | `single_judge_regression` |
| 7 | An `uncertain` pair, or a `failed` one | <span class="chip warn">! WARN</span> | held | `uncertain`, `replay_failed` |
| 8 | Nothing to replay | <span class="chip pass">✓ PASS</span> | merged | `no_affected_traces` |
| 9 | Every pair is `pass`, `improved` or `drift` | <span class="chip pass">✓ PASS</span> | merged | `all_clear` (+ `drift`) |

Anything that matches no rule is WARN, held, `uncertain`. After the decision, two git
checks can still turn a PASS into a hold: a merge conflict with `main` (`merge_conflict`), or
an earlier held edit in the way (`stacked_on_held`, below).

**What each decision means for you:**

- **PASS.** The edit is merged into `main`. Your agent uses the new skill on its next task.
  **Rollback** stays available.
- **WARN.** The edit is held on `candidate`; `main` is unchanged. Read the evidence, then
  **Approve** (merge) or **Rollback** (revert).
- **BLOCK.** The edit is reverted on `candidate`; `main` never changed. If the block was
  wrong, **Approve anyway** re-applies the edit and merges it.

The headline always starts with the decision word and names the skill, for example "Blocked:
the new github-triage would close issues in 6 of 14 replays." Ultra writes a summary of
at most 180 words, which ends with one of four sentences: "Nothing to do.", "Approve if this
is intended.", "Roll back if this is not intended." or "Re-bless the changed tasks." If
Ultra's text fails validation, Ratchet uses a deterministic template instead.

A notification goes to `RATCHET_NOTIFY_WEBHOOK` when it is set: `Ratchet {decision}:
{headline} — {url}`, at most 160 characters.

## Stacked edits

Edits pile up on `candidate`. A merge fast-forwards `main` over every commit between them, so
merging edit N would also ship a held edit N−1 that never got a passing verdict. Ratchet
does not allow that (ADR 0021):

- Before any merge, automatic or manual, Ratchet lists the commits between `main` and the
  edit. If one belongs to another edit that is held or still being judged, the merge does
  not happen.
- An automatic PASS becomes WARN `stacked_on_held`: "Held: an earlier held edit is still
  waiting on candidate. Review before merging."
- A manual Approve answers `409 stacked_on_held`.
- A rolled-back edit doesn't block, because its revert commit cancels it out. A reverted
  ancestor counts as resolved only once its revert is an ancestor of the merge target
  (ADR 0022).

In practice, resolve held edits oldest first, the order a reviewer reads them. `main` only
ever holds edits that individually passed or were approved.

## Costs and the counterfactual

The capture proxy prices every model call from its `usage` block at the catalog prices in
`ratchet/config/pricing.py`, and tags it with its tier and purpose (`agent`, `screen`,
`check`, `adjudicate`, `extract`, `narrative`, `bench`, `embed`). Every cost Ratchet shows
carries its tier, for example `$0.04 (nano)`.

| Tier | Token Factory model | Input / output, USD per 1M tokens |
| --- | --- | --- |
| <span class="tier nano">N</span> nano | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 0.06 / 0.24 |
| <span class="tier super">S</span> super | `nvidia/nemotron-3-super-120b-a12b` | 0.30 / 0.90 |
| <span class="tier ultra">U</span> ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` | 1.00 / 3.00 |

For each verdict, the cost panel shows:

- **Judging cost by tier:** screening, checking, adjudicating, fact extraction and the
  summary.
- **The all-Ultra counterfactual:** the same judge tokens priced as if every judge call had
  run on Ultra. "cascade saved $X (N×)" is the difference.
- **Agent replays,** separately: the agent runs on the same tier as the original runs, so
  this cost doesn't depend on the judging strategy.

Repeated judge prompts hit a response cache keyed by the request and model, valid for
`RATCHET_CACHE_TTL_DAYS` (7). A hit costs $0. Agent calls are never cached. Set the TTL to 0
to measure cold reliability.

## Simulated models

`RATCHET_UPSTREAM=sim` routes the capture proxy to `ratchet.sim`, an in-repo, deterministic
stand-in for Token Factory (ADR 0018). It answers agent calls with scripted skill policies
that read the same skill text a real model follows, and judge calls with heuristics over the
prompt sections.

- It exists for CI, the end-to-end suite and development without keys.
- Every verdict it produces has `simulated_models: true`. The dashboard shows a
  <span class="badge warn">Simulated models</span> badge in the header and on the verdict,
  and the CLI prints `[Simulated models]`.
- Its token counts are priced like real ones, so the cost views work, but no money is spent.
- **No published number comes from it.** Benchmark output produced on the simulator is
  labeled "SIMULATED MODELS — pipeline smoke test; not benchmark results" on every page.

## Recorded runs

A demo host can serve Simulate from stored verdicts instead of computing them
(`RATCHET_SIMULATE_MODE=replayed`). The stored runs under `scenarios/recorded/<k>/` were
recorded on live models with `ratchet simulate <k> --record-fixture`. In this mode a Simulate
press copies the record under fresh ids and re-emits its events at their original relative
times, compressed ×2, so the progress strip still moves. No model is called and nothing is
spent. Every such verdict carries a <span class="badge">Recorded run</span> badge.
