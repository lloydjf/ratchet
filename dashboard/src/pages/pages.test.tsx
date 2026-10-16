import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type RatchetApi } from '../api/client'
import { createFixtureApi } from '../api/fixtures/index'
import { setApi } from '../api/select'
import { renderUI } from '../test/utils'
import { KitRoute } from '../App'
import { describeEvent } from '../components/TimelineItem'
import { LANDING_COPY } from '../copy'
import CostsPage from './CostsPage'
import LandingPage from './LandingPage'
import VerdictPage from './VerdictPage'
import DigestPage from './DigestPage'
import PairPage from './PairPage'
import TracePage from './TracePage'
import TracesPage from './TracesPage'

let fixture: RatchetApi

beforeEach(() => {
  sessionStorage.setItem('ratchet.fixture.operator', '1')
  fixture = createFixtureApi()
  setApi(fixture)
})

function Where() {
  const l = useLocation()
  return <output data-testid="where">{l.pathname + l.search}</output>
}

function at(route: string, path: string, element: React.ReactElement) {
  renderUI(
    <>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
      <Where />
    </>,
    { route },
  )
}

describe('Trace page: Unbless is a confirmed, reversible action', () => {
  it('confirms, unblesses, shows the undo path, and offers Bless again', async () => {
    const user = userEvent.setup()
    at('/traces/run_gt_01', '/traces/:runId', <TracePage />)
    const button = await screen.findByRole('button', { name: 'Unbless' })
    expect(screen.getByText('Undo: bless it again from the trace page.')).toBeInTheDocument()
    await user.click(button)
    const dialog = screen.getByRole('group', { name: 'Unbless this task?' })
    expect(dialog).toHaveTextContent(/^Unbless this task\?Ratchet stops replaying ".+" when its skills change\. The run and its steps are kept\./)
    await user.click(within(dialog).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Unblessed. Ratchet no longer replays this task.')
    const bless = await screen.findByRole('button', { name: 'Bless' })
    expect(screen.getByText('Live run')).toBeInTheDocument()
    await user.click(bless)
    const confirm = screen.getByRole('group', { name: 'Bless this run?' })
    expect(confirm).toHaveTextContent(/This makes the run the reference for ".+"\. Ratchet replays it whenever a skill it uses changes\./)
    await user.click(within(confirm).getByTestId('confirm-action'))
    expect(await screen.findByText('Blessed. Future edits to its skills are now checked against this run.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Unbless' })).toBeInTheDocument()
  })

  it('reports a failed Unbless as a toast with the code-mapped copy', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, unbless: () => Promise.reject(new ApiError('rate_limited', 'slow down', 429)) })
    at('/traces/run_gt_01', '/traces/:runId', <TracePage />)
    await user.click(await screen.findByRole('button', { name: 'Unbless' }))
    await user.click(within(screen.getByTestId('inline-confirm')).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Too many actions in a minute. Wait a moment and try again.')
    expect(screen.queryByTestId('inline-confirm')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unbless' })).toBeInTheDocument()
  })
})

describe('empty states have one sentence and one action', () => {
  it('golden library → Show recent runs', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, listTraces: async () => ({ items: [], next_cursor: null }) })
    at('/traces', '/traces', <TracesPage />)
    const empty = await screen.findByTestId('empty-state')
    expect(empty).toHaveTextContent('No blessed tasks. Run a task, then press Bless on it.')
    await user.click(within(empty).getByRole('button', { name: 'Show recent runs' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/traces?kind=live')
    expect(await screen.findByRole('heading', { level: 1, name: 'Recent runs' })).toBeInTheDocument()
  })

  it('digest → Open the timeline', async () => {
    setApi({ ...fixture, getDigest: () => Promise.reject(new ApiError('not_found', 'none', 404)) })
    at('/digest', '/digest', <DigestPage />)
    const empty = await screen.findByTestId('empty-state')
    expect(empty).toHaveTextContent('No digest yet. The first one is written at the end of the week.')
    expect(within(empty).getByRole('link', { name: 'Open the timeline' })).toHaveAttribute('href', '/timeline')
  })
})

