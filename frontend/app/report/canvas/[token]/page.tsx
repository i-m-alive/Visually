'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { Loader2, AlertCircle, RefreshCw } from 'lucide-react'
import { publicCanvasApi, analystApi } from '@/lib/api'
import type { FilterItem } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'
import SlicerWidget from '@/components/canvas/SlicerWidget'

import { LeftRail } from '@/components/report/LeftRail'
import { KpiStrip } from '@/components/report/KpiStrip'
import type { KpiData } from '@/components/report/KpiStrip'
import { MorningBrief } from '@/components/report/MorningBrief'
import { ExecutiveCopilot } from '@/components/report/ExecutiveCopilot'
import { CustomerRankList } from '@/components/report/CustomerRankList'
import type { CustomerRow } from '@/components/report/CustomerRankList'
import { YoYTable } from '@/components/report/YoYTable'
import type { YoYRow } from '@/components/report/YoYTable'
import { NewLostSection } from '@/components/report/NewLostSection'
import type { WinLossRow } from '@/components/report/NewLostSection'

const ResponsiveGrid = WidthProvider(Responsive)

// ── Types ──────────────────────────────────────────────────────────────────────

interface WidgetData {
  id: string
  title: string
  chart_type: string
  position_x: number
  position_y: number
  width: number
  height: number
  config: Record<string, unknown>
  chart_data: { rows: Record<string, unknown>[]; columns: string[] }
  filterable_columns: string[]
}

interface CanvasData {
  id: string
  name: string
  theme: string
  pages: { id: string; name: string; order: number }[]
  share_mode: string
  widgets: WidgetData[]
}

// ── Data-shape detection ───────────────────────────────────────────────────────

const YEAR_RE = /^20\d{2}$/

function getValues(rows: Record<string, unknown>[], cols: string[]): number[] {
  const numCol = cols.find(c => typeof rows[0]?.[c] === 'number') ?? cols[1]
  return numCol ? rows.map(r => Number(r[numCol] ?? 0)) : []
}

function getLabels(rows: Record<string, unknown>[], cols: string[]): string[] {
  const strCol = cols.find(c => typeof rows[0]?.[c] === 'string') ?? cols[0]
  return strCol ? rows.map(r => String(r[strCol] ?? '')) : []
}

function buildKpis(widgets: WidgetData[]): KpiData[] {
  return widgets
    .filter(w => !['slicer', 'table'].includes(w.chart_type) && w.chart_data?.rows?.length)
    .slice(0, 5)
    .map((w, i) => {
      const vals = getValues(w.chart_data.rows, w.chart_data.columns)
      const total = vals.reduce((a, b) => a + b, 0)
      const mid = Math.max(1, Math.floor(vals.length / 2))
      const h1 = vals.slice(0, mid).reduce((a, b) => a + b, 0)
      const h2 = vals.slice(mid).reduce((a, b) => a + b, 0)
      const changePct = h1 ? ((h2 - h1) / Math.abs(h1)) * 100 : 0
      return { title: w.title, value: total, values: vals, changePct, isLead: i === 0 }
    })
}

// Detect customer rank list: string primary col + single numeric col
function buildCustomerRank(widget: WidgetData): CustomerRow[] | null {
  const { rows, columns } = widget.chart_data
  if (!rows.length || columns.length < 2) return null
  const strCol = columns.find(c => typeof rows[0][c] === 'string')
  const numCols = columns.filter(c => c !== strCol && rows.every(r => !isNaN(Number(r[c]))))
  if (!strCol || !numCols.length) return null
  return rows.map(r => ({
    label: String(r[strCol]),
    value: Number(r[numCols[0]]),
    values: numCols.map(c => Number(r[c])),
  }))
}

// Detect YoY table: string primary col + 2+ year-like columns
function buildYoY(widget: WidgetData): YoYRow[] | null {
  const { rows, columns } = widget.chart_data
  if (!rows.length) return null
  const yearCols = columns.filter(c => YEAR_RE.test(c))
  if (yearCols.length < 2) return null
  const labelCol = columns.find(c => !YEAR_RE.test(c) && typeof rows[0][c] === 'string') ?? columns[0]
  return rows.map(r => ({
    label: String(r[labelCol] ?? ''),
    yearValues: yearCols.map(y => ({ year: y, value: Number(r[y] ?? 0) })),
  }))
}

// Detect win/loss: rows where title or columns contain 'new'/'win' or 'lost'/'loss'
function isWinWidget(w: WidgetData): boolean {
  const t = w.title.toLowerCase()
  return t.includes('new') || t.includes('win') || w.chart_data.columns.some(c => /new|win/i.test(c))
}
function isLossWidget(w: WidgetData): boolean {
  const t = w.title.toLowerCase()
  return t.includes('lost') || t.includes('loss') || w.chart_data.columns.some(c => /lost?|loss/i.test(c))
}

