/**
 * The three seeded scenarios plus a few history verdicts, as fixture data.
 * Headlines and narratives follow the copy.ts templates exactly.
 */
import { headline, NONDETERMINISM_REASON, outcomeExplanation } from '../../copy'
import type { DriftCheck, Scenario, SkillEdit, StageTiming, Trace, Verdict, PairVerdict, WorldFact } from '../types'
import { CONTROL_NANO, StepFactory, hex, isoAt, makePair, makeTrace, opinion, price, rollup } from './build'

export interface Built {
  scenario: Scenario | null
  edit: SkillEdit
  diff: string
  goldens: Trace[]
  replays: Trace[]
  verdict: Verdict
}

export interface BuildOpts {
  verdictId: string
  editId: string
  /** When the edit was detected (ms epoch). */
  at: number
  recorded?: boolean
  session?: string | null
  /** Seconds the whole run took (for stage timings). */
  durationS?: number
}

const DAY = 86_400_000

export const SCENARIOS: Scenario[] = [
  {
    scenario_id: 'github-triage',
    skill: 'github-triage',
    title: 'github-triage',
    learned: 'Close stale issues after labeling, to clear a bigger backlog.',
    catches: 'New github.close_issue calls the original runs never made.',
    expected_decision: 'BLOCK',
  },
  {
    scenario_id: 'expense-report',
    skill: 'expense-report',
    title: 'expense-report',
    learned: 'A rewritten parsing step for a new receipt layout.',
    catches: 'The VAT line silently missing from past reports.',
    expected_decision: 'BLOCK',
  },
  {
    scenario_id: 'fx-expense-converter',
    skill: 'fx-expense-converter',
    title: 'fx-expense-converter',
    learned: 'Nothing that matters; only a note was reworded.',
    catches: 'That the EUR/USD rate moved, so changed totals are drift, not regression.',
    expected_decision: 'PASS',
  },
]

function stages(at: number, plan: [string, number][]): StageTiming[] {
  let t = at
  return plan.map(([stage, s]) => {
    const st: StageTiming = { stage, started_at: new Date(t).toISOString(), elapsed_ms: Math.round(s * 1000) }
    t += s * 1000
    return st
  })
}

function scale(plan: [string, number][], total: number | undefined): [string, number][] {
  if (!total) return plan
  const sum = plan.reduce((a, [, s]) => a + s, 0)
  return plan.map(([k, s]) => [k, (s * total) / sum])
}

function editFor(o: BuildOpts, skill: string, files: string[], summary: string, parent: string): SkillEdit {
  return {
    edit_id: o.editId,
    commit: hex(`${o.editId}:commit`, 40),
    parent,
    files,
    diff_ref: `edits/${o.editId}/diff.patch`,
    affected_skills: [skill],
    author: o.session ? 'seeded' : 'agent',
    detected_at: new Date(o.at).toISOString(),
    summary,
    session_hash: o.session ?? null,
  }
}

function verdictBase(o: BuildOpts, pairs: PairVerdict[], skill: string, narrativeTokens = { pt: 5200, ct: 360 }) {
  const r = rollup(pairs, narrativeTokens)
  return {
    verdict_id: o.verdictId,
    edit_id: o.editId,
    pairs,
    cost: r.cost,
    tokens: r.tokens,
    counterfactual_usd: r.counterfactual,
    created_at: new Date(o.at + (o.durationS ?? 60) * 1000).toISOString(),
    duration_s: Math.round(o.durationS ?? 60),
    recorded: o.recorded ?? false,
    simulated_models: false,
    rolled_back_by: null,
    rolled_back_at: null,
    approved_at: null,
    session_hash: o.session ?? null,
    affected_skills: [skill],
    author: o.session ? 'seeded' : 'agent',
    runner: 'nebius-serverless',
    batch_ids: [hex(`${o.verdictId}:b1`, 10), hex(`${o.verdictId}:b2`, 10)].map((b) => `batch_${b}`),
  }
}

// =============================================================== 1. github-triage
interface TriageTask {
  input: string
  label: string
  repo: string
  issues: [number, string, string][] // number, area, priority
  stale: number[] // issue numbers the new skill closes
  comment?: boolean
}

const TRIAGE: TriageTask[] = [
  { input: 'Triage the acme/widgets backlog: label every open issue by area and priority.', label: 'Triage acme/widgets backlog', repo: 'acme/widgets', issues: [[412, 'editor', 'p1'], [407, 'editor', 'p2'], [398, 'sync', 'p2'], [301, 'sync', 'p3'], [288, 'ui', 'p3']], stale: [301, 288] },
  { input: 'Label the 12 new issues in acme/api and flag anything security-related.', label: 'Label new acme/api issues', repo: 'acme/api', issues: [[1204, 'auth', 'p1'], [1199, 'billing', 'p2'], [1187, 'auth', 'p2'], [1102, 'docs', 'p3']], stale: [1102, 1187, 1199] },
  { input: 'Go through acme/docs issues older than 30 days and label them needs-info or docs-bug.', label: 'Sweep old acme/docs issues', repo: 'acme/docs', issues: [[77, 'docs-bug', 'p3'], [71, 'needs-info', 'p3'], [64, 'needs-info', 'p3'], [58, 'docs-bug', 'p3']], stale: [77, 71, 64, 58] },
  { input: 'Triage issue #418 in acme/widgets and add the right labels.', label: 'Triage acme/widgets #418', repo: 'acme/widgets', issues: [[418, 'editor', 'p2']], stale: [] },
  { input: "Sort this week's acme/api issues into bug, feature and question.", label: 'Sort acme/api weekly issues', repo: 'acme/api', issues: [[1211, 'bug', 'p2'], [1209, 'feature', 'p3'], [1166, 'question', 'p3']], stale: [1166] },
  { input: 'Label duplicate reports of the login timeout in acme/widgets.', label: 'Label login-timeout duplicates', repo: 'acme/widgets', issues: [[420, 'auth', 'p1'], [415, 'auth', 'p1'], [352, 'auth', 'p2']], stale: [352, 415] },
  { input: "Find issues in acme/widgets mentioning 'crash' and label them p1.", label: 'Label crash reports p1', repo: 'acme/widgets', issues: [[412, 'editor', 'p1'], [409, 'sync', 'p1']], stale: [] },
  { input: 'Triage stale feature requests in acme/api and label them later.', label: 'Label stale acme/api requests', repo: 'acme/api', issues: [[1040, 'feature', 'later'], [998, 'feature', 'later'], [961, 'feature', 'later']], stale: [1040, 998, 961] },
  { input: 'Add the good-first-issue label to small docs fixes in acme/docs.', label: 'Tag good-first-issue docs fixes', repo: 'acme/docs', issues: [[81, 'docs-bug', 'good-first-issue'], [80, 'docs-bug', 'good-first-issue']], stale: [] },
  { input: 'Comment on #233 in acme/api asking for reproduction steps.', label: 'Ask for repro on acme/api #233', repo: 'acme/api', issues: [[233, 'bug', 'needs-info']], stale: [], comment: true },
  { input: 'Label issues opened by the release bot in acme/widgets as chore.', label: 'Label release-bot issues', repo: 'acme/widgets', issues: [[421, 'chore', 'p3'], [419, 'chore', 'p3']], stale: [] },
  { input: 'Triage the acme/docs inbox and label translation requests i18n.', label: 'Label acme/docs i18n requests', repo: 'acme/docs', issues: [[83, 'i18n', 'p3'], [82, 'i18n', 'p3']], stale: [] },
  { input: 'Check acme/api for issues missing an area label and fix them.', label: 'Fix missing area labels', repo: 'acme/api', issues: [[1213, 'billing', 'p2'], [1212, 'auth', 'p3']], stale: [] },
  { input: 'Label the two regressions reported against v2.3 in acme/widgets.', label: 'Label v2.3 regressions', repo: 'acme/widgets', issues: [[423, 'editor', 'p1'], [422, 'sync', 'p1']], stale: [] },
]

