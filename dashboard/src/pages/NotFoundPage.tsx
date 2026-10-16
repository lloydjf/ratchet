import { EmptyState } from '../components/EmptyState'
import { errorCopy } from '../copy'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export default function NotFoundPage() {
  useDocumentTitle(['Not found'])
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-h1 font-semibold text-text">Page not found</h1>
      <EmptyState title={errorCopy('not_found')} action={{ label: 'Go to the start', to: '/' }} />
    </div>
  )
}
