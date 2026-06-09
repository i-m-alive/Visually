'use client'
import { useState, useEffect, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Camera, Loader2, AlertCircle, CheckCircle2, ArrowRight,
  LayoutDashboard, Eye, Database, Zap, ShieldCheck, Layers,
  HelpCircle, RefreshCw, Clock, ScanSearch, FileText, Calendar,
  ChevronDown, ChevronRight, Upload,
} from 'lucide-react'
import { screenshotApi, canvasApi, projectApi } from '@/lib/api'
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

interface FilterConfig {
  id: string
  column: string
  display_name: string
  filter_type: string
  available_values: string[]
  table: string
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
    case 'context.parsed': {
      const intent = event.chart_intent as string | undefined
      const filters = event.filter_count as number | undefined
      const hasDate = event.has_date_range as boolean | undefined
      return {
        ts, level: 'success',
        text: `✓ Context parsed${intent ? `: "${intent}"` : ''}${filters ? ` · ${filters} filter${filters !== 1 ? 's' : ''}` : ''}${hasDate ? ' · date range detected' : ''}`,
      }
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
  const [dashboardName, setDashboardName] = useState('')
  const [renamingSaving, setRenamingSaving] = useState(false)
  const [nameSaved, setNameSaved] = useState(false)
  const [tokenUsage, setTokenUsage] = useState<Record<string, { input_tokens: number; output_tokens: number; calls: number }> | null>(null)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing' | 'done' | 'error'>('idle')
  const [log, setLog] = useState<LogEntry[]>([])
  const [steps, setSteps] = useState<Record<StepKey, StepStatus>>({
    upload: 'idle', vision: 'idle', sql: 'idle',
    execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle',
  })
  // Mode 1 / Mode 2 / Mode 3 / CSV / PBIT state
  const [uiMode, setUiMode] = useState<'db' | 'db_hint' | 'context' | 'csv' | 'pbit'>('db')
  const [csvFiles, setCsvFiles] = useState<File[]>([])
  const [tableHints, setTableHints] = useState<string[]>([])
  const [allTables, setAllTables] = useState<Array<{ qualified: string; name: string; columns: string[] }>>([])
  const [tableSearch, setTableSearch] = useState('')
  const [loadingTables, setLoadingTables] = useState(false)
  const [userContext, setUserContext] = useState('')
  const [contextFiles, setContextFiles] = useState<File[]>([])
  // PBIT mode state
  const [pbitFile, setPbitFile] = useState<File | null>(null)
  const [columnHints, setColumnHints] = useState<Record<string, { dimension: string; metric: string; date: string; group_by: string }>>({})
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  // Date filter state (for done card)
  const [resultDateFilters, setResultDateFilters] = useState<FilterConfig[]>([])
  const [resultDateRange, setResultDateRange] = useState<Record<string, { start: string; end: string }>>({})
  const [applyingDate, setApplyingDate] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const processedEventCount = useRef(0)

  const jobs = usePipelineStore((s) => s.jobs)
  usePipelineSocket(jobId)

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [log])

