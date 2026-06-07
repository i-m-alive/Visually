'use client'
import { useState, useEffect, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Camera, Loader2, AlertCircle, CheckCircle2, ArrowRight,
  LayoutDashboard, Eye, Database, Zap, ShieldCheck, Layers,
  HelpCircle, RefreshCw, Clock, ScanSearch,
} from 'lucide-react'
import { screenshotApi } from '@/lib/api'
import { UploadDropzone } from '@/components/screenshot/UploadDropzone'
import { HintDialog } from '@/components/screenshot/HintDialog'
import { usePipelineSocket } from '@/hooks/usePipelineSocket'
import { usePipelineStore } from '@/stores/pipelineStore'

interface HintRequest {
  hint_id: string
  chart_id: string
  chart_title?: string
  chart_type?: string
  message: string
  options: { value: string; label: string }[]
}

interface LogEntry {
  ts: number
  level: 'info' | 'success' | 'warn' | 'error'
  text: string
}

const PIPELINE_STEPS = [
  { key: 'upload',     icon: Camera,      label: 'Upload',           desc: 'Sending screenshots to server' },
  { key: 'vision',     icon: Eye,         label: 'AI Vision',        desc: 'Claude analyzes each chart in the screenshot' },
  { key: 'sql',        icon: Database,    label: 'SQL Generation',   desc: 'Generating SQL queries for each chart' },
  { key: 'execute',    icon: Zap,         label: 'Query Execution',  desc: 'Running queries against your database' },
  { key: 'validate',   icon: ShieldCheck, label: 'Validation',       desc: 'Verifying data matches the original chart' },
  { key: 'verify',     icon: ScanSearch,  label: 'Verification',     desc: 'Comparing assembled charts against screenshots' },
  { key: 'assemble',   icon: Layers,      label: 'Dashboard Build',  desc: 'Assembling charts into a live dashboard' },
]

type StepKey = 'upload' | 'vision' | 'sql' | 'execute' | 'validate' | 'verify' | 'assemble'
type StepStatus = 'idle' | 'active' | 'done' | 'error'

