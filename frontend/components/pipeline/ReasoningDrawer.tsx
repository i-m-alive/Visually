'use client'
import { usePipelineStore } from '@/stores/pipelineStore'
import { X, Copy, Check } from 'lucide-react'
import { useState } from 'react'

export function ReasoningDrawer({ jobId }: { jobId: string | null }) {
  const drawerOpen = usePipelineStore((s) => s.drawerOpen)
  const setDrawerOpen = usePipelineStore((s) => s.setDrawerOpen)
  const jobs = usePipelineStore((s) => s.jobs)
  const [copied, setCopied] = useState(false)

  if (!drawerOpen || !jobId) return null

  const job = jobs[jobId]
  if (!job) return null

  const handleCopy = () => {
    if (job.generatedSql) {
      navigator.clipboard.writeText(job.generatedSql)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const score = job.validationScore
  const details = job.chartResult?.validation_details as Record<string, unknown> | undefined
  const dims = details?.dimension_scores as Record<string, number> | undefined

  return (
    <div className="fixed right-0 top-0 h-full w-96 bg-white border-l border-gray-200 shadow-xl z-50 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <h3 className="font-semibold text-gray-900 font-display">Agent Reasoning</h3>
        <button onClick={() => setDrawerOpen(false)} className="p-1 hover:bg-gray-100 rounded">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {job.currentIntent && (
          <section>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Detected Intent</h4>
            <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-brand-light text-brand">
              {job.currentIntent}
            </span>
            {job.retryAttempt && (
              <p className="text-xs text-amber-600 mt-1">Retried (attempt {job.retryAttempt})</p>
            )}
          </section>
        )}

        {job.generatedSql && (
          <section>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Generated SQL</h4>
              <button onClick={handleCopy} className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700">
                {copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre className="bg-gray-900 text-green-400 text-xs p-3 rounded-lg overflow-x-auto font-mono leading-relaxed whitespace-pre-wrap">
              {job.generatedSql}
            </pre>
          </section>
        )}

        {job.tableUsed && (
          <section>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Table Selected</h4>
            <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-mono bg-gray-100 text-gray-700">
              {job.tableUsed}
            </span>
          </section>
        )}

        {score !== undefined && (
          <section>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Validation Score</h4>
            <div className="flex items-center gap-3 mb-2">
              <div className="flex-1 bg-gray-100 rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all ${score >= 0.8 ? 'bg-green-500' : score >= 0.5 ? 'bg-amber-500' : 'bg-red-500'}`}
                  style={{ width: `${score * 100}%` }}
                />
              </div>
              <span className={`text-sm font-semibold ${score >= 0.8 ? 'text-green-600' : score >= 0.5 ? 'text-amber-600' : 'text-red-600'}`}>
                {(score * 100).toFixed(0)}%
              </span>
            </div>
            {dims && (
              <div className="space-y-1">
                {Object.entries(dims).map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs">
                    <span className="text-gray-500 capitalize">{k.replace(/_/g, ' ')}</span>
                    <span className="font-medium">{(v * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {job.error && (
          <section>
            <h4 className="text-xs font-semibold text-red-500 uppercase tracking-wide mb-2">Error</h4>
            <p className="text-xs text-red-600 bg-red-50 p-2 rounded">{job.error}</p>
          </section>
        )}
      </div>
    </div>
  )
}
