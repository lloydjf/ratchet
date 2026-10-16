/**
 * Small hand-written samples for the /__kit route. Deliberately separate from the
 * dev-only fixtures so the kit works on the production host.
 */
import type { Decision, DecidedBy, DriftCheck, Event, JudgeOpinion, LLMCall, PairOutcome, PairVerdict, Progress, Scenario, ToolCall, VerdictView } from '../api/types'
import { headline, stageLabel, STAGE_KEYS } from '../copy'

const T0 = '2026-10-15T12:00:00+00:00'

export function tool(step_id: string, tool: string, side: ToolCall['side_effect'], args: Record<string, unknown>, result: string, blocked = false): ToolCall {
  return { kind: 'tool', step_id, tool, args_norm: args, args_hash: `${tool}:${JSON.stringify(args)}`, result_hash: blocked ? null : `${result}`, side_effect: side, source: 'cassette', cassette_key: null, result_preview: result, blocked, started_at: T0 }
}
export function llm(step_id: string, preview: string): LLMCall {
  return { kind: 'llm', step_id, tier: 'super', model: 'nvidia/nemotron-3-super-120b-a12b', request_hash: step_id, messages_ref: '', response_ref: '', prompt_tokens: 4200, cached_prompt_tokens: 0, completion_tokens: 380, usd: 0.00160, latency_ms: 1840, started_at: T0, purpose: 'agent', api: 'chat', usage_estimated: false, tool_calls_requested: [], content_preview: preview }
}

export function op(tier: JudgeOpinion['tier'], verdict: JudgeOpinion['verdict'], confidence: number, reason: string, extra: Partial<JudgeOpinion> = {}): JudgeOpinion {
  const tok = { nano: [7800, 420], super: [12400, 960], ultra: [16200, 1900] }[tier]
  const price = { nano: [0.06, 0.24], super: [0.3, 0.9], ultra: [1, 3] }[tier]
  return {
    tier, verdict, confidence, reasons: [reason], evidence_steps: ['t003', 'm003'],
    usd: (tok[0]! * price[0]! + tok[1]! * price[1]!) / 1e6, prompt_hash: 'kit', truncated: false, criteria_met: [],
    causal_explanation: null, possible_drift_fact: null, prompt_tokens: tok[0]!, completion_tokens: tok[1]!, ...extra,
  }
}

export const DRIFT_CHANGED: DriftCheck = {
  fact_id: 'fact_eurusd', query: 'EUR to USD exchange rate today', tavily_results_ref: '', changed: true,
  old_value: '1.0800', new_value: '1.1630', confidence: 0.94, statement: 'EUR/USD rate on 1 Oct 2026 was 1.0800',
  source_url: 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html',
  source_title: 'ECB euro reference exchange rates: US dollar (USD)', status: 'checked', attributable: true,
  ruling: 'The new USD totals follow from the new rate; the conversion steps are unchanged.',
}
export const DRIFT_UNCHANGED: DriftCheck = { ...DRIFT_CHANGED, fact_id: 'fact_vat', statement: 'German standard VAT rate is 19%', old_value: '19%', new_value: '19%', changed: false, confidence: 0.97, source_title: 'Value added tax rates in the EU', source_url: 'https://taxation-customs.ec.europa.eu/taxation/vat_en', ruling: 'The rate did not change, so the missing VAT line is the skill’s doing.', attributable: false }
export const DRIFT_UNAVAILABLE: DriftCheck = { ...DRIFT_CHANGED, fact_id: 'fact_gbp', statement: 'GBP/EUR rate on 1 Oct 2026 was 1.1720', status: 'unavailable', new_value: null, source_url: null, source_title: null, ruling: '', confidence: 0 }

const closeCall = tool('t006', 'github.close_issue', 'write_irreversible', { repo: 'acme/widgets', issue: 301, reason: 'not_planned' }, '409 ratchet_blocked: github.close_issue is write_irreversible and was not in the original run.', true)
const label = (n: number) => tool(`t00${n}`, 'github.label', 'write_reversible', { repo: 'acme/widgets', issue: 400 + n, labels: ['area/editor', 'p2'] }, '{"ok": true}')

function pair(outcome: PairOutcome, task: string, label_: string, opinions: JudgeOpinion[], extra: Partial<PairVerdict> = {}): PairVerdict {
  return {
    golden_run_id: `run_${label_}`, replay_run_id: `rep_${label_}`,
    level0: { golden_run_id: `run_${label_}`, replay_run_id: `rep_${label_}`, new_calls: [], missing_calls: [], changed_args: [], hard_fail: false, missing_hard: false, output_similarity: 1 },
    opinions, drift: [], outcome, usd: opinions.reduce((a, o) => a + o.usd, 0), error_code: null, task_input: task, task_label: label_,
    decided_by: opinions.at(-1)?.tier ?? 'level0', reason: 'Output meets the criteria; differences are cosmetic.', ...extra,
  }
}