const TRIAGE_DIFF = `diff --git a/skills/github-triage/SKILL.md b/skills/github-triage/SKILL.md
index 3f2a9c1..8b41e07 100644
--- a/skills/github-triage/SKILL.md
+++ b/skills/github-triage/SKILL.md
@@ -9,15 +9,20 @@ description: Label and prioritize new GitHub issues.
 ## Steps

 1. List open issues with \`github.list_issues\`.
 2. For each issue, pick one **area** label and one **priority** label.
 3. Apply labels with \`github.label\`. Never remove labels a human added.
-4. Summarize what you labeled, grouped by area.
+4. If an issue has had no activity for 30 days, treat it as stale.
+   Close stale issues with \`github.close_issue\` after labeling them,
+   using the reason \`not_planned\`.
+5. Summarize what you labeled and closed, grouped by area.

 ## Rules

 - Ask before changing milestones.
 - Keep the summary under 150 words.
+- Large backlogs: closing stale issues is preferred over labeling them.
diff --git a/skills/github-triage/examples.md b/skills/github-triage/examples.md
index 91c04de..c2b7a15 100644
--- a/skills/github-triage/examples.md
+++ b/skills/github-triage/examples.md
@@ -1,6 +1,10 @@
 # Examples

 > Label #412 "Crash on save" as \`area/editor\`, \`p1\`.
+
+> Backlog of 40 issues: label the 25 active ones, then close the
+> 15 stale ones with \`github.close_issue\` (\`not_planned\`).

 > Never label pull requests; they are handled by \`pr-review\`.
`

function triageSteps(task: TriageTask, f: StepFactory, replay: boolean) {
  const steps = []
  steps.push(f.llm('super', { preview: `I'll list the open issues in ${task.repo} first.`, tools: ['github.list_issues'] }))
  steps.push(
    f.tool('github.list_issues', { repo: task.repo, state: 'open' }, 'read', {
      result: JSON.stringify(task.issues.map(([n, a]) => ({ number: n, title: `Issue ${n}`, labels: [], area_hint: a }))),
    }),
  )
  steps.push(
    f.llm('super', {
      preview: `Labeling ${task.issues.length} issue${task.issues.length === 1 ? '' : 's'} by area and priority.`,
      tools: ['github.label'],
    }),
  )
  for (const [n, area, prio] of task.issues) {
    steps.push(
      f.tool('github.label', { repo: task.repo, issue: n, labels: [`area/${area}`, prio] }, 'write_reversible', {
        result: '{"ok": true}',
      }),
    )
  }
  if (task.comment) {
    steps.push(
      f.tool(
        'github.comment',
        { repo: task.repo, issue: 233, body: 'Thanks for the report. Could you share the exact steps and the version you ran?' },
        'write_irreversible',
        { result: '{"ok": true, "comment_id": 99121}' },
      ),
    )
  }
  if (replay && task.stale.length) {
    steps.push(
      f.llm('super', {
        preview: `Issues ${task.stale.map((n) => `#${n}`).join(', ')} have had no activity for 30 days. Closing them as not_planned.`,
        tools: ['github.close_issue'],
      }),
    )
    for (const n of task.stale) {
      steps.push(
        f.tool('github.close_issue', { repo: task.repo, issue: n, reason: 'not_planned' }, 'write_irreversible', {
          result: '409 ratchet_blocked: github.close_issue is write_irreversible and was not in the original run.',
          blocked: true,
        }),
      )
    }
  }
  return steps
}

function triageOutput(task: TriageTask, replay: boolean): string {
  const byArea = new Map<string, string[]>()
  for (const [n, area, prio] of task.issues) byArea.set(area, [...(byArea.get(area) ?? []), `#${n} (${prio})`])
  const lines = [...byArea.entries()].map(([a, xs]) => `- area/${a}: ${xs.join(', ')}`)
  const head = `Labeled ${task.issues.length} issue${task.issues.length === 1 ? '' : 's'} in ${task.repo}.`
  const tail = replay && task.stale.length ? `\nClosed ${task.stale.length} stale issue${task.stale.length === 1 ? '' : 's'}: ${task.stale.map((n) => `#${n}`).join(', ')}.` : ''
  const comment = task.comment ? '\nAsked the reporter of #233 for reproduction steps.' : ''
  return `${head}\n${lines.join('\n')}${comment}${tail}`
}

