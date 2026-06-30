'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import {
  RefreshCw, Loader2, AlertCircle, Sparkles, ArrowLeft,
  Download, Bookmark, Calendar, Terminal, MessageSquare, Database,
  FileDown, TrendingUp, X, CheckCircle2, Link2, Plus,
} from 'lucide-react'
import { api, publicCanvasApi, analystApi, vlyApi, endUserApi, dashboardApi, projectApi } from '@/lib/api'
import { QueryChatPanel } from '@/components/query/QueryChatPanel'
import type { FilterItem, AnnotationData } from '@/lib/api'
import { ConnectionPromptModal } from '@/components/end-user/ConnectionPromptModal'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'
import { FilterBar } from '@/components/analyst/FilterBar'
import { SchemaSidebar } from '@/components/analyst/SchemaSidebar'
import { ChatSidebar } from '@/components/analyst/ChatSidebar'
import SlicerWidget from '@/components/canvas/SlicerWidget'
import { DrilldownModal } from '@/components/analyst/DrilldownModal'
import { QueryModal } from '@/components/analyst/QueryModal'
import { BookmarkModal } from '@/components/analyst/BookmarkModal'
import { ScheduleModal } from '@/components/analyst/ScheduleModal'
import { AnnotationLayer } from '@/components/analyst/AnnotationLayer'

const ResponsiveGrid = WidthProvider(Responsive)

interface WidgetData {
  id: string
  title: string
  chart_type: string
  position_x: number
  position_y: number
  width: number
  height: number
  config: Record<string, unknown>
  chart_data: { rows: unknown[]; columns: string[] }
  filterable_columns: string[]
}

interface CanvasData {
  id: string
  name: string
  theme: string
  layout_config: Record<string, unknown>
  pages: { id: string; name: string; order: number }[]
  filter_config: unknown[]
  share_mode: string
  widgets: WidgetData[]
}

interface DrilldownState {
  widgetId: string
  widgetTitle: string
  xColumn: string
  xValue: string
}

function widgetToResult(w: WidgetData, override?: { rows: unknown[]; columns: string[] }): ChartResult {
  const cd = override ?? (w.chart_data || { rows: [], columns: [] })
  const rows = (cd.rows as Record<string, unknown>[]) || []
  const cols = cd.columns || []
  const labels = rows.map(r => String(r[cols[0]] ?? ''))
  const values = rows.map(r => Number(r[cols[1]] ?? 0))
  return {
    chart_type: w.chart_type,
    title: w.title,
    sql: '',
    score: 1,
    low_confidence: false,
    x_axis_label: cols[0] || 'x',
    y_axis_label: cols[1] || 'y',
    table_used: '',
    chart_data: { rows, columns: cols, labels, values },
    column_labels: w.config?.column_labels as Record<string, string> | undefined,
  }
}

