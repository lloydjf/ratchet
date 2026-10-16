/**
 * Compile-time drift check between the hand-written `types.ts` and the backend's OpenAPI
 * (`openapi.gen.ts`, regenerated with `npm run gen:api`). Field names must match exactly;
 * `tsc -b` (npm run typecheck) fails on drift. The runtime test only keeps vitest happy.
 */
import { expect, it } from 'vitest'
import type { components, paths } from './openapi.gen'
import type * as T from './types'

type S = components['schemas']
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false
type Assert<X extends true> = X
type Keys<A> = keyof A & string
/** The JSON body of a route's 200 response. */
type Ok<P extends keyof paths, M extends 'get' | 'post'> = paths[P][M] extends { responses: { 200: { content: { 'application/json': infer R } } } } ? R : never

export type Drift = [
  Assert<Same<Keys<S['Meta']>, Keys<T.Meta>>>,
  Assert<Same<Keys<S['ScenarioView']>, Keys<T.Scenario>>>,
  Assert<Same<Keys<S['SimulateView']>, Keys<T.SimulateResponseLoose>>>,
  Assert<Same<Keys<S['VerdictSummary']>, Keys<T.VerdictSummary>>>,
  Assert<Same<Keys<S['VerdictView']>, Keys<T.VerdictView>>>,
  Assert<Same<Keys<S['Progress']>, Keys<T.Progress>>>,
  Assert<Same<Keys<S['StageView']>, Keys<T.ProgressStage>>>,
  Assert<Same<Keys<S['PairView']>, Keys<T.PairView>>>,
  Assert<Same<Keys<S['ReblessView']>, Keys<T.ReblessResponse>>>,
  Assert<Same<Keys<S['EditView']>, Keys<T.EditView>>>,
  Assert<Same<Keys<S['TraceSummary']>, Keys<T.TraceSummary>>>,
  Assert<Same<Keys<S['CostsView']>, Keys<T.CostsView>>>,
  Assert<Same<Keys<S['CostTile']>, Keys<T.CostTile>>>,
  Assert<Same<Keys<S['CostWindow']>, Keys<T.CostWindow>>>,
  Assert<Same<Keys<S['DigestView']>, Keys<T.Digest>>>,
  Assert<Same<Keys<S['TickerView']>, Keys<T.Ticker>>>,
  Assert<Same<Keys<S['SettingsView']>, Keys<T.SettingsView>>>,
  Assert<Same<Keys<S['Event']>, Keys<T.Event>>>,
  Assert<Same<Keys<S['Verdict']>, Keys<T.Verdict>>>,
  Assert<Same<Keys<S['PairVerdict']>, Keys<T.PairVerdict>>>,
  Assert<Same<Keys<S['JudgeOpinion']>, Keys<T.JudgeOpinion>>>,
  Assert<Same<Keys<S['DriftCheck']>, Keys<T.DriftCheck>>>,
  Assert<Same<Keys<S['Level0Finding']>, Keys<T.Level0Finding>>>,
  Assert<Same<Keys<S['Trace']>, Keys<T.Trace>>>,
  Assert<Same<Keys<S['SkillEdit']>, Keys<T.SkillEdit>>>,
  Assert<Same<Keys<S['ToolCall']>, Keys<T.ToolCall>>>,
  Assert<Same<Keys<S['LLMCall']>, Keys<T.LLMCall>>>,
  Assert<Same<Keys<S['WorldFact']>, Keys<T.WorldFact>>>,
  Assert<Same<Keys<S['StageTiming']>, Keys<T.StageTiming>>>,
  Assert<Same<Keys<S['DemoEventIn']>, Keys<T.DemoEvent>>>,
  Assert<Same<Keys<S['Counterfactual']>, Keys<T.Counterfactual>>>,
  Assert<Same<Keys<S['LastEditCost']>, Keys<NonNullable<T.CostsView['last_edit']>>>>,
  Assert<Same<Keys<S['MetricRow']>, Keys<T.MetricRow>>>,
  Assert<Same<Keys<S['MetricsView']>, Keys<T.MetricsView>>>,
  Assert<Same<Keys<S['LogsView']>, Keys<T.LogsView>>>,
  Assert<Same<Keys<S['RunView']>, Keys<T.RunView>>>,
  Assert<Same<Keys<S['RunsView']>, Keys<T.RunsView>>>,
  Assert<Same<S['RunView']['source'], T.RunView['source']>>,
  // Routes the client calls whose bodies were hand-typed before they reached the OpenAPI.
  Assert<Same<Keys<Ok<'/api/v1/edits/{edit_id}/logs', 'get'>>, Keys<T.LogsView>>>,
  Assert<Same<Keys<Ok<'/api/v1/metrics/product', 'get'>>, Keys<T.MetricsView>>>,
  Assert<Same<Keys<Ok<'/api/v1/metrics/demo', 'get'>>, Keys<T.MetricsView>>>,
  Assert<Same<Keys<Ok<'/api/v1/runs', 'get'>>, Keys<T.RunsView>>>,
  Assert<Same<Keys<Ok<'/api/v1/verdicts/{verdict_id}/rollback', 'post'>>, Keys<T.VerdictView>>>,
  Assert<Same<Keys<Ok<'/api/v1/verdicts/{verdict_id}/approve', 'post'>>, Keys<T.VerdictView>>>,
  Assert<Same<Keys<Ok<'/api/v1/pairs/{golden}/{replay}/rebless', 'post'>>, Keys<T.ReblessResponse>>>,
]

it('types.ts field names match the OpenAPI schema (checked by tsc)', () => {
  expect(true).toBe(true)
})