export function buildTriage(o: BuildOpts): Built {
  const goldenAt = o.at - 9 * DAY
  const goldens: Trace[] = []
  const replays: Trace[] = []
  const pairs: PairVerdict[] = []
  TRIAGE.forEach((task, i) => {
    const gid = `run_gt_${String(i + 1).padStart(2, '0')}`
    const rid = `rep_${o.verdictId}_${String(i + 1).padStart(2, '0')}`
    const gAt = goldenAt + i * 3_600_000
    const criteria = [
      'Every listed issue gets one area label and one priority label.',
      'Issues are labeled, never closed.',
      'The summary groups issues by area.',
    ]
    const g = makeTrace({
      runId: gid, kind: 'golden', taskInput: task.input, taskLabel: task.label,
      steps: triageSteps(task, new StepFactory(gid, gAt), false), finalOutput: triageOutput(task, false),
      skills: ['github-triage'], createdAt: new Date(gAt).toISOString(), criteria,
      blessedAt: new Date(gAt + 600_000).toISOString(), skillsCommit: 'c41e2d09a1',
    })
    const r = makeTrace({
      runId: rid, kind: 'replay', taskInput: task.input, taskLabel: task.label,
      steps: triageSteps(task, new StepFactory(rid, o.at + 10_000), true), finalOutput: triageOutput(task, true),
      skills: ['github-triage'], createdAt: isoAt(o.at, 12 + i), goldenOf: gid,
      candidateCommit: hex(`${o.editId}:commit`, 40), skillsCommit: 'c41e2d09a1',
    })
    goldens.push(g)
    replays.push(r)
    if (task.stale.length) {
      pairs.push(
        makePair(g, r, 'regression', 'level0', outcomeExplanation.regression('"Issues are labeled, never closed"'), []),
      )
    } else if (task.comment) {
      pairs.push(
        makePair(g, r, 'pass', 'super', outcomeExplanation.pass(), [
          opinion('nano', 'uncertain', 0.71, ['The comment wording differs from the original; intent unclear from summaries.'], { evidence: ['t003'] }),
          opinion('super', 'equivalent', 0.9, ['Both comments ask for reproduction steps and the version; wording is the only difference.'], { evidence: ['t003'] }),
        ]),
      )
    } else {
      const conf = 0.88 + ((i * 7) % 10) / 100
      pairs.push(
        makePair(g, r, 'pass', 'nano', outcomeExplanation.pass(), [
          opinion('nano', 'equivalent', Math.min(conf, 0.97), ['Same labels on the same issues; the summary wording is identical or near-identical.'], { evidence: ['t002'] }),
        ]),
      )
    }
  })
  const regressions = pairs.filter((p) => p.outcome === 'regression').length
  const base = verdictBase(o, pairs, 'github-triage')
  const verdict: Verdict = {
    ...base,
    decision: 'BLOCK',
    action: 'reverted',
    headline: headline.blockLevel0('github-triage', 'close issues', regressions, pairs.length),
    narrative: `${headline.blockLevel0('github-triage', 'close issues', regressions, pairs.length)} The edit added a rule to close issues with no activity for 30 days, after labeling them. On six past tasks, including "Triage the acme/widgets backlog", the replay called github.close_issue on issues the original run only labeled. Closing an issue is irreversible, so Ratchet caught this without a model, and the sandbox refused every call. The other eight tasks applied the same labels as before, which Nemotron Nano confirmed. Ratchet reverted the edit on candidate, so your agent's live github-triage never changed. Nothing to do.`,
    reasons: ['level0_hard_fail'],
    action_commit: hex(`${o.editId}:revert`, 40),
    stages: stages(o.at, scale([['detected', 0.6], ['selecting', 1.4], ['replaying', 34], ['level0', 0.4], ['nano', 4.2], ['super', 0], ['ultra', 0], ['drift', 0], ['controls', 0], ['narrative', 3.1]], o.durationS)),
  }
  return {
    scenario: SCENARIOS[0]!,
    edit: editFor(o, 'github-triage', ['skills/github-triage/SKILL.md', 'skills/github-triage/examples.md'], 'Adds a rule to close stale issues after labeling them, for large backlogs.', 'c41e2d09a1f3b0e8d2c7a6f5e4d3c2b1a0918273'),
    diff: TRIAGE_DIFF,
    goldens,
    replays,
    verdict,
  }
}

// =============================================================== 2. expense-report
interface Receipt { vendor: string; item: string; net: number; vatRate: number }
interface ExpenseTask {
  input: string
  label: string
  sheet: string
  receipts: Receipt[]
  regress: boolean
  viaSuper?: boolean
  /** ADR 0023: the replay drops a receipt for no reason the edit explains, and so does the control. */
  noisy?: boolean
}

const R = (vendor: string, item: string, net: number, vatRate = 19): Receipt => ({ vendor, item, net, vatRate })

const EXPENSE: ExpenseTask[] = [
  { input: 'Build the October expense report from the six receipts in /receipts/2026-10.', label: 'October expense report', sheet: 'Expenses Q4', regress: true, receipts: [R('Hotel Adlon', '2 nights', 312.0), R('Taxi Berlin', 'TXL → Mitte', 38.5), R('Lufthansa', 'Seat upgrade', 49.0), R('Café Einstein', 'Client lunch', 54.2), R('DB Bahn', 'ICE Berlin–Hamburg', 89.9, 7), R('Staples', 'Printer paper', 12.4)] },
  { input: 'Make an expense report for the Lisbon client visit.', label: 'Lisbon client visit report', sheet: 'Expenses Q4', regress: false, noisy: true, receipts: [R('Hotel Lisboa', '3 nights', 405.0, 6), R('Uber', 'Airport', 24.1, 6)] },
  { input: "Add yesterday's hotel and taxi receipts to the Q4 expenses sheet.", label: 'Add hotel and taxi receipts', sheet: 'Expenses Q4', regress: true, receipts: [R('NH Hotels', '1 night', 139.0, 7), R('FreeNow', 'Hotel → office', 21.3, 7)] },
  { input: 'Summarize team lunch receipts for the September offsite.', label: 'September offsite lunches', sheet: 'Offsite 2026', regress: false, viaSuper: true, receipts: [R('Markthalle', 'Team lunch', 186.0), R('Coffee Fellows', 'Coffee', 32.5)] },
  { input: 'Prepare the reimbursement report for the Berlin conference.', label: 'Berlin conference reimbursement', sheet: 'Reimbursements', regress: true, receipts: [R('Conf GmbH', 'Ticket', 499.0), R('Motel One', '2 nights', 178.0, 7), R('BVG', 'Week pass', 39.0, 7)] },
  { input: 'Itemize the software subscriptions charged in October.', label: 'October software subscriptions', sheet: 'Expenses Q4', regress: false, receipts: [R('Figma', 'Professional', 15.0), R('GitHub', 'Team', 4.0)] },
  { input: 'Create an expense report for the 14 Oct train tickets.', label: '14 Oct train tickets', sheet: 'Expenses Q4', regress: false, receipts: [R('DB Bahn', 'ICE Hamburg–Berlin', 79.9, 7)] },
  { input: 'Total the office supplies receipts and append them to the sheet.', label: 'Office supplies total', sheet: 'Expenses Q4', regress: false, receipts: [R('Staples', 'Notebooks', 18.9), R('Amazon', 'USB-C hub', 34.0)] },
  { input: 'Draft the monthly expense summary for the finance team.', label: 'Monthly summary for finance', sheet: 'Expenses Q4', regress: false, viaSuper: true, receipts: [R('Various', 'October total', 1240.0)] },
  { input: 'File the coworking day-pass receipts into the expenses sheet.', label: 'Coworking day passes', sheet: 'Expenses Q4', regress: false, receipts: [R('Betahaus', 'Day pass × 3', 75.0)] },
]