  // React to pipeline WebSocket events — processes ALL new events since last render,
  // never regresses a step from 'done' back to 'active' (parallel charts cause interleaved events).
  useEffect(() => {
    if (!jobId || !jobs[jobId]) return
    const allEvents = jobs[jobId].events as Record<string, unknown>[]
    if (!allEvents?.length) return

    // Process only newly arrived events since the last render
    const newEvents = allEvents.slice(processedEventCount.current)
    if (!newEvents.length) return
    processedEventCount.current = allEvents.length

    // Accumulate log entries and step updates across all new events
    const newLogEntries: LogEntry[] = []
    const aggregatedStepUpdate: Partial<Record<StepKey, StepStatus>> = {}
    let assembledEvent: Record<string, unknown> | null = null
    let errorEvent: Record<string, unknown> | null = null
    let hintEvent: Record<string, unknown> | null = null

    for (const event of newEvents) {
      const type = event.type as string

      const entry = eventToLog(event)
      if (entry) newLogEntries.push(entry)

      // Accumulate step updates — later events win within a batch
      const stepUpdate = eventToStepUpdate(type)
      Object.assign(aggregatedStepUpdate, stepUpdate)

      if (type === 'hint.requested' && !hint) hintEvent = event
      if (type === 'dashboard.assembled') assembledEvent = event
      if (type === 'pipeline.error') errorEvent = event
    }

    if (newLogEntries.length) setLog(prev => [...prev, ...newLogEntries])

    if (Object.keys(aggregatedStepUpdate).length) {
      setSteps(prev => {
        const next = { ...prev }
        for (const [k, v] of Object.entries(aggregatedStepUpdate) as [StepKey, StepStatus][]) {
          // Never regress: don't go from 'done' back to 'active'.
          // Parallel chart events arrive out of order and would otherwise keep resetting indicators.
          if (prev[k] !== 'done' && prev[k] !== 'error') next[k] = v
        }
        return next
      })
    }

    if (hintEvent) {
      try { setHint(hintEvent as HintRequest) } catch {}
    }

    if (assembledEvent) {
      setSteps({ upload: 'done', vision: 'done', sql: 'done', execute: 'done', validate: 'done', verify: 'done', assemble: 'done' })
      if (assembledEvent.token_usage && typeof assembledEvent.token_usage === 'object') {
        setTokenUsage(assembledEvent.token_usage as Record<string, { input_tokens: number; output_tokens: number; calls: number }>)
      }
      setPhase('done')
    }

    if (errorEvent) {
      setError(errorEvent.message as string)
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

  const resultDashboardId = screenshotJob?.result_dashboard_id as string | undefined

  // When the pipeline completes, load the new dashboard to discover date filter columns
  useEffect(() => {
    if (!resultDashboardId) return
    canvasApi.get(resultDashboardId).then(resp => {
      const data = resp.data as {
        filter_config?: FilterConfig[]
        widgets?: Array<{ config?: { date_filter?: { column: string; start: string; end: string | null } } }>
      }
      const dateFCs = (data.filter_config || []).filter(fc => fc.filter_type === 'date_range')
      setResultDateFilters(dateFCs)
      const seed: Record<string, { start: string; end: string }> = {}
      for (const w of data.widgets || []) {
        const df = w.config?.date_filter
        if (df?.column && df.start && !seed[df.column]) {
          seed[df.column] = { start: df.start, end: df.end ?? df.start }
        }
      }
      setResultDateRange(seed)
    }).catch(() => {})
  }, [resultDashboardId])

  const handleModeChange = async (newMode: 'db' | 'db_hint' | 'context' | 'csv' | 'pbit') => {
    setUiMode(newMode)
    // Table Hint, Guided, and PBIT modes all show the table picker — load schema once
    if ((newMode === 'db_hint' || newMode === 'context' || newMode === 'pbit') && allTables.length === 0) {
      setLoadingTables(true)
      try {
        const resp = await projectApi.getSchema(projectId)
        const rawTables: Array<{ name: string; schema?: string; columns?: Array<{ name: string }> }> = resp.data?.schema?.tables || []
        setAllTables(
          rawTables.map(t => ({
            qualified: t.schema ? `${t.schema}.${t.name}` : t.name,
            name: t.name,
            columns: (t.columns || []).map((c: { name: string }) => c.name),
          }))
        )
      } catch {}
      setLoadingTables(false)
    }
  }

  const handleUpload = async () => {
    if (!files.length) return
    if (uiMode === 'csv' && csvFiles.length === 0) {
      setError('Please add at least one CSV file before uploading')
      return
    }
    if (uiMode === 'context' && !userContext.trim() && contextFiles.length === 0) {
      setError('Please describe what the screenshot shows or upload a context document (Guided mode requires at least one)')
      return
    }
    if (uiMode === 'pbit' && !pbitFile && tableHints.length === 0) {
      setError('Please upload a PBIT file or select at least one table in PBIT mode')
      return
    }
    setUploading(true)
    setError(null)
    setPhase('uploading')
    const csvNote = uiMode === 'csv' ? ` + ${csvFiles.length} CSV file${csvFiles.length !== 1 ? 's' : ''}` : ''
    const modeNote = uiMode === 'context' ? ' [Guided mode — context + AI]'
      : uiMode === 'db_hint' ? ' [Table Hint mode]'
      : uiMode === 'pbit' ? ' [PBIT mode — Power BI ground truth]'
      : ''
    setLog([{ ts: Date.now(), level: 'info', text: `📤 Uploading ${files.length} screenshot${files.length !== 1 ? 's' : ''}${csvNote}${modeNote}...` }])
    setSteps({ upload: 'active', vision: 'idle', sql: 'idle', execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle' })

    // Build user_column_hints from PBIT mode column selections
    const builtColumnHints = uiMode === 'pbit'
      ? Object.entries(columnHints)
          .filter(([table]) => tableHints.includes(table))
          .map(([table, cols]) => ({ table, ...cols }))
          .filter(h => h.dimension || h.metric || h.date || h.group_by)
      : undefined

    try {
      const resp = await screenshotApi.upload({
        projectId,
        files,
        mode: uiMode === 'csv' ? 'csv' : 'db',
        userTableHints: (uiMode === 'db_hint' || uiMode === 'context' || uiMode === 'pbit') && tableHints.length > 0 ? tableHints : undefined,
        csvFiles: uiMode === 'csv' ? csvFiles : undefined,
        userContext: uiMode === 'context' && userContext.trim() ? userContext.trim() : undefined,
        contextFiles: uiMode === 'context' && contextFiles.length > 0 ? contextFiles : undefined,
        pbitFile: uiMode === 'pbit' && pbitFile ? pbitFile : undefined,
        userColumnHints: builtColumnHints?.length ? builtColumnHints : undefined,
      })
      processedEventCount.current = 0
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

              {/* Mode selector */}
              <div className="flex gap-1 p-1 bg-gray-100 rounded-xl">
                {(
                  [
                    { id: 'db'      as const, label: 'Auto' },
                    { id: 'db_hint' as const, label: 'Tables' },
                    { id: 'context' as const, label: 'Guided' },
                    { id: 'pbit'    as const, label: 'PBIT' },
                    { id: 'csv'     as const, label: 'CSV' },
                  ]
                ).map(m => (
                  <button
                    key={m.id}
                    onClick={() => handleModeChange(m.id)}
                    disabled={uploading}
                    className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all disabled:opacity-50 ${
                      uiMode === m.id
                        ? 'bg-white text-gray-900 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>

              {/* Mode description */}
              <p className="text-xs text-gray-400">
                {uiMode === 'db'      && 'AI automatically matches your charts to the connected database tables.'}
                {uiMode === 'db_hint' && 'You specify which database tables the screenshot uses — bypasses the automatic schema matcher.'}
                {uiMode === 'context' && 'Describe what the screenshot shows and optionally pick tables — AI uses your context to generate more accurate SQL.'}
                {uiMode === 'pbit'    && 'Upload a Power BI PBIT file — provides exact field bindings, DAX measures, and relationships for ground-truth replication.'}
                {uiMode === 'csv'     && 'Upload CSV files as the data source. No database connection required.'}
              </p>

              {/* Screenshot dropzone (shown for all modes) */}
              <UploadDropzone onFilesSelected={setFiles} disabled={uploading} />

              {/* Table Hint mode — searchable multi-select */}
              {uiMode === 'db_hint' && (
                <div className="border border-gray-200 rounded-xl p-3 space-y-2">
                  <p className="text-xs font-semibold text-gray-600">Select tables this screenshot uses</p>
                  {loadingTables ? (
                    <div className="flex items-center gap-2 text-xs text-gray-400">
                      <Loader2 size={12} className="animate-spin" /> Loading schema…
                    </div>
                  ) : allTables.length === 0 ? (
                    <p className="text-xs text-amber-500">
                      No schema found — crawl the schema first from the project Schema page.
                    </p>
                  ) : (
                    <>
                      <input
                        type="text"
                        placeholder="Search tables…"
                        value={tableSearch}
                        onChange={e => setTableSearch(e.target.value)}
                        className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-brand"
                      />
                      <div className="max-h-36 overflow-y-auto space-y-0.5 pr-1">
                        {allTables
                          .filter(t => t.qualified.toLowerCase().includes(tableSearch.toLowerCase()))
                          .slice(0, 50)
                          .map(t => (
                            <label
                              key={t.qualified}
                              className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-50 cursor-pointer"
                            >
                              <input
                                type="checkbox"
                                checked={tableHints.includes(t.qualified)}
                                onChange={e => {
                                  if (e.target.checked) {
                                    setTableHints(prev => [...prev, t.qualified])
                                  } else {
                                    setTableHints(prev => prev.filter(x => x !== t.qualified))
                                  }
                                }}
                                className="accent-brand"
                              />
                              <span className="font-mono text-xs text-gray-700 truncate">{t.qualified}</span>
                            </label>
                          ))}
                      </div>
                      {tableHints.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-1">
                          {tableHints.map(t => (
                            <span
                              key={t}
                              className="flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 text-xs rounded-full"
                            >
                              <Database size={9} />
                              <span className="max-w-[120px] truncate">{t.split('.').pop()}</span>
                              <button
                                onClick={() => setTableHints(prev => prev.filter(x => x !== t))}
                                className="hover:text-blue-500 ml-0.5"
                              >
                                ×
                              </button>
                            </span>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* Guided mode (Mode 3) — context textarea + optional table picker */}
              {uiMode === 'context' && (
                <div className="border border-blue-200 rounded-xl p-3 space-y-3">
                  {/* Context textarea */}
                  <div className="space-y-1">
                    <p className="text-xs font-semibold text-gray-600">
                      Describe what the screenshot shows
                      <span className="ml-1 text-gray-400 font-normal">(optional if uploading a doc)</span>
                    </p>
                    <textarea
                      placeholder={`e.g. "Active placements by employment type for Q1 2024. Only open job orders."`}
                      value={userContext}
                      onChange={e => setUserContext(e.target.value)}
                      rows={3}
                      disabled={uploading}
                      className="w-full text-xs border border-gray-200 rounded-lg px-3 py-2 outline-none focus:border-blue-400 resize-none disabled:opacity-50 placeholder-gray-300"
                    />
                    {userContext.trim().length > 0 && (
                      <p className="text-xs text-blue-500">
                        ✓ {userContext.trim().length} chars — AI will parse filters, date ranges, and aggregation hints
                      </p>
                    )}
                  </div>

                  {/* Context document upload */}
                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold text-gray-600">
                      Or upload a context document
                      <span className="ml-1 font-normal text-gray-400">(PDF, DOCX, PPTX, TXT — up to 3 files)</span>
                    </p>
                    <label className={`flex items-center justify-center gap-2 border-2 border-dashed rounded-xl py-3 px-3 cursor-pointer transition-colors ${uploading ? 'opacity-50 cursor-not-allowed' : 'border-gray-200 hover:border-blue-400 hover:bg-blue-50/30'}`}>
                      <FileText size={14} className="text-gray-400 shrink-0" />
                      <span className="text-xs text-gray-500">
                        {contextFiles.length > 0
                          ? `${contextFiles.length} file${contextFiles.length !== 1 ? 's' : ''} selected — click to add more`
                          : 'Click or drag PDF / DOCX / PPTX / TXT here'}
                      </span>
                      <input
                        type="file"
                        accept=".pdf,.docx,.doc,.pptx,.ppt,.txt"
                        multiple
                        disabled={uploading}
                        className="hidden"
                        onChange={e => {
                          const chosen = Array.from(e.target.files || [])
                          setContextFiles(prev => {
                            const existing = new Set(prev.map(f => f.name + f.size))
                            const fresh = chosen.filter(f => !existing.has(f.name + f.size))
                            return [...prev, ...fresh].slice(0, 3)
                          })
                          e.target.value = ''
                        }}
                      />
                    </label>
                    {contextFiles.length > 0 && (
                      <div className="flex flex-wrap gap-1 pt-0.5">
                        {contextFiles.map((f, i) => (
                          <span
                            key={f.name + i}
                            className="flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 text-xs rounded-full max-w-[180px]"
                          >
                            <FileText size={9} className="shrink-0" />
                            <span className="truncate">{f.name}</span>
                            <button
                              onClick={() => setContextFiles(prev => prev.filter((_, idx) => idx !== i))}
                              className="hover:text-indigo-500 ml-0.5 shrink-0"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Optional table picker */}
                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold text-gray-600">
                      Tables to use <span className="font-normal text-gray-400">(optional)</span>
                    </p>
                    {loadingTables ? (
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <Loader2 size={12} className="animate-spin" /> Loading schema…
                      </div>
                    ) : allTables.length === 0 ? (
                      <p className="text-xs text-amber-500">
                        No schema found — crawl the schema first from the project Schema page.
                      </p>
                    ) : (
                      <>
                        <input
                          type="text"
                          placeholder="Search tables…"
                          value={tableSearch}
                          onChange={e => setTableSearch(e.target.value)}
                          className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-blue-400"
                        />
                        <div className="max-h-28 overflow-y-auto space-y-0.5 pr-1">
                          {allTables
                            .filter(t => t.qualified.toLowerCase().includes(tableSearch.toLowerCase()))
                            .slice(0, 50)
                            .map(t => (
                              <label
                                key={t.qualified}
                                className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-50 cursor-pointer"
                              >
                                <input
                                  type="checkbox"
                                  checked={tableHints.includes(t.qualified)}
                                  onChange={e => {
                                    if (e.target.checked) {
                                      setTableHints(prev => [...prev, t.qualified])
                                    } else {
                                      setTableHints(prev => prev.filter(x => x !== t.qualified))
                                    }
                                  }}
                                  className="accent-brand"
                                />
                                <span className="font-mono text-xs text-gray-700 truncate">{t.qualified}</span>
                              </label>
                            ))}
                        </div>
                        {tableHints.length > 0 && (
                          <div className="flex flex-wrap gap-1 pt-0.5">
                            {tableHints.map(t => (
                              <span
                                key={t}
                                className="flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 text-xs rounded-full"
                              >
                                <Database size={9} />
                                <span className="max-w-[120px] truncate">{t.split('.').pop()}</span>
                                <button
                                  onClick={() => setTableHints(prev => prev.filter(x => x !== t))}
                                  className="hover:text-blue-500 ml-0.5"
                                >
                                  ×
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )}

              {/* PBIT mode — Power BI Template + table/column selector */}
              {uiMode === 'pbit' && (
                <div className="border border-purple-200 rounded-xl p-3 space-y-3">

                  {/* PBIT file upload */}
                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold text-gray-600">
                      Upload Power BI Template
                      <span className="ml-1 font-normal text-gray-400">(.pbit file)</span>
                    </p>
                    <label className={`flex items-center justify-center gap-2 border-2 border-dashed rounded-xl py-3 px-3 cursor-pointer transition-colors ${uploading ? 'opacity-50 cursor-not-allowed' : pbitFile ? 'border-purple-400 bg-purple-50/40' : 'border-gray-200 hover:border-purple-400 hover:bg-purple-50/30'}`}>
                      <Upload size={14} className={pbitFile ? 'text-purple-500' : 'text-gray-400'} />
                      <span className="text-xs text-gray-500">
                        {pbitFile ? pbitFile.name : 'Click or drag .pbit file here'}
                      </span>
                      <input
                        type="file"
                        accept=".pbit"
                        disabled={uploading}
                        className="hidden"
                        onChange={e => {
                          const f = e.target.files?.[0]
                          if (f) setPbitFile(f)
                          e.target.value = ''
                        }}
                      />
                    </label>
                    {pbitFile && (
                      <div className="flex items-center gap-2 px-2 py-1 bg-purple-50 rounded-lg">
                        <span className="text-xs text-purple-700 flex-1 truncate">{pbitFile.name}</span>
                        <span className="text-xs text-purple-400">{(pbitFile.size / 1024 / 1024).toFixed(1)} MB</span>
                        <button onClick={() => setPbitFile(null)} className="text-purple-300 hover:text-purple-500 text-sm leading-none">×</button>
                      </div>
                    )}
                    <p className="text-xs text-gray-400">The PBIT file provides exact columns, DAX measures, and table relationships — highest accuracy mode.</p>
                  </div>

                  {/* Table picker with per-table column selection */}
                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold text-gray-600">
                      Select tables &amp; specify columns
                      <span className="ml-1 font-normal text-gray-400">(optional — AI will infer if not set)</span>
                    </p>
                    {loadingTables ? (
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <Loader2 size={12} className="animate-spin" /> Loading schema…
                      </div>
                    ) : allTables.length === 0 ? (
                      <p className="text-xs text-amber-500">No schema found — crawl the schema first.</p>
                    ) : (
                      <>
                        <input
                          type="text"
                          placeholder="Search tables…"
                          value={tableSearch}
                          onChange={e => setTableSearch(e.target.value)}
                          className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-purple-400"
                        />
                        <div className="max-h-64 overflow-y-auto space-y-1 pr-1">
                          {allTables
                            .filter(t => t.qualified.toLowerCase().includes(tableSearch.toLowerCase()))
                            .slice(0, 50)
                            .map(t => {
                              const isChecked = tableHints.includes(t.qualified)
                              const isExpanded = expandedTables.has(t.qualified)
                              const cols = t.columns || []
                              const hint = columnHints[t.qualified] || { dimension: '', metric: '', date: '', group_by: '' }
                              return (
                                <div key={t.qualified} className={`rounded-lg border transition-colors ${isChecked ? 'border-purple-200 bg-purple-50/30' : 'border-transparent'}`}>
                                  <label className="flex items-center gap-2 px-2 py-1.5 cursor-pointer">
                                    <input
                                      type="checkbox"
                                      checked={isChecked}
                                      onChange={e => {
                                        if (e.target.checked) {
                                          setTableHints(prev => [...prev, t.qualified])
                                          setExpandedTables(prev => new Set(Array.from(prev).concat(t.qualified)))
                                        } else {
                                          setTableHints(prev => prev.filter(x => x !== t.qualified))
                                          setExpandedTables(prev => { const s = new Set(prev); s.delete(t.qualified); return s })
                                        }
                                      }}
                                      className="accent-purple-600 shrink-0"
                                    />
                                    <Database size={10} className="text-gray-400 shrink-0" />
                                    <span className="font-mono text-xs text-gray-700 truncate flex-1">{t.qualified}</span>
                                    {isChecked && cols.length > 0 && (
                                      <button
                                        type="button"
                                        onClick={e => { e.preventDefault(); setExpandedTables(prev => { const s = new Set(prev); isExpanded ? s.delete(t.qualified) : s.add(t.qualified); return s }) }}
                                        className="text-gray-400 hover:text-purple-600 shrink-0"
                                      >
                                        {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                      </button>
                                    )}
                                  </label>

                                  {/* Column selectors — shown when table is checked and expanded */}
                                  {isChecked && isExpanded && cols.length > 0 && (
                                    <div className="px-6 pb-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
                                      {(
                                        [
                                          { key: 'dimension' as const, label: 'Dimension', placeholder: 'Group-by column (e.g. employment_type)' },
                                          { key: 'metric'    as const, label: 'Metric',    placeholder: 'Count/sum column (e.g. id)' },
                                          { key: 'date'      as const, label: 'Date',       placeholder: 'Date column (e.g. date_added)' },
                                          { key: 'group_by'  as const, label: 'Series',     placeholder: 'Legend/series column (optional)' },
                                        ]
                                      ).map(f => (
                                        <div key={f.key} className="space-y-0.5">
                                          <p className="text-xs text-gray-500">{f.label}</p>
                                          <select
                                            value={hint[f.key]}
                                            onChange={e => setColumnHints(prev => ({
                                              ...prev,
                                              [t.qualified]: { ...hint, [f.key]: e.target.value }
                                            }))}
                                            className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1 outline-none focus:border-purple-400 bg-white"
                                          >
                                            <option value="">— not set —</option>
                                            {cols.map(c => (
                                              <option key={c} value={c}>{c}</option>
                                            ))}
                                          </select>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                        </div>
                        {tableHints.length > 0 && (
                          <div className="flex flex-wrap gap-1 pt-1">
                            {tableHints.map(t => {
                              const h = columnHints[t]
                              const setCols = [h?.dimension, h?.metric, h?.date, h?.group_by].filter(Boolean)
                              return (
                                <span key={t} className="flex items-center gap-1 px-2 py-0.5 bg-purple-50 text-purple-700 text-xs rounded-full">
                                  <Database size={9} />
                                  <span className="max-w-[100px] truncate">{t.split('.').pop()}</span>
                                  {setCols.length > 0 && <span className="text-purple-400">·{setCols.length}col</span>}
                                  <button onClick={() => { setTableHints(prev => prev.filter(x => x !== t)); setExpandedTables(prev => { const s = new Set(prev); s.delete(t); return s }) }} className="hover:text-purple-500 ml-0.5">×</button>
                                </span>
                              )
                            })}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )}

              {/* CSV Upload mode — separate CSV dropzone */}
              {uiMode === 'csv' && (
                <div className="border border-gray-200 rounded-xl p-3 space-y-2">
                  <p className="text-xs font-semibold text-gray-600">Upload CSV data files</p>
                  <label className="flex items-center justify-center gap-2 border-2 border-dashed border-gray-200 rounded-xl py-4 px-3 cursor-pointer hover:border-blue-400 hover:bg-blue-50/30 transition-colors">
                    <FileText size={16} className="text-gray-400" />
                    <span className="text-xs text-gray-500">
                      {csvFiles.length > 0
                        ? `${csvFiles.length} file${csvFiles.length !== 1 ? 's' : ''} selected — click to add more`
                        : 'Click or drag CSV files here'}
                    </span>
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      multiple
                      className="hidden"
                      onChange={e => {
                        const chosen = Array.from(e.target.files || [])
                        setCsvFiles(prev => {
                          const existing = new Set(prev.map(f => f.name))
                          return [...prev, ...chosen.filter(f => !existing.has(f.name))]
                        })
                        e.target.value = ''
                      }}
                    />
                  </label>
                  {csvFiles.length > 0 && (
                    <div className="space-y-0.5">
                      {csvFiles.map((f, i) => (
                        <div key={i} className="flex items-center justify-between px-2 py-1 bg-gray-50 rounded-lg">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <FileText size={11} className="text-gray-400 shrink-0" />
                            <span className="text-xs font-mono text-gray-700 truncate">{f.name}</span>
                            <span className="text-xs text-gray-400 shrink-0">
                              ({(f.size / 1024 / 1024).toFixed(1)} MB)
                            </span>
                          </div>
                          <button
                            onClick={() => setCsvFiles(prev => prev.filter((_, idx) => idx !== i))}
                            className="text-gray-300 hover:text-red-400 ml-2 shrink-0 text-sm leading-none"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-gray-400">Max 50 MB each · up to 10 files · table names = filenames without extension</p>
                </div>
              )}

              {error && (
                <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-xl">
                  <AlertCircle size={14} /> {error}
                </div>
              )}
              <div className="flex justify-between items-center">
                <p className="text-xs text-gray-400">
                  {uiMode === 'csv'
                    ? `${files.length || 'No'} screenshot${files.length !== 1 ? 's' : ''} · ${csvFiles.length || 'no'} CSV file${csvFiles.length !== 1 ? 's' : ''}`
                    : 'PNG / JPEG / WebP · max 20 MB · up to 5 files'
                  }
                </p>
                <button
                  onClick={handleUpload}
                  disabled={!files.length || uploading || (uiMode === 'csv' && csvFiles.length === 0) || (uiMode === 'context' && !userContext.trim() && contextFiles.length === 0)}
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
              {/* Canvas rename — required before navigation */}
              {resultDashboardId && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <label className="text-xs text-gray-500 font-semibold shrink-0">
                      Canvas name <span className="text-red-400">*</span>
                    </label>
                    {nameSaved && (
                      <span className="text-xs text-green-600 flex items-center gap-1">
                        <CheckCircle2 size={11} /> Saved
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      autoFocus
                      value={dashboardName}
                      onChange={e => { setDashboardName(e.target.value); setNameSaved(false) }}
                      placeholder="e.g. Q2 Insurance Report"
                      className="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-1.5 outline-none focus:border-blue-400"
                      onKeyDown={async e => {
                        if (e.key === 'Enter' && dashboardName.trim() && resultDashboardId) {
                          setRenamingSaving(true)
                          try { await canvasApi.rename(resultDashboardId, dashboardName.trim()); setNameSaved(true) } catch {}
                          setRenamingSaving(false)
                        }
                      }}
                    />
                    <button
                      disabled={!dashboardName.trim() || renamingSaving}
                      onClick={async () => {
                        if (!dashboardName.trim() || !resultDashboardId) return
                        setRenamingSaving(true)
                        try { await canvasApi.rename(resultDashboardId, dashboardName.trim()); setNameSaved(true) } catch {}
                        setRenamingSaving(false)
                      }}
                      className="btn-secondary text-xs px-3 py-1.5 flex items-center gap-1 disabled:opacity-40"
                    >
                      {renamingSaving ? <Loader2 size={11} className="animate-spin" /> : null}
                      Save
                    </button>
                  </div>
                  {!nameSaved && (
                    <p className="text-xs text-amber-500">Save a canvas name to continue to dashboard or canvas view.</p>
                  )}
                </div>
              )}

              {/* Date filter — visible when the new dashboard has date-range charts */}
              {resultDateFilters.length > 0 && resultDashboardId && (
                <div className="border border-blue-100 rounded-xl p-3 bg-blue-50/40 space-y-3">
                  <div className="flex items-center gap-2">
                    <Calendar size={13} className="text-blue-600" />
                    <p className="text-xs font-semibold text-blue-700">Date filter</p>
                    <span className="text-xs text-blue-400">— adjust the date range and apply to all charts</span>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    {resultDateFilters.map(fc => (
                      <div key={fc.column} className="flex items-center gap-1.5">
                        <span className="text-xs font-medium text-gray-600">{fc.display_name}</span>
                        <input
                          type="date"
                          value={resultDateRange[fc.column]?.start || ''}
                          onChange={e =>
                            setResultDateRange(prev => ({
                              ...prev,
                              [fc.column]: { end: prev[fc.column]?.end || '', ...prev[fc.column], start: e.target.value },
                            }))
                          }
                          className="text-xs border border-blue-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
                        />
                        <span className="text-xs text-gray-400">→</span>
                        <input
                          type="date"
                          value={resultDateRange[fc.column]?.end || ''}
                          onChange={e =>
                            setResultDateRange(prev => ({
                              ...prev,
                              [fc.column]: { start: prev[fc.column]?.start || '', ...prev[fc.column], end: e.target.value },
                            }))
                          }
                          className="text-xs border border-blue-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
                        />
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={async () => {
                      const active = Object.fromEntries(
                        Object.entries(resultDateRange).filter(([, v]) => v.start && v.end)
                      )
                      if (!Object.keys(active).length || !resultDashboardId) return
                      setApplyingDate(true)
                      try {
                        await canvasApi.requery(resultDashboardId, active)
                      } catch {}
                      setApplyingDate(false)
                    }}
                    disabled={applyingDate}
                    className="flex items-center gap-1.5 px-3 py-1 text-xs font-semibold text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
                  >
                    {applyingDate && <Loader2 size={11} className="animate-spin" />}
                    Apply to all charts
                  </button>
                </div>
              )}

              {/* Token usage summary */}
              {tokenUsage && Object.keys(tokenUsage).length > 0 && (
                <div className="border border-gray-100 rounded-lg p-3 bg-gray-50">
                  <p className="text-xs font-semibold text-gray-400 mb-2 uppercase tracking-wide">AI Token Usage</p>
                  <div className="space-y-1">
                    {Object.entries(tokenUsage).map(([model, stats]) => (
                      <div key={model} className="flex items-center justify-between text-xs">
                        <span className="text-gray-500 font-mono truncate max-w-[55%]">
                          {model.split('.').slice(-2).join('.')}
                        </span>
                        <span className="text-gray-400 shrink-0">
                          ↑ {stats.input_tokens.toLocaleString()} in · {stats.output_tokens.toLocaleString()} out
                          <span className="text-gray-300 ml-1">({stats.calls}x)</span>
                        </span>
                      </div>
                    ))}
                    <div className="border-t border-gray-200 pt-1 mt-1 flex justify-between text-xs font-semibold">
                      <span className="text-gray-500">Total</span>
                      <span className="text-gray-600">
                        ↑ {Object.values(tokenUsage).reduce((s, v) => s + v.input_tokens, 0).toLocaleString()} ·{' '}
                        {Object.values(tokenUsage).reduce((s, v) => s + v.output_tokens, 0).toLocaleString()} out
                      </span>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex justify-end gap-3 flex-wrap">
                <button
                  onClick={() => { processedEventCount.current = 0; setPhase('idle'); setFiles([]); setCsvFiles([]); setTableHints([]); setChartStates([]); setJobId(null); setLog([]); setNameSaved(false); setTokenUsage(null); setSteps({ upload: 'idle', vision: 'idle', sql: 'idle', execute: 'idle', validate: 'idle', verify: 'idle', assemble: 'idle' }) }}
                  className="btn-secondary text-sm flex items-center gap-1.5"
                >
                  <RefreshCw size={13} /> Upload another
                </button>
                {resultDashboardId && (
                  <button
                    disabled={!nameSaved}
                    onClick={() => router.push(`/projects/${projectId}/dashboard`)}
                    className="btn-secondary flex items-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed"
                    title={!nameSaved ? 'Save a canvas name first' : undefined}
                  >
                    <LayoutDashboard size={15} /> Dashboard
                  </button>
                )}
                {resultDashboardId && (
                  <button
                    disabled={!nameSaved}
                    onClick={() => router.push(`/projects/${projectId}/canvas/${resultDashboardId}`)}
                    className="btn-primary flex items-center gap-2 text-sm disabled:opacity-40 disabled:cursor-not-allowed"
                    title={!nameSaved ? 'Save a canvas name first' : undefined}
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