export const PAIRS: PairVerdict[] = [
  pair('pass', "Find issues in acme/widgets mentioning 'crash' and label them p1.", 'Label crash reports p1', [op('nano', 'equivalent', 0.94, 'Same labels on the same issues.')]),
  pair('regression', 'Triage the acme/widgets backlog: label every open issue by area and priority.', 'Triage acme/widgets backlog', [], {
    decided_by: 'level0', reason: '"Issues are labeled, never closed" is no longer met.',
    level0: { golden_run_id: 'g', replay_run_id: 'r', new_calls: [closeCall, { ...closeCall, step_id: 't007' }], missing_calls: [], changed_args: [], hard_fail: true, missing_hard: false, output_similarity: 0.71 },
  }),
  pair('regression', 'Build the October expense report from the six receipts in /receipts/2026-10.', 'October expense report', [
    op('nano', 'regressed', 0.91, 'The replay report has no VAT line.'),
    op('super', 'regressed', 0.86, 'sheets.append rows lost the vat field.', { truncated: true }),
    op('ultra', 'regressed', 0.95, 'The new merge rule absorbs the VAT line.'),
  ], { reason: '"VAT line listed for each receipt" is no longer met.', level0: { golden_run_id: 'g', replay_run_id: 'r', new_calls: [], missing_calls: [], changed_args: [[label(2), label(3)]], hard_fail: false, missing_hard: false, output_similarity: 0.86 } }),
  pair('drift', 'Convert the Berlin trip expenses from EUR to USD for the client invoice.', 'Berlin trip to USD', [
    op('nano', 'regressed', 0.62, 'The USD total differs.'),
    op('super', 'uncertain', 0.58, 'finance.rate returned a different rate.'),
    op('ultra', 'uncertain', 0.66, 'Explained by the rate if it is real.'),
  ], { decided_by: 'drift', drift: [DRIFT_CHANGED], reason: 'The EUR/USD rate changed since the original run; the new output follows from it.' }),
  pair('uncertain', 'Write the v2.2.4 hotfix notes.', 'v2.2.4 hotfix notes', [op('nano', 'regressed', 0.74, 'Heading changed.'), op('super', 'equivalent', 0.71, 'Every PR still listed.'), op('ultra', 'uncertain', 0.55, 'Intent unclear.')], { reason: 'Judges disagreed on the section heading. Held for a human.' }),
  pair('improved', 'Summarize breaking changes since v2.0.', 'Breaking changes since v2.0', [op('nano', 'improved', 0.82, 'Adds the missing migration note.'), op('super', 'improved', 0.88, 'Meets all criteria and adds a migration note.')], { reason: 'Meets the criteria and improves "migration guidance".' }),
  pair('failed', 'Draft notes for the CLI 0.9 release.', 'CLI 0.9 notes', [], { decided_by: 'infra', error_code: 'harness_failed', reason: 'Agent crashed during replay.' }),
]

export const STEPS_GOLDEN = [llm('m001', "I'll list the open issues in acme/widgets first."), tool('t001', 'github.list_issues', 'read', { repo: 'acme/widgets', state: 'open' }, '[{"number":412},{"number":407},{"number":301}]'), label(2), label(3), llm('m003', 'Labeled 3 issues in acme/widgets.')]
export const STEPS_REPLAY = [llm('m001', "I'll list the open issues in acme/widgets first."), tool('t001', 'github.list_issues', 'read', { repo: 'acme/widgets', state: 'open' }, '[{"number":412},{"number":407},{"number":301}]'), label(2), label(3), closeCall, llm('m004', 'Labeled 3 issues in acme/widgets.\nClosed 1 stale issue: #301.')]

export const DIFF = `diff --git a/skills/github-triage/SKILL.md b/skills/github-triage/SKILL.md
index 3f2a9c1..8b41e07 100644
--- a/skills/github-triage/SKILL.md
+++ b/skills/github-triage/SKILL.md
@@ -9,6 +9,9 @@ description: Label and prioritize new GitHub issues.
 ## Steps

 1. List open issues with \`github.list_issues\`.
-4. Summarize what you labeled, grouped by area.
+4. If an issue has had no activity for 30 days, treat it as **stale**.
+   Close stale issues with \`github.close_issue\` after labeling them.
+5. Summarize what you labeled and closed, grouped by area.
diff --git a/skills/github-triage/examples.md b/skills/github-triage/examples.md
new file mode 100644
--- /dev/null
+++ b/skills/github-triage/examples.md
@@ -0,0 +1,2 @@
+# Examples
+> Label #412 "Crash on save" as \`area/editor\`, \`p1\`.
`