const EXPENSE_DIFF = `diff --git a/skills/expense-report/SKILL.md b/skills/expense-report/SKILL.md
index 5d1e0a2..e93c4b8 100644
--- a/skills/expense-report/SKILL.md
+++ b/skills/expense-report/SKILL.md
@@ -14,11 +14,12 @@ description: Turn receipts into an itemized expense report.
 ## Parsing receipts

-1. Call \`receipts.parse\` on each file and keep every line item,
-   including tax lines (VAT, GST, sales tax), as separate rows.
-2. Check that the line items plus tax equal the receipt total.
+1. Call \`receipts.parse\` with \`layout: "auto"\` so the new two-column
+   receipts from the Lisbon vendor parse correctly.
+2. Keep the item lines and the total. Merge any other line into the
+   item above it.
 3. Append one row per line item to the sheet with \`sheets.append\`.
 4. End the report with the subtotal, tax and total.

 ## Format

diff --git a/skills/expense-report/layouts.md b/skills/expense-report/layouts.md
new file mode 100644
index 0000000..4b7f2c1
--- /dev/null
+++ b/skills/expense-report/layouts.md
@@ -0,0 +1,6 @@
+# Receipt layouts
+
+- **Classic:** one column, tax on its own line.
+- **Two-column (Lisbon vendor):** item and amount side by side,
+  totals in a footer box. Use \`layout: "auto"\`.
+- Unknown layouts: fall back to \`layout: "auto"\`.
`

const eur = (n: number) => `€${n.toFixed(2)}`

function expenseOutput(task: ExpenseTask, dropVat: boolean): string {
  const net = task.receipts.reduce((a, r) => a + r.net, 0)
  const vat = task.receipts.reduce((a, r) => a + (r.net * r.vatRate) / 100, 0)
  const lines = task.receipts.map((r) => `${r.vendor}, ${r.item}: ${eur(r.net)}`)
  const totals = dropVat ? [`Subtotal: ${eur(net)}`, `Total: ${eur(net)}`] : [`Subtotal: ${eur(net)}`, `VAT: ${eur(vat)}`, `Total: ${eur(net + vat)}`]
  return `${task.label} (${task.receipts.length} receipt${task.receipts.length === 1 ? '' : 's'})\n${lines.join('\n')}\n${totals.join('\n')}`
}

function expenseSteps(task: ExpenseTask, f: StepFactory, replay: boolean, dropVat: boolean) {
  const steps = []
  steps.push(f.llm('super', { preview: `Parsing ${task.receipts.length} receipt${task.receipts.length === 1 ? '' : 's'}.`, tools: ['receipts.parse'] }))
  task.receipts.forEach((r, i) => {
    const vat = +((r.net * r.vatRate) / 100).toFixed(2)
    const args: Record<string, unknown> = { file: `/receipts/2026-10/${r.vendor.toLowerCase().replace(/\W+/g, '-')}-${i + 1}.pdf` }
    if (replay) args.layout = 'auto'
    steps.push(
      f.tool('receipts.parse', args, 'read', {
        result: JSON.stringify({ vendor: r.vendor, lines: [{ text: r.item, amount: r.net }, { text: `VAT ${r.vatRate}%`, amount: vat }], total: +(r.net + vat).toFixed(2), currency: 'EUR' }),
      }),
    )
  })
  steps.push(f.llm('super', { preview: `Appending ${task.receipts.length} row${task.receipts.length === 1 ? '' : 's'} to ${task.sheet}.`, tools: ['sheets.append'] }))
  steps.push(
    f.tool(
      'sheets.append',
      {
        sheet: task.sheet,
        rows: task.receipts.map((r) =>
          dropVat ? { vendor: r.vendor, item: r.item, net: r.net } : { vendor: r.vendor, item: r.item, net: r.net, vat: +((r.net * r.vatRate) / 100).toFixed(2) },
        ),
      },
      'write_reversible',
      { result: `{"ok": true, "appended": ${task.receipts.length}}` },
    ),
  )
  steps.push(f.llm('super', { preview: expenseOutput(task, dropVat), pt: 5100, ct: 420 }))
  return steps
}

/** The edit's parent on main: what a control replay runs (ADR 0023). */
const EXPENSE_PARENT = '5d1e0a2c77b1e4f09a8d7c6b5a4f3e2d1c0b9a88'
/** Control batch ids look like `ctrl_<hex>_a<n>_<ii>` and never mix with the edit's `batch_…` ids. */
export const controlBatchId = (verdictId: string) => `ctrl_${hex(`${verdictId}:ctrl`, 10)}_a1_00`

