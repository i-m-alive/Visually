'use client'
import { useState, useEffect, useCallback } from 'react'
import {
  Database, Loader2, TrendingUp, CheckCircle2, AlertCircle,
  RefreshCw, X, BarChart2,
} from 'lucide-react'
import { endUserApi, dashboardApi, projectApi } from '@/lib/api'
import { ConnectionPromptModal } from '@/components/end-user/ConnectionPromptModal'
import { QueryChatPanel } from '@/components/query/QueryChatPanel'

type Step = 'loading' | 'choose' | 'crawling' | 'ready' | 'error'

interface ConnectedReport {
  id: string
  name: string
  project_id: string
  connection_label?: string
}

const STORAGE_KEY = 'eu-query-project'

function loadSaved(): { projectId: string; label: string } | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return null
}

function saveCurrent(projectId: string, label: string) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ projectId, label })) } catch {}
}

function clearSaved() {
  try { localStorage.removeItem(STORAGE_KEY) } catch {}
}

const CRAWL_STAGES = [
  { label: 'Connecting to database',    upTo: 20  },
  { label: 'Reading table schemas',     upTo: 60  },
  { label: 'Extracting metadata',       upTo: 90  },
  { label: 'Preparing query engine',    upTo: 100 },
]

function stageIndex(pct: number): number {
  for (let i = 0; i < CRAWL_STAGES.length; i++) {
    if (pct < CRAWL_STAGES[i].upTo) return i
  }
  return CRAWL_STAGES.length - 1
}