function eventToLog(event: Record<string, unknown>): LogEntry | null {
  const ts = Date.now()
  const type = event.type as string
  switch (type) {
    case 'vision.started':
      return { ts, level: 'info', text: '🔍 AI vision analysis started — reading screenshot layout' }
    case 'vision.parsed': {
      const n = event.chart_count as number
      return { ts, level: 'success', text: `✓ Detected ${n} chart${n !== 1 ? 's' : ''} in the screenshot` }
    }
    case 'schema.matching':
      return { ts, level: 'info', text: '🔍 Schema analysis — matching chart columns to database tables' }
    case 'chart.racing': {
      const chartId = event.chart_id as string | undefined
      const n = event.candidate_count as number | undefined
      return { ts, level: 'info', text: `  🏁 Racing ${n ?? 2} candidates${chartId ? ` for chart [${chartId}]` : ''} — parallel SQL test` }
    }
    case 'chart.race_winner': {
      const chartId = event.chart_id as string | undefined
      const tables = event.winning_tables as string[] | undefined
      return {
        ts, level: 'success',
        text: `  🏆 Race winner${chartId ? ` [${chartId}]` : ''}${tables?.length ? `: ${tables.join(', ')}` : ''}`,
      }
    }
    case 'chart.visual_comparison': {
      const chartId = event.chart_id as string | undefined
      const match = event.match as boolean
      const score = event.score !== undefined ? Math.round((event.score as number) * 100) : null
      return {
        ts,
        level: match ? 'success' : 'warn',
        text: `  👁 Visual check${chartId ? ` [${chartId}]` : ''}: ${match ? '✓ match' : '⚠ mismatch'}${score !== null ? ` (${score}%)` : ''}`,
      }
    }
    case 'dashboard.decomposing':
      return { ts, level: 'info', text: '⚙️ Decomposing dashboard intent...' }
    case 'dashboard.decomposed':
      return { ts, level: 'success', text: '✓ Dashboard structure ready — starting chart SQL generation' }
    case 'query.generated': {
      const tbl = event.table_used as string
      return { ts, level: 'info', text: `  ↳ SQL generated${tbl ? ` (using table: ${tbl})` : ''}` }
    }
    case 'query.executed':
      return { ts, level: 'info', text: '  ↳ Query executed successfully' }
    case 'chart.rendered':
      return { ts, level: 'info', text: '  ↳ Chart rendered' }
    case 'validation.retry': {
      const chartId = event.chart_id as string | undefined
      const attempt = event.attempt as number
      return { ts, level: 'warn', text: `  ↻ Retrying${chartId ? ` chart ${chartId}` : ''} — attempt ${attempt}` }
    }
    case 'validation.scored': {
      const score = Math.round((event.score as number) * 100)
      const chartId = event.chart_id as string | undefined
      const passed = event.passed as boolean
      return {
        ts,
        level: passed ? 'success' : 'warn',
        text: `  ${passed ? '✓' : '⚠'} Validation score: ${score}%${chartId ? ` (chart ${chartId})` : ''}`,
      }
    }
    case 'chart.confirmed': {
      const chartId = event.chart_id as string | undefined
      const score = event.score !== undefined ? Math.round((event.score as number) * 100) : null
      return {
        ts, level: 'success',
        text: `✅ Chart confirmed${chartId ? ` [${chartId}]` : ''}${score !== null ? ` — ${score}% match` : ''}`,
      }
    }
    case 'chart.low_confidence': {
      const chartId = event.chart_id as string | undefined
      return { ts, level: 'warn', text: `⚠️  Low confidence${chartId ? ` chart [${chartId}]` : ''} — included with warning` }
    }
    case 'hint.requested': {
      const chartId = event.chart_id as string | undefined
      return { ts, level: 'warn', text: `❓ AI needs clarification${chartId ? ` about chart [${chartId}]` : ''} — check the dialog above` }
    }
    case 'dashboard.assembled':
      return { ts, level: 'success', text: '🎉 Dashboard assembled! All charts are live.' }
    case 'verification.started': {
      const loop = event.loop as number
      const n = event.chart_count as number
      return { ts, level: 'info', text: `🔎 Verification loop ${loop} — checking ${n} chart${n !== 1 ? 's' : ''} against originals` }
    }
    case 'verification.chart.result': {
      const chartId = event.chart_id as string
      const passed = event.passed as boolean
      const score = Math.round((event.overall_score as number) * 100)
      const issues = (event.issues as string[] | undefined) ?? []
      return {
        ts,
        level: passed ? 'success' : 'warn',
        text: `  ${passed ? '✓' : '✗'} [${chartId}] verify score ${score}%${!passed && issues.length ? ` — ${issues[0]}` : ''}`,
      }
    }
    case 'verification.retry.started': {
      const n = event.failed_count as number
      return { ts, level: 'warn', text: `  ↻ Re-running ${n} chart${n !== 1 ? 's' : ''} that failed verification` }
    }
    case 'verification.complete': {
      const passed = event.passed as boolean
      const score = Math.round((event.overall_score as number) * 100)
      const nPassed = event.passed_charts as number
      const nFailed = event.failed_charts as number
      return {
        ts,
        level: passed ? 'success' : 'warn',
        text: passed
          ? `✅ All charts verified (${score}% avg match)`
          : `⚠️  Verification done — ${nPassed} passed, ${nFailed} below threshold (${score}% avg)`,
      }
    }
    case 'pipeline.error':
      return { ts, level: 'error', text: `❌ Error: ${event.message}` }
    default:
      return null
  }
}

function eventToStepUpdate(type: string): Partial<Record<StepKey, StepStatus>> {
  switch (type) {
    case 'vision.started':           return { upload: 'done', vision: 'active' }
    case 'vision.parsed':            return { vision: 'done', sql: 'active' }
    case 'schema.matching':          return { vision: 'done', sql: 'active' }
    case 'dashboard.decomposed':     return { vision: 'done', sql: 'active' }
    case 'query.generated':          return { sql: 'active', execute: 'active' }
    case 'query.executed':           return { execute: 'done', validate: 'active' }
    case 'chart.racing':             return { sql: 'active' }
    case 'chart.race_winner':        return { sql: 'done', execute: 'done', validate: 'active' }
    case 'chart.visual_comparison':  return { validate: 'active' }
    case 'validation.scored':        return { execute: 'done', validate: 'active' }
    case 'chart.confirmed':          return { validate: 'done' }
    case 'chart.low_confidence':     return { validate: 'done' }
    case 'verification.started':     return { validate: 'done', verify: 'active' }
    case 'verification.retry.started': return { verify: 'active' }
    case 'verification.complete':    return { verify: 'done', assemble: 'active' }
    case 'dashboard.assembled':      return { sql: 'done', execute: 'done', validate: 'done', verify: 'done', assemble: 'done' }
    case 'pipeline.error':           return {}
    default:                         return {}
  }
}