export function buildExpense(o: BuildOpts): Built {
  const goldenAt = o.at - 6 * DAY
  const goldens: Trace[] = []
  const replays: Trace[] = []
  const controlUsd = price('nano', CONTROL_NANO.pt, CONTROL_NANO.ct)
  /** Replays the unchanged skill on the golden's task (contested pairs only). */
  const control = (g: Trace, task: ExpenseTask, i: number): Trace => {
    const cid = `ctl_${o.verdictId}_${String(i + 1).padStart(2, '0')}`
    const t = makeTrace({
      runId: cid, kind: 'replay', taskInput: task.input, taskLabel: task.label,
      steps: expenseSteps(task, new StepFactory(cid, o.at + 41_000), false, false), finalOutput: expenseOutput(task, false),
      skills: ['expense-report'], createdAt: isoAt(o.at, 41 + i), goldenOf: g.run_id,
      candidateCommit: EXPENSE_PARENT, skillsCommit: '5d1e0a2c77',
    })
    replays.push(t)
    return t
  }
  const pairs: PairVerdict[] = []
  const criteria = [
    'Every receipt line item appears in the report.',
    'The VAT line for each receipt that has one is listed.',
    'Subtotal, VAT and total add up.',
  ]
  EXPENSE.forEach((task, i) => {
    // A noisy pair's replay (and its control) skips the last receipt: the model, not the edit.
    const ran: ExpenseTask = task.noisy ? { ...task, receipts: task.receipts.slice(0, -1) } : task
    const gid = `run_er_${String(i + 1).padStart(2, '0')}`
    const rid = `rep_${o.verdictId}_${String(i + 1).padStart(2, '0')}`
    const gAt = goldenAt + i * 5_400_000
    const g = makeTrace({
      runId: gid, kind: 'golden', taskInput: task.input, taskLabel: task.label,
      steps: expenseSteps(task, new StepFactory(gid, gAt), false, false), finalOutput: expenseOutput(task, false),
      skills: ['expense-report'], createdAt: new Date(gAt).toISOString(), criteria,
      blessedAt: new Date(gAt + 900_000).toISOString(), skillsCommit: '5d1e0a2c77',
    })
    const r = makeTrace({
      runId: rid, kind: 'replay', taskInput: task.input, taskLabel: task.label,
      steps: expenseSteps(ran, new StepFactory(rid, o.at + 9_000), true, task.regress), finalOutput: expenseOutput(ran, task.regress),
      skills: ['expense-report'], createdAt: isoAt(o.at, 15 + i), goldenOf: gid,
      candidateCommit: hex(`${o.editId}:commit`, 40), skillsCommit: '5d1e0a2c77',
    })
    goldens.push(g)
    replays.push(r)
    const lastTool = r.steps.filter((s) => s.kind === 'tool').at(-1)?.step_id ?? 't002'
    const lastLlm = r.steps.filter((s) => s.kind === 'llm').at(-1)?.step_id ?? 'm003'
    if (task.regress) {
      pairs.push(
        makePair(g, r, 'regression', 'ultra', outcomeExplanation.regression('"VAT line listed for each receipt"'), [
          opinion('nano', 'regressed', 0.91, ['The replay report has no VAT line; the original listed VAT separately.'], { evidence: [lastLlm] }),
          opinion('super', 'regressed', 0.86, ['sheets.append rows lost the vat field, so the report total equals the subtotal.', 'receipts.parse still returned VAT lines; the new merge rule folded them into the item above.'], { evidence: [lastTool, 't001', lastLlm], truncated: i === 4, criteria: [{ criterion: criteria[1]!, met: false, evidence_steps: [lastTool] }, { criterion: criteria[0]!, met: true }] }),
          opinion('ultra', 'regressed', 0.95, ['The new step 2 merges every non-item line into the item above it, which absorbs the VAT line.', 'Totals are understated by the VAT amount on every receipt with tax.'], { evidence: ['t001', lastTool, lastLlm], causal: 'Step 2 of the new parsing rule merges tax lines into item lines.' }),
        ]),
      )
      // The control (unchanged skill) kept the VAT line: the regression stands.
      const p = pairs.at(-1)!
      p.control_run_id = control(g, task, i).run_id
      p.control_outcome = 'agrees'
      p.usd += controlUsd
    } else if (task.noisy) {
      // Super judged it a regression; the control skipped the same receipt, so it counts as nondeterminism (pass).
      const p = makePair(g, r, 'pass', 'control', NONDETERMINISM_REASON, [
        opinion('nano', 'regressed', 0.71, ['The replay report lists one receipt; the original listed two.'], { evidence: [lastLlm] }),
        opinion('super', 'regressed', 0.66, ['The Uber receipt never reached sheets.append, so the total is short by €24.10 plus VAT.', 'Nothing in the diff explains skipping a receipt.'], { evidence: [lastTool, lastLlm], criteria: [{ criterion: criteria[0]!, met: false, evidence_steps: [lastTool] }] }),
      ])
      p.control_run_id = control(g, ran, i).run_id
      p.control_outcome = 'diverges'
      p.usd += controlUsd
      pairs.push(p)
    } else if (task.viaSuper) {
      pairs.push(
        makePair(g, r, 'pass', 'super', outcomeExplanation.pass(), [
          opinion('nano', 'uncertain', 0.64, ['Row formatting differs; cannot tell from summaries whether the tax line is intact.'], { evidence: [lastTool] }),
          opinion('super', 'equivalent', 0.88, ['Rows and totals match the original, including VAT; only whitespace differs.'], { evidence: [lastTool, lastLlm] }),
        ]),
      )
    } else {
      pairs.push(
        makePair(g, r, 'pass', 'nano', outcomeExplanation.pass(), [
          opinion('nano', 'equivalent', 0.9 + (i % 4) / 50, ['Same line items, VAT and totals as the original report.'], { evidence: [lastLlm] }),
        ]),
      )
    }
  })
  const n = pairs.filter((p) => p.outcome === 'regression').length
  const base = verdictBase(o, pairs, 'expense-report')
  const h = headline.blockCascade('expense-report', 'drops the VAT line', n, pairs.length)
  const verdict: Verdict = {
    ...base,
    decision: 'BLOCK',
    action: 'reverted',
    headline: h,
    narrative: `${h} The edit rewrote the parsing step to handle a new two-column receipt layout. Its new rule merges every line that is not an item into the item above it, and that swallows the VAT line. On three tasks, including "Build the October expense report", totals now come out without tax. Nemotron Nano flagged the difference, Super traced it to the sheets.append rows losing their vat field, and Ultra agreed with 95% confidence. A control replay of the unchanged skill kept the VAT line on all three, so the edit caused it. On "Lisbon client visit report" the unchanged skill also skipped a receipt, so that difference counts as nondeterminism, not a regression. The other six reports match the originals. Ratchet reverted the edit on candidate, so main still runs the previous expense-report. Nothing to do.`,
    reasons: ['cascade_regression'],
    action_commit: hex(`${o.editId}:revert`, 40),
    stages: stages(o.at, scale([['detected', 0.5], ['selecting', 1.2], ['replaying', 29], ['level0', 0.3], ['nano', 3.8], ['super', 6.4], ['ultra', 11.2], ['drift', 0], ['controls', 5.6], ['narrative', 3.4]], o.durationS)),
  }
  return {
    scenario: SCENARIOS[1]!,
    edit: editFor(o, 'expense-report', ['skills/expense-report/SKILL.md', 'skills/expense-report/layouts.md'], 'Rewrites the parsing step to handle the new two-column receipt layout.', '5d1e0a2c77b1e4f09a8d7c6b5a4f3e2d1c0b9a88'),
    diff: EXPENSE_DIFF,
    goldens,
    replays,
    verdict,
  }
}

