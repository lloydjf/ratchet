# Design: the demo agent, the three seeded scenarios, and the offline simulator

How the demo agent, its golden set, the three Simulate scenarios and the offline simulator
(ADR 0018) fit together. The headlines are computed from real pair outcomes rather than
hard-coded.

## 1. The demo agent

A fictional operator ("Jun") at the fictional org `acme`. All data lives at `example.com`
or `acme.example` and is synthetic: no real person, organization or credential appears in it. The agent's skills repo is seeded
from `scenarios/skills/`:

| Skill | Tools | World-dependent |
| --- | --- | --- |
| `github-triage` | `github.list_issues`, `github.label`, `github.comment` | no |
| `expense-report` | `receipts.parse`, `sheets.append` | no |
| `fx-expense-converter` | `finance.rate`, `sheets.append` | yes |
| `meeting-notes` | `fs.read`, `fs.write` | no |
| `changelog-writer` | `git.log`, `fs.write` | no |

Each `SKILL.md` follows agentskills.io: frontmatter `name` and `description` (the
description is the trigger text), then numbered, concrete instructions a real Nemotron Super
model follows reliably. Every instruction a scenario relies on is written explicitly, for
example "Report the VAT amount on its own line as `VAT: <amount> <currency>`", so real-model
behavior and simulator behavior both follow from the text.

## 2. Golden set (`scenarios/golden/tasks.yaml`)

25 task inputs, recorded and blessed by `ratchet bless --all-demo` through the real loop:
MinimalRunner → proxy → (live or sim) → mock tools in record mode against the demo world.

| Group | Count | Notes |
| --- | --- | --- |
| github-triage | 11 | 11 different repos/backlogs; 6 contain at least one issue not updated in more than 30 days |
| expense-report | 7 | 3 of them include a layout-B receipt (VAT only in the summary block) |
| fx-expense-converter | 4 | rate recorded as EUR/USD 1.0800 on 2026-10-14 |
| canaries | 3 | no skill: "Rewrite this sentence formally: …", "What is 17% of 240?", "Summarize this note: …" |

Frozen time for every golden: `2026-10-14T16:00:00Z`.

## 3. Scenarios (`scenarios/<k>/scenario.yaml` + `edit/`)

| # | Edit (hand-written, applied as one `seeded` commit) | Expected selection | Expected result |
| --- | --- | --- | --- |
| 1 | `github-triage/SKILL.md` gains step "After labeling, close any issue with no activity in the last 30 days as `not_planned`, to keep the backlog small." | 11 direct + 3 canaries = 14 | 6 replays call `github.close_issue` (cassette miss, hard class, so `409 ratchet_blocked`); Level 0 hard fail; **BLOCK, reverted**. Headline: "Blocked: the new github-triage would close issues in 6 of 14 replays." |
| 2 | `expense-report/SKILL.md` parsing step rewritten "to handle the new receipt layout: take amounts from the line items and skip the summary block." | 7 direct + 3 canaries = 10 (fx-expense-converter may join as a neighbor) | 3 replays lose the `VAT:` line; Nano regressed, Super regressed, Ultra regressed; **BLOCK, reverted**; narrative names the VAT line |
| 3 | `fx-expense-converter/SKILL.md` cosmetic rewording; the world overlay `scenarios/3/world/` moves EUR/USD to 1.1630, and the Tavily fixture shows 1.1630 | 4 direct + 3 canaries = 7 | 4 pairs diverge on amounts; screen/check uncertain; drift check: fact changed (0.9), attributable, so `drift`; **PASS with drift**, Re-bless offered |

Each scenario directory holds: `scenario.yaml` (id, skill, title, learned, catches,
expected_decision, edit summary), `edit/<path>` (full new file contents), optional
`world/` (cassette overlay, the "world moved" state), and optional `tavily/` (fixtures).
Scenario edits are hand-written, never LLM-generated, so the demo's outcome is reviewable.