describe('pair page', () => {
  const route = '/verdicts/v_rec_gt/pairs/run_gt_01/rep_v_rec_gt_01'
  const path = '/verdicts/:id/pairs/:golden/:replay'

  it('shows the Recorded run badge when the pair comes from a recorded run', async () => {
    at(route, path, <PairPage />)
    const header = await screen.findByTestId('pair-header')
    expect(within(header).getByText('Recorded run')).toBeInTheDocument()
  })

  it('has no badge for a live pair', async () => {
    setApi({ ...fixture, getPair: async (...a) => ({ ...(await fixture.getPair(...a)), recorded: false }) })
    at(route, path, <PairPage />)
    const header = await screen.findByTestId('pair-header')
    expect(within(header).queryByText('Recorded run')).not.toBeInTheDocument()
  })
})

describe('pair page: Level 0 final output (ADR 0024)', () => {
  const path = '/verdicts/:id/pairs/:golden/:replay'

  it('a Nano-cleared pair reads identical with no note', async () => {
    at('/verdicts/v_rec_gt/pairs/run_gt_04/rep_v_rec_gt_04', path, <PairPage />)
    const card = await screen.findByTestId('level0-card')
    expect(within(card).getByTestId('level0-output')).toHaveTextContent('Final output: identical')
    expect(within(card).queryByTestId('level0-nano-gated')).toBeNull()
  })

  it('a Nano-cleared pair whose output changed only its rate date shows the percentage and no note', async () => {
    at('/verdicts/v_rec_fx/pairs/run_fx_07/rep_v_rec_fx_07', path, <PairPage />)
    const card = await screen.findByTestId('level0-card')
    expect(within(card).getByTestId('level0-output')).toHaveTextContent(/^Final output: 9\d % similar$/)
    expect(within(card).queryByTestId('level0-nano-gated')).toBeNull()
  })

  it('an Ultra-decided pair with a Level 0 finding says Nano could not clear it alone', async () => {
    at('/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01', path, <PairPage />)
    const card = await screen.findByTestId('level0-card')
    expect(within(card).getByTestId('level0-output')).toHaveTextContent(/^Final output: \d+ % similar$/)
    expect(within(card).getByTestId('level0-nano-gated')).toHaveTextContent('Nano could not clear this pair alone (Level 0 found differences).')
  })
})

describe('pair page: control replays (ADR 0023)', () => {
  const route = '/verdicts/v_rec_er/pairs/run_er_02/rep_v_rec_er_02'
  const path = '/verdicts/:id/pairs/:golden/:replay'

  it('a control-held pair shows the marker, the C badge, the server reason and a Control run section', async () => {
    const user = userEvent.setup()
    at(route, path, <PairPage />)
    const header = await screen.findByTestId('pair-header')
    expect(within(header).getByText('Control: diverges')).toBeInTheDocument()
    expect(within(header).getByRole('img', { name: 'Outcome: Same result' })).toBeInTheDocument()
    expect(within(header).getByTestId('noisy-task')).toHaveTextContent('(noisy task)')
    expect(within(header).getByTestId('trail-control')).toHaveAccessibleName('Control replay: the unchanged skill also produced a different result')
    expect(within(header).getByText(/so the difference is nondeterminism, not the edit\.$/)).toBeInTheDocument()
    const section = screen.getByTestId('control-run')
    await user.click(within(section).getByRole('button', { name: 'Show' }))
    expect(within(section).getByRole('link', { name: /Open the control trace/ })).toHaveAttribute('href', '/traces/ctl_v_rec_er_02')
    expect(await fixture.getTrace('ctl_v_rec_er_02')).toMatchObject({ golden_of: 'run_er_02' })
  })

  it('the public host shows only the marker (the trace route is hidden there)', async () => {
    sessionStorage.removeItem('ratchet.fixture.operator')
    setApi(createFixtureApi())
    at(route, path, <PairPage />)
    const header = await screen.findByTestId('pair-header')
    expect(within(header).getByText('Control: diverges')).toBeInTheDocument()
    expect(screen.queryByTestId('control-run')).not.toBeInTheDocument()
  })
})