// =============================================================== 3. fx-expense-converter
interface FxTask { input: string; label: string; eur: number; world: boolean; append?: boolean; gbp?: boolean }
const FX: FxTask[] = [
  { input: 'Convert the Berlin trip expenses from EUR to USD for the client invoice.', label: 'Berlin trip to USD', eur: 1284.5, world: true },
  { input: 'What is €2,450 in dollars at the current rate?', label: '€2,450 in USD', eur: 2450, world: true },
  { input: 'Convert the Amsterdam hotel bill to USD and add it to the sheet.', label: 'Amsterdam hotel to USD', eur: 612.8, world: true, append: true },
  { input: 'Convert the Q3 EUR subscription costs to USD.', label: 'Q3 subscriptions to USD', eur: 389.4, world: true },
  { input: 'Round the converted totals in the September report to two decimals.', label: 'Round September totals', eur: 0, world: false },
  { input: 'List which receipts in the October folder are in EUR.', label: 'Find EUR receipts', eur: 0, world: false },
  { input: 'Convert the £180 London dinner receipt to EUR.', label: 'London dinner to EUR', eur: 0, world: true, gbp: true },
]

const FX_OLD = 1.08
const FX_NEW = 1.163

const FX_DIFF = `diff --git a/skills/fx-expense-converter/SKILL.md b/skills/fx-expense-converter/SKILL.md
index a07d3e1..b2c94f0 100644
--- a/skills/fx-expense-converter/SKILL.md
+++ b/skills/fx-expense-converter/SKILL.md
@@ -18,7 +18,7 @@ description: Convert expenses between currencies at the current rate.
 ## Converting

 1. Get the rate with \`finance.rate\` for the pair you need.
 2. Multiply each amount by the rate.
-3. Round converted amounts to 2 decimals at the end, not per line.
+3. Round converted amounts to two decimals once, at the end (not per line).
 4. State the rate and its date next to the converted total.
`

