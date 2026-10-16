import { describe, expect, it } from 'vitest'
import {
  apiErrorCopy,
  cacheShareLine,
  LEVEL0,
  outputSimilarityCopy,
  CONTROL,
  NONDETERMINISM_REASON,
  STAGE_KEYS,
  stageLabelShort,
  BLESS_TOAST,
  confirmBody,
  confirmTitle,
  errorCopy,
  firstSentence,
  headline,
  headlineMatchesTemplate,
  LANDING_COPY,
  level0Marker,
  NOT_READY_HOST,
  outcomeExplanation,
  overVerdicts,
  stageLabel,
  stalledCopy,
  successToast,
  tierTooltip,
  undoHint,
} from './copy'

describe('copy templates', () => {
  it('builds the decision headlines exactly', () => {
    expect(headline.blockLevel0('github-triage', 'close issues', 6, 14)).toBe('Blocked: the new github-triage would close issues in 6 of 14 replays.')
    expect(headline.blockCascade('expense-report', 'drops the VAT line', 3, 10)).toBe('Blocked: expense-report now drops the VAT line on 3 of 10 past tasks.')
    expect(headline.warn('judges disagreed', 1, 12)).toBe('Held: judges disagreed on 1 of 12 replays. Review before merging.')
    expect(headline.pass('changelog-writer', 9)).toBe('Passed: changelog-writer still handles all 9 past tasks.')
    expect(headline.passDrift(10, 4)).toBe('Passed: 10 tasks checked; 4 changed because the world did, not the skill.')
    expect(headline.noTraces('pr-review')).toBe('Passed: no blessed tasks use pr-review yet. Bless one to protect it.')
  })

  it('validates headlines against the decision word and skill', () => {
    expect(headlineMatchesTemplate('Blocked: the new github-triage would close issues in 6 of 14 replays.', 'BLOCK', ['github-triage'])).toBe(true)
    expect(headlineMatchesTemplate('Passed: 7 tasks checked; 4 changed because the world did, not the skill.', 'PASS', ['fx-expense-converter'])).toBe(true)
    expect(headlineMatchesTemplate('Something changed.', 'BLOCK', ['x'])).toBe(false)
  })

  it('has the tier, Level 0 and stall copy', () => {
    expect(tierTooltip('nano', 'equivalent', 0.934, 'Same labels')).toBe('Nemotron Nano judged equivalent with 93% confidence. Same labels.')
    expect(level0Marker(1, 'write_irreversible')).toBe('Checked without a model: 1 new write_irreversible call.')
    expect(level0Marker(3, 'external_send')).toBe('Checked without a model: 3 new external_send calls.')
    expect(stalledCopy('Nano screening (14)')).toBe('Still working — Nano screening (14) is taking longer than usual.')
  })

  it('has the fixed progress stage labels', () => {
    // 11 keys: controls (ADR 0023) comes after drift (judging → drifting → controlling → deciding).
    expect(STAGE_KEYS).toEqual(['detected', 'selecting', 'replaying', 'level0', 'nano', 'super', 'ultra', 'drift', 'controls', 'summary', 'done'])
    expect(STAGE_KEYS.map((k, i) => stageLabel(k, k === 'replaying' ? 9 : k === 'controls' ? 1 : null, [null, 12, 14, null, 14, 4, 1, 2, 2, null, null][i], 'nebius'))).toEqual([
      'Detected edit',
      'Choosing tasks (12)',
      'Replaying on Nebius Jobs (9/14)',
      'Checking actions',
      'Nano screening (14)',
      'Super checking (4)',
      'Ultra deciding (1)',
      'Asking Tavily (2 facts)',
      'Replaying controls (1/2)',
      'Writing the summary',
      'Done',
    ])
  })

  it('labels the controls stage (ADR 0023) in both variants', () => {
    expect(stageLabel('controls')).toBe('Replaying controls')
    expect(stageLabel('controls', null, 3)).toBe('Replaying controls')
    expect(stageLabel('controls', 2, 3)).toBe('Replaying controls (2/3)')
    expect(stageLabel('controlling', 2, 3)).toBe('Replaying controls (2/3)')
    expect(stageLabelShort('controls')).toBe('Controls')
    expect(stageLabelShort('controls', 2, 3)).toBe('Controls 2/3')
    expect(NONDETERMINISM_REASON).toBe('The unchanged skill also produced a different result on this task, so the difference is nondeterminism, not the edit.')
    expect(CONTROL.evidenceValue(4, 1)).toBe('4 (1 diverged)')
    expect(CONTROL.timeline('agrees', 'October expense report')).toBe('Control replay agreed for October expense report')
    expect(CONTROL.timeline('diverges', 'Lisbon client visit report')).toBe('Control replay diverged for Lisbon client visit report — counted as nondeterminism')
  })

  it('names the runner honestly: Nebius Jobs only when replays run there', () => {
    expect(stageLabel('replaying', 3, 14, 'nebius')).toBe('Replaying on Nebius Jobs (3/14)')
    expect(stageLabel('replaying', 3, 14, 'local-process')).toBe('Replaying on the local runner (3/14)')
    expect(stageLabel('replaying', null, null, null)).toBe('Replaying on the replay workers')
    expect(stageLabel('narrative')).toBe('Writing the summary')
  })

  it('has confirmations, toasts and error copy by code', () => {
    expect(confirmBody.rollback('abc1234')).toBe('This reverts commit abc1234 on candidate and leaves main unchanged. You can re-apply it later from the edit page.')
    expect(successToast.rollback()).toBe('Rolled back. main is unchanged.')
    expect(successToast.approve('expense-report')).toBe('Merged. expense-report is live.')
    expect(errorCopy('budget_exceeded')).toBe('Spending cap reached for today. The edit is held; raise RATCHET_DAILY_USD_CAP in .env or wait until tomorrow.')
    expect(errorCopy('not_public')).toBe('Blessing and recording are off on the demo. Run Ratchet locally to protect your own agent.')
    expect(outcomeExplanation.drift('The EUR/USD rate')).toBe('The EUR/USD rate changed since the original run; the new output follows from it.')
  })

  it('has Approve anyway copy for a reverted (BLOCK) verdict', () => {
    expect(confirmBody.approveAnyway('github-triage')).toBe(
      'This re-applies the reverted edit to github-triage and merges it into main, overriding the block. Your agent will use it on its next task. Rollback stays available.',
    )
    expect(successToast.approveAnyway('github-triage')).toBe('Approved anyway. github-triage is re-applied and live on main.')
    expect(successToast.rollbackMerged()).toBe('Rolled back. main no longer has this edit.')
  })

  it('shows the server message for 429s and maps every other code', () => {
    const busy = 'You already have a scenario running. Wait for it to finish, then try another.'
    expect(apiErrorCopy({ code: 'simulate_busy', status: 429, message: busy })).toBe(busy)
    expect(apiErrorCopy({ code: 'simulate_busy', status: 429, message: 'simulate_busy' })).toBe(errorCopy('simulate_busy'))
    expect(apiErrorCopy({ code: 'rate_limited', status: 429, message: '' })).toBe(errorCopy('rate_limited'))
    expect(apiErrorCopy({ code: 'not_ready', status: 409, message: "this edit's revert has not run yet" })).toBe(errorCopy('not_ready'))
    expect(apiErrorCopy({ code: 'payload_too_large', status: 413, message: 'x' })).toBe('That request is too large. Ratchet only accepts small JSON bodies.')
    expect(apiErrorCopy(null)).toBe(errorCopy('network'))
  })

  it('503 not_ready says the host is starting (server message first); the 409 text stays for actions', () => {
    const host = 'This host is not fully started yet; try again in a moment.'
    expect(NOT_READY_HOST).toBe(host)
    expect(apiErrorCopy({ code: 'not_ready', status: 503, message: 'The store is still opening. Try again shortly.' })).toBe('The store is still opening. Try again shortly.')
    expect(apiErrorCopy({ code: 'not_ready', status: 503, message: 'not_ready' })).toBe(host)
    expect(apiErrorCopy({ code: 'not_ready', status: 503 })).toBe(host)
    expect(apiErrorCopy({ code: 'not_ready', status: 409, message: 'Ratchet is busy.' })).toBe(errorCopy('not_ready'))
  })

  it('bless has the standard confirm pattern and toast', () => {
    expect(successToast.bless()).toBe('Blessed. Future edits to its skills are now checked against this run.')
    expect(BLESS_TOAST).toBe(successToast.bless())
    expect(confirmTitle.bless).toBe('Bless this run?')
    expect(confirmBody.bless('Triage acme/widgets')).toBe('This makes the run the reference for "Triage acme/widgets". Ratchet replays it whenever a skill it uses changes.')
    expect(undoHint.bless).toBe('Undo: unbless it from the trace page.')
    expect(overVerdicts(1)).toBe('over 1 verdict in this window')
    expect(overVerdicts(12)).toBe('over 12 verdicts in this window')
  })

  it('has short, actionable copy for every action and Simulate error code (product voice)', () => {
    const codes = ['rate_limited', 'simulate_busy', 'action_token_invalid', 'already_reverted', 'not_ready', 'stacked_on_held', 'worker_lost', 'simulate_disabled', 'payload_too_large']
    const fallback = errorCopy('definitely_not_a_code')
    for (const code of codes) {
      const text = errorCopy(code)
      expect(text, code).not.toBe(fallback)
      expect(text, code).not.toMatch(/!/)
      for (const sentence of text.split(/(?<=\.)\s+/)) expect(sentence.split(/\s+/).length, `${code}: ${sentence}`).toBeLessThan(20)
    }
    expect(errorCopy('simulate_busy')).toBe('Many judges are running scenarios right now. Try again in a minute, or view a recorded run.')
    expect(errorCopy('rate_limited')).toBe('Too many actions in a minute. Wait a moment and try again.')
    expect(errorCopy('already_reverted')).toBe('This edit is already reverted; main is unchanged.')
    expect(errorCopy('stacked_on_held')).toBe('An earlier edit on candidate is still held. Approve or roll it back first; merging this one would ship it too.')
    expect(errorCopy('ratchet_blocked')).toBe('blocked action')
    expect(errorCopy('not_ready')).toBe('Ratchet is still acting on this verdict. Try again in a few seconds.')
    expect(errorCopy('action_token_invalid')).toBe('This page is out of date. Reload and try again.')
  })

  it('keeps the landing copy verbatim and within 90 words', () => {
    expect(LANDING_COPY.split(/\s+/).length).toBeLessThanOrEqual(90)
    expect(LANDING_COPY.startsWith('Ratchet is regression CI for self-improving agents.')).toBe(true)
  })

  it('splits the first sentence without breaking on decimals', () => {
    expect(firstSentence('Passed: rate moved 1.0800 to 1.1630. Next.')).toBe('Passed: rate moved 1.0800 to 1.1630.')
  })
})