describe('costs page', () => {
  it('defaults to the last 14 days, shows the window tile, and re-queries on a preset chip', async () => {
    const user = userEvent.setup()
    const calls: (string | undefined)[][] = []
    setApi({ ...fixture, getCosts: (r) => { calls.push([r?.from, r?.to]); return fixture.getCosts(r) } })
    at('/costs', '/costs', <CostsPage />)
    const tile = await screen.findByTestId('window-tile')
    expect(tile).toHaveTextContent('Last 14 days')
    expect(screen.getByRole('button', { name: '14 days' })).toHaveAttribute('aria-pressed', 'true')
    const [from, to] = calls[0]!
    expect((Date.parse(to!) - Date.parse(from!)) / 86_400_000).toBeCloseTo(14, 0)
    expect(screen.getByRole('heading', { name: 'By purpose: last 14 days' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Today' }))
    await waitFor(() => expect(screen.getByTestId('window-tile')).toHaveTextContent('Today'))
    expect(screen.getByRole('button', { name: 'Today' })).toHaveAttribute('aria-pressed', 'true')
    const [f2, t2] = calls.at(-1)!
    expect((Date.parse(t2!) - Date.parse(f2!)) / 86_400_000).toBeCloseTo(1, 0)
    // "This edit" needs the newest verdict's times; it becomes available once they load.
    const edit = screen.getByRole('button', { name: 'This edit' })
    await waitFor(() => expect(edit).toBeEnabled())
    await user.click(edit)
    await waitFor(() => expect(screen.getByTestId('window-tile')).toHaveTextContent('This edit'))
  })

  it('the counterfactual line names its verdict count and the tiers that spent', async () => {
    at('/costs', '/costs', <CostsPage />)
    const line = await screen.findByTestId('counterfactual')
    expect(within(line).getByTestId('counterfactual-n')).toHaveTextContent(/^over \d+ verdicts? in this window$/)
    expect(within(line).getByTestId('usd-tiers')).toHaveTextContent(/^\$[\d.]+ \((nano|super|ultra)(, (super|ultra))*\)$/)
  })


  it('gives every tile its prompt-cache share, per tier', async () => {
    at('/costs', '/costs', <CostsPage />)
    const tile = await screen.findByTestId('window-tile')
    expect(within(tile).getByTestId('cache-share')).toHaveTextContent(/^Prompt-cache share: 0\.\d\d of prompt tokens \(nano 0\.52 · super (0\.38|n\/a) · ultra (0\.12|n\/a)\)$/)
    expect(screen.getAllByTestId('cache-share').length).toBe(screen.getAllByTestId('tile-total').length)
  })

  it('says not recorded when the backend has no prompt tokens for a tile', async () => {
    setApi({ ...fixture, getCosts: async (r) => { const c = await fixture.getCosts(r); return { ...c, today: { ...c.today, cache_share: null } } } })
    at('/costs', '/costs', <CostsPage />)
    const today = await screen.findByRole('region', { name: 'Today' })
    expect(within(today).getByTestId('cache-share')).toHaveTextContent('Prompt-cache share: not recorded')
  })

  it('gives every tile total its tiers', async () => {
    at('/costs', '/costs', <CostsPage />)
    await waitFor(() => expect(screen.getAllByTestId('tile-total').length).toBeGreaterThan(0))
    for (const t of screen.getAllByTestId('tile-total')) expect(t).toHaveTextContent(/^\$[\d.]+\((nano|super|ultra)(, (super|ultra))*\)$|^\$0\.00\(no model\)$/)
  })
})

describe('traces page: recent runs (GET /runs?unblessed=1)', () => {
  it('lists unblessed runs with source, tier, steps and a confirmed Bless that removes the row', async () => {
    const user = userEvent.setup()
    const listRuns = vi.fn(fixture.listRuns)
    setApi({ ...fixture, listRuns })
    at('/traces?kind=live', '/traces', <TracesPage />)
    const rows = await screen.findAllByTestId('run-row')
    expect(listRuns).toHaveBeenCalledWith({ unblessed: true, limit: 50 })
    const first = rows[0]!
    const runId = first.getAttribute('data-run-id')!
    expect(first).toHaveTextContent(/Captured|Live run/)
    expect(within(first).getByRole('link')).toHaveAttribute('href', `/traces/${runId}`)
    await user.click(within(first).getByRole('button', { name: 'Bless' }))
    const dialog = screen.getByRole('group', { name: 'Bless this run?' })
    await user.click(within(dialog).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Blessed. Future edits to its skills are now checked against this run.')
    await waitFor(() => expect(screen.queryAllByTestId('run-row').map((r) => r.getAttribute('data-run-id'))).not.toContain(runId))
    expect(screen.getByText('Undo: unbless it from the trace page.')).toBeInTheDocument()
  })

  it('a refused Bless reports the code-mapped copy as a toast', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, bless: () => Promise.reject(new ApiError('rate_limited', 'slow down', 429)) })
    at('/traces?kind=live', '/traces', <TracesPage />)
    const [first] = await screen.findAllByTestId('run-row')
    await user.click(within(first!).getByRole('button', { name: 'Bless' }))
    await user.click(within(screen.getByTestId('inline-confirm')).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Too many actions in a minute. Wait a moment and try again.')
  })

  it('503 not_ready: says the host is still starting', async () => {
    setApi({ ...fixture, listRuns: () => Promise.reject(new ApiError('not_ready', 'This host is not fully started yet; try again in a moment.', 503)) })
    at('/traces?kind=live', '/traces', <TracesPage />)
    expect(await screen.findByTestId('error-state')).toHaveTextContent('This host is not fully started yet; try again in a moment.')
  })
})

describe('digest page', () => {
  it('renders the product-metrics table under the narrative', async () => {
    at('/digest', '/digest', <DigestPage />)
    const table = await screen.findByTestId('digest-metrics')
    expect(within(table).getByRole('heading', { name: 'Product metrics' })).toBeInTheDocument()
    expect(within(table).getByRole('rowheader', { name: 'Blessed traces' }).closest('tr')).toHaveTextContent('Blessed traces42')
  })

  it('a digest without metrics has no table', async () => {
    setApi({ ...fixture, getDigest: async () => ({ ...(await fixture.getDigest()), metrics: [] }) })
    at('/digest', '/digest', <DigestPage />)
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByTestId('digest-metrics')).not.toBeInTheDocument()
  })
})

describe('debug drawer', () => {
  it('says "(last 500 lines)" when the server truncated the log', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, getEditLogs: async () => ({ lines: ['tail line'], truncated: true }) })
    at('/verdicts/v_rec_gt', '/verdicts/:id', <VerdictPage />)
    await user.click(await screen.findByRole('button', { name: 'Show logs' }))
    expect(await screen.findByTestId('logs-truncated')).toHaveTextContent('(last 500 lines)')
  })

  it('operator host: Show logs renders the server log lines', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, getEditLogs: async () => ({ lines: ['--- worker log batch_1', 'server line'], truncated: false }) })
    at('/verdicts/v_rec_gt', '/verdicts/:id', <VerdictPage />)
    await user.click(await screen.findByRole('button', { name: 'Show logs' }))
    expect(await screen.findByTestId('worker-log')).toHaveTextContent('server line')
    expect(screen.getByTestId('debug-batches')).not.toHaveTextContent('—')
  })

  it('a backend without the logs route falls back to the CLI hint', async () => {
    const user = userEvent.setup()
    setApi({ ...fixture, getEditLogs: () => Promise.reject(new ApiError('not_found', 'nope', 404)) })
    at('/verdicts/v_rec_gt', '/verdicts/:id', <VerdictPage />)
    await user.click(await screen.findByRole('button', { name: 'Show logs' }))
    expect(await screen.findByTestId('logs-hint')).toHaveTextContent('Worker logs: ratchet logs --edit e_rec_gt')
  })

  it('public host: batch ids, runner and the CLI hint; never calls the logs route', async () => {
    const user = userEvent.setup()
    sessionStorage.removeItem('ratchet.fixture.operator')
    const pub = createFixtureApi()
    const getEditLogs = vi.fn(pub.getEditLogs)
    setApi({ ...pub, getEditLogs })
    at('/verdicts/v_rec_gt', '/verdicts/:id', <VerdictPage />)
    await user.click(await screen.findByRole('button', { name: 'Show logs' }))
    expect(screen.getByTestId('logs-hint')).toHaveTextContent('ratchet logs --edit e_rec_gt')
    expect(screen.getByText('nebius-serverless')).toBeInTheDocument()
    expect(getEditLogs).not.toHaveBeenCalled()
  })
})