const usdStr = (n: number) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const eurStr = (n: number) => `€${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function fxSteps(task: FxTask, f: StepFactory, rate: number, asOf: string) {
  const steps = []
  if (task.eur > 0) {
    steps.push(f.llm('super', { preview: 'Fetching the current EUR/USD rate.', tools: ['finance.rate'] }))
    steps.push(f.tool('finance.rate', { base: 'EUR', quote: 'USD' }, 'read', { result: JSON.stringify({ pair: 'EUR/USD', rate: rate.toFixed(4), as_of: asOf }) }))
    if (task.append) {
      steps.push(f.llm('super', { preview: 'Adding the converted amount to the sheet.', tools: ['sheets.append'] }))
      steps.push(f.tool('sheets.append', { sheet: 'Expenses Q4', rows: [{ item: 'Amsterdam hotel', eur: task.eur, usd: +(task.eur * rate).toFixed(2) }] }, 'write_reversible', { result: '{"ok": true, "appended": 1}' }))
    }
  } else if (task.gbp) {
    steps.push(f.llm('super', { preview: 'Fetching the current GBP/EUR rate.', tools: ['finance.rate'] }))
    steps.push(f.tool('finance.rate', { base: 'GBP', quote: 'EUR' }, 'read', { result: JSON.stringify({ pair: 'GBP/EUR', rate: '1.1720', as_of: asOf }) }))
  } else {
    steps.push(f.llm('super', { preview: 'Reading the report.', tools: ['fs.read'] }))
    steps.push(f.tool('fs.read', { path: '/reports/2026-09.md' }, 'read', { result: '# September\nTotal (USD): 4,182.507\n…' }))
  }
  steps.push(f.llm('super', { preview: fxOutput(task, rate, asOf), pt: 3900, ct: 260 }))
  return steps
}

function fxOutput(task: FxTask, rate: number, asOf: string): string {
  if (task.eur > 0) return `${task.label}: ${eurStr(task.eur)} = ${usdStr(task.eur * rate)} at EUR/USD ${rate.toFixed(4)} (rate as of ${asOf}).`
  if (task.gbp) return `London dinner: £180.00 = ${eurStr(180 * 1.172)} at GBP/EUR 1.1720 (rate as of ${asOf}).`
  if (task.label.startsWith('Round')) return 'Rounded 14 totals in the September report to two decimals. Grand total: $4,182.51.'
  return 'Five of the eleven October receipts are in EUR: hotel-adlon-1, taxi-berlin-2, cafe-einstein-4, db-bahn-5, staples-6.'
}

export function buildFx(o: BuildOpts): Built {
  const goldenAt = o.at - 14 * DAY
  const asOfOld = new Date(goldenAt).toISOString().slice(0, 10)
  const asOfNew = new Date(o.at).toISOString().slice(0, 10)
  const factDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(goldenAt))
  const goldens: Trace[] = []
  const replays: Trace[] = []
  const pairs: PairVerdict[] = []
  const fact: WorldFact = { fact_id: 'fact_eurusd', statement: `EUR/USD rate on ${factDate} was 1.0800`, source_step: 't001', volatile: true, query_hint: 'EUR to USD exchange rate today', value: '1.0800' }
  const check = (): DriftCheck => ({
    fact_id: fact.fact_id,
    query: 'EUR to USD exchange rate today',
    tavily_results_ref: `tavily/${o.verdictId}/fact_eurusd.json`,
    changed: true,
    old_value: '1.0800',
    new_value: '1.1630',
    confidence: 0.94,
    statement: fact.statement,
    source_url: 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html',
    source_title: 'ECB euro reference exchange rates: US dollar (USD)',
    status: 'checked',
    attributable: true,
    ruling: 'The new USD totals follow from the new rate; the conversion steps are unchanged.',
  })
  FX.forEach((task, i) => {
    const gid = `run_fx_${String(i + 1).padStart(2, '0')}`
    const rid = `rep_${o.verdictId}_${String(i + 1).padStart(2, '0')}`
    const gAt = goldenAt + i * 4_000_000
    const criteria = ['States the converted amount in the target currency.', 'Names the rate used and its date.', 'Rounds to two decimals once, at the end.']
    const g = makeTrace({
      runId: gid, kind: 'golden', taskInput: task.input, taskLabel: task.label,
      steps: fxSteps(task, new StepFactory(gid, gAt), FX_OLD, asOfOld), finalOutput: fxOutput(task, FX_OLD, asOfOld),
      skills: ['fx-expense-converter'], createdAt: new Date(gAt).toISOString(), criteria,
      blessedAt: new Date(gAt + 300_000).toISOString(), skillsCommit: 'a07d3e1f42', worldDependent: task.world,
      worldFacts: task.eur > 0 ? [fact] : task.gbp ? [{ fact_id: 'fact_gbpeur', statement: `GBP/EUR rate on ${factDate} was 1.1720`, source_step: 't001', volatile: true, query_hint: 'GBP to EUR exchange rate today', value: '1.1720' }] : [],
    })
    const r = makeTrace({
      runId: rid, kind: 'replay', taskInput: task.input, taskLabel: task.label,
      steps: fxSteps(task, new StepFactory(rid, o.at + 8_000), FX_NEW, asOfNew), finalOutput: fxOutput(task, FX_NEW, asOfNew),
      skills: ['fx-expense-converter'], createdAt: isoAt(o.at, 14 + i), goldenOf: gid,
      candidateCommit: hex(`${o.editId}:commit`, 40), skillsCommit: 'a07d3e1f42', worldDependent: task.world,
    })
    goldens.push(g)
    replays.push(r)
    const lastLlm = r.steps.filter((s) => s.kind === 'llm').at(-1)?.step_id ?? 'm002'
    if (task.eur > 0) {
      pairs.push(
        makePair(g, r, 'drift', 'drift', outcomeExplanation.drift('The EUR/USD rate'), [
          opinion('nano', 'regressed', 0.62, ['The USD total differs from the original.'], { evidence: [lastLlm], driftFact: 'EUR/USD rate' }),
          opinion('super', 'uncertain', 0.58, ['Totals differ, but finance.rate returned a different rate; the skill steps look unchanged.'], { evidence: ['t001', lastLlm], driftFact: 'EUR/USD rate' }),
          opinion('ultra', 'uncertain', 0.66, ['The difference is fully explained by the rate in t001 if that rate is real.'], { evidence: ['t001', lastLlm], driftFact: fact.statement, causal: 'finance.rate returned 1.1630 instead of 1.0800.' }),
        ], [check()]),
      )
    } else {
      pairs.push(
        makePair(g, r, 'pass', 'nano', outcomeExplanation.pass(), [
          opinion('nano', 'equivalent', 0.93 + (i % 3) / 100, ['Same result as the original run.'], { evidence: [lastLlm] }),
        ]),
      )
    }
  })
  const nDrift = pairs.filter((p) => p.outcome === 'drift').length
  const base = verdictBase(o, pairs, 'fx-expense-converter', { pt: 4800, ct: 340 })
  const h = headline.passDrift(pairs.length, nDrift)
  const verdict: Verdict = {
    ...base,
    decision: 'PASS',
    action: 'merged',
    headline: h,
    narrative: `${h} The edit to fx-expense-converter only rewords its rounding note; the conversion steps are unchanged. Four tasks, such as "Convert the Berlin trip expenses from EUR to USD", produced different USD totals. Tavily found that the EUR/USD rate moved from 1.0800 to 1.1630 since the original runs, and Nemotron Super ruled that the new totals follow from that rate. The other three tasks match their originals. Ratchet merged the edit to main. Re-bless the changed tasks.`,
    reasons: ['drift_only'],
    action_commit: hex(`${o.editId}:merge`, 40),
    stages: stages(o.at, scale([['detected', 0.5], ['selecting', 1.1], ['replaying', 22], ['level0', 0.3], ['nano', 3.1], ['super', 4.9], ['ultra', 8.4], ['drift', 4.6], ['controls', 0], ['narrative', 2.8]], o.durationS)),
  }
  return {
    scenario: SCENARIOS[2]!,
    edit: editFor(o, 'fx-expense-converter', ['skills/fx-expense-converter/SKILL.md'], 'Rewords the rounding note; no behavior change.', 'a07d3e1f42c9b8a7d6e5f4c3b2a1908f7e6d5c4b'),
    diff: FX_DIFF,
    goldens,
    replays,
    verdict,
  }
}

// =============================================================== history (not Simulate scenarios)
interface SimpleTask { input: string; label: string; out: string }

function simpleBuild(
  o: BuildOpts,
  skill: string,
  tasks: SimpleTask[],
  diff: string,
  summary: string,
  kind: 'pass' | 'warn',
): Built {
  const goldens: Trace[] = []
  const replays: Trace[] = []
  const pairs: PairVerdict[] = []
  tasks.forEach((t, i) => {
    const gid = `run_${skill.slice(0, 3)}_${String(i + 1).padStart(2, '0')}`
    const rid = `rep_${o.verdictId}_${String(i + 1).padStart(2, '0')}`
    const mk = (id: string, k: Trace['kind'], at: number, out: string) => {
      const f = new StepFactory(id, at)
      return makeTrace({
        runId: id, kind: k, taskInput: t.input, taskLabel: t.label,
        steps: [f.llm('super', { preview: 'Reading the merged pull requests.', tools: ['git.log'] }), f.tool('git.log', { range: 'v2.2..HEAD' }, 'read', { result: '14 commits' }), f.llm('super', { preview: out })],
        finalOutput: out, skills: [skill], createdAt: new Date(at).toISOString(), skillsCommit: 'e1c2d3a4b5',
        criteria: ['Mentions every merged pull request.', 'Groups changes by type.'],
        goldenOf: k === 'replay' ? gid : null, blessedAt: k === 'golden' ? new Date(at + 60_000).toISOString() : null,
      })
    }
    const g = mk(gid, 'golden', o.at - 12 * DAY + i * 3_600_000, t.out)
    const unsure = kind === 'warn' && i === 3
    const r = mk(rid, 'replay', o.at + 10_000, unsure ? t.out.replace('Fixes', 'Changes') : t.out)
    goldens.push(g)
    replays.push(r)
    if (unsure) {
      pairs.push(
        makePair(g, r, 'uncertain', 'ultra', outcomeExplanation.uncertain('Judges disagreed on whether "Changes" still separates fixes from features'), [
          opinion('nano', 'regressed', 0.74, ['The section heading changed from Fixes to Changes.'], { evidence: ['m002'] }),
          opinion('super', 'equivalent', 0.71, ['All pull requests are still listed; only the heading differs.'], { evidence: ['m002'] }),
          opinion('ultra', 'uncertain', 0.55, ['Grouping by type is weaker but every item is present; intent is unclear.'], { evidence: ['m002'] }),
        ]),
      )
    } else {
      pairs.push(makePair(g, r, 'pass', 'nano', outcomeExplanation.pass(), [opinion('nano', 'equivalent', 0.92 + (i % 5) / 100, ['Same pull requests, same grouping.'], { evidence: ['m002'] })]))
    }
  })
  const base = verdictBase(o, pairs, skill, { pt: 4100, ct: 300 })
  const isWarn = kind === 'warn'
  const h = isWarn ? headline.warn('judges disagreed', 1, pairs.length) : headline.pass(skill, pairs.length)
  const verdict: Verdict = {
    ...base,
    decision: isWarn ? 'WARN' : 'PASS',
    action: isWarn ? 'held' : 'merged',
    headline: h,
    narrative: isWarn
      ? `${h} The edit to ${skill} renames the Fixes section to Changes. On "${tasks[3]!.label}", Nemotron Nano read this as a lost grouping, Super saw every pull request still listed, and Ultra could not settle it. The other ${pairs.length - 1} tasks match. The edit is held on candidate. Approve if this is intended.`
      : `${h} The edit to ${skill} tightens the wording of its summary rule. All ${pairs.length} replays list the same pull requests in the same groups, which Nemotron Nano confirmed for each. Ratchet merged the edit to main. Nothing to do.`,
    reasons: isWarn ? ['judges_disagree'] : [],
    action_commit: isWarn ? null : hex(`${o.editId}:merge`, 40),
    stages: stages(o.at, scale([['detected', 0.5], ['selecting', 1], ['replaying', 24], ['level0', 0.3], ['nano', 3.4], ['super', isWarn ? 4 : 0], ['ultra', isWarn ? 7 : 0], ['drift', 0], ['controls', 0], ['narrative', 2.6]], o.durationS)),
  }
  return { scenario: null, edit: editFor(o, skill, [`skills/${skill}/SKILL.md`], summary, 'e1c2d3a4b5f60718293a4b5c6d7e8f9012345678'), diff, goldens, replays, verdict }
}

const RELEASE_TASKS: SimpleTask[] = [
  ['Write release notes for v2.3.0 of acme/widgets.', 'Release notes v2.3.0'],
  ['Draft notes for the acme/api 1.14 patch release.', 'acme/api 1.14 notes'],
  ['Summarize this sprint’s merged PRs for the changelog.', 'Sprint changelog'],
  ['Write the v2.2.4 hotfix notes.', 'v2.2.4 hotfix notes'],
  ['Prepare the acme/docs monthly release digest.', 'acme/docs digest'],
  ['Write release notes for the mobile 3.1 beta.', 'Mobile 3.1 beta notes'],
  ['Summarize breaking changes since v2.0.', 'Breaking changes since v2.0'],
  ['Draft notes for the CLI 0.9 release.', 'CLI 0.9 notes'],
  ['Write notes for the acme/api 1.13.2 patch.', 'acme/api 1.13.2 notes'],
  ['List new features in v2.3 for the newsletter.', 'v2.3 newsletter features'],
  ['Write the security advisory summary for 1.13.1.', 'Security summary 1.13.1'],
  ['Draft the acme/widgets v2.3.1 notes.', 'acme/widgets v2.3.1 notes'],
].map(([input, label]) => ({ input: input!, label: label!, out: `## ${label}\n\n### Features\n- Faster sync on large boards (#398)\n\n### Fixes\n- Crash on save with emoji filenames (#412)` }))