describe('Level 0 output similarity (ADR 0024)', () => {
  it('reads identical at 0.999 and above, a whole percentage below', () => {
    expect(outputSimilarityCopy(1)).toBe('Final output: identical')
    expect(outputSimilarityCopy(0.9993)).toBe('Final output: identical')
    expect(outputSimilarityCopy(0.9989)).toBe('Final output: 99 % similar')
    expect(outputSimilarityCopy(0.87)).toBe('Final output: 87 % similar')
    expect(outputSimilarityCopy(0)).toBe('Final output: 0 % similar')
  })

  it('rounds down, so a pair under the 0.90 floor never reads as 90 %', () => {
    expect(outputSimilarityCopy(0.8996)).toBe('Final output: 89 % similar')
  })

  it('treats a missing value as the backend default of 1.0', () => {
    expect(outputSimilarityCopy(undefined)).toBe('Final output: identical')
    expect(outputSimilarityCopy(null)).toBe('Final output: identical')
  })

  it('names Level 0 as the reason Nano could not clear the pair alone', () => {
    expect(LEVEL0.nanoGated).toBe('Nano could not clear this pair alone (Level 0 found differences).')
  })
})

describe('Costs page prompt-cache share', () => {
  it('gives the overall share and one share per tier, like `ratchet status`', () => {
    expect(
      cacheShareLine({
        cache_share: 0.41,
        prompt_tokens_by_tier: { nano: 10_000, super: 5_000, ultra: 2_500 },
        cached_prompt_tokens_by_tier: { nano: 5_200, super: 1_900, ultra: 300 },
      }),
    ).toBe('Prompt-cache share: 0.41 of prompt tokens (nano 0.52 · super 0.38 · ultra 0.12)')
  })

  it('says n/a for a tier that sent no prompt tokens', () => {
    expect(cacheShareLine({ cache_share: 0, prompt_tokens_by_tier: { nano: 800 }, cached_prompt_tokens_by_tier: {} })).toBe(
      'Prompt-cache share: 0.00 of prompt tokens (nano 0.00 · super n/a · ultra n/a)',
    )
  })

  it('says not recorded when the share is null or the backend predates it', () => {
    expect(cacheShareLine({ cache_share: null, prompt_tokens_by_tier: {}, cached_prompt_tokens_by_tier: {} })).toBe('Prompt-cache share: not recorded')
    expect(cacheShareLine({})).toBe('Prompt-cache share: not recorded')
  })
})