function widgetToRows(w: WidgetData): WinLossRow[] {
  const { rows, columns } = w.chart_data
  const labels = getLabels(rows, columns)
  const vals = getValues(rows, columns)
  return labels.map((label, i) => ({ label, value: vals[i] ?? 0 }))
}

function widgetToResult(w: WidgetData, override?: { rows: unknown[]; columns: string[] }): ChartResult {
  const cd = override ?? w.chart_data
  const rows = (cd.rows as Record<string, unknown>[]) || []
  const cols = cd.columns || []
  const labels = rows.map(r => String(r[cols[0]] ?? ''))
  const values = rows.map(r => Number(r[cols[1]] ?? 0))
  return {
    chart_type: w.chart_type, title: w.title, sql: '', score: 1, low_confidence: false,
    x_axis_label: cols[0] || 'x', y_axis_label: cols[1] || 'y', table_used: '',
    chart_data: { rows, columns: cols, labels, values },
  }
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ExecutiveReportPage() {
  const { token } = useParams<{ token: string }>()

  const [canvas, setCanvas] = useState<CanvasData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [activePageId, setActivePageId] = useState('')
  const lastRefresh = useRef<Date | null>(null)

  const [slicerFilters, setSlicerFilters] = useState<Record<string, FilterItem | null>>({})
  const [widgetLiveData, setWidgetLiveData] = useState<Record<string, { rows: unknown[]; columns: string[] }>>({})

  const load = useCallback(async () => {
    try {
      const resp = await publicCanvasApi.get(token)
      const data: CanvasData = resp.data
      setCanvas(data)
      if (data.pages.length && !activePageId) setActivePageId(data.pages[0].id)
    } catch {
      setError('This share link is invalid, expired, or has been revoked.')
    } finally {
      setLoading(false)
    }
  }, [token, activePageId])

  useEffect(() => { load() }, [load])

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

  const handleSlicerChange = useCallback((widgetId: string, filter: FilterItem | null) => {
    setSlicerFilters(prev => {
      const next = { ...prev, [widgetId]: filter }
      const items = Object.values(next).filter(Boolean) as FilterItem[]
      if (!canvas) return next
      Promise.allSettled(
        canvas.widgets.filter(w => w.chart_type !== 'slicer').map(async w => {
          try {
            const r = await analystApi.getWidgetData(token, w.id, items)
            setWidgetLiveData(d => ({ ...d, [w.id]: { rows: r.data.rows, columns: r.data.columns } }))
          } catch { /* keep cached */ }
        })
      )
      return next
    })
  }, [canvas, token])

  // ── Loading / Error ──────────────────────────────────────────────────────────

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: '#F8FAFC' }}>
      <div className="flex flex-col items-center gap-3">
        <Loader2 size={28} className="animate-spin text-blue-500" />
        <p className="text-sm text-gray-500">Loading report…</p>
      </div>
    </div>
  )

  if (error || !canvas) return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: '#F8FAFC' }}>
      <div className="flex flex-col items-center gap-3 max-w-sm text-center px-4">
        <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
          <AlertCircle size={20} className="text-red-400" />
        </div>
        <p className="text-sm font-medium text-gray-700">Report unavailable</p>
        <p className="text-xs text-gray-500">{error}</p>
      </div>
    </div>
  )

  // ── Derive page state ────────────────────────────────────────────────────────

  const defaultPageId = canvas.pages[0]?.id ?? ''
  const currentPageId = activePageId || defaultPageId
  const activePage = canvas.pages.find(p => p.id === currentPageId) ?? canvas.pages[0]

  const pageWidgets = canvas.widgets.filter(w => {
    const pid = (w.config?.page_id as string) || ''
    return pid ? pid === currentPageId : currentPageId === defaultPageId
  })

  const gridLayout: LayoutItem[] = pageWidgets.map(w => ({
    i: w.id, x: w.position_x, y: w.position_y, w: w.width, h: w.height, static: true,
  }))

  const kpis = buildKpis(pageWidgets)

  // Intelligence: customer rank lists (string + numeric, no year cols)
  const rankLists = pageWidgets
    .filter(w => !['slicer', 'table'].includes(w.chart_type) && !buildYoY(w))
    .map(w => ({ widget: w, rows: buildCustomerRank(w) }))
    .filter(x => x.rows && x.rows.length > 1) as { widget: WidgetData; rows: CustomerRow[] }[]

  // Intelligence: YoY tables
  const yoyTables = pageWidgets
    .map(w => ({ widget: w, rows: buildYoY(w) }))
    .filter(x => x.rows && x.rows.length) as { widget: WidgetData; rows: YoYRow[] }[]

  // Intelligence: new/lost
  const winWidgets = pageWidgets.filter(isWinWidget)
  const lossWidgets = pageWidgets.filter(isLossWidget)
  const wins: WinLossRow[] = winWidgets.flatMap(widgetToRows)
  const losses: WinLossRow[] = lossWidgets.flatMap(widgetToRows)
  const hasNewLost = wins.length > 0 || losses.length > 0

  // Exclude intelligence widgets from chart grid to avoid duplication
  const intelligenceIds = new Set([
    ...rankLists.map(x => x.widget.id),
    ...yoyTables.map(x => x.widget.id),
    ...winWidgets.map(w => w.id),
    ...lossWidgets.map(w => w.id),
  ])
  const chartGridWidgets = pageWidgets.filter(w => !intelligenceIds.has(w.id))
  const chartGridLayout = chartGridWidgets.map(w => ({
    i: w.id, x: w.position_x, y: w.position_y, w: w.width, h: w.height, static: true,
  }))

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: '#F8FAFC' }}>

      {/* ── Left rail ─────────────────────────────────────────────────────────── */}
      {canvas.pages.length > 0 && (
        <LeftRail
          pages={canvas.pages}
          activePageId={currentPageId}
          onPageChange={id => { setActivePageId(id); setWidgetLiveData({}) }}
          reportName={canvas.name}
        />
      )}

      {/* ── Main column ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">

        {/* Header */}
        <header className="flex items-center justify-between px-4 py-2.5 bg-white border-b border-gray-100 flex-shrink-0 z-10" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}>
          <div className="flex items-center gap-2 min-w-0">
            <p className="text-[10px] text-gray-400">Executive Reports</p>
            <span className="text-gray-300">·</span>
            <h1 className="text-sm font-semibold text-gray-900 truncate">{canvas.name}</h1>
            {activePage && (
              <>
                <span className="text-gray-300">·</span>
                <span className="text-sm text-gray-500 truncate">{activePage.name}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="flex items-center gap-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: canvas.share_mode === 'live' ? '#22C55E' : '#9CA3AF' }}
              />
              <span className="text-[10px] text-gray-500">{canvas.share_mode === 'live' ? 'Live data' : 'Snapshot'}</span>
              {lastRefresh.current && (
                <span className="text-[10px] text-gray-400">· {lastRefresh.current.toLocaleTimeString()}</span>
              )}
            </div>
            {canvas.share_mode === 'live' && (
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                <RefreshCw size={10} className={refreshing ? 'animate-spin' : ''} />
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
            )}
          </div>
        </header>

        {/* KPI strip */}
        {kpis.length > 0 && <KpiStrip kpis={kpis} />}

        {/* Morning brief */}
        {activePage && <MorningBrief token={token} pageName={activePage.name} />}

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto px-4 pb-6">

          {/* Chart grid */}
          {chartGridWidgets.length > 0 && (
            <ResponsiveGrid
              className="layout"
              layouts={{ lg: chartGridLayout }}
              breakpoints={{ lg: 1200, md: 768 }}
              cols={{ lg: 12, md: 8 }}
              rowHeight={60}
              isDraggable={false}
              isResizable={false}
              margin={[12, 12]}
            >
              {chartGridWidgets.map(w => {
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

                return (
                  <div key={w.id} className="bg-white rounded-xl border border-gray-100 overflow-hidden flex flex-col" style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)' }}>
                    <div className="px-3 pt-3 pb-1 flex items-center gap-2 flex-shrink-0">
                      <h3 className="text-xs font-semibold text-gray-700 truncate flex-1">{w.title}</h3>
                      {liveData && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 flex-shrink-0" title="Filtered data" />}
                    </div>
                    <div className="flex-1 px-2 pb-2 min-h-0">
                      <ChartRenderer result={result} height={undefined} />
                    </div>
                  </div>
                )
              })}
            </ResponsiveGrid>
          )}

          {/* Intelligence: YoY tables */}
          {yoyTables.length > 0 && (
            <div className="space-y-4 mt-4">
              {yoyTables.map(x => (
                <YoYTable key={x.widget.id} title={x.widget.title} rows={x.rows} />
              ))}
            </div>
          )}

          {/* Intelligence: customer rank lists */}
          {rankLists.length > 0 && (
            <div className="grid gap-4 mt-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
              {rankLists.map(x => (
                <CustomerRankList key={x.widget.id} title={x.widget.title} rows={x.rows} />
              ))}
            </div>
          )}

          {/* Intelligence: new vs lost */}
          {hasNewLost && (
            <div className="mt-4">
              <NewLostSection wins={wins} losses={losses} />
            </div>
          )}

          {/* Empty state */}
          {pageWidgets.length === 0 && (
            <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
              No charts on this page
            </div>
          )}
        </div>
      </div>

      {/* ── Copilot (always open) ─────────────────────────────────────────────── */}
      <div
        className="flex-shrink-0 border-l border-gray-100 flex flex-col overflow-hidden"
        style={{ width: 360, boxShadow: '-2px 0 12px rgba(0,0,0,0.04)' }}
      >
        <ExecutiveCopilot token={token} canvasName={canvas.name} pageName={activePage?.name ?? ''} />
      </div>
    </div>
  )
}