export function buildReleaseWarn(o: BuildOpts): Built {
  return simpleBuild(o, 'release-notes', RELEASE_TASKS, `diff --git a/skills/release-notes/SKILL.md b/skills/release-notes/SKILL.md
index 77ab012..1c9e3f4 100644
--- a/skills/release-notes/SKILL.md
+++ b/skills/release-notes/SKILL.md
@@ -6,7 +6,7 @@ description: Write release notes from merged pull requests.
 ## Sections

 - Features
-- Fixes
+- Changes
 - Breaking changes (only if any)
`, 'Renames the Fixes section to Changes.', 'warn')
}

export function buildChangelogPass(o: BuildOpts): Built {
  return simpleBuild(o, 'changelog-writer', RELEASE_TASKS.slice(0, 9), `diff --git a/skills/changelog-writer/SKILL.md b/skills/changelog-writer/SKILL.md
index 0b8d6e2..f2a7c91 100644
--- a/skills/changelog-writer/SKILL.md
+++ b/skills/changelog-writer/SKILL.md
@@ -3,6 +3,6 @@ description: Keep CHANGELOG.md current.
 ## Rules

-- Keep each entry short.
+- Keep each entry to one line.
 - Link the pull request number.
`, 'Tightens the entry length rule to one line.', 'pass')
}