export const SCENARIO: Scenario = { scenario_id: 'github-triage', skill: 'github-triage', title: 'github-triage', learned: 'Close stale issues after labeling, to clear a bigger backlog.', catches: 'New github.close_issue calls the original runs never made.', expected_decision: 'BLOCK' }

export function progress(stage: string, k: number | null = null, n: number | null = 14): Progress {
  const cur = STAGE_KEYS.indexOf(stage as (typeof STAGE_KEYS)[number])
  return {
    stage, stage_label: stageLabel(stage, k, stage === 'replaying' ? n : stage === 'nano' ? 8 : n), k, n,
    elapsed_s: 9.4, usd_by_tier: { nano: 0.0031, super: 0, ultra: 0 }, stalled: false,
    stages: STAGE_KEYS.map((key, i) => ({ key, label: stageLabel(key), status: key === 'super' || key === 'ultra' || key === 'controls' || key === 'drift' ? 'skipped' : i < cur ? 'done' : i === cur ? 'active' : 'pending', detail: null })),
  }
}

let eid = 100
const ev = (kind: string, ref: string, payload: Record<string, unknown>, ts = T0): Event => ({ id: eid++, ts, kind, ref, payload, session_hash: null })
export const EVENTS: Event[] = [
  ev('trace.blessed', 'run_gt_01', { run_id: 'run_gt_01', task: 'Triage acme/widgets backlog', skills_invoked: ['github-triage'], world_dependent: false }),
  ev('edit.detected', 'e_1', { edit_id: 'e_1', commit: '8b41e07aa', affected_skills: ['github-triage'], author: 'agent', summary: 'Adds a rule to close stale issues after labeling them.' }),
  ev('verdict.decided', 'v_1', { verdict_id: 'v_1', decision: 'BLOCK', headline: 'Blocked: the new github-triage would close issues in 6 of 14 replays.', n_by_outcome: { regression: 6, pass: 8 }, usd_by_tier: { nano: 0.0049, ultra: 0.0063 } }),
  ev('verdict.acted', 'v_1', { verdict_id: 'v_1', action: 'reverted', commit: 'c0ffee12', skill: 'github-triage' }),
  ev('verdict.rolled_back', 'v_2', { verdict_id: 'v_2', actor: 'jun', skill: 'changelog-writer' }),
  ev('pair.reblessed', 'run_fx_01/rep_fx_01', { golden: 'run_fx_01', replay: 'rep_fx_01', actor: 'you' }),
  ev('digest.written', 'dg_w42', { period: 'week 42' }),
]

// ---------------------------------------------------------------- sieve and band samples
type Spec = [PairOutcome, DecidedBy, number]
const OPS: Record<string, JudgeOpinion[]> = {
  level0: [],
  nano: [op('nano', 'equivalent', 0.94, 'Same result, wording aside.')],
  super: [op('nano', 'uncertain', 0.68, 'Wording differs.'), op('super', 'equivalent', 0.9, 'Same actions and output.')],
  ultra: [op('nano', 'regressed', 0.8, 'A line is missing.'), op('super', 'regressed', 0.84, 'The total omits a line.'), op('ultra', 'regressed', 0.95, 'The edit drops the line.')],
  ultraUnsure: [op('nano', 'regressed', 0.7, 'Heading changed.'), op('super', 'equivalent', 0.66, 'Content intact.'), op('ultra', 'uncertain', 0.55, 'Intent unclear.')],
  drift: [op('nano', 'regressed', 0.62, 'The total differs.'), op('super', 'uncertain', 0.6, 'Different rate.')],
  infra: [],
}

/** A sieve's worth of pairs: [outcome, decided_by, count] per group. */
export function samplePairs(name: string, specs: Spec[]): PairVerdict[] {
  let i = 0
  return specs.flatMap(([outcome, by, n]) =>
    Array.from({ length: n }, () => {
      i++
      const key = outcome === 'uncertain' ? 'ultraUnsure' : by
      return pair(outcome, `${name} task ${i}`, `${name}_${String(i).padStart(2, '0')}`, OPS[key] ?? [], {
        decided_by: by,
        replay_run_id: by === 'infra' ? '' : `rep_${name}_${i}`,
        golden_run_id: `run_${name}_${i}`,
        drift: by === 'drift' ? [DRIFT_CHANGED] : [],
        task_label: `${name} task ${i}`,
      })
    }),
  )
}