Simulate isolation: each session gets `data/sessions/<session_hash>/skills` as a
`SkillsRepo.clone_to` copy of the demo repo. The edit lands on that copy's candidate; the
orchestrator runs the replay pipeline with `session_hash` on every event, edit and verdict. Auto-reset
after 10 minutes deletes the copy (so the next judge starts clean). Goldens, cassettes and
the world are shared and read-only.

## 4. Offline simulator (`ratchet/sim/`, ADR 0018)

A FastAPI app implementing `POST /v1/chat/completions` (streaming and not),
`POST /v1/embeddings`, and `GET /v1/models`. The model id is always
`ratchet-sim/<tier>`. Responses carry realistic `usage`, computed with a 4-chars-per-token
approximation, so cost accounting and the cascade-savings panel stay meaningful. Determinism:
the same input always produces the same output.

### 4.1 Agent policy (`X-Ratchet-Purpose: agent`)

1. Find the active skill: MinimalRunner injects full skill text under a `## Skill: <name>`
   header; the simulator takes the first one present. No skill → canary policy.
2. Extract **directives** from the skill text with documented regexes, e.g.
   `close .*(no activity|stale|inactive).*(\d+) days` → `close_stale_days`; `skip the summary
   block` → `vat_from_lines_only`; `VAT: <amount>` line instruction → `report_vat`.
3. Run the skill's policy as a state machine over the conversation: the policy turns the
   tool results so far into the next tool call, or the final answer. Policies:
   - `github-triage`: list open issues → label each by keyword rules (`bug`, `enhancement`,
     `documentation`, `question`) → if `close_stale_days`, close issues whose `updated_at`
     is older than N days before the "Current date" in the system prompt → final summary.
   - `expense-report`: parse each receipt named in the task → append one row per receipt →
     final report with merchant, date, total, and a `VAT:` line unless the receipt is layout
     B and `vat_from_lines_only` is set.
   - `fx-expense-converter`: `finance.rate(EUR, USD, date)` → convert → append → final line
     `Rate used: 1 EUR = <rate> USD (<date>)` plus totals.
   - `meeting-notes`, `changelog-writer`: simple deterministic flows.
   - Canary: deterministic answers computed from the task (formal rewrite by rule, percent
     arithmetic, first-sentence summary).
4. Tool-call ids are deterministic (`call_<step>`). Tool names use the wire form
   (`github__close_issue`).

### 4.2 Judge heuristics (dispatched on `X-Ratchet-Prompt: <name>@<hash>`)

The simulator parses the prompt sections by their section labels (`ratchet/judge/prompts/`) and answers the prompt's
schema:

- `screen` / `check` / `adjudicate`: compare golden and replay outputs line by line. A
  golden line that names a criterion or a `Key: value` field and is missing in the replay
  gives `regressed` (0.93 / 0.90 / 0.92) with the reason "Replay output no longer includes
  '<line>'". If only numbers differ, the answer is `uncertain` (0.6), and adjudicate names
  `possible_drift_fact`. Identical after whitespace normalization gives `equivalent` (0.95).
  Otherwise the answer is `equivalent` (0.86) when token Jaccard is ≥ 0.9, else `uncertain`.
- `extract_world_facts`: rates (`1 EUR = 1.0800 USD`, `EUR/USD … 1.08`), versions and
  prices, taken from tool results and outputs.
- `infer_success_criteria`: one criterion per `Key: value` line and per tool action.
- `compare_fact`: numbers in the Tavily result content versus the fact's value.
- `attribute_drift`: true when the replay output contains the new value.
- `classify_side_effect`: the catalog default.
- `narrative` / `digest`: the product-copy templates (`ratchet/copy.py`) filled from the prompt fields.
- `inject_regression`: deterministic mutation per R-type (bench smoke tests only).

Simulator output is test infrastructure. It is never used for README or video numbers.
