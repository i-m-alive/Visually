'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import {
  RefreshCw, Loader2, AlertCircle, Sparkles, ChevronLeft, ChevronRight,
  Download, Bookmark, Calendar, Terminal, MessageSquare, Database,
  FileDown, MoreHorizontal, CheckCircle2,
} from 'lucide-react'
import { publicCanvasApi, analystApi } from '@/lib/api'
import type { FilterItem, AnnotationData } from '@/lib/api'
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

// ── Types ─────────────────────────────────────────────────────────────────────

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

// ── Helpers ───────────────────────────────────────────────────────────────────

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
  }
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function AnalystCanvasPage() {
  const { token } = useParams<{ token: string }>()

  // Canvas state
  const [canvas, setCanvas] = useState<CanvasData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [activePageId, setActivePageId] = useState('')
  const lastRefresh = useRef<Date | null>(null)

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

  // ── Load canvas ─────────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    try {
      const resp = await publicCanvasApi.get(token)
      const data: CanvasData = resp.data
      setCanvas(data)
      if (data.pages.length > 0 && !activePageId) {
        setActivePageId(data.pages[0].id)
      }
    } catch {
      setError('This share link is invalid, expired, or has been revoked.')
    } finally {
      setLoading(false)
    }
  }, [token, activePageId])

  useEffect(() => { load() }, [load])

  // Load annotations on mount
  useEffect(() => {
    if (!token) return
    analystApi.listAnnotations(token).then(r => setAnnotations(r.data.annotations)).catch(() => {})
  }, [token])

  // Close export menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setExportMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ── Live refresh ─────────────────────────────────────────────────────────────

  const handleRefresh = async () => {
    if (!canvas || canvas.share_mode !== 'live' || refreshing) return
    setRefreshing(true)
    try {
      const resp = await publicCanvasApi.refresh(token)
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

  // ── Filter application ────────────────────────────────────────────────────────

  const applyFilters = useCallback(async (filters: FilterItem[]) => {
    if (!canvas || filters.length === 0) {
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
            const r = await analystApi.getWidgetData(token, w.id, filters)
            updates[w.id] = { rows: r.data.rows, columns: r.data.columns }
          } catch { /* keep cached */ }
        })
    )
    setWidgetLiveData(updates)
    setApplyingFilters(false)
  }, [canvas, token])

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
    const r = await analystApi.createAnnotation(token, { widget_id: widgetId, content, author_name: authorName, x_percent: x, y_percent: y })
    setAnnotations(prev => [r.data, ...prev])
  }

  const handleDeleteAnnotation = async (id: string) => {
    await analystApi.deleteAnnotation(token, id)
    setAnnotations(prev => prev.filter(a => a.id !== id))
  }

  const handleResolveAnnotation = async (id: string) => {
    await analystApi.resolveAnnotation(token, id)
    setAnnotations(prev => prev.filter(a => a.id !== id))
  }

  // ── Export ────────────────────────────────────────────────────────────────────

  const handleExportPdf = async () => {
    setExportMenu(false)
    try {
      await analystApi.exportPdf(token)
      alert('PDF export queued. You will receive it via email or download when ready.')
    } catch { /* ignore */ }
  }

  const handleExportWidgetCsv = (widgetId: string) => {
    window.open(analystApi.csvExportUrl(token, widgetId), '_blank')
  }

  // ── Bookmark load ─────────────────────────────────────────────────────────────

  const handleLoadBookmark = (filters: FilterItem[], pageIndex: number) => {
    setActiveFilters(filters)
    applyFilters(filters)
    if (canvas && canvas.pages[pageIndex]) {
      setActivePageId(canvas.pages[pageIndex].id)
    }
  }

  // ── Loading / Error states ────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <Loader2 size={28} className="animate-spin text-blue-500" />
          <p className="text-sm text-gray-500">Loading canvas…</p>
        </div>
      </div>
    )
  }

  if (error || !canvas) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3 max-w-sm text-center px-4">
          <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
            <AlertCircle size={20} className="text-red-400" />
          </div>
          <p className="text-sm font-medium text-gray-700">Share link unavailable</p>
          <p className="text-xs text-gray-500">{error}</p>
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
    <div className="min-h-screen flex flex-col" style={{ background: '#F8FAFC' }}>

      {/* ── Header ───────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-4 py-3 bg-white border-b border-gray-100 flex-shrink-0 shadow-sm z-20">
        <div className="flex items-center gap-3">
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
              <span className={`w-1.5 h-1.5 rounded-full inline-block ${canvas.share_mode === 'live' ? 'bg-green-400' : 'bg-gray-400'}`} />
              {canvas.share_mode === 'live' ? 'Live data' : 'Snapshot'}
              {lastRefresh.current && ` · ${lastRefresh.current.toLocaleTimeString()}`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Refresh (live mode) */}
          {canvas.share_mode === 'live' && (
            <button
              onClick={handleRefresh}
              disabled={refreshing || applyingFilters}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={11} className={(refreshing || applyingFilters) ? 'animate-spin' : ''} />
              {refreshing ? 'Refreshing…' : applyingFilters ? 'Filtering…' : 'Refresh'}
            </button>
          )}

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

          {/* Annotations badge */}
          <button
            onClick={() => setRightOpen(v => !v)}
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
              token={token}
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
                        token={token}
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
                    {/* Widget header */}
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

                    {/* Chart */}
                    <div className="flex-1 px-2 pb-2 min-h-0">
                      <ChartRenderer
                        result={result}
                        height={undefined}
                        onDataPointClick={handleDataPointClick(w.id, w.title)}
                      />
                    </div>

                    {/* Annotation layer — pointer-events controlled internally */}
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
            <ChatSidebar token={token} dashboardName={canvas.name} />
          </div>
        )}
      </div>

      {/* ── Modals ───────────────────────────────────────────────────────────── */}

      {drilldown && (
        <DrilldownModal
          token={token}
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
          token={token}
          initialSql={queryModal.sql}
          onClose={() => setQueryModal({ open: false, sql: '' })}
        />
      )}

      {bookmarkModal && (
        <BookmarkModal
          token={token}
          currentFilters={activeFilters}
          currentPageIndex={pageIndex >= 0 ? pageIndex : 0}
          onLoad={handleLoadBookmark}
          onClose={() => setBookmarkModal(false)}
        />
      )}

      {scheduleModal && (
        <ScheduleModal
          token={token}
          dashboardName={canvas.name}
          onClose={() => setScheduleModal(false)}
        />
      )}
    </div>
  )
}
