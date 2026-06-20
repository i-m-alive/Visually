'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useParams, useRouter } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { Layout, LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import {
  Layers, MessageSquare, Plus, Save, ChevronLeft,
  Loader2, AlertCircle, CheckCircle2, LayoutGrid, Sparkles, Pencil, Calendar,
  RotateCcw, ZoomIn, ZoomOut, Link2, RefreshCw, Eye, EyeOff, FileJson,
  FunctionSquare, Clock, Shield, FileDown, Zap, Table2,
} from 'lucide-react'
import { canvasApi, widgetApi, vlyApi, scheduleApi } from '@/lib/api'
import { CanvasWidget, type CanvasWidgetData } from '@/components/canvas/CanvasWidget'
import { ZoomModal } from '@/components/canvas/ZoomModal'
import { CanvasChatPanel } from '@/components/canvas/CanvasChatPanel'
import { TableScopePicker } from '@/components/canvas/TableScopePicker'
import { useTableScopeStore } from '@/stores/tableScopeStore'
import { VlyExportModal } from '@/components/canvas/VlyExportModal'
import { VisuallReport } from '@/components/canvas/VisuallReport'
import { CanvasPageTabs, type CanvasPage } from '@/components/canvas/CanvasPageTabs'
import { MeasuresPanel } from '@/components/canvas/MeasuresPanel'
import { ScheduleRefreshModal } from '@/components/canvas/ScheduleRefreshModal'
import { RLSModal } from '@/components/canvas/RLSModal'

const ResponsiveGrid = WidthProvider(Responsive)

type GridItem = LayoutItem

// ── Smart auto-arrange ─────────────────────────────────────────────────────────
const CHART_SIZES: Record<string, { w: number; h: number }> = {
  kpi:                    { w: 3, h: 4 },
  kpi_card:               { w: 3, h: 4 },
  gauge:                  { w: 3, h: 5 },
  multi_row_card:         { w: 4, h: 6 },
  pie:                    { w: 4, h: 6 },
  donut:                  { w: 4, h: 6 },
  sunburst:               { w: 5, h: 6 },
  line:                   { w: 6, h: 6 },
  area:                   { w: 6, h: 6 },
  bar_vertical:           { w: 6, h: 6 },
  bar:                    { w: 6, h: 6 },
  scatter:                { w: 5, h: 6 },
  bubble:                 { w: 6, h: 6 },
  histogram:              { w: 6, h: 6 },
  waterfall:              { w: 7, h: 6 },
  funnel:                 { w: 5, h: 7 },
  treemap:                { w: 6, h: 6 },
  combo:                  { w: 7, h: 6 },
  stacked_bar:            { w: 7, h: 6 },
  stacked_bar_100:        { w: 7, h: 6 },
  grouped_bar:            { w: 7, h: 6 },
  stacked_area:           { w: 7, h: 6 },
  bar_horizontal:         { w: 7, h: 6 },
  stacked_bar_horizontal: { w: 8, h: 6 },
  heatmap:                { w: 8, h: 7 },
  table:                  { w: 8, h: 7 },
  data_table:             { w: 8, h: 7 },
  pivot_table:            { w: 9, h: 7 },
  box_plot:               { w: 7, h: 6 },
  bullet:                 { w: 7, h: 6 },
  scorecard:              { w: 5, h: 7 },
  dot_plot:               { w: 5, h: 7 },
  radar:                  { w: 6, h: 6 },
  ribbon:                 { w: 7, h: 6 },
  sankey:                 { w: 8, h: 7 },
  chord:                  { w: 6, h: 6 },
  network:                { w: 7, h: 7 },
  gantt:                  { w: 10, h: 7 },
  timeline:               { w: 10, h: 5 },
  calendar_heatmap:       { w: 10, h: 5 },
  word_cloud:             { w: 6, h: 6 },
  org_chart:              { w: 8, h: 7 },
  marimekko:              { w: 8, h: 7 },
  choropleth:             { w: 7, h: 8 },
}
const DEFAULT_SIZE = { w: 6, h: 6 }
const COLS = 12

const TYPE_GROUP: Record<string, number> = {
  kpi: 0, kpi_card: 0, gauge: 0, multi_row_card: 0,
  bullet: 0, scorecard: 0,
  pie: 1, donut: 1, sunburst: 1, scatter: 1, bubble: 1, funnel: 1,
  dot_plot: 1, radar: 1, word_cloud: 1, chord: 1,
  bar_vertical: 2, bar: 2, line: 2, area: 2, histogram: 2, treemap: 2,
  ribbon: 2, box_plot: 2,
  stacked_bar: 3, stacked_bar_100: 3, grouped_bar: 3, stacked_area: 3, combo: 3, waterfall: 3,
  bar_horizontal: 3, stacked_bar_horizontal: 3, marimekko: 3,
  heatmap: 4, calendar_heatmap: 4, network: 4, sankey: 4,
  gantt: 5, timeline: 5, org_chart: 5, choropleth: 5,
  table: 6, data_table: 6, pivot_table: 6,
}