export default function EndUserQueryPage() {
  const [step, setStep]             = useState<Step>('loading')
  const [projectId, setProjectId]   = useState('')
  const [connLabel, setConnLabel]   = useState('')
  const [reports, setReports]       = useState<ConnectedReport[]>([])
  const [errorMsg, setErrorMsg]     = useState('')
  const [showNewConn, setShowNewConn] = useState(false)
  const [crawlPct, setCrawlPct]     = useState(0)
  const [crawlMsg, setCrawlMsg]     = useState('')

  // Restore previously saved session on mount
  useEffect(() => {
    const saved = loadSaved()
    if (saved?.projectId) {
      setProjectId(saved.projectId)
      setConnLabel(saved.label)
      setStep('ready')
    } else {
      loadReports()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadReports = useCallback(async () => {
    setStep('loading')
    try {
      const resp = await dashboardApi.sharedWithMe()
      const list = ((resp.data.dashboards ?? []) as {
        id: string; name: string; project_id: string; connection_label?: string
      }[]).filter(d => d.project_id)
        .map(d => ({ id: d.id, name: d.name, project_id: d.project_id, connection_label: d.connection_label }))
      setReports(list)
    } catch {
      setReports([])
    }
    setStep('choose')
  }, [])

  // Pick an existing report's project — schema already crawled by the builder
  const handlePickReport = (report: ConnectedReport) => {
    const label = report.connection_label || report.name
    setProjectId(report.project_id)
    setConnLabel(label)
    saveCurrent(report.project_id, label)
    setStep('ready')
  }

  // New connection: create → trigger crawl → poll until done → ready
  const handleNewConnection = async (details: {
    db_type: string; host: string; port: string; database_name: string
    username: string; password: string; ssl_enabled?: boolean; iam_role_arn?: string
  }) => {
    setShowNewConn(false)
    setStep('crawling')
    setCrawlPct(5)
    setCrawlMsg('Creating connection…')
    setErrorMsg('')

    try {
      // ── 1. Create the connection (tests SELECT 1 before saving) ──
      const connResp = await endUserApi.createConnection({
        db_type: details.db_type,
        host: details.host,
        port: details.port ? Number(details.port) : undefined,
        database_name: details.database_name,
        username: details.username,
        password: details.password,
        ssl_enabled: details.ssl_enabled,
        iam_role_arn: details.iam_role_arn || undefined,
      })
      const pid = connResp.data.project_id
      setCrawlPct(15)
      setCrawlMsg('Starting schema crawl…')

      // ── 2. Trigger schema crawl — returns a job_id ──
      const crawlResp = await projectApi.triggerCrawl(pid)
      const jobId = (crawlResp.data as { job_id: string }).job_id
      setCrawlPct(25)
      setCrawlMsg('Crawling table schemas…')

      // ── 3. Poll until the crawl finishes (max 3 min = 90 × 2 s) ──
      const MAX_POLLS = 90
      let completed = false
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(r => setTimeout(r, 2000))

        const statusResp = await projectApi.getCrawlStatus(pid, jobId)
        const { status, error } = statusResp.data as { status: string; error?: string | null }

        if (status === 'completed') {
          completed = true
          setCrawlPct(100)
          setCrawlMsg('Schema ready!')
          break
        }
        if (status === 'failed') {
          throw new Error(error || 'Schema crawl failed')
        }

        // Map polling progress (0–MAX_POLLS) linearly into 25–95 %
        const pct = Math.round(25 + ((i + 1) / MAX_POLLS) * 70)
        setCrawlPct(pct)

        // Update stage label as progress advances
        if (pct < 40) setCrawlMsg('Crawling table schemas…')
        else if (pct < 70) setCrawlMsg('Reading column types and relationships…')
        else if (pct < 90) setCrawlMsg('Extracting metadata for query AI…')
        else setCrawlMsg('Finalising…')
      }

      if (!completed) {
        // Timed out — schema might still be crawling in the background; proceed anyway
        setCrawlMsg('Taking a bit longer — continuing…')
      }

      await new Promise(r => setTimeout(r, 500))
      const label = `${details.db_type} · ${details.database_name}`
      setProjectId(pid)
      setConnLabel(label)
      saveCurrent(pid, label)
      setStep('ready')

    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setErrorMsg(e.response?.data?.detail ?? e.message ?? 'Failed to connect. Check your credentials and try again.')
      setStep('error')
    }
  }

  const handleSwitch = () => {
    clearSaved()
    setProjectId('')
    setConnLabel('')
    loadReports()
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  if (step === 'loading') {
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={26} className="animate-spin text-brand" />
          <p className="text-sm text-gray-500">Loading…</p>
        </div>
      </div>
    )
  }

  if (step === 'error') {
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-4 max-w-sm text-center px-6">
          <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
            <AlertCircle size={20} className="text-red-400" />
          </div>
          <p className="text-sm font-medium text-gray-800">{errorMsg}</p>
          <button onClick={() => setStep('choose')} className="flex items-center gap-2 px-4 py-2 text-sm bg-brand text-white rounded-lg hover:bg-brand-dark transition-colors">
            <RefreshCw size={13} /> Try again
          </button>
        </div>
      </div>
    )
  }

  if (step === 'crawling') {
    const activeStage = stageIndex(crawlPct)
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-6 max-w-sm w-full px-6">

          {/* Spinner icon */}
          <div className="relative w-16 h-16">
            <div className="absolute inset-0 rounded-full border-4 border-brand/20" />
            <div className="absolute inset-0 rounded-full border-4 border-brand border-t-transparent animate-spin" />
            <div className="absolute inset-0 flex items-center justify-center">
              <Database size={20} className="text-brand" />
            </div>
          </div>

          {/* Title */}
          <div className="text-center">
            <p className="text-sm font-semibold text-gray-900 mb-1">{crawlMsg}</p>
            <p className="text-xs text-gray-400">This usually takes 20–60 seconds.</p>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-gray-200 rounded-full h-1.5 overflow-hidden">
            <div
              className="bg-brand h-1.5 rounded-full transition-all duration-700"
              style={{ width: `${crawlPct}%` }}
            />
          </div>
          <p className="text-xs text-gray-400 -mt-3">{crawlPct}%</p>

          {/* Stage checklist */}
          <div className="w-full space-y-2.5">
            {CRAWL_STAGES.map(({ label }, i) => {
              const done    = i < activeStage
              const current = i === activeStage
              return (
                <div key={label} className="flex items-center gap-2.5 text-xs">
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 transition-all ${
                    done    ? 'bg-green-100'
                    : current ? 'bg-brand/10'
                    : 'bg-gray-100'
                  }`}>
                    {done
                      ? <CheckCircle2 size={11} className="text-green-500" />
                      : current
                        ? <Loader2 size={10} className="animate-spin text-brand" />
                        : <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                    }
                  </div>
                  <span className={done || current ? 'text-gray-700' : 'text-gray-400'}>{label}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

  if (step === 'ready' && projectId) {
    return (
      <div className="h-full flex flex-col overflow-hidden">
        {/* Status bar */}
        <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-100 flex-shrink-0">
          <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
          <span className="text-xs text-gray-600 truncate flex-1">
            Connected to <span className="font-medium text-gray-900">{connLabel}</span>
          </span>
          <button onClick={handleSwitch} className="text-xs text-brand hover:underline flex-shrink-0 flex items-center gap-1">
            <X size={11} /> Switch
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <QueryChatPanel
            projectId={projectId}
            connectionLabel={connLabel}
            onSwitchConnection={handleSwitch}
          />
        </div>
      </div>
    )
  }

  // ── Step: choose ─────────────────────────────────────────────────────────────
  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-2xl mx-auto px-4 py-10">

        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-brand/10 mb-4">
            <TrendingUp size={26} className="text-brand" />
          </div>
          <h1 className="text-xl font-bold text-gray-900 font-display mb-1">Query Chat</h1>
          <p className="text-sm text-gray-500">Ask questions about your data, generate charts, and explore insights.</p>
        </div>

        {/* Option A: connected report */}
        {reports.length > 0 && (
          <div className="mb-6">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-3">Use a connected report</p>
            <div className="space-y-2">
              {reports.map(r => (
                <button key={r.id} onClick={() => handlePickReport(r)}
                  className="w-full flex items-center gap-3 px-4 py-3.5 bg-white border border-gray-200 rounded-xl hover:border-brand/50 hover:bg-brand/5 transition-all text-left group"
                >
                  <div className="w-9 h-9 rounded-lg bg-brand/10 flex items-center justify-center flex-shrink-0 group-hover:bg-brand/20 transition-colors">
                    <BarChart2 size={16} className="text-brand" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{r.name}</p>
                    {r.connection_label && <p className="text-xs text-gray-400 truncate">{r.connection_label}</p>}
                  </div>
                  <CheckCircle2 size={15} className="text-green-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          </div>
        )}

        {reports.length > 0 && (
          <div className="relative flex items-center mb-6">
            <div className="flex-1 border-t border-gray-200" />
            <span className="px-4 text-xs text-gray-400 bg-gray-50">or connect a new database</span>
            <div className="flex-1 border-t border-gray-200" />
          </div>
        )}

        {/* Option B: new connection */}
        <button onClick={() => setShowNewConn(true)}
          className="w-full flex items-center gap-3 px-4 py-4 bg-white border-2 border-dashed border-gray-200 rounded-xl hover:border-brand/50 hover:bg-brand/5 transition-all group"
        >
          <div className="w-9 h-9 rounded-lg bg-gray-100 flex items-center justify-center flex-shrink-0 group-hover:bg-brand/10 transition-colors">
            <Database size={16} className="text-gray-400 group-hover:text-brand transition-colors" />
          </div>
          <div className="flex-1 text-left">
            <p className="text-sm font-medium text-gray-700 group-hover:text-brand transition-colors">Connect a new database</p>
            <p className="text-xs text-gray-400">PostgreSQL, MySQL, BigQuery, Snowflake, Redshift, and more</p>
          </div>
        </button>

        <p className="text-center text-xs text-gray-400 mt-6">
          Credentials are encrypted and never shared with other users.
        </p>
      </div>

      {showNewConn && (
        <ConnectionPromptModal
          fileName="Query Chat"
          onConnect={handleNewConnection}
          onClose={() => setShowNewConn(false)}
        />
      )}
    </div>
  )
}