describe('component kit is operator-only', () => {
  it('public host: /__kit is the landing page', async () => {
    sessionStorage.removeItem('ratchet.fixture.operator')
    setApi(createFixtureApi())
    at('/__kit', '/__kit', <KitRoute />)
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Your agent learns new skills every day.')
  })
  it('operator host: /__kit renders the kit', async () => {
    at('/__kit', '/__kit', <KitRoute />)
    expect(await screen.findByText('/__kit')).toBeInTheDocument()
  })
})

describe('landing "How it works" names the runner', () => {
  it.each([
    ['nebius-serverless', 'Replay on Nebius Jobs'],
    ['local', 'Replay on the local runner'],
  ])('%s → %s', async (runner, text) => {
    setApi({ ...fixture, getMeta: async () => ({ ...(await fixture.getMeta()), runner }) })
    at('/', '/', <LandingPage />)
    const strip = await screen.findByRole('list', { name: 'How a check runs' })
    await waitFor(() => expect(strip).toHaveTextContent(text))
    expect(screen.getByText(LANDING_COPY)).toBeInTheDocument()
  })
})

describe('timeline rows read the new event fields', () => {
  const ev = (kind: string, payload: Record<string, unknown>) => ({ id: 1, ts: new Date().toISOString(), kind, ref: 'v_1', payload, session_hash: null })
  it('verdict.acted with override reads "Approved anyway"', () => {
    expect(describeEvent(ev('verdict.acted', { verdict_id: 'v_1', action: 'merged', override: true, skill: 'github-triage' })).title).toBe('Approved anyway: github-triage')
  })
  it("verdict.rolled_back uses the server's toast for what happened to main", () => {
    expect(describeEvent(ev('verdict.rolled_back', { verdict_id: 'v_1', actor: 'jun', toast: 'Rolled back. main no longer has this edit.' })).summary).toBe('By jun · main no longer has this edit')
    expect(describeEvent(ev('verdict.rolled_back', { verdict_id: 'v_1', actor: 'jun' })).summary).toBe('By jun · main is unchanged')
  })
  it('pair.control reads "Control replay agreed/diverged for <task>" and links the pair (ADR 0023)', () => {
    const agreed = describeEvent({ ...ev('pair.control', { verdict_id: 'v_1', golden: 'run_er_01', replay: 'rep_1', control: 'ctl_1', control_outcome: 'agrees', task_label: 'October expense report' }), ref: 'e_1' })
    expect(agreed.title).toBe('Control replay agreed for October expense report')
    expect(agreed.href).toBe('/verdicts/v_1/pairs/run_er_01/rep_1')
    // The backend payload has no task label: fall back to the golden run id.
    expect(describeEvent(ev('pair.control', { verdict_id: 'v_1', golden: 'run_er_02', replay: 'rep_2', control: 'ctl_2', control_outcome: 'diverges' })).title).toBe('Control replay diverged for run_er_02 — counted as nondeterminism')
  })
  it('pair.reblessed names the drifted facts', () => {
    expect(describeEvent(ev('pair.reblessed', { golden: 'run_1', replay: 'rep_1', drift_fact_ids: ['eur_usd'] })).summary).toContain('(world changed: eur_usd)')
  })
})

describe('landing hero: the recorded verdict', () => {
  it('uses the recorded verdict the scenarios endpoint names, even with no recorded verdict in the list', async () => {
    setApi({
      ...fixture,
      listScenarios: async () => (await fixture.listScenarios()).map((s, i) => (i === 0 ? { ...s, recorded_verdict_id: 'v_rec_gt' } : s)),
      listVerdicts: async () => ({ items: [], next_cursor: null }),
    })
    at('/', '/', <LandingPage />)
    const hero = await screen.findByTestId('hero')
    expect(within(hero).getByTestId('decision-word')).toHaveTextContent('Blocked.')
  })

  it('falls back to a recorded verdict in the verdict list', async () => {
    at('/', '/', <LandingPage />)
    expect(await screen.findByTestId('hero')).toBeInTheDocument()
  })

  it('shows the scenario cards alone when no recorded verdict exists', async () => {
    setApi({ ...fixture, listVerdicts: async () => ({ items: [], next_cursor: null }) })
    at('/', '/', <LandingPage />)
    expect(await screen.findAllByTestId('scenario-card')).toHaveLength(3)
    expect(screen.queryByTestId('hero')).not.toBeInTheDocument()
  })
})