export default function ScreenshotPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const router = useRouter()
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [chartStates, setChartStates] = useState<Record<string, unknown>[]>([])
  const [screenshotJob, setScreenshotJob] = useState<Record<string, unknown> | null>(null)
  const [hint, setHint] = useState<HintRequest | null>(null)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing' | 'done' | 'error'>('idle')
  const [log, setLog] = useState<LogEntry[]>([])
  const [steps, setSteps] = useState<Record<StepKey, StepStatus>>({
    upload: 'idle', vision: 'idle', sql: 'idle',
    execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle',
  })
  const logEndRef = useRef<HTMLDivElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const jobs = usePipelineStore((s) => s.jobs)
  usePipelineSocket(jobId)

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [log])

  // React to pipeline WebSocket events
  useEffect(() => {
    if (!jobId || !jobs[jobId]) return
    const job = jobs[jobId]
    const events = job.events as Record<string, unknown>[]
    if (!events?.length) return

    const lastEvent = events[events.length - 1]
    const type = lastEvent.type as string

    const entry = eventToLog(lastEvent)
    if (entry) setLog((prev) => [...prev, entry])

    const stepUpdate = eventToStepUpdate(type)
    if (Object.keys(stepUpdate).length) {
      setSteps((prev) => ({ ...prev, ...stepUpdate }))
    }

    // Hint dialog
    if (type === 'hint.requested' && !hint) {
      try {
        const hintData = typeof lastEvent === 'object' ? lastEvent : JSON.parse(lastEvent as unknown as string)
        setHint(hintData as HintRequest)
      } catch {}
    }

    if (type === 'dashboard.assembled') {
      setSteps((prev) => ({
        ...prev, upload: 'done', vision: 'done', sql: 'done',
        execute: 'done', validate: 'done', assemble: 'done',
      }))
      setPhase('done')
    }
    if (type === 'pipeline.error') {
      setError(lastEvent.message as string)
      setPhase('error')
    }
  }, [jobs, jobId])

  // Poll chart states while processing (every 8s — chart_states is cheap but no need to flood)
  useEffect(() => {
    if (phase !== 'processing' || !jobId) return
    let pollCount = 0
    const MAX_POLLS = 150  // safety stop after ~20 min (150 × 8s)
    const poll = async () => {
      pollCount++
      if (pollCount > MAX_POLLS) {
        clearInterval(pollRef.current!)
        setPhase('error')
        setError('Pipeline timed out after 20 minutes. Please try again.')
        return
      }
      try {
        const resp = await screenshotApi.getJob(jobId)
        setChartStates(resp.data.chart_states || [])
        setScreenshotJob(resp.data.screenshot_job || null)
        if (resp.data.screenshot_job?.status === 'completed') {
          setPhase('done')
          clearInterval(pollRef.current!)
        }
      } catch {}
    }
    poll()
    pollRef.current = setInterval(poll, 8000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [phase, jobId])

  const handleUpload = async () => {
    if (!files.length) return
    setUploading(true)
    setError(null)
    setPhase('uploading')
    setLog([{ ts: Date.now(), level: 'info', text: `📤 Uploading ${files.length} screenshot${files.length !== 1 ? 's' : ''}...` }])
    setSteps({ upload: 'active', vision: 'idle', sql: 'idle', execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle' })
    try {
      const resp = await screenshotApi.upload({ projectId, files })
      setJobId(resp.data.job_id)
      setPhase('processing')
      setSteps((prev) => ({ ...prev, upload: 'done', vision: 'active' }))
      setLog((prev) => [...prev, { ts: Date.now(), level: 'success', text: '✓ Upload complete — AI pipeline starting...' }])
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      const msg = e.response?.data?.detail || 'Upload failed'
      setError(msg)
      setPhase('error')
      setSteps((prev) => ({ ...prev, upload: 'error' }))
      setLog((prev) => [...prev, { ts: Date.now(), level: 'error', text: `❌ Upload failed: ${msg}` }])
    } finally {
      setUploading(false)
    }
  }

  const handleHintSubmit = async (value: string) => {
    if (!hint || !jobId) return
    setLog((prev) => [...prev, { ts: Date.now(), level: 'info', text: `💬 Hint submitted: ${value}` }])
    try {
      await screenshotApi.submitHint(jobId, { hint_id: hint.hint_id, response: value })
    } catch {}
    setHint(null)
  }

  const resultDashboardId = screenshotJob?.result_dashboard_id as string | undefined

  const stepIcon = (key: StepKey, Icon: React.ElementType) => {
    const s = steps[key]
    if (s === 'done')   return <CheckCircle2 size={16} className="text-green-500" />
    if (s === 'active') return <Loader2 size={16} className="animate-spin text-brand" />
    if (s === 'error')  return <AlertCircle size={16} className="text-red-500" />
    return <Icon size={16} className="text-gray-300" />
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {hint && (
        <HintDialog
          chartId={hint.chart_id}
          chartTitle={hint.chart_title}
          chartType={hint.chart_type}
          message={hint.message}
          options={hint.options}
          onSubmit={handleHintSubmit}
          onSkip={() => {
            if (jobId && hint) screenshotApi.submitHint(jobId, { hint_id: hint.hint_id, response: '' }).catch(() => {})
            setHint(null)
          }}
        />
      )}

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto space-y-6">

          {/* Header */}
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-brand flex items-center justify-center shrink-0">
              <Camera size={20} className="text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900 font-display">Screenshot Replication</h1>
              <p className="text-sm text-gray-400">Upload dashboard screenshots — AI replicates them with live data</p>
            </div>
          </div>

          {/* Upload card (idle/uploading) */}
          {(phase === 'idle' || phase === 'uploading') && (
            <div className="card p-5 space-y-4">
              <UploadDropzone onFilesSelected={setFiles} disabled={uploading} />
              {error && (
                <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-xl">
                  <AlertCircle size={14} /> {error}
                </div>
              )}
              <div className="flex justify-between items-center">
                <p className="text-xs text-gray-400">PNG / JPEG / WebP · max 20 MB · up to 5 files</p>
                <button
                  onClick={handleUpload}
                  disabled={!files.length || uploading}
                  className="btn-primary flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {uploading
                    ? <><Loader2 size={16} className="animate-spin" /> Uploading...</>
                    : <>Replicate {files.length || ''} screenshot{files.length !== 1 ? 's' : ''} <ArrowRight size={16} /></>}
                </button>
              </div>
            </div>
          )}

          {/* Processing / Done */}
          {(phase === 'processing' || phase === 'done' || phase === 'error') && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

              {/* Step timeline */}
              <div className="card p-4 space-y-1">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Pipeline steps</p>
                {PIPELINE_STEPS.map(({ key, icon: Icon, label, desc }) => {
                  const s = steps[key as StepKey]
                  return (
                    <div key={key} className={`flex items-start gap-3 py-2 px-3 rounded-lg transition-colors ${
                      s === 'active' ? 'bg-brand-light' : s === 'done' ? 'bg-green-50' : s === 'error' ? 'bg-red-50' : ''
                    }`}>
                      <div className="mt-0.5 shrink-0">{stepIcon(key as StepKey, Icon)}</div>
                      <div className="min-w-0">
                        <p className={`text-sm font-medium ${
                          s === 'active' ? 'text-brand' : s === 'done' ? 'text-green-700'
                          : s === 'error' ? 'text-red-600' : 'text-gray-400'
                        }`}>{label}</p>
                        {s === 'active' && <p className="text-xs text-gray-500 mt-0.5 truncate">{desc}</p>}
                      </div>
                      {s === 'active' && (
                        <span className="ml-auto text-xs text-brand font-medium shrink-0">Running</span>
                      )}
                      {s === 'done' && (
                        <span className="ml-auto text-xs text-green-600 shrink-0">Done</span>
                      )}
                    </div>
                  )
                })}

                {/* Chart summary */}
                {chartStates.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-gray-100 space-y-1.5">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Charts</p>
                    {chartStates.map((c) => {
                      const status = c.status as string
                      const chartId = c.chart_id as string
                      const score = c.validation_score as number | undefined
                      const attempts = c.attempt_count as number
                      return (
                        <div key={chartId} className="flex items-center gap-2 py-1.5 px-2 rounded-lg bg-gray-50 text-xs">
                          {status === 'confirmed'      && <CheckCircle2 size={12} className="text-green-500 shrink-0" />}
                          {status === 'low_confidence' && <AlertCircle  size={12} className="text-amber-500 shrink-0" />}
                          {status === 'failed'         && <AlertCircle  size={12} className="text-red-500 shrink-0" />}
                          {status === 'pending'        && <Clock        size={12} className="text-gray-300 shrink-0" />}
                          {!['confirmed','low_confidence','failed','pending'].includes(status) &&
                            <Loader2 size={12} className="animate-spin text-brand shrink-0" />}
                          <span className="font-mono text-gray-700 truncate flex-1">{chartId}</span>
                          {attempts > 0 && <span className="text-gray-400">×{attempts}</span>}
                          {score !== undefined && (
                            <span className={`font-semibold ${
                              score >= 0.95 ? 'text-green-600' : score >= 0.7 ? 'text-amber-600' : 'text-red-500'
                            }`}>{Math.round(score * 100)}%</span>
                          )}
                          <span className={`capitalize ${
                            status === 'confirmed' ? 'text-green-600' :
                            status === 'low_confidence' ? 'text-amber-600' :
                            status === 'failed' ? 'text-red-500' : 'text-gray-400'
                          }`}>{status.replace('_', ' ')}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* Verification Phase UI */}
              {(() => {
                const ssSteps = jobs[jobId ?? '']?.screenshotSteps ?? {}
                const verifyStatus = ssSteps.verification_status as string | undefined
                const overallScore = ssSteps.verification_overall_score as string | undefined
                const passedCharts = ssSteps.verification_passed_charts as string | undefined
                const failedCount = ssSteps.verification_failed_count as string | undefined
                if (!verifyStatus) return null

                const verifyResults = chartStates.map(c => {
                  const cid = c.chart_id as string
                  const passed = ssSteps[`verify_${cid}_passed`]
                  const score = ssSteps[`verify_${cid}_score`]
                  return { chartId: cid, passed, score }
                }).filter(r => r.score !== undefined)

                return (
                  <div className="card p-4 space-y-3 col-span-full">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <ScanSearch size={14} className={
                          verifyStatus === 'active'   ? 'text-brand animate-pulse' :
                          verifyStatus === 'retrying' ? 'text-amber-500 animate-pulse' :
                          verifyStatus === 'passed'   ? 'text-green-500' : 'text-amber-500'
                        } />
                        <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Verification Phase</p>
                      </div>
                      {overallScore && (
                        <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                          verifyStatus === 'passed' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
                        }`}>
                          {overallScore}% avg match
                        </span>
                      )}
                    </div>

                    {verifyStatus === 'active' && (
                      <p className="text-xs text-gray-400 flex items-center gap-1.5">
                        <Loader2 size={11} className="animate-spin" />
                        Comparing assembled charts against original screenshots...
                      </p>
                    )}
                    {verifyStatus === 'retrying' && (
                      <p className="text-xs text-amber-600 flex items-center gap-1.5">
                        <RefreshCw size={11} className="animate-spin" />
                        Re-running {failedCount} chart{Number(failedCount) !== 1 ? 's' : ''} that failed verification...
                      </p>
                    )}
                    {(verifyStatus === 'passed' || verifyStatus === 'partial') && (
                      <div className="flex items-center gap-2 text-xs">
                        {verifyStatus === 'passed'
                          ? <><CheckCircle2 size={13} className="text-green-500" /> <span className="text-green-700 font-medium">All {passedCharts} charts verified successfully</span></>
                          : <><AlertCircle size={13} className="text-amber-500" /> <span className="text-amber-700 font-medium">{passedCharts} charts verified, some below threshold — included with warnings</span></>
                        }
                      </div>
                    )}

                    {verifyResults.length > 0 && (
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                        {verifyResults.map(({ chartId, passed, score }) => (
                          <div key={chartId} className={`flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs ${
                            passed === 'true' ? 'bg-green-50 border border-green-100' :
                            passed === 'false' ? 'bg-amber-50 border border-amber-100' : 'bg-gray-50'
                          }`}>
                            {passed === 'true'
                              ? <CheckCircle2 size={10} className="text-green-500 shrink-0" />
                              : <AlertCircle size={10} className="text-amber-500 shrink-0" />}
                            <span className="font-mono truncate flex-1 text-gray-600">{chartId}</span>
                            {score && (
                              <span className={`font-semibold shrink-0 ${
                                Number(score) >= 80 ? 'text-green-600' :
                                Number(score) >= 60 ? 'text-amber-600' : 'text-red-500'
                              }`}>{score}%</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Live log */}
              <div className="card p-4 flex flex-col" style={{ minHeight: 340 }}>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3 shrink-0">Live log</p>
                <div className="flex-1 overflow-y-auto space-y-1 font-mono text-xs pr-1" style={{ maxHeight: 360 }}>
                  {log.map((entry, i) => (
                    <div key={i} className={`flex gap-2 ${
                      entry.level === 'success' ? 'text-green-700' :
                      entry.level === 'warn'    ? 'text-amber-700' :
                      entry.level === 'error'   ? 'text-red-600'   : 'text-gray-600'
                    }`}>
                      <span className="text-gray-300 shrink-0">
                        {new Date(entry.ts).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                      <span className="break-all">{entry.text}</span>
                    </div>
                  ))}
                  {phase === 'processing' && (
                    <div className="flex gap-2 text-gray-400">
                      <span className="text-gray-300">
                        {new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                      <span className="animate-pulse">waiting for next event...</span>
                    </div>
                  )}
                  <div ref={logEndRef} />
                </div>
              </div>
            </div>
          )}

          {/* Done — success card */}
          {phase === 'done' && (
            <div className="card p-5 space-y-4">
              <div className="flex items-center gap-3">
                <CheckCircle2 size={24} className="text-green-500" />
                <div>
                  <p className="font-semibold text-gray-900">Dashboard replicated!</p>
                  <p className="text-sm text-gray-400">
                    {(chartStates.filter(c => c.status === 'confirmed' || c.status === 'low_confidence')).length} of{' '}
                    {Number(screenshotJob?.total_charts) || chartStates.length} charts live
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-3 flex-wrap">
                <button
                  onClick={() => { setPhase('idle'); setFiles([]); setChartStates([]); setJobId(null); setLog([]); setSteps({ upload: 'idle', vision: 'idle', sql: 'idle', execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle' }) }}
                  className="btn-secondary text-sm flex items-center gap-1.5"
                >
                  <RefreshCw size={13} /> Upload another
                </button>
                {resultDashboardId && (
                  <button
                    onClick={() => router.push(`/projects/${projectId}/dashboard`)}
                    className="btn-secondary flex items-center gap-2 text-sm"
                  >
                    <LayoutDashboard size={15} /> Dashboard
                  </button>
                )}
                {resultDashboardId && (
                  <button
                    onClick={() => router.push(`/projects/${projectId}/canvas/${resultDashboardId}`)}
                    className="btn-primary flex items-center gap-2 text-sm"
                  >
                    <Layers size={15} /> Open in Canvas
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Error card */}
          {phase === 'error' && (
            <div className="card p-5 space-y-3">
              <div className="flex items-center gap-3 text-red-600">
                <AlertCircle size={20} />
                <div>
                  <p className="font-semibold text-sm">Replication failed</p>
                  <p className="text-xs text-red-400">{error || 'An unexpected error occurred'}</p>
                </div>
              </div>
              <button
                onClick={() => { setPhase('idle'); setError(null); setLog([]); setSteps({ upload: 'idle', vision: 'idle', sql: 'idle', execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle' }) }}
                className="btn-secondary text-sm"
              >
                Try again
              </button>
            </div>
          )}

          {/* Hints */}
          {phase === 'processing' && !hint && (
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <HelpCircle size={12} />
              If AI needs clarification, a dialog will appear automatically
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