function autoArrange(widgets: CanvasWidgetData[]): GridItem[] {
  const sorted = [...widgets].sort((a, b) =>
    (TYPE_GROUP[a.chart_type] ?? 2) - (TYPE_GROUP[b.chart_type] ?? 2)
  )
  let x = 0, y = 0, rowHeight = 0
  return sorted.map(w => {
    const size = CHART_SIZES[w.chart_type] || DEFAULT_SIZE
    if (x + size.w > COLS) { x = 0; y += rowHeight; rowHeight = 0 }
    const item: GridItem = { i: w.id, x, y, w: size.w, h: size.h, minW: 1, minH: 1 }
    x += size.w
    rowHeight = Math.max(rowHeight, size.h)
    return item
  })
}

interface WidgetWithPosition extends CanvasWidgetData {
  position_x: number
  position_y: number
  width: number
  height: number
}

interface FilterConfig {
  id: string
  column: string
  display_name: string
  filter_type: string
  available_values: string[]
  table: string
}

interface CanvasDetail {
  id: string
  name: string
  theme: string
  project_id: string
  description?: string
  filter_config?: FilterConfig[]
  widgets: WidgetWithPosition[]
  pages?: CanvasPage[]
  layout_config?: Record<string, unknown>
}

export default function CanvasEditorPage() {
  const { id: projectId, canvasId } = useParams<{ id: string; canvasId: string }>()
  const router = useRouter()

  const [canvas, setCanvas]           = useState<CanvasDetail | null>(null)
  const [widgets, setWidgets]         = useState<WidgetWithPosition[]>([])
  const [layout, setLayout]           = useState<GridItem[]>([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState<string | null>(null)
  const [saving, setSaving]           = useState(false)
  const [savedOk, setSavedOk]         = useState(false)
  const [showChat, setShowChat]       = useState(false)
  const [showTablePicker, setShowTablePicker] = useState(false)
  const [showExport, setShowExport]   = useState(false)
  const [tablesPopPos, setTablesPopPos] = useState<{ top: number; right: number } | null>(null)
  const tablesBtnRef = useRef<HTMLButtonElement>(null)
  const [showReport, setShowReport]   = useState(false)
  const [zoomTarget, setZoomTarget]   = useState<{ widget: CanvasWidgetData; colors: string[] } | null>(null)
  const [isDirty, setIsDirty]         = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleValue, setTitleValue]     = useState('')
  const [activeDateRange, setActiveDateRange] = useState<Record<string, { start: string; end: string }>>({})
  const [isRequeryingDate, setIsRequeryingDate] = useState(false)

  // Pages state
  const [pages, setPages]               = useState<CanvasPage[]>([])
  const [activePageId, setActivePageId] = useState<string>('')

  // New state
  const [lockedWidgets, setLockedWidgets]   = useState<Set<string>>(new Set())
  const [refreshingId, setRefreshingId]     = useState<string | null>(null)
  const [gridZoom, setGridZoom]             = useState(1)
  const [isViewOnly, setIsViewOnly]         = useState(false)
  const [showMeasures, setShowMeasures]     = useState(false)
  const [showSchedule, setShowSchedule]     = useState(false)
  const [showRLS, setShowRLS]               = useState(false)
  const [toastMsg, setToastMsg]             = useState<string | null>(null)
  const [canUndo, setCanUndo]               = useState(false)
  const [canRedo, setCanRedo]               = useState(false)

  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const undoStackRef   = useRef<GridItem[][]>([])
  const redoStackRef   = useRef<GridItem[][]>([])
  const toastTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  function showToast(msg: string) {
    setToastMsg(msg)
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    toastTimerRef.current = setTimeout(() => setToastMsg(null), 2500)
  }

  const savePagesConfig = useCallback(async (newPages: CanvasPage[]) => {
    try { await canvasApi.updateLayoutConfig(canvasId, { pages: newPages }) } catch { /* ignore */ }
  }, [canvasId])

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const resp = await canvasApi.get(canvasId)
      const data: CanvasDetail = resp.data
      setCanvas(data)
      const ws: WidgetWithPosition[] = (data.widgets || []) as WidgetWithPosition[]
      setWidgets(ws)

      // Initialize pages — create default "Page 1" for canvases without pages yet
      const loadedPages: CanvasPage[] = data.pages || []
      if (loadedPages.length === 0) {
        const defaultPage: CanvasPage = { id: crypto.randomUUID(), name: 'Page 1', order: 0 }
        setPages([defaultPage])
        setActivePageId(defaultPage.id)
        void canvasApi.updateLayoutConfig(canvasId, { pages: [defaultPage] })
      } else {
        setPages(loadedPages)
        setActivePageId(prev => {
          const still = loadedPages.find(p => p.id === prev)
          return still ? prev : loadedPages[0].id
        })
      }
      const seedDates: Record<string, { start: string; end: string }> = {}
      for (const w of ws) {
        const df = (w as WidgetWithPosition & { config?: Record<string, unknown> }).config?.date_filter as
          | { column: string; start: string; end: string | null } | undefined
        if (df?.column && df.start) {
          if (!seedDates[df.column]) {
            seedDates[df.column] = { start: df.start, end: df.end ?? df.start }
          }
        }
      }
      if (Object.keys(seedDates).length) setActiveDateRange(seedDates)
      // Auto-arrange when all widgets are stacked at x=0 (fresh canvas or
      // backend-stored default positions where y increments but x stays 0).
      const allAtX0 = ws.every(w => (w.position_x ?? 0) === 0)
      const shouldAutoArrange = allAtX0 && ws.length > 0
      const computed: GridItem[] = shouldAutoArrange
        ? autoArrange(ws)
        : ws.map(w => ({
            i: w.id,
            x: w.position_x ?? 0,
            y: w.position_y ?? 0,
            w: w.width || 6,
            h: w.height || 6,
            minW: 2,
            minH: 3,
          }))
      setLayout(computed)
      if (shouldAutoArrange) {
        void canvasApi.updateLayout(canvasId, computed.map(l => ({
          widget_id: l.i, x: l.x, y: l.y, w: l.w, h: l.h,
        })))
      }
    } catch {
      setError('Failed to load canvas')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  // Preload the project's table list once on canvas open, into the shared store,
  // so the Canvas Assistant + toolbar table picker are instant (no re-fetch).
  const loadTables = useTableScopeStore((s) => s.loadTables)
  useEffect(() => {
    if (!canvasId || !projectId) return
    const connId = widgets.find(w => w.connection_id)?.connection_id
    loadTables(canvasId, projectId, connId, canvasId)
  }, [canvasId, projectId, widgets, loadTables])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ignore when typing in inputs
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as Element)?.tagName)) return
      // N → open chat (add chart)
      if (e.key === 'n' && !e.ctrlKey && !e.metaKey) {
        setShowChat(true)
        return
      }
      // Ctrl+Z → undo
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault()
        handleUndo()
        return
      }
      // Ctrl+Y or Ctrl+Shift+Z → redo
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault()
        handleRedo()
        return
      }
      // Ctrl+S → manual save
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        handleManualSave()
        return
      }
      // + / - for zoom
      if (e.key === '=' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); setGridZoom(z => Math.min(1.5, z + 0.1)) }
      if (e.key === '-' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); setGridZoom(z => Math.max(0.5, z - 0.1)) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canUndo, canRedo])

  const persistLayout = useCallback(async (items: LayoutItem[]) => {
    setSaving(true)
    try {
      await canvasApi.updateLayout(canvasId, items.map(l => ({
        widget_id: l.i, x: l.x, y: l.y, w: l.w, h: l.h,
      })))
      setSavedOk(true)
      setIsDirty(false)
      setTimeout(() => setSavedOk(false), 2000)
    } catch { /* silently ignore */ }
    finally { setSaving(false) }
  }, [canvasId])

  const pushUndo = useCallback((snap: GridItem[]) => {
    undoStackRef.current.push(snap)
    if (undoStackRef.current.length > 30) undoStackRef.current.shift()
    redoStackRef.current = []
    setCanUndo(true)
    setCanRedo(false)
  }, [])

  const handleUndo = useCallback(() => {
    const stack = undoStackRef.current
    if (!stack.length) return
    const prev = stack.pop()!
    redoStackRef.current.push([...layout])
    setLayout(prev)
    setCanUndo(stack.length > 0)
    setCanRedo(true)
    void persistLayout(prev)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, persistLayout])

  const handleRedo = useCallback(() => {
    const stack = redoStackRef.current
    if (!stack.length) return
    const next = stack.pop()!
    undoStackRef.current.push([...layout])
    setLayout(next)
    setCanUndo(true)
    setCanRedo(stack.length > 0)
    void persistLayout(next)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, persistLayout])

  const handleLayoutChange = useCallback((newLayout: Layout, _allLayouts: Partial<Record<string, Layout>>) => {
    setLayout([...newLayout])
    setIsDirty(true)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    saveTimeoutRef.current = setTimeout(() => persistLayout([...newLayout]), 3000)
  }, [persistLayout])

  const handleDragStop = useCallback((newLayout: Layout) => {
    pushUndo([...layout])
    setLayout([...newLayout])
    void persistLayout([...newLayout])
  }, [layout, persistLayout, pushUndo])

  const handleResizeStop = useCallback((newLayout: Layout) => {
    pushUndo([...layout])
    setLayout([...newLayout])
    void persistLayout([...newLayout])
  }, [layout, persistLayout, pushUndo])

  const handleDeleteWidget = useCallback(async (widgetId: string) => {
    try {
      await widgetApi.delete(widgetId)
      setWidgets(prev => prev.filter(w => w.id !== widgetId))
      setLayout(prev => prev.filter(l => l.i !== widgetId))
    } catch { /* ignore */ }
  }, [])

  const handleUpdateWidget = useCallback(async (
    widgetId: string,
    data: { title?: string; chart_type?: string; config?: Record<string, unknown> }
  ) => {
    try {
      await widgetApi.update(widgetId, data)
      setWidgets(prev => prev.map(w => {
        if (w.id !== widgetId) return w
        return {
          ...w,
          title: data.title ?? w.title,
          chart_type: data.chart_type ?? w.chart_type,
          config: data.config ? { ...(w.config || {}), ...data.config } : w.config,
        }
      }))
    } catch { /* ignore */ }
  }, [])

  const handleDuplicate = useCallback(async (widget: CanvasWidgetData) => {
    try {
      const existing = layout.find(l => l.i === widget.id)
      await canvasApi.addWidget(canvasId, {
        title: `${widget.title} (copy)`,
        chart_type: widget.chart_type,
        chart_data: widget.chart_data as Record<string, unknown>,
        config: { ...(widget.config || {}), page_id: activePageId },
        sql_query: widget.sql_query,
        width: existing?.w ?? 6,
        height: existing?.h ?? 6,
        position_x: (existing?.x ?? 0) + 1,
        position_y: (existing?.y ?? 0) + 1,
      })
      await load()
      showToast(`Duplicated "${widget.title}"`)
    } catch { /* ignore */ }
  }, [canvasId, layout, load, activePageId])

  const handleToggleLock = useCallback((widgetId: string) => {
    setLockedWidgets(prev => {
      const next = new Set(prev)
      if (next.has(widgetId)) next.delete(widgetId)
      else next.add(widgetId)
      showToast(next.has(widgetId) ? 'Widget locked' : 'Widget unlocked')
      return next
    })
    setLayout(prev => prev.map(l =>
      l.i === widgetId ? { ...l, static: !lockedWidgets.has(widgetId) } : l
    ))
  }, [lockedWidgets])

  // Re-execute SQL on the server (replacing cached chart_data with live results),
  // THEN reload the canvas. Without the refresh-now call we'd just re-read the same
  // stale cache and falsely report "refreshed".
  const handleRefreshWidget = useCallback(async (widgetId: string) => {
    setRefreshingId(widgetId)
    try {
      // Re-run ONLY this widget's query (not the whole dashboard), then re-read
      // its fresh data silently (no full-page spinner).
      await scheduleApi.refreshWidget(canvasId, widgetId)
      await load(true)
      showToast('Data refreshed from database')
    } catch {
      showToast('Refresh failed')
    } finally {
      setRefreshingId(null)
    }
  }, [canvasId, load])

  const handleRefreshAll = useCallback(async () => {
    setRefreshingId('all')
    try {
      const resp = await scheduleApi.refreshNow(canvasId)
      await load()
      const r = (resp?.data ?? {}) as { refreshed?: number; total?: number }
      showToast(
        typeof r.refreshed === 'number'
          ? `Refreshed ${r.refreshed}/${r.total ?? r.refreshed} widgets`
          : 'All widgets refreshed',
      )
    } catch {
      showToast('Refresh failed')
    } finally {
      setRefreshingId(null)
    }
  }, [canvasId, load])

  const handleApplyDateFilter = async () => {
    const active = Object.fromEntries(
      Object.entries(activeDateRange).filter(([, v]) => v.start && v.end)
    )
    if (!Object.keys(active).length) return
    setIsRequeryingDate(true)
    try {
      const resp = await canvasApi.requery(canvasId, active)
      const updatedWidgets: WidgetWithPosition[] = (resp.data.widgets || []) as WidgetWithPosition[]
      setWidgets(prev =>
        prev.map(w => {
          const upd = updatedWidgets.find(u => u.id === w.id)
          return upd ? { ...w, chart_data: upd.chart_data } : w
        })
      )
    } catch { /* ignore */ }
    setIsRequeryingDate(false)
  }

  const handleManualSave = () => {
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    persistLayout(layout)
  }

  // ── Page management ────────────────────────────────────────────────────────
  const handleAddPage = useCallback(async () => {
    const newPage: CanvasPage = {
      id: crypto.randomUUID(),
      name: `Page ${pages.length + 1}`,
      order: pages.length,
    }
    const newPages = [...pages, newPage]
    setPages(newPages)
    setActivePageId(newPage.id)
    await savePagesConfig(newPages)
  }, [pages, savePagesConfig])

  const handleRenamePage = useCallback(async (pageId: string, newName: string) => {
    const newPages = pages.map(p => p.id === pageId ? { ...p, name: newName } : p)
    setPages(newPages)
    await savePagesConfig(newPages)
  }, [pages, savePagesConfig])

  const handleDeletePage = useCallback(async (pageId: string) => {
    if (pages.length <= 1) return
    const defaultPageId = pages[0].id
    const pageWidgets = widgets.filter(w => {
      const wPid = (w.config?.page_id as string) || ''
      return wPid ? wPid === pageId : pageId === defaultPageId
    })
    await Promise.all(pageWidgets.map(w => widgetApi.delete(w.id)))
    const newPages = pages.filter(p => p.id !== pageId).map((p, i) => ({ ...p, order: i }))
    setPages(newPages)
    if (activePageId === pageId) setActivePageId(newPages[0].id)
    await savePagesConfig(newPages)
    await load()
    showToast('Page deleted')
  }, [pages, widgets, activePageId, savePagesConfig, load])

  const handleDuplicatePage = useCallback(async (pageId: string) => {
    const sourcePage = pages.find(p => p.id === pageId)
    if (!sourcePage) return
    const newPageId = crypto.randomUUID()
    const defaultPageId = pages[0]?.id ?? ''
    const pageWidgets = widgets.filter(w => {
      const wPid = (w.config?.page_id as string) || ''
      return wPid ? wPid === pageId : pageId === defaultPageId
    })
    await Promise.all(pageWidgets.map(w => {
      const existing = layout.find(l => l.i === w.id)
      return canvasApi.addWidget(canvasId, {
        title: w.title,
        chart_type: w.chart_type,
        sql_query: w.sql_query,
        chart_data: w.chart_data as Record<string, unknown>,
        config: { ...(w.config || {}), page_id: newPageId },
        width: existing?.w ?? 6,
        height: existing?.h ?? 6,
        connection_id: w.connection_id,
      })
    }))
    const newPage: CanvasPage = { id: newPageId, name: `${sourcePage.name} (copy)`, order: pages.length }
    const newPages = [...pages, newPage]
    setPages(newPages)
    setActivePageId(newPageId)
    await savePagesConfig(newPages)
    await load()
    showToast(`Duplicated "${sourcePage.name}"`)
  }, [pages, widgets, layout, canvasId, savePagesConfig, load])

  const handleTitleSave = async () => {
    setEditingTitle(false)
    const trimmed = titleValue.trim()
    if (!trimmed || trimmed === canvas?.name) return
    try {
      await canvasApi.rename(canvas!.id, trimmed)
      setCanvas(prev => prev ? { ...prev, name: trimmed } : prev)
    } catch { /* ignore */ }
  }

  const handleAutoArrange = useCallback(() => {
    pushUndo([...layout])
    const arranged = autoArrange(widgets)
    setLayout(arranged)
    setIsDirty(false)
    persistLayout(arranged)
  }, [widgets, persistLayout, layout, pushUndo])

  const handleCopyLink = () => {
    try {
      navigator.clipboard.writeText(window.location.href)
      showToast('Canvas link copied!')
    } catch { showToast('Could not copy link') }
  }

  const handleExportJSON = async () => {
    const data = {
      canvas: { id: canvas?.id, name: canvas?.name },
      widgets: widgets.map(w => ({
        id: w.id, title: w.title, chart_type: w.chart_type,
        sql_query: w.sql_query, config: w.config,
      })),
      layout,
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const suggestedName = `${canvas?.name ?? 'canvas'}.json`

    if ('showSaveFilePicker' in window) {
      try {
        const handle = await (window as any).showSaveFilePicker({
          suggestedName,
          types: [{ description: 'JSON file', accept: { 'application/json': ['.json'] } }],
        })
        const writable = await handle.createWritable()
        await writable.write(blob)
        await writable.close()
        showToast('JSON exported')
        return
      } catch (e: any) {
        if (e?.name === 'AbortError') return
      }
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = suggestedName
    a.click()
    URL.revokeObjectURL(url)
    showToast('JSON exported')
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500">
        <AlertCircle className="w-10 h-10 text-red-400" />
        <p className="text-sm">{error}</p>
        <button onClick={() => router.back()} className="text-sm text-blue-600 hover:underline">Go back</button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-gray-50">
      {/* Toast */}
      {toastMsg && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-4 py-2 bg-gray-900 text-white text-sm rounded-xl shadow-lg pointer-events-none select-none">
          {toastMsg}
        </div>
      )}

      {/* Top bar */}
      {/* ── Ribbon ── Title bar ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-white border-b border-gray-200 flex-shrink-0"
        style={{ background: 'linear-gradient(180deg,#f8fafd 0%,#f1f5f9 100%)' }}>
        <button
          onClick={() => router.push(`/projects/${projectId}/canvas`)}
          className="p-1 text-gray-400 hover:text-gray-700 rounded hover:bg-white/80 transition-colors flex-shrink-0"
          title="Back to canvases"
        >
          <ChevronLeft size={16} />
        </button>
        <Layers size={14} className="text-blue-600 flex-shrink-0" />
        {editingTitle ? (
          <input
            autoFocus
            value={titleValue}
            onChange={e => setTitleValue(e.target.value)}
            onBlur={handleTitleSave}
            onKeyDown={e => {
              if (e.key === 'Enter') handleTitleSave()
              if (e.key === 'Escape') { setTitleValue(canvas?.name ?? ''); setEditingTitle(false) }
            }}
            className="text-sm font-semibold text-gray-900 bg-white border border-blue-400 rounded px-1.5 outline-none min-w-0 w-44"
          />
        ) : (
          <button
            onClick={() => { setTitleValue(canvas?.name ?? ''); setEditingTitle(true) }}
            className="group/title flex items-center gap-1 text-sm font-semibold text-gray-800 hover:text-blue-600 transition-colors min-w-0"
            title="Click to rename"
          >
            <span className="truncate max-w-[200px]">{canvas?.name}</span>
            <Pencil size={11} className="text-gray-300 group-hover/title:text-blue-500 shrink-0 transition-colors" />
          </button>
        )}
        <div className="flex items-center gap-1 ml-1 flex-shrink-0 text-xs">
          {saving && <span className="flex items-center gap-1 text-gray-400"><Loader2 size={11} className="animate-spin" /> Saving…</span>}
          {savedOk && <span className="flex items-center gap-1 text-green-600"><CheckCircle2 size={11} /> Saved</span>}
          {isDirty && !saving && !savedOk && (
            <button onClick={handleManualSave} className="flex items-center gap-1 px-2 py-0.5 text-xs font-medium text-white bg-blue-600 rounded hover:bg-blue-700 transition-colors">
              <Save size={11} /> Save
            </button>
          )}
        </div>
      </div>

      {/* ── Ribbon ── Command bar ───────────────────────────────────────────────── */}
      <div className="flex items-end gap-0 px-0 bg-white border-b border-gray-200 flex-shrink-0 overflow-x-auto"
        style={{ background: 'white' }}>

        {/* Helper for ribbon groups */}
        {/* Group: Edit */}
        <div className="flex flex-col items-center px-3 py-1 border-r border-gray-100">
          <div className="flex items-center gap-0.5">
            <button onClick={handleUndo} disabled={!canUndo} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 disabled:opacity-30 transition-colors group" title="Undo (Ctrl+Z)">
              <RotateCcw size={16} className="text-gray-600" />
              <span className="text-[9px] text-gray-500">Undo</span>
            </button>
            <button onClick={handleRedo} disabled={!canRedo} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 disabled:opacity-30 transition-colors scale-x-[-1]" title="Redo (Ctrl+Y)">
              <RotateCcw size={16} className="text-gray-600" />
            </button>
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">Edit</span>
        </div>

        {/* Group: View */}
        <div className="flex flex-col items-center px-3 py-1 border-r border-gray-100">
          <div className="flex items-center gap-0.5">
            <button onClick={() => setGridZoom(z => Math.max(0.5, z - 0.1))} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 transition-colors" title="Zoom out">
              <ZoomOut size={16} className="text-gray-600" />
              <span className="text-[9px] text-gray-500">Out</span>
            </button>
            <span className="text-xs font-semibold text-gray-600 min-w-[36px] text-center px-1 py-1 bg-gray-50 rounded border border-gray-200 select-none" style={{ fontSize: 11 }}>
              {Math.round(gridZoom * 100)}%
            </span>
            <button onClick={() => setGridZoom(z => Math.min(1.5, z + 0.1))} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 transition-colors" title="Zoom in">
              <ZoomIn size={16} className="text-gray-600" />
              <span className="text-[9px] text-gray-500">In</span>
            </button>
            <button
              onClick={() => { setIsViewOnly(v => !v); showToast(!isViewOnly ? 'View-only mode on' : 'Edit mode') }}
              className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded transition-colors ${isViewOnly ? 'bg-amber-50 text-amber-600' : 'hover:bg-gray-100 text-gray-600'}`}
              title={isViewOnly ? 'Exit view-only mode' : 'Enter view-only mode'}
            >
              {isViewOnly ? <EyeOff size={16} /> : <Eye size={16} />}
              <span className="text-[9px]">View</span>
            </button>
            <button onClick={handleRefreshAll} disabled={refreshingId === 'all'} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 disabled:opacity-50 transition-colors text-gray-600" title="Refresh all">
              <RefreshCw size={16} className={refreshingId === 'all' ? 'animate-spin' : ''} />
              <span className="text-[9px] text-gray-500">Refresh</span>
            </button>
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">View</span>
        </div>

        {/* Group: Insert */}
        <div className="flex flex-col items-center px-3 py-1 border-r border-gray-100">
          <div className="flex items-center gap-0.5">
            {!isViewOnly && (
              <button onClick={() => setShowChat(true)} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-blue-50 hover:text-blue-700 transition-colors text-gray-600" title="Add chart">
                <Plus size={16} />
                <span className="text-[9px]">Add Chart</span>
              </button>
            )}
            {widgets.length > 0 && (
              <button onClick={handleAutoArrange} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 transition-colors text-gray-600" title="Auto-arrange">
                <LayoutGrid size={16} />
                <span className="text-[9px]">Auto</span>
              </button>
            )}
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">Insert</span>
        </div>

        {/* Group: Share */}
        <div className="flex flex-col items-center px-3 py-1 border-r border-gray-100">
          <div className="flex items-center gap-0.5">
            <button onClick={handleCopyLink} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 transition-colors text-gray-600" title="Copy link">
              <Link2 size={16} />
              <span className="text-[9px]">Copy Link</span>
            </button>
            <button onClick={handleExportJSON} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-gray-100 transition-colors text-gray-600" title="Export JSON">
              <FileJson size={16} />
              <span className="text-[9px]">JSON</span>
            </button>
            <button onClick={() => setShowExport(true)} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-teal-50 hover:text-teal-700 transition-colors text-gray-600" title="Export .vly">
              <FileDown size={16} />
              <span className="text-[9px]">Export .vly</span>
            </button>
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">Share</span>
        </div>

        {/* Group: Configure */}
        <div className="flex flex-col items-center px-3 py-1 border-r border-gray-100">
          <div className="flex items-center gap-0.5">
            <button onClick={() => setShowMeasures(true)} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-purple-50 hover:text-purple-700 transition-colors text-gray-600" title="Calculated Measures">
              <FunctionSquare size={16} />
              <span className="text-[9px]">Measures</span>
            </button>
            <button onClick={() => setShowSchedule(true)} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-green-50 hover:text-green-700 transition-colors text-gray-600" title="Schedule Refresh">
              <Clock size={16} />
              <span className="text-[9px]">Schedule</span>
            </button>
            <button onClick={() => setShowRLS(true)} className="flex flex-col items-center gap-0.5 px-2 py-1 rounded hover:bg-orange-50 hover:text-orange-700 transition-colors text-gray-600" title="Row-Level Security">
              <Shield size={16} />
              <span className="text-[9px]">RLS</span>
            </button>
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">Configure</span>
        </div>

        {/* Group: AI — primary actions, visually prominent */}
        <div className="flex flex-col items-center px-3 py-1">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowChat(v => !v)}
              className={`flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-md border transition-colors ${showChat ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-gray-600 border-gray-200 hover:bg-blue-50 hover:text-blue-700 hover:border-blue-300'}`}
            >
              <MessageSquare size={17} />
              <span className="text-[9px] font-semibold">AI Chat</span>
            </button>
            {/* Table scope — same control as in the chat, shared via the store.
                Rendered in a portal (below) so it can't be hidden behind the canvas grid. */}
            <button
              ref={tablesBtnRef}
              onClick={() => {
                const r = tablesBtnRef.current?.getBoundingClientRect()
                if (r) setTablesPopPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) })
                setShowTablePicker(v => !v)
              }}
              className={`flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-md border transition-colors ${showTablePicker ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-gray-600 border-gray-200 hover:bg-blue-50 hover:text-blue-700 hover:border-blue-300'}`}
              title="Choose which tables the AI assistant focuses on"
            >
              <Table2 size={17} />
              <span className="text-[9px] font-semibold">Tables</span>
            </button>
            {showTablePicker && typeof document !== 'undefined' && createPortal(
              <>
                <div style={{ position: 'fixed', inset: 0, zIndex: 9998 }} onClick={() => setShowTablePicker(false)} />
                <div style={{
                  position: 'fixed',
                  top: tablesPopPos?.top ?? 64,
                  right: tablesPopPos?.right ?? 12,
                  width: 320,
                  zIndex: 9999,
                  borderRadius: 12,
                  background: '#fff',
                  border: '1px solid #e2e8f0',
                  boxShadow: '0 16px 40px rgba(10,33,58,0.18)',
                }}>
                  <TableScopePicker canvasId={canvasId} />
                </div>
              </>,
              document.body,
            )}
            {showExport && (
              <VlyExportModal
                canvasId={canvasId}
                onClose={() => setShowExport(false)}
              />
            )}
            <button
              onClick={() => setShowReport(true)}
              className="flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-md text-white border border-transparent transition-all hover:opacity-90"
              style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
              title="View as rich report"
            >
              <Sparkles size={17} />
              <span className="text-[9px] font-semibold">Visually</span>
            </button>
            <button
              onClick={() => router.push(`/intelligence/${canvasId}`)}
              className="flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-md text-white border border-transparent transition-all hover:opacity-90"
              style={{ background: 'linear-gradient(135deg, #00a9d4, #16c0e8)' }}
              title="Open Executive Intelligence"
            >
              <Zap size={17} />
              <span className="text-[9px] font-semibold">Intelligence</span>
            </button>
          </div>
          <span className="text-[9px] text-gray-400 uppercase tracking-wider mt-0.5">AI</span>
        </div>
      </div>

      {/* Date filter bar */}
      {(canvas?.filter_config?.filter(f => f.filter_type === 'date_range') ?? []).length > 0 && (
        <div className="flex items-center gap-3 px-4 py-2 bg-blue-50 border-b border-blue-100 flex-shrink-0 flex-wrap">
          <Calendar size={14} className="text-blue-600 flex-shrink-0" />
          <span className="text-xs font-semibold text-blue-700">Date filter</span>
          {canvas!.filter_config!.filter(f => f.filter_type === 'date_range').map(fc => (
            <div key={fc.column} className="flex items-center gap-1.5">
              <span className="text-xs text-blue-600 font-medium">{fc.display_name}</span>
              <input
                type="date"
                value={activeDateRange[fc.column]?.start || ''}
                onChange={e =>
                  setActiveDateRange(prev => ({
                    ...prev,
                    [fc.column]: { ...prev[fc.column], start: e.target.value },
                  }))
                }
                className="text-xs border border-blue-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
              <span className="text-xs text-gray-400">→</span>
              <input
                type="date"
                value={activeDateRange[fc.column]?.end || ''}
                onChange={e =>
                  setActiveDateRange(prev => ({
                    ...prev,
                    [fc.column]: { ...prev[fc.column], end: e.target.value },
                  }))
                }
                className="text-xs border border-blue-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
          ))}
          <button
            onClick={handleApplyDateFilter}
            disabled={isRequeryingDate}
            className="flex items-center gap-1.5 px-3 py-1 text-xs font-semibold text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {isRequeryingDate && <Loader2 size={11} className="animate-spin" />}
            Apply to all charts
          </button>
        </div>
      )}

      {/* Body */}
      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        {/* Canvas grid + page tabs — always full width, chat overlays on top */}
        <div className="flex flex-col flex-1 min-w-0 min-h-0">
        <div className="flex-1 overflow-auto min-w-0" style={{
          backgroundColor: '#f1f5f9',
          backgroundImage: 'radial-gradient(circle, #cbd5e1 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          padding: '20px',
        }}>
          {/* Power BI–style white page sheet */}
          <div style={{
            background: 'white',
            border: '2px dashed #CBD5E1',
            borderRadius: 4,
            minHeight: '70vh',
            overflow: 'hidden',
          }}>
          {(() => {
            const defaultPageId = pages[0]?.id ?? ''
            const activePageWidgets = widgets.filter(w => {
              const wPageId = (w.config?.page_id as string) || ''
              return wPageId ? wPageId === activePageId : activePageId === defaultPageId
            })
            const activePageLayout = layout.filter(l => activePageWidgets.some(w => w.id === l.i))
            return activePageWidgets.length === 0 ? (
            // Enhanced 3-step empty state
            <div className="flex flex-col items-center justify-center h-full min-h-64 text-gray-400 gap-6 py-12">
              <div className="flex gap-2 opacity-30">
                {[40, 60, 40, 80].map((h, i) => (
                  <div key={i} className="w-8 rounded-t-sm bg-blue-400" style={{ height: h }} />
                ))}
              </div>
              <div className="text-center">
                <p className="font-semibold text-gray-600 text-base">Your canvas is empty</p>
                <p className="text-sm mt-1 text-gray-400 max-w-sm">
                  Build your first visualization in 3 steps
                </p>
              </div>
              <div className="flex gap-4 flex-wrap justify-center">
                {[
                  { step: '1', label: 'Connect data', desc: 'Add a database or CSV in project settings' },
                  { step: '2', label: 'Ask the AI', desc: 'Describe a chart in natural language' },
                  { step: '3', label: 'Arrange & share', desc: 'Drag, resize, then open Visually' },
                ].map(s => (
                  <div key={s.step} className="flex flex-col items-center gap-1.5 w-36 text-center">
                    <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-600 font-bold text-sm flex items-center justify-center">
                      {s.step}
                    </div>
                    <span className="text-xs font-semibold text-gray-600">{s.label}</span>
                    <span className="text-xs text-gray-400">{s.desc}</span>
                  </div>
                ))}
              </div>
              <button
                onClick={() => setShowChat(true)}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 transition-colors"
              >
                <MessageSquare size={14} /> Open AI Chat
              </button>
            </div>
            ) : (
              <div style={{ transform: `scale(${gridZoom})`, transformOrigin: 'top left', transition: 'transform 0.15s ease' }}>
                <ResponsiveGrid
                  className="layout"
                  layouts={{ lg: activePageLayout, md: activePageLayout, sm: activePageLayout }}
                  breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
                  cols={{ lg: 12, md: 12, sm: 6, xs: 4, xxs: 2 }}
                  rowHeight={70}
                  onLayoutChange={handleLayoutChange}
                  onDragStop={(newLayout) => handleDragStop(newLayout)}
                  onResizeStop={(newLayout) => handleResizeStop(newLayout)}
                  draggableHandle=".drag-handle"
                  isDraggable={!isViewOnly}
                  isResizable={!isViewOnly}
                  margin={[12, 12]}
                  containerPadding={[4, 4]}
                  resizeHandles={['se', 'e', 's']}
                >
                  {activePageWidgets.map(widget => (
                    <div key={widget.id} className="overflow-hidden rounded-xl">
                      <CanvasWidget
                        widget={widget}
                        onDelete={handleDeleteWidget}
                        onUpdate={handleUpdateWidget}
                        onZoom={(w, cols) => setZoomTarget({ widget: w, colors: cols })}
                        onDuplicate={handleDuplicate}
                        onRefresh={handleRefreshWidget}
                        onToggleLock={handleToggleLock}
                        isLocked={lockedWidgets.has(widget.id)}
                        isRefreshing={refreshingId === widget.id}
                      />
                    </div>
                  ))}
                </ResponsiveGrid>
              </div>
            )
          })()}
          </div>{/* /page sheet */}
        </div>

        {/* Page tabs */}
        {pages.length > 0 && (
          <CanvasPageTabs
            pages={pages}
            activePageId={activePageId}
            onSwitch={setActivePageId}
            onAdd={handleAddPage}
            onRename={handleRenamePage}
            onDelete={handleDeletePage}
            onDuplicate={handleDuplicatePage}
            onReorder={async (newPages) => {
              setPages(newPages)
              await savePagesConfig(newPages)
              showToast('Pages reordered')
            }}
          />
        )}
        </div>

        {/* Chat panel — absolute overlay so grid width is never affected */}
        {showChat && (
          <div style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, zIndex: 40,
            display: 'flex', flexDirection: 'column',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.10)',
          }}>
            <CanvasChatPanel
              projectId={projectId}
              canvasId={canvasId}
              widgets={widgets}
              pages={pages}
              activePageId={activePageId}
              onClose={() => setShowChat(false)}
              onWidgetAdded={() => load(true)}
            />
          </div>
        )}
      </div>

      {/* Zoom modal */}
      {zoomTarget && (
        <ZoomModal
          widget={zoomTarget.widget}
          colors={zoomTarget.colors}
          onClose={() => setZoomTarget(null)}
        />
      )}

      {/* Visually report overlay */}
      {showReport && canvas && (
        <VisuallReport
          canvas={{ id: canvas.id, name: canvas.name, project_id: canvas.project_id, filter_config: canvas.filter_config }}
          widgets={widgets}
          pages={pages}
          initialPageId={activePageId}
          projectId={projectId}
          onClose={() => setShowReport(false)}
          onWidgetAdded={load}
          onPageRename={handleRenamePage}
          onPageDelete={handleDeletePage}
          onPageDuplicate={handleDuplicatePage}
          onPageReorder={async (newPages) => {
            setPages(newPages)
            await canvasApi.updateLayoutConfig(canvasId, { pages: newPages })
          }}
        />
      )}

      {/* Tier 5: Calculated Measures */}
      {showMeasures && (
        <MeasuresPanel
          canvasId={canvasId}
          onClose={() => setShowMeasures(false)}
        />
      )}

      {/* Tier 5: Schedule Refresh */}
      {showSchedule && (
        <ScheduleRefreshModal
          canvasId={canvasId}
          onClose={() => setShowSchedule(false)}
          onRefreshedNow={load}
        />
      )}

      {/* Tier 5: Row-Level Security */}
      {showRLS && (
        <RLSModal
          canvasId={canvasId}
          onClose={() => setShowRLS(false)}
        />
      )}
    </div>
  )
}
