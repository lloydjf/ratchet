import { lazy, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { isApiError } from './api/client'
import { useMeta } from './api/hooks'
import { LiveEventsProvider } from './api/live'
import { ToastProvider } from './components/Toast'
import { AppShell } from './layout/AppShell'
import LandingPage from './pages/LandingPage'

const VerdictPage = lazy(() => import('./pages/VerdictPage'))
const VerdictsPage = lazy(() => import('./pages/VerdictsPage'))
const PairPage = lazy(() => import('./pages/PairPage'))
const EditPage = lazy(() => import('./pages/EditPage'))
const TimelinePage = lazy(() => import('./pages/TimelinePage'))
const TracesPage = lazy(() => import('./pages/TracesPage'))
const TracePage = lazy(() => import('./pages/TracePage'))
const DigestPage = lazy(() => import('./pages/DigestPage'))
const CostsPage = lazy(() => import('./pages/CostsPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const KitPage = lazy(() => import('./pages/KitPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => !(isApiError(err) && err.status >= 400 && err.status < 500) && count < 2,
      },
    },
  })
}

/**
 * The component kit is an operator tool: on the public host `/__kit` is the landing page
 * (it is never linked from navigation either). Nothing renders until `/meta` says which host.
 */
export function KitRoute() {
  const { data: meta, isError } = useMeta()
  if (!meta && !isError) return null
  if (isError || meta?.public_mode !== false) return <LandingPage />
  return <KitPage />
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<LandingPage />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="verdicts" element={<VerdictsPage />} />
        <Route path="verdicts/:id" element={<VerdictPage />} />
        <Route path="verdicts/:id/pairs/:golden/:replay" element={<PairPage />} />
        <Route path="edits/:id" element={<EditPage />} />
        <Route path="traces" element={<TracesPage />} />
        <Route path="traces/:runId" element={<TracePage />} />
        <Route path="digest" element={<DigestPage />} />
        <Route path="costs" element={<CostsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="__kit" element={<KitRoute />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export function App() {
  const [client] = useState(makeQueryClient)
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <ToastProvider>
          <LiveEventsProvider>
            <AppRoutes />
          </LiveEventsProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
