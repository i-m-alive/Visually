'use client'
import { usePipelineStore, StepStatus } from '@/stores/pipelineStore'
import { cn } from '@/lib/utils'

const STEPS = [
  { key: 'intent', label: 'Intent' },
  { key: 'schema', label: 'Schema' },
  { key: 'query', label: 'Query' },
  { key: 'execute', label: 'Execute' },
  { key: 'render', label: 'Render' },
  { key: 'validate', label: 'Validate' },
] as const

function StepPill({ label, status, onClick }: { label: string; status: StepStatus; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-all',
        status === 'idle' && 'bg-gray-100 text-gray-400',
        status === 'active' && 'bg-amber-100 text-amber-700 animate-pulse',
        status === 'done' && 'bg-green-100 text-green-700 cursor-pointer hover:bg-green-200',
        status === 'error' && 'bg-red-100 text-red-700',
      )}
    >
      <span className={cn(
        'w-1.5 h-1.5 rounded-full',
        status === 'idle' && 'bg-gray-300',
        status === 'active' && 'bg-amber-500',
        status === 'done' && 'bg-green-500',
        status === 'error' && 'bg-red-500',
      )} />
      {label}
    </button>
  )
}

export function IntentStatusBar({ jobId }: { jobId: string | null }) {
  const jobs = usePipelineStore((s) => s.jobs)
  const setDrawerOpen = usePipelineStore((s) => s.setDrawerOpen)

  if (!jobId) return null

  const job = jobs[jobId]
  if (!job) return null

  const anyActive = Object.values(job.steps).some((s) => s !== 'idle')
  if (!anyActive) return null

  return (
    <div className="flex items-center gap-1.5 px-4 py-2 bg-white border-b border-gray-100 overflow-x-auto">
      <span className="text-xs text-gray-500 mr-2 shrink-0">Pipeline:</span>
      {STEPS.map((step, i) => (
        <div key={step.key} className="flex items-center gap-1.5">
          <StepPill
            label={step.label}
            status={job.steps[step.key]}
            onClick={job.steps[step.key] === 'done' ? () => setDrawerOpen(true) : undefined}
          />
          {i < STEPS.length - 1 && (
            <span className="text-gray-300 text-xs">→</span>
          )}
        </div>
      ))}
    </div>
  )
}