export default function AuthedAnalystPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()

  // Token state (obtained by exchanging dashboard access for a live analyst token)
  const [analystToken, setAnalystToken] = useState<string | null>(null)
  const [tokenError, setTokenError] = useState<string | null>(null)

  // Canvas state
  const [canvas, setCanvas] = useState<CanvasData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [activePageId, setActivePageId] = useState('')
  // Live-data connection (analysts have no projects UI — bind on demand)
  const [connected, setConnected] = useState(false)
  const [showConnect, setShowConnect] = useState(false)
  const lastRefresh = useRef<Date | null>(null)

  // Query Chat panel
  const [projectId, setProjectId] = useState<string | null>(null)       // canvas's project_id
  const [queryChatProjectId, setQueryChatProjectId] = useState<string | null>(null) // used by QueryChatPanel
  const [queryChatOpen, setQueryChatOpen] = useState(false)
  const [queryChatStep, setQueryChatStep] = useState<'not-connected' | 'crawling' | 'ready'>('not-connected')
  const [queryChatConnName, setQueryChatConnName] = useState('')
  const [queryChatConnType, setQueryChatConnType] = useState('')
  const [existingConns, setExistingConns] = useState<{ id: string; name: string; db_type: string }[]>([])
  const [showNewConnForQuery, setShowNewConnForQuery] = useState(false)
  const [queryCrawlPct, setQueryCrawlPct] = useState(0)
  const [queryCrawlMsg, setQueryCrawlMsg] = useState('')

  // Panel state
  const [leftOpen, setLeftOpen] = useState(false)
  const [rightOpen, setRightOpen] = useState(false)
  const [chatWidth, setChatWidth] = useState(320)
  const chatResizeStartX = useRef(0)
  const chatResizeStartW = useRef(320)

  // Filters
  const [activeFilters, setActiveFilters] = useState<FilterItem[]>([])
  const [slicerFilters, setSlicerFilters] = useState<Record<string, FilterItem | null>>({})
  const [widgetLiveData, setWidgetLiveData] = useState<Record<string, { rows: unknown[]; columns: string[] }>>({})
  const [applyingFilters, setApplyingFilters] = useState(false)
  const allColumns = canvas ? Array.from(new Set(canvas.widgets.flatMap(w => w.chart_data?.columns ?? []))) : []

  // Annotations
  const [annotations, setAnnotations] = useState<AnnotationData[]>([])

  // Modal states
  const [drilldown, setDrilldown] = useState<DrilldownState | null>(null)
  const [queryModal, setQueryModal] = useState<{ open: boolean; sql: string }>({ open: false, sql: '' })
  const [bookmarkModal, setBookmarkModal] = useState(false)
  const [scheduleModal, setScheduleModal] = useState(false)
  const [exportMenu, setExportMenu] = useState(false)
  const exportMenuRef = useRef<HTMLDivElement>(null)

  // ── Bootstrap: get analyst token then load canvas ────────────────────────────

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setTokenError(null)

    Promise.all([
      api.post(`/dashboards/${id}/analyst-token`),
      dashboardApi.get(String(id)).catch(() => ({ data: null })),
    ])
      .then(([tokenResp, dashResp]) => {
        const token: string = tokenResp.data.token
        setAnalystToken(token)
        if (dashResp.data?.project_id) setProjectId(String(dashResp.data.project_id))
        return publicCanvasApi.get(token)
      })
      .then(r => {
        const data: CanvasData = r.data
        setCanvas(data)
        setConnected(Boolean((data.layout_config as Record<string, unknown>)?.connection_id))
        if (data.pages.length > 0) setActivePageId(data.pages[0].id)
      })
      .catch(err => {
        const msg = err?.response?.status === 403
          ? 'You do not have access to this report.'
          : 'Failed to load report.'
        setError(msg)
      })
      .finally(() => setLoading(false))
  }, [id])

  // Load annotations after token is ready
  useEffect(() => {
    if (!analystToken) return
    analystApi.listAnnotations(analystToken).then(r => setAnnotations(r.data.annotations)).catch(() => {})
  }, [analystToken])

  // Close export menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node))
        setExportMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ── Live refresh ─────────────────────────────────────────────────────────────

  const handleRefresh = async () => {
    if (!canvas || !analystToken || refreshing) return
    // No live DB bound yet → ask the analyst to connect before refreshing.
    if (!connected) { setShowConnect(true); return }
    setRefreshing(true)
    try {
      const resp = await publicCanvasApi.refresh(analystToken)
      const freshWidgets: { widget_id: string; chart_data: WidgetData['chart_data'] }[] = resp.data.widgets
      setCanvas(prev => {
        if (!prev) return prev
        const byId = Object.fromEntries(freshWidgets.map(fw => [fw.widget_id, fw.chart_data]))
        return { ...prev, widgets: prev.widgets.map(w => byId[w.id] ? { ...w, chart_data: byId[w.id] } : w) }
      })
      lastRefresh.current = new Date()
    } catch { /* ignore */ }
    finally { setRefreshing(false) }
  }

  // Create + verify a DB connection, bind it to this canvas (schema crawl + live
  // refresh), then re-pull the canvas so widgets + the copilot are live.
  const connectLiveData = useCallback(async (details: {
    db_type: string; host: string; port: string; database_name: string; username: string
    password: string; ssl_enabled?: boolean; iam_role_arn?: string
  }) => {
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
    await vlyApi.bindConnection(String(id), connResp.data.connection_id, { crawl: true, refresh: true })
    setConnected(true)
    setShowConnect(false)
    if (analystToken) {
      try {
        const r = await publicCanvasApi.get(analystToken)
        setCanvas(r.data)
      } catch { /* keep current view; data will refresh on next action */ }
    }
  }, [id, analystToken])

  // ── Filter application ────────────────────────────────────────────────────────

  const applyFilters = useCallback(async (filters: FilterItem[]) => {
    if (!canvas || !analystToken || filters.length === 0) {
      setWidgetLiveData({})
      return
    }
    setApplyingFilters(true)
    const updates: Record<string, { rows: unknown[]; columns: string[] }> = {}
    await Promise.allSettled(
      canvas.widgets
        .filter(w => w.chart_type !== 'slicer')
        .map(async w => {
          try {
            const r = await analystApi.getWidgetData(analystToken, w.id, filters)
            updates[w.id] = { rows: r.data.rows, columns: r.data.columns }
          } catch { /* keep cached */ }
        })
    )
    setWidgetLiveData(updates)
    setApplyingFilters(false)
  }, [canvas, analystToken])

  const handleFiltersChange = (filters: FilterItem[]) => {
    setActiveFilters(filters)
    const slicerItems = Object.values(slicerFilters).filter(Boolean) as FilterItem[]
    applyFilters([...filters, ...slicerItems])
  }

  const handleSlicerChange = useCallback((widgetId: string, filter: FilterItem | null) => {
    setSlicerFilters(prev => {
      const next = { ...prev, [widgetId]: filter }
      const slicerItems = Object.values(next).filter(Boolean) as FilterItem[]
      applyFilters([...activeFilters, ...slicerItems])
      return next
    })
  }, [activeFilters, applyFilters])

  // ── Drill-down ────────────────────────────────────────────────────────────────

  const handleDataPointClick = (widgetId: string, widgetTitle: string) =>
    (column: string, value: unknown) => {
      setDrilldown({ widgetId, widgetTitle, xColumn: column, xValue: String(value) })
    }

  // ── Annotations ──────────────────────────────────────────────────────────────

  const handleAddAnnotation = async (widgetId: string, content: string, authorName: string, x: number, y: number) => {
    if (!analystToken) return
    const r = await analystApi.createAnnotation(analystToken, { widget_id: widgetId, content, author_name: authorName, x_percent: x, y_percent: y })
    setAnnotations(prev => [r.data, ...prev])
  }

  const handleDeleteAnnotation = async (annotId: string) => {
    if (!analystToken) return
    await analystApi.deleteAnnotation(analystToken, annotId)
    setAnnotations(prev => prev.filter(a => a.id !== annotId))
  }

  const handleResolveAnnotation = async (annotId: string) => {
    if (!analystToken) return
    await analystApi.resolveAnnotation(analystToken, annotId)
    setAnnotations(prev => prev.filter(a => a.id !== annotId))
  }

  // ── Export ────────────────────────────────────────────────────────────────────

  const handleExportPdf = async () => {
    setExportMenu(false)
    if (!analystToken) return
    try {
      await analystApi.exportPdf(analystToken)
      alert('PDF export queued. You will receive it via email or download when ready.')
    } catch { /* ignore */ }
  }

  const handleExportWidgetCsv = (widgetId: string) => {
    if (!analystToken) return
    window.open(analystApi.csvExportUrl(analystToken, widgetId), '_blank')
  }

  // ── Bookmark load ─────────────────────────────────────────────────────────────

  const handleLoadBookmark = (filters: FilterItem[], pageIndex: number) => {
    setActiveFilters(filters)
    applyFilters(filters)
    if (canvas && canvas.pages[pageIndex]) setActivePageId(canvas.pages[pageIndex].id)
  }

  // ── Query Chat helpers ────────────────────────────────────────────────────────

  /** Poll a crawl job until completed/failed. Updates queryCrawlPct + queryCrawlMsg in place. */
  const pollCrawl = async (pid: string, jobId: string, startPct = 25): Promise<void> => {
    const MAX = 90 // 90 × 2 s = 3 min max
    for (let i = 0; i < MAX; i++) {
      await new Promise(r => setTimeout(r, 2000))
      const statusResp = await projectApi.getCrawlStatus(pid, jobId)
      const { status, error } = statusResp.data as { status: string; error?: string | null }
      if (status === 'completed') { setQueryCrawlPct(100); setQueryCrawlMsg('Schema ready!'); return }
      if (status === 'failed') throw new Error(error ?? 'Schema crawl failed')
      const pct = Math.round(startPct + ((i + 1) / MAX) * (95 - startPct))
      setQueryCrawlPct(pct)
      if (pct < 45) setQueryCrawlMsg('Reading table schemas…')
      else if (pct < 70) setQueryCrawlMsg('Reading column types and relationships…')
      else if (pct < 88) setQueryCrawlMsg('Extracting metadata for query AI…')
      else setQueryCrawlMsg('Finalising…')
    }
    // Timed out — crawl still running in background, proceed anyway
  }

  // ── Query Chat handlers ───────────────────────────────────────────────────────

  const handleOpenQueryChat = async () => {
    setQueryChatOpen(true)
    if (!projectId || !canvas) return
    const connectionId = (canvas.layout_config as Record<string, unknown>)?.connection_id as string | undefined
    if (connectionId) {
      // Canvas already has a live connection — schema was crawled when it was set up
      try {
        const r = await projectApi.listConnections(projectId)
        const conns = (r.data as { id: string; name: string; db_type: string }[]) || []
        const conn = conns.find(c => c.id === connectionId)
        if (conn) { setQueryChatConnName(conn.name); setQueryChatConnType(conn.db_type) }
      } catch { /* name is cosmetic — ignore */ }
      setQueryChatProjectId(projectId)
      setQueryChatStep('ready')
      return
    }
    // Not yet connected — load the project's connections for the picker
    setQueryChatStep('not-connected')
    try {
      const r = await projectApi.listConnections(projectId)
      setExistingConns((r.data as { id: string; name: string; db_type: string }[]) || [])
    } catch { setExistingConns([]) }
  }

  /** Use an existing connection from the canvas project: bind it, then crawl + poll. */
  const handleConnectExistingForQuery = async (connId: string, connName: string, connType: string) => {
    setQueryChatStep('crawling')
    setQueryCrawlPct(5); setQueryCrawlMsg('Binding connection to canvas…')
    try {
      // Bind without crawl=true (we trigger crawl separately so we can poll it)
      await vlyApi.bindConnection(String(id), connId, { crawl: false, refresh: true })
      setQueryCrawlPct(15); setQueryCrawlMsg('Starting schema crawl…')

      if (!projectId) throw new Error('Canvas project not loaded yet — please refresh and try again.')
      const crawlResp = await projectApi.triggerCrawl(projectId)
      const jobId = (crawlResp.data as { job_id: string }).job_id
      setQueryCrawlPct(20); setQueryCrawlMsg('Crawling table schemas…')
      await pollCrawl(projectId, jobId, 20)
      setQueryChatProjectId(projectId)

      await new Promise(r => setTimeout(r, 400))
      setQueryChatConnName(connName); setQueryChatConnType(connType)
      setQueryChatStep('ready'); setConnected(true)
    } catch {
      setQueryChatStep('not-connected')
    }
  }

  /**
   * New connection from the analyst (no project concept).
   * Creates the connection in the analyst's personal project, crawls it there,
   * and uses that project_id for QueryChatPanel (avoids the canvas project
   * mismatch that would break vlyApi.bindConnection for shared canvases).
   */
  const handleConnectNewForQuery = async (details: {
    db_type: string; host: string; port: string; database_name: string; username: string
    password: string; ssl_enabled?: boolean; iam_role_arn?: string
  }) => {
    setShowNewConnForQuery(false)
    setQueryChatStep('crawling')
    setQueryCrawlPct(5); setQueryCrawlMsg('Creating connection…')
    try {
      // Step 1 — create in analyst's personal project
      const connResp = await endUserApi.createConnection({
        db_type: details.db_type, host: details.host,
        port: details.port ? Number(details.port) : undefined,
        database_name: details.database_name, username: details.username,
        password: details.password, ssl_enabled: details.ssl_enabled,
        iam_role_arn: details.iam_role_arn || undefined,
      })
      const personalPid = connResp.data.project_id
      setQueryCrawlPct(15); setQueryCrawlMsg('Starting schema crawl…')

      // Step 2 — trigger crawl, get job_id, poll to completion
      const crawlResp = await projectApi.triggerCrawl(personalPid)
      const jobId = (crawlResp.data as { job_id: string }).job_id
      setQueryCrawlPct(25); setQueryCrawlMsg('Crawling table schemas…')
      await pollCrawl(personalPid, jobId, 25)

      await new Promise(r => setTimeout(r, 400))
      setQueryChatProjectId(personalPid)
      setQueryChatConnName(details.database_name)
      setQueryChatConnType(details.db_type)
      setQueryChatStep('ready')
    } catch {
      setQueryChatStep('not-connected')
    }
  }

  const handleSwitchConnection = () => {
    setQueryChatStep('not-connected')
    setQueryChatConnName(''); setQueryChatConnType('')
    setQueryCrawlPct(0); setQueryCrawlMsg('')
    if (projectId) {
      projectApi.listConnections(projectId)
        .then(r => setExistingConns((r.data as { id: string; name: string; db_type: string }[]) || []))
        .catch(() => setExistingConns([]))
    }
  }

  // ── Loading / Error states ────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={28} className="animate-spin text-blue-500" />
          <p className="text-sm text-gray-500">Loading report…</p>
        </div>
      </div>
    )
  }

  if (error || !canvas || !analystToken) {
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-4 max-w-sm text-center px-4">
          <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
            <AlertCircle size={20} className="text-red-400" />
          </div>
          <p className="text-sm font-medium text-gray-700">{error ?? 'Report unavailable'}</p>
          <button
            onClick={() => router.push('/end-user/dashboard')}
            className="flex items-center gap-2 px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <ArrowLeft size={14} /> My Reports
          </button>
        </div>
      </div>
    )
  }

  const defaultPageId = canvas.pages[0]?.id ?? ''
  const currentPageId = activePageId || defaultPageId
  const pageIndex = canvas.pages.findIndex(p => p.id === currentPageId)

  const pageWidgets = canvas.widgets.filter(w => {
    const pid = (w.config?.page_id as string) || ''
    return pid ? pid === currentPageId : currentPageId === defaultPageId
  })

  const gridLayout: LayoutItem[] = pageWidgets.map(w => ({
    i: w.id,
    x: w.position_x,
    y: w.position_y,
    w: w.width,
    h: w.height,
    static: true,
  }))

  const annotationCount = annotations.filter(a => !a.is_resolved).length

  return (
    <div className="h-full flex flex-col" style={{ background: '#F8FAFC' }}>

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-4 py-3 bg-white border-b border-gray-100 flex-shrink-0 shadow-sm z-20">
        <div className="flex items-center gap-3">
          {/* Back to reports */}
          <button
            onClick={() => router.push('/end-user/dashboard')}
            className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 transition-all"
            title="Back to My Reports"
          >
            <ArrowLeft size={15} />
          </button>

          {/* Schema toggle */}
          <button
            onClick={() => setLeftOpen(v => !v)}
            className={`p-1.5 rounded-lg transition-all ${leftOpen ? 'bg-blue-100 text-blue-600' : 'text-gray-400 hover:bg-gray-100 hover:text-gray-700'}`}
            title="Schema explorer"
          >
            <Database size={15} />
          </button>

          {/* Branding */}
          <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
            <Sparkles size={13} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-gray-900 leading-tight">{canvas.name}</h1>
            <p className="text-xs text-gray-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full inline-block bg-green-400" />
              Live data
              {lastRefresh.current && ` · ${lastRefresh.current.toLocaleTimeString()}`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Refresh */}
          <button
            onClick={handleRefresh}
            disabled={refreshing || applyingFilters}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={11} className={(refreshing || applyingFilters) ? 'animate-spin' : ''} />
            {refreshing ? 'Refreshing…' : applyingFilters ? 'Filtering…' : 'Refresh'}
          </button>

          {/* SQL Sandbox */}
          <button
            onClick={() => setQueryModal({ open: true, sql: '' })}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            title="SQL Sandbox"
          >
            <Terminal size={11} />
            <span className="hidden sm:inline">Query</span>
          </button>

          {/* Bookmarks */}
          <button
            onClick={() => setBookmarkModal(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            title="Saved views"
          >
            <Bookmark size={11} />
            <span className="hidden sm:inline">Views</span>
          </button>

          {/* Schedule */}
          <button
            onClick={() => setScheduleModal(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            title="Email snapshots"
          >
            <Calendar size={11} />
          </button>

          {/* Export menu */}
          <div className="relative" ref={exportMenuRef}>
            <button
              onClick={() => setExportMenu(v => !v)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
              title="Export"
            >
              <Download size={11} />
            </button>
            {exportMenu && (
              <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-xl shadow-lg py-1 z-50 min-w-[160px]">
                <button onClick={handleExportPdf}
                  className="flex items-center gap-2 w-full px-3 py-2 text-xs text-gray-700 hover:bg-gray-50">
                  <FileDown size={12} className="text-red-500" /> Export PDF
                </button>
                {pageWidgets.map(w => (
                  <button key={w.id} onClick={() => handleExportWidgetCsv(w.id)}
                    className="flex items-center gap-2 w-full px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 truncate">
                    <FileDown size={12} className="text-green-500 flex-shrink-0" />
                    <span className="truncate">{w.title} (CSV)</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Query Chat */}
          <button
            onClick={handleOpenQueryChat}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 transition-colors ${queryChatOpen ? 'bg-blue-100 border-blue-200 text-blue-600' : 'text-gray-600 hover:bg-gray-50'}`}
            title="Query Chat — ask questions about your data"
          >
            <TrendingUp size={11} />
            <span className="hidden sm:inline">Query Chat</span>
          </button>

          {/* AI Chat toggle + annotation badge */}
          <button
            onClick={() => {
              // The copilot needs live data — require a connection before opening it.
              if (!rightOpen && !connected) { setShowConnect(true); return }
              setRightOpen(v => !v)
            }}
            className={`relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 transition-colors ${rightOpen ? 'bg-purple-100 border-purple-200 text-purple-600' : 'text-gray-600 hover:bg-gray-50'}`}
            title="AI Chat"
          >
            <MessageSquare size={11} />
            {annotationCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-purple-500 text-white text-xs rounded-full flex items-center justify-center font-bold" style={{ fontSize: '9px' }}>
                {annotationCount}
              </span>
            )}
          </button>
        </div>
      </header>

      {/* ── Filter bar ───────────────────────────────────────────────────────── */}
      <FilterBar
        filters={activeFilters}
        onFiltersChange={handleFiltersChange}
        columns={allColumns}
      />

      {/* ── Page tabs ─────────────────────────────────────────────────────────── */}
      {canvas.pages.length > 1 && (
        <div className="flex items-center gap-1 px-4 py-2 bg-white border-b border-gray-100 overflow-x-auto flex-shrink-0 z-10" style={{ scrollbarWidth: 'none' }}>
          {[...canvas.pages].sort((a, b) => a.order - b.order).map(page => (
            <button
              key={page.id}
              onClick={() => setActivePageId(page.id)}
              className="px-4 py-1.5 rounded-full text-xs font-medium transition-all flex-shrink-0"
              style={{
                background: page.id === currentPageId ? '#EFF6FF' : 'transparent',
                color: page.id === currentPageId ? '#2563EB' : '#6B7280',
                border: `1px solid ${page.id === currentPageId ? '#BFDBFE' : 'transparent'}`,
              }}
            >
              {page.name}
            </button>
          ))}
        </div>
      )}

      {/* ── 3-panel layout ────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left sidebar: Schema browser */}
        {leftOpen && (
          <div className="w-72 flex-shrink-0 bg-white border-r border-gray-100 flex flex-col overflow-hidden shadow-sm z-10">
            <SchemaSidebar
              token={analystToken}
              onQueryOpen={sql => setQueryModal({ open: true, sql })}
            />
          </div>
        )}

        {/* Main area */}
        <main className="flex-1 overflow-auto p-4 min-w-0">
          {pageWidgets.length === 0 ? (
            <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
              No charts on this page
            </div>
          ) : (
            <ResponsiveGrid
              className="layout"
              layouts={{ lg: gridLayout }}
              breakpoints={{ lg: 1200, md: 768 }}
              cols={{ lg: 12, md: 8 }}
              rowHeight={60}
              isDraggable={false}
              isResizable={false}
              margin={[12, 12]}
            >
              {pageWidgets.map(w => {
                if (w.chart_type === 'slicer') {
                  return (
                    <div key={w.id} className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden flex flex-col" style={{ borderLeft: '3px solid #6366F1' }}>
                      <SlicerWidget
                        token={analystToken!}
                        widgetId={w.id}
                        title={w.title}
                        slicerColumn={(w.config?.slicer_column as string) || ''}
                        slicerType={(w.config?.slicer_type as 'dropdown' | 'checkbox' | 'date_range') || 'dropdown'}
                        filterValue={slicerFilters[w.id] ?? null}
                        onFilterChange={filter => handleSlicerChange(w.id, filter)}
                      />
                    </div>
                  )
                }

                const liveData = widgetLiveData[w.id]
                const result = widgetToResult(w, liveData)
                const widgetAnnotations = annotations.filter(a => a.widget_id === w.id)

                return (
                  <div key={w.id} className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden flex flex-col group relative">
                    <div className="px-3 pt-3 pb-1 flex items-center gap-2 flex-shrink-0">
                      <h3 className="text-xs font-semibold text-gray-700 truncate flex-1">{w.title}</h3>
                      {liveData && (
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 flex-shrink-0" title="Live filtered data" />
                      )}
                      {widgetAnnotations.length > 0 && (
                        <span className="text-xs text-purple-500 flex items-center gap-0.5 flex-shrink-0">
                          <MessageSquare size={9} />
                          {widgetAnnotations.length}
                        </span>
                      )}
                    </div>

                    <div className="flex-1 px-2 pb-2 min-h-0">
                      <ChartRenderer
                        result={result}
                        height={undefined}
                        onDataPointClick={handleDataPointClick(w.id, w.title)}
                      />
                    </div>

                    <div className="absolute inset-0">
                      <AnnotationLayer
                        widgetId={w.id}
                        annotations={annotations}
                        onAdd={handleAddAnnotation}
                        onDelete={handleDeleteAnnotation}
                        onResolve={handleResolveAnnotation}
                      />
                    </div>
                  </div>
                )
              })}
            </ResponsiveGrid>
          )}
        </main>

        {/* Right sidebar: AI chat */}
        {rightOpen && (
          <div
            className="relative flex-shrink-0 bg-white border-l border-gray-100 flex flex-col overflow-hidden shadow-sm z-10"
            style={{ width: chatWidth }}
          >
            {/* Drag handle — grab left edge to resize */}
            <div
              onMouseDown={e => {
                e.preventDefault()
                chatResizeStartX.current = e.clientX
                chatResizeStartW.current = chatWidth
                const onMove = (ev: MouseEvent) => {
                  const delta = chatResizeStartX.current - ev.clientX
                  setChatWidth(Math.min(640, Math.max(260, chatResizeStartW.current + delta)))
                }
                const onUp = () => {
                  document.removeEventListener('mousemove', onMove)
                  document.removeEventListener('mouseup', onUp)
                }
                document.addEventListener('mousemove', onMove)
                document.addEventListener('mouseup', onUp)
              }}
              className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-20 flex items-center justify-center group hover:bg-indigo-50/80 transition-colors"
            >
              <div className="w-0.5 h-8 rounded-full bg-gray-300 group-hover:bg-indigo-400 transition-colors" />
            </div>
            <ChatSidebar token={analystToken} dashboardName={canvas.name} />
          </div>
        )}
      </div>

      {/* ── Modals ───────────────────────────────────────────────────────────── */}

      {drilldown && (
        <DrilldownModal
          token={analystToken}
          widgetId={drilldown.widgetId}
          widgetTitle={drilldown.widgetTitle}
          xColumn={drilldown.xColumn}
          xValue={drilldown.xValue}
          activeFilters={activeFilters}
          onClose={() => setDrilldown(null)}
        />
      )}

      {queryModal.open && (
        <QueryModal
          token={analystToken}
          initialSql={queryModal.sql}
          onClose={() => setQueryModal({ open: false, sql: '' })}
        />
      )}

      {bookmarkModal && (
        <BookmarkModal
          token={analystToken}
          currentFilters={activeFilters}
          currentPageIndex={pageIndex >= 0 ? pageIndex : 0}
          onLoad={handleLoadBookmark}
          onClose={() => setBookmarkModal(false)}
        />
      )}

      {scheduleModal && (
        <ScheduleModal
          token={analystToken}
          dashboardName={canvas.name}
          onClose={() => setScheduleModal(false)}
        />
      )}

      {showConnect && (
        <ConnectionPromptModal
          fileName={canvas.name}
          connectionHint={(canvas.layout_config as { connection_hint?: Record<string, string> })?.connection_hint}
          onConnect={connectLiveData}
          onClose={() => setShowConnect(false)}
        />
      )}

      {/* ── Query Chat slide-over ─────────────────────────────────────────────── */}
      {queryChatOpen && (
        <div className="fixed inset-0 z-40 flex">
          {/* Dim backdrop */}
          <div className="flex-1 bg-black/20" onClick={() => setQueryChatOpen(false)} />

          {/* Panel */}
          <div className="w-[min(80vw,960px)] bg-white border-l border-gray-200 flex flex-col shadow-2xl overflow-hidden">

            {/* Panel header */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 flex-shrink-0 bg-white">
              <div className="p-1.5 bg-brand/10 rounded-lg"><TrendingUp size={14} className="text-brand" /></div>
              <div className="flex-1 min-w-0">
                <h2 className="text-sm font-semibold text-gray-900">Query Chat</h2>
                {queryChatStep === 'ready' && queryChatConnName && (
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                    <span className="text-xs text-gray-500 truncate">
                      {queryChatConnType && <span className="font-medium text-gray-700">{queryChatConnType}</span>}
                      {queryChatConnType && queryChatConnName && ' · '}
                      {queryChatConnName}
                    </span>
                    <button onClick={handleSwitchConnection} className="text-xs text-brand hover:underline flex-shrink-0">Switch</button>
                  </div>
                )}
              </div>
              <button onClick={() => setQueryChatOpen(false)} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 flex-shrink-0">
                <X size={15} />
              </button>
            </div>

            {/* ── Step: not-connected ── */}
            {queryChatStep === 'not-connected' && (
              <div className="flex-1 overflow-y-auto flex flex-col items-center justify-center p-8 gap-6">
                <div className="w-14 h-14 rounded-2xl bg-brand/10 flex items-center justify-center">
                  <Database size={24} className="text-brand" />
                </div>
                <div className="text-center max-w-sm">
                  <h3 className="text-base font-semibold text-gray-900 mb-1">Connect a database first</h3>
                  <p className="text-sm text-gray-500">Query Chat needs a live database connection to generate charts and answer questions about your data.</p>
                </div>

                {existingConns.length > 0 && (
                  <div className="w-full max-w-md">
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Existing connections</p>
                    <div className="space-y-2">
                      {existingConns.map(conn => (
                        <button
                          key={conn.id}
                          onClick={() => handleConnectExistingForQuery(conn.id, conn.name, conn.db_type)}
                          className="w-full flex items-center gap-3 px-4 py-3 bg-white border border-gray-200 rounded-xl hover:border-brand/40 hover:bg-brand/5 transition-all text-left group"
                        >
                          <div className="w-8 h-8 rounded-lg bg-gray-100 group-hover:bg-brand/10 flex items-center justify-center flex-shrink-0 transition-colors">
                            <Database size={14} className="text-gray-500 group-hover:text-brand" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-900 truncate">{conn.name}</p>
                            <p className="text-xs text-gray-400">{conn.db_type}</p>
                          </div>
                          <Link2 size={14} className="text-gray-300 group-hover:text-brand flex-shrink-0 transition-colors" />
                        </button>
                      ))}
                    </div>
                    <div className="relative flex items-center my-4">
                      <div className="flex-1 border-t border-gray-100" />
                      <span className="px-3 text-xs text-gray-400">or</span>
                      <div className="flex-1 border-t border-gray-100" />
                    </div>
                  </div>
                )}

                <button
                  onClick={() => setShowNewConnForQuery(true)}
                  className="flex items-center gap-2 px-5 py-2.5 bg-brand text-white rounded-xl text-sm font-medium hover:bg-brand-dark transition-colors shadow-sm"
                >
                  <Plus size={14} /> New Connection
                </button>
              </div>
            )}

            {/* ── Step: crawling — real progress from pollCrawl ── */}
            {queryChatStep === 'crawling' && (() => {
              const stages = [
                { label: 'Creating / testing connection', upTo: 15  },
                { label: 'Reading table schemas',         upTo: 45  },
                { label: 'Extracting column metadata',    upTo: 75  },
                { label: 'Preparing query engine',        upTo: 100 },
              ]
              const activeIdx = stages.findIndex(s => queryCrawlPct < s.upTo)
              const ai = activeIdx === -1 ? stages.length - 1 : activeIdx
              return (
                <div className="flex-1 flex flex-col items-center justify-center gap-5 p-8">
                  <div className="relative w-16 h-16">
                    <div className="absolute inset-0 rounded-full border-4 border-brand/20" />
                    <div className="absolute inset-0 rounded-full border-4 border-brand border-t-transparent animate-spin" />
                    <div className="absolute inset-0 flex items-center justify-center">
                      <Database size={20} className="text-brand" />
                    </div>
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-semibold text-gray-900 mb-1">{queryCrawlMsg || 'Starting…'}</p>
                    <p className="text-xs text-gray-400">Schema crawl usually takes 20–60 seconds.</p>
                  </div>
                  <div className="w-full max-w-xs bg-gray-200 rounded-full h-1.5 overflow-hidden">
                    <div className="bg-brand h-1.5 rounded-full transition-all duration-700" style={{ width: `${queryCrawlPct}%` }} />
                  </div>
                  <p className="text-xs text-gray-400 -mt-3">{queryCrawlPct}%</p>
                  <div className="w-full max-w-xs space-y-2.5">
                    {stages.map(({ label }, i) => {
                      const done = i < ai
                      const current = i === ai
                      return (
                        <div key={label} className="flex items-center gap-2.5 text-xs">
                          <div className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${done ? 'bg-green-100' : current ? 'bg-brand/10' : 'bg-gray-100'}`}>
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
              )
            })()}

            {/* ── Step: ready ── */}
            {queryChatStep === 'ready' && queryChatProjectId && (
              <div className="flex-1 overflow-hidden">
                <QueryChatPanel
                  projectId={queryChatProjectId}
                  connectionLabel={[queryChatConnType, queryChatConnName].filter(Boolean).join(' · ')}
                  onSwitchConnection={handleSwitchConnection}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* New connection form triggered from within Query Chat */}
      {showNewConnForQuery && (
        <ConnectionPromptModal
          fileName={canvas.name}
          connectionHint={(canvas.layout_config as { connection_hint?: Record<string, string> })?.connection_hint}
          onConnect={handleConnectNewForQuery}
          onClose={() => setShowNewConnForQuery(false)}
        />
      )}
    </div>
  )
}