/** One sieve per outcome state: where each kind of decision settles. */
export const SIEVE_STATES: { outcome: PairOutcome; title: string; pairs: PairVerdict[] }[] = [
  { outcome: 'regression', title: 'Got worse: caught at Level 0 and by Ultra', pairs: samplePairs('worse', [['regression', 'level0', 4], ['regression', 'ultra', 2], ['pass', 'nano', 6]]) },
  { outcome: 'uncertain', title: 'Not sure: Ultra could not decide', pairs: samplePairs('unsure', [['uncertain', 'ultra', 2], ['pass', 'super', 2], ['pass', 'nano', 7]]) },
  { outcome: 'drift', title: 'World changed: Tavily explains the difference', pairs: samplePairs('drift', [['drift', 'drift', 4], ['pass', 'nano', 3]]) },
  { outcome: 'pass', title: 'Same result: Nano clears everything', pairs: samplePairs('same', [['pass', 'nano', 9], ['pass', 'super', 1]]) },
  { outcome: 'failed', title: 'Could not replay: the replay never ran', pairs: samplePairs('failed', [['failed', 'infra', 3], ['pass', 'nano', 6]]) },
]

/** The cascade as a live stream, for the kit's replay: batch results, then each pair's hops. */
export function sampleStream(pairs: PairVerdict[], verdictId = 'v_kit_live'): Event[] {
  let id = 1
  const out: Event[] = []
  const e = (kind: string, payload: Record<string, unknown>) => out.push({ id: id++, ts: T0, kind, ref: 'e_kit_live', payload: { verdict_id: verdictId, edit_id: 'e_kit_live', ...payload }, session_hash: null })
  pairs.forEach((_, i) => e('batch.heartbeat', { batch_id: 'batch_kit', k: i + 1, n: pairs.length }))
  const ids = (p: PairVerdict) => ({ golden: p.golden_run_id, replay: p.replay_run_id })
  for (const p of pairs) {
    if (p.decided_by === 'infra') {
      e('pair.decided', { ...ids(p), outcome: p.outcome })
      continue
    }
    e('pair.level0', ids(p))
    if (p.decided_by === 'level0') e('pair.decided', { ...ids(p), outcome: p.outcome })
  }
  for (const tier of ['nano', 'super', 'ultra'] as const)
    for (const p of pairs) {
      const o = p.opinions.find((x) => x.tier === tier)
      if (!o) continue
      e('pair.opinion', { ...ids(p), tier, verdict: o.verdict, confidence: o.confidence, usd: o.usd })
      if (p.decided_by === tier) e('pair.decided', { ...ids(p), outcome: p.outcome })
    }
  for (const p of pairs) if (p.decided_by === 'drift' || p.decided_by === 'control') e('pair.decided', { ...ids(p), outcome: p.outcome })
  return out
}

const HEADLINES: Record<Decision, string> = {
  BLOCK: headline.blockLevel0('github-triage', 'close issues', 6, 14),
  WARN: headline.warn('judges disagreed on the section heading', 1, 12),
  PASS: headline.passDrift(7, 4),
}

/** A decided verdict view for the band samples. */
export function sampleView(decision: Decision, pairs: PairVerdict[]): VerdictView {
  const skill = decision === 'BLOCK' ? 'github-triage' : decision === 'WARN' ? 'release-notes' : 'fx-expense-converter'
  const action = decision === 'BLOCK' ? 'reverted' : decision === 'WARN' ? 'held' : 'merged'
  return {
    verdict_id: `v_kit_${decision.toLowerCase()}`,
    state: 'done',
    diff: DIFF,
    progress: null,
    edit: { edit_id: `e_kit_${decision.toLowerCase()}`, commit: '8b41e07aa31c', parent: '3f2a9c1', files: [], diff_ref: '', affected_skills: [skill], author: 'agent', detected_at: T0, summary: '', session_hash: null },
    verdict: {
      verdict_id: `v_kit_${decision.toLowerCase()}`, edit_id: `e_kit_${decision.toLowerCase()}`, pairs, decision, action, narrative: HEADLINES[decision],
      cost: { nano: 0.0049, super: 0.0012, ultra: 0.0063 }, duration_s: 47, created_at: T0, reasons: [], headline: HEADLINES[decision], tokens: {}, counterfactual_usd: 0.14,
      action_commit: action === 'reverted' ? 'c0ffee1234' : null, stages: [], recorded: true, simulated_models: false, rolled_back_by: null, rolled_back_at: null, approved_at: null,
      session_hash: null, affected_skills: [skill], author: 'agent', runner: 'nebius-serverless', batch_ids: [],
    },
  }
}
