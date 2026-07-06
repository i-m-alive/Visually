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
  FunctionSquare, Clock, Shield, FileDown, Zap, Table2, Database, Type,
  Bell, History as HistoryIcon, Image as ImageIcon, ChevronDown,
} from 'lucide-react'
import { canvasApi, widgetApi, vlyApi, scheduleApi, versionsApi } from '@/lib/api'
import { HistoryDrawer, AlertsModal, ExplainPopover } from '@/components/canvas/AgentPanels'
import { CanvasWidget, type CanvasWidgetData } from '@/components/canvas/CanvasWidget'
import { ZoomModal } from '@/components/canvas/ZoomModal'
import { CanvasChatPanel } from '@/components/canvas/CanvasChatPanel'
import { TableScopePicker } from '@/components/canvas/TableScopePicker'
import { useTableScopeStore } from '@/stores/tableScopeStore'
import { VlyExportModal } from '@/components/canvas/VlyExportModal'
import { ConnectLiveDbModal } from '@/components/canvas/ConnectLiveDbModal'
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
  is_offline?: boolean
  connection_hint?: import('@/components/canvas/ConnectLiveDbModal').ConnHint
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
  const [showConnectDb, setShowConnectDb] = useState(false)
  const [tablesPopPos, setTablesPopPos] = useState<{ top: number; right: number } | null>(null)
  const tablesBtnRef = useRef<HTMLButtonElement>(null)
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
  const [chatPrefill, setChatPrefill]       = useState<string | undefined>(undefined)
  // Fixed-width page sheet (Power BI-style document). Grid always lays out at
  // this width; the sheet scales to fit the viewport (letterboxed).
  const PAGE_W = 1280
  const canvasScrollRef = useRef<HTMLDivElement>(null)
  const hasAutoFitRef   = useRef(false)
  const [isViewOnly, setIsViewOnly]         = useState(false)
  const [showMeasures, setShowMeasures]     = useState(false)
  const [shareMenu, setShareMenu]           = useState(false)
  const [configMenu, setConfigMenu]         = useState(false)
  // Positions for portal-rendered dropdowns (fixed to viewport so they escape
  // the toolbar's overflow-x:auto clipping context).
  const [shareMenuPos, setShareMenuPos]     = useState<{ top: number; left: number } | null>(null)
  const [configMenuPos, setConfigMenuPos]   = useState<{ top: number; left: number } | null>(null)
  const shareBtnRef  = useRef<HTMLButtonElement>(null)
  const configBtnRef = useRef<HTMLButtonElement>(null)
  const [showAlerts, setShowAlerts]         = useState(false)
  const [showHistory, setShowHistory]       = useState(false)
  const [explainTarget, setExplainTarget]   = useState<{ widgetId: string; column: string; value: string } | null>(null)
  const [showSchedule, setShowSchedule]     = useState(false)
  const [showRLS, setShowRLS]               = useState(false)
  const [toastMsg, setToastMsg]             = useState<string | null>(null)
  const [canUndo, setCanUndo]               = useState(false)
  const [canRedo, setCanRedo]               = useState(false)
  // Ribbon collapsed = only identity + assistant visible (Power-BI/Word style)
  const [ribbonCollapsed, setRibbonCollapsed] = useState(false)

  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const undoStackRef   = useRef<GridItem[][]>([])
  const redoStackRef   = useRef<GridItem[][]>([])
  const toastTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Set to true while load() is updating the layout programmatically so that
  // handleLayoutChange doesn't treat it as a user edit (isDirty / save timer).
  const programmaticLayoutRef = useRef(false)

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

      // Restore persisted widget locks (config.locked)
      const locked = new Set(ws.filter(w => (w.config as Record<string, unknown> | undefined)?.locked === true).map(w => w.id))
      setLockedWidgets(locked)

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
            static: locked.has(w.id),
          }))
      programmaticLayoutRef.current = true
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

  // Silent background sync: scheduled/cron refreshes update the DB, but an open
  // canvas never re-read it — users saw stale widgets until a manual page reload.
  // Poll every 2 min while the tab is visible and there are no unsaved edits.
  useEffect(() => {
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible' && !isDirty) {
        load(true)
      }
    }, 120_000)
    return () => clearInterval(id)
  }, [load, isDirty])

  // Zoom-to-fit once on first render: scale the 1280px page to the available
  // width (capped at 100%). Manual zoom controls still override afterwards.
  useEffect(() => {
    if (loading || hasAutoFitRef.current) return
    const el = canvasScrollRef.current
    if (!el) return
    hasAutoFitRef.current = true
    const avail = el.clientWidth - 56
    if (avail > 0 && avail < PAGE_W) {
      setGridZoom(Math.max(0.5, Math.round((avail / PAGE_W) * 100) / 100))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading])

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
    // Suppress dirty-marking when the layout was set programmatically by load()
    if (programmaticLayoutRef.current) {
      programmaticLayoutRef.current = false
      setLayout([...newLayout])
      return
    }
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

  // Insert a static text widget on the active page (also creatable via copilot)
  const handleInsertText = useCallback(async () => {
    try {
      await canvasApi.addWidget(canvasId, {
        title: 'Text',
        chart_type: 'text',
        chart_data: { rows: [], columns: [], labels: [], values: [] },
        config: { page_id: activePageId, content: '# New text\nDouble-click to edit', widget_type: 'text' },
        sql_query: '',
        width: 4,
        height: 3,
        position_x: 0,
        position_y: 0,
      })
      await load(true)
      showToast('Text box added')
    } catch { showToast('Failed to add text box') }
  }, [canvasId, activePageId, load])

  const handleInsertImage = useCallback(async () => {
    const url = window.prompt('Image URL (or data URI) — you can also add it later by clicking the widget:') ?? ''
    try {
      await canvasApi.addWidget(canvasId, {
        title: 'Image',
        chart_type: 'image',
        chart_data: { rows: [], columns: [], labels: [], values: [] },
        config: { page_id: activePageId, src: url, widget_type: 'image' },
        sql_query: '',
        width: 4,
        height: 4,
        position_x: 0,
        position_y: 0,
      })
      await load(true)
      showToast('Image widget added')
    } catch { showToast('Failed to add image') }
  }, [canvasId, activePageId, load])

  const handleToggleLock = useCallback((widgetId: string) => {
    const nowLocked = !lockedWidgets.has(widgetId)
    setLockedWidgets(prev => {
      const next = new Set(prev)
      if (nowLocked) next.add(widgetId)
      else next.delete(widgetId)
      showToast(nowLocked ? 'Widget locked' : 'Widget unlocked')
      return next
    })
    setLayout(prev => prev.map(l =>
      l.i === widgetId ? { ...l, static: nowLocked } : l
    ))
    // Persist so locks survive reload (previously in-memory only)
    void widgetApi.update(widgetId, { config: { locked: nowLocked } }).catch(() => {})
    setWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, config: { ...(w.config || {}), locked: nowLocked } } : w
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
      const resp = await scheduleApi.refreshWidget(canvasId, widgetId)
      console.log('[refresh] widget', widgetId, resp?.data)
      await load(true)
      const r = (resp?.data ?? {}) as { refreshed?: number; errors?: { error: string }[] }
      if (r.refreshed === 0) {
        showToast(`Refresh failed: ${r.errors?.[0]?.error?.slice(0, 80) || 'widget skipped (no SQL/connection)'}`)
      } else {
        showToast('Data refreshed from database')
      }
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
      console.log('[refresh] all widgets', resp?.data)
      // Silent reload: keep widgets mounted and update their data in-place so
      // the user sees the chart update without a full spinner/unmount cycle.
      await load(true)
      const r = (resp?.data ?? {}) as { refreshed?: number; total?: number; skipped?: number; errors?: { error: string }[] }
      showToast(
        typeof r.refreshed === 'number'
          ? `Refreshed ${r.refreshed}/${r.total ?? r.refreshed} widgets${r.skipped ? ` (${r.skipped} skipped)` : ''}${r.errors?.length ? ` — ${r.errors.length} failed, see console` : ''}`
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
    // Auto-capture a version on every explicit save (backend debounces dupes)
    void versionsApi.snapshot(canvasId).catch(() => {})
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

      {/* ── Unified toolbar ─────────────────────────────────────────────────────
          Dropdowns use createPortal + position:fixed so they are never clipped
          by the toolbar's overflow-x:auto scroll container.
          ribbonCollapsed hides the tool sections (Word-style). */}
      <div className="flex items-center gap-1 h-[52px] px-3 bg-white/90 backdrop-blur border-b border-gray-200 flex-shrink-0 overflow-x-auto whitespace-nowrap">

        {/* Identity — always visible */}
        <button
          onClick={() => router.push(`/projects/${projectId}/canvas`)}
          className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-800 hover:bg-gray-100 transition-colors flex-shrink-0"
          title="Back to canvases"
        >
          <ChevronLeft size={17} />
        </button>
        <div className="h-8 w-8 inline-flex items-center justify-center rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 flex-shrink-0 shadow-sm">
          <Layers size={15} className="text-white" />
        </div>
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
            className="text-[15px] font-bold text-gray-900 bg-white border border-blue-400 rounded-lg px-2 py-1 outline-none ring-2 ring-blue-100 min-w-0 w-52 flex-shrink-0"
          />
        ) : (
          <button
            onClick={() => { setTitleValue(canvas?.name ?? ''); setEditingTitle(true) }}
            className="group/title flex items-center gap-1.5 px-1.5 text-[15px] font-bold text-gray-900 hover:text-blue-700 transition-colors min-w-0 flex-shrink-0"
            title="Click to rename"
          >
            <span className="truncate max-w-[220px]">{canvas?.name}</span>
            <Pencil size={12} className="text-transparent group-hover/title:text-blue-400 shrink-0 transition-colors" />
          </button>
        )}
        <div className="flex items-center flex-shrink-0 text-xs mr-1">
          {saving && <span className="flex items-center gap-1 px-2 py-1 text-gray-400"><Loader2 size={11} className="animate-spin" /> Saving</span>}
          {savedOk && <span className="flex items-center gap-1 px-2 py-1 text-green-600 bg-green-50 rounded-full"><CheckCircle2 size={11} /> Saved</span>}
          {isDirty && !saving && !savedOk && (
            <button onClick={handleManualSave} className="flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-white bg-blue-600 rounded-full hover:bg-blue-700 transition-colors shadow-sm">
              <Save size={11} /> Save
            </button>
          )}
        </div>

        {/* Tool sections — hidden when ribbon is collapsed */}
        {!ribbonCollapsed && (
          <>
            <div className="w-px h-5 bg-gray-200 mx-1 flex-shrink-0" />

            {/* Undo / Redo */}
            <button onClick={handleUndo} disabled={!canUndo} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-25 disabled:hover:bg-transparent transition-colors flex-shrink-0" title="Undo (Ctrl+Z)">
              <RotateCcw size={15} />
            </button>
            <button onClick={handleRedo} disabled={!canRedo} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-25 disabled:hover:bg-transparent transition-colors scale-x-[-1] flex-shrink-0" title="Redo (Ctrl+Y)">
              <RotateCcw size={15} />
            </button>

            <div className="w-px h-5 bg-gray-200 mx-1 flex-shrink-0" />

            {/* Zoom cluster */}
            <div className="flex items-center bg-gray-50 border border-gray-200 rounded-lg overflow-hidden flex-shrink-0">
              <button onClick={() => setGridZoom(z => Math.max(0.5, Math.round((z - 0.1) * 10) / 10))} className="h-7 w-7 inline-flex items-center justify-center text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors" title="Zoom out (Ctrl −)">
                <ZoomOut size={13} />
              </button>
              <span className="text-[11px] font-semibold text-gray-600 min-w-[38px] text-center select-none tabular-nums">
                {Math.round(gridZoom * 100)}%
              </span>
              <button onClick={() => setGridZoom(z => Math.min(1.5, Math.round((z + 0.1) * 10) / 10))} className="h-7 w-7 inline-flex items-center justify-center text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors" title="Zoom in (Ctrl +)">
                <ZoomIn size={13} />
              </button>
            </div>
            <button
              onClick={() => { setIsViewOnly(v => !v); showToast(!isViewOnly ? 'View-only mode on' : 'Edit mode') }}
              className={`h-8 w-8 inline-flex items-center justify-center rounded-lg transition-colors flex-shrink-0 ${isViewOnly ? 'bg-amber-100 text-amber-600' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'}`}
              title={isViewOnly ? 'Exit view-only mode' : 'View-only mode'}
            >
              {isViewOnly ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
            <button onClick={handleRefreshAll} disabled={refreshingId === 'all'} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-50 transition-colors flex-shrink-0" title="Refresh all widgets">
              <RefreshCw size={15} className={refreshingId === 'all' ? 'animate-spin text-blue-600' : ''} />
            </button>

            <div className="w-px h-5 bg-gray-200 mx-1 flex-shrink-0" />

            {/* Insert */}
            {!isViewOnly && (
              <>
                <button onClick={() => setShowChat(true)} className="h-8 inline-flex items-center gap-1.5 px-3 rounded-lg text-sm font-semibold text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-100 transition-colors flex-shrink-0" title="Ask the AI to build a chart">
                  <Plus size={15} /> Chart
                </button>
                <button onClick={handleInsertText} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors flex-shrink-0" title="Insert text box">
                  <Type size={15} />
                </button>
                <button onClick={handleInsertImage} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors flex-shrink-0" title="Insert image">
                  <ImageIcon size={15} />
                </button>
              </>
            )}
            {widgets.length > 0 && (
              <button onClick={handleAutoArrange} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-900 hover:bg-gray-100 transition-colors flex-shrink-0" title="Auto-arrange widgets">
                <LayoutGrid size={15} />
              </button>
            )}

            <div className="w-px h-5 bg-gray-200 mx-1 flex-shrink-0" />

            {/* Share — button only; dropdown rendered in portal below toolbar */}
            <button
              ref={shareBtnRef}
              onClick={() => {
                const r = shareBtnRef.current?.getBoundingClientRect()
                if (r) setShareMenuPos({ top: r.bottom + 4, left: r.left })
                setShareMenu(v => !v)
                setConfigMenu(false)
              }}
              className={`h-8 inline-flex items-center gap-1 px-2.5 rounded-lg text-sm transition-colors flex-shrink-0 ${shareMenu ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'}`}
              title="Share & export"
            >
              <Link2 size={15} /> <ChevronDown size={12} className="opacity-60" />
            </button>

            {/* Configure — button only; dropdown rendered in portal below toolbar */}
            <button
              ref={configBtnRef}
              onClick={() => {
                const r = configBtnRef.current?.getBoundingClientRect()
                if (r) setConfigMenuPos({ top: r.bottom + 4, left: r.left })
                setConfigMenu(v => !v)
                setShareMenu(false)
              }}
              className={`h-8 inline-flex items-center gap-1 px-2.5 rounded-lg text-sm transition-colors flex-shrink-0 ${configMenu ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'}`}
              title="Measures, schedule & security"
            >
              <FunctionSquare size={15} /> <ChevronDown size={12} className="opacity-60" />
            </button>

            {/* Agentic quick actions */}
            <button onClick={() => setShowAlerts(true)} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-amber-600 hover:bg-amber-50 transition-colors flex-shrink-0" title="Data alerts">
              <Bell size={15} />
            </button>
            <button onClick={() => setShowHistory(true)} className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-gray-500 hover:text-blue-700 hover:bg-blue-50 transition-colors flex-shrink-0" title="Version history">
              <HistoryIcon size={15} />
            </button>
          </>
        )}

        {/* Right cluster — always visible */}
        <div className="flex items-center gap-1.5 ml-auto pl-2 flex-shrink-0">
          {!ribbonCollapsed && (
            <>
              <button
                ref={tablesBtnRef}
                onClick={() => {
                  const r = tablesBtnRef.current?.getBoundingClientRect()
                  if (r) setTablesPopPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) })
                  setShowTablePicker(v => !v)
                }}
                className={`h-8 inline-flex items-center gap-1.5 px-2.5 rounded-lg text-sm transition-colors ${showTablePicker ? 'bg-gray-900 text-white' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'}`}
                title="Choose which tables the AI focuses on"
              >
                <Table2 size={15} /> Tables
              </button>
              <button
                onClick={() => router.push(`/intelligence/${canvasId}`)}
                className="h-8 inline-flex items-center gap-1.5 px-3 rounded-lg text-sm font-semibold text-cyan-700 bg-cyan-50 hover:bg-cyan-100 border border-cyan-100 transition-colors"
                title="Open Executive Intelligence"
              >
                <Zap size={15} /> Intelligence
              </button>
            </>
          )}
          <button
            onClick={() => setShowChat(v => !v)}
            className={`h-8 inline-flex items-center gap-1.5 px-3.5 rounded-lg text-sm font-semibold transition-all shadow-sm ${
              showChat
                ? 'bg-gray-900 text-white'
                : 'text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700'
            }`}
            title="Canvas Assistant (N)"
          >
            <Sparkles size={15} /> Assistant
          </button>

          {/* Ribbon collapse toggle — mimics Word's ∧ ribbon pin */}
          <div className="w-px h-5 bg-gray-200 mx-1 flex-shrink-0" />
          <button
            onClick={() => setRibbonCollapsed(v => !v)}
            className="h-7 w-7 inline-flex items-center justify-center rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors flex-shrink-0"
            title={ribbonCollapsed ? 'Expand toolbar' : 'Collapse toolbar'}
          >
            <ChevronDown size={14} className={`transition-transform duration-200 ${ribbonCollapsed ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {/* Portal-rendered dropdowns — rendered at document.body so the toolbar's
          overflow-x:auto container never clips them. z-index 9980 sits above
          every canvas surface but below critical overlays (modals, etc.). */}
      {shareMenu && shareMenuPos && typeof document !== 'undefined' && createPortal(
        <>
          <div className="fixed inset-0 z-[9980]" onClick={() => setShareMenu(false)} />
          <div className="fixed z-[9981] w-52 bg-white border border-gray-200 rounded-xl shadow-2xl py-1.5"
            style={{ top: shareMenuPos.top, left: shareMenuPos.left }}>
            {[
              { icon: <Link2 size={14} />, label: 'Copy link', fn: handleCopyLink },
              { icon: <FileJson size={14} />, label: 'Export JSON', fn: handleExportJSON },
              { icon: <FileDown size={14} />, label: 'Export .vly bundle', fn: () => setShowExport(true) },
            ].map(item => (
              <button key={item.label} onClick={() => { setShareMenu(false); item.fn() }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-sm text-gray-700 hover:bg-gray-50 hover:text-gray-900 transition-colors">
                <span className="text-gray-400">{item.icon}</span>{item.label}
              </button>
            ))}
            {(canvas?.is_offline || (canvas?.layout_config as { data_mode?: string } | undefined)?.data_mode === 'offline') && (
              <button onClick={() => { setShareMenu(false); setShowConnectDb(true) }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-sm text-indigo-600 hover:bg-indigo-50 transition-colors border-t border-gray-100 mt-1 pt-2">
                <Database size={14} /> Connect live database
              </button>
            )}
          </div>
        </>,
        document.body,
      )}
      {configMenu && configMenuPos && typeof document !== 'undefined' && createPortal(
        <>
          <div className="fixed inset-0 z-[9980]" onClick={() => setConfigMenu(false)} />
          <div className="fixed z-[9981] w-56 bg-white border border-gray-200 rounded-xl shadow-2xl py-1.5"
            style={{ top: configMenuPos.top, left: configMenuPos.left }}>
            {[
              { icon: <FunctionSquare size={14} />, label: 'Calculated measures', fn: () => setShowMeasures(true) },
              { icon: <Clock size={14} />, label: 'Scheduled refresh', fn: () => setShowSchedule(true) },
              { icon: <Shield size={14} />, label: 'Row-level security', fn: () => setShowRLS(true) },
            ].map(item => (
              <button key={item.label} onClick={() => { setConfigMenu(false); item.fn() }}
                className="w-full flex items-center gap-2.5 px-3.5 py-2 text-sm text-gray-700 hover:bg-gray-50 hover:text-gray-900 transition-colors">
                <span className="text-gray-400">{item.icon}</span>{item.label}
              </button>
            ))}
          </div>
        </>,
        document.body,
      )}

      {/* Floating surfaces owned by the toolbar */}
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
      {showConnectDb && (
        <ConnectLiveDbModal
          projectId={projectId}
          dashboardId={canvasId}
          hint={canvas?.connection_hint}
          onClose={() => setShowConnectDb(false)}
          onConnected={() => window.location.reload()}
        />
      )}

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
        <div ref={canvasScrollRef} className="flex-1 overflow-auto min-w-0" style={{
          backgroundColor: '#EBEEF3',
          backgroundImage: 'radial-gradient(circle, #d8dee8 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          padding: '28px 28px 56px',
        }}>
          {/* Letterbox wrapper — centers the fixed-width page and reserves the
              scaled footprint so scrollbars stay correct at any zoom */}
          <div style={{ width: PAGE_W * gridZoom, margin: '0 auto', maxWidth: '100%' }}>
          {/* Document-style page sheet (fixed 1280px, scaled to fit) */}
          <div style={{
            width: PAGE_W,
            transform: `scale(${gridZoom})`,
            transformOrigin: 'top left',
            transition: 'transform 0.15s ease',
            background: 'white',
            border: '1px solid #E2E8F0',
            borderRadius: 10,
            boxShadow: '0 1px 3px rgba(16,24,40,0.06), 0 12px 40px rgba(16,24,40,0.10)',
            minHeight: 720,
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
            // Prompt-first empty state — the chat input IS the starting point
            <div className="flex flex-col items-center justify-center text-gray-400 gap-7 py-12" style={{ minHeight: 660 }}>
              <div className="flex items-end gap-2 opacity-25">
                {[36, 56, 44, 72, 52].map((h, i) => (
                  <div key={i} className="w-7 rounded-t-md bg-gradient-to-t from-blue-500 to-indigo-400" style={{ height: h }} />
                ))}
              </div>
              <div className="text-center">
                <p className="font-bold text-gray-700 text-lg">What do you want to see?</p>
                <p className="text-sm mt-1 text-gray-400 max-w-md">
                  Describe a chart in plain language — the AI writes the SQL, builds the visual, and places it on this page.
                </p>
              </div>
              {/* Command bar — opens the copilot with the typed question */}
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  const q = (new FormData(e.currentTarget).get('q') as string || '').trim()
                  setChatPrefill(q || undefined)
                  setShowChat(true)
                }}
                className="w-full max-w-xl"
              >
                <div className="flex items-center gap-2 bg-white border border-gray-300 rounded-2xl pl-4 pr-2 py-2 shadow-sm focus-within:border-blue-400 focus-within:ring-4 focus-within:ring-blue-100 transition-all">
                  <MessageSquare size={16} className="text-blue-500 flex-shrink-0" />
                  <input
                    name="q"
                    placeholder="e.g. Monthly placements trend for the last 12 months…"
                    className="flex-1 text-sm text-gray-800 placeholder-gray-400 outline-none bg-transparent"
                    autoComplete="off"
                  />
                  <button type="submit" className="px-3.5 py-1.5 bg-blue-600 text-white text-sm font-medium rounded-xl hover:bg-blue-700 transition-colors flex-shrink-0">
                    Create
                  </button>
                </div>
              </form>
              {/* Suggestion chips */}
              <div className="flex gap-2 flex-wrap justify-center max-w-xl">
                {[
                  'Top 10 by revenue',
                  'Daily activity — last 7 days',
                  'KPI card: total records',
                  'Breakdown by category',
                ].map(s => (
                  <button
                    key={s}
                    onClick={() => { setChatPrefill(s); setShowChat(true) }}
                    className="px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-200 rounded-full hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            ) : (
              <div>
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
                  resizeHandles={['se', 'e', 's', 'sw', 'w', 'ne', 'nw', 'n']}
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
                        onDataPointClick={(column, value) =>
                          setExplainTarget({ widgetId: widget.id, column, value: String(value) })}
                      />
                    </div>
                  ))}
                </ResponsiveGrid>
              </div>
            )
          })()}
          </div>{/* /page sheet */}
          </div>{/* /letterbox wrapper */}
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

        {/* Chat panel — floating overlay so grid width is never affected */}
        {showChat && (
          <div style={{
            position: 'absolute', right: 10, top: 10, bottom: 10, zIndex: 40,
            display: 'flex', flexDirection: 'column',
            animation: 'canvasChatIn 0.22s cubic-bezier(0.16, 1, 0.3, 1)',
          }}>
            <style>{`@keyframes canvasChatIn { from { opacity: 0; transform: translateX(16px) } to { opacity: 1; transform: translateX(0) } }`}</style>
            <CanvasChatPanel
              projectId={projectId}
              canvasId={canvasId}
              widgets={widgets}
              pages={pages}
              activePageId={activePageId}
              prefillMessage={chatPrefill}
              onClose={() => { setShowChat(false); setChatPrefill(undefined) }}
              onWidgetAdded={() => load(true)}
              isOffline={canvas?.is_offline || (canvas?.layout_config as { data_mode?: string } | undefined)?.data_mode === 'offline'}
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

      {/* Agent panels: version history, data alerts, explain-this-point */}
      {showHistory && (
        <HistoryDrawer
          canvasId={canvasId}
          onClose={() => setShowHistory(false)}
          onRestored={() => { load(); showToast('Version restored') }}
        />
      )}
      {showAlerts && (
        <AlertsModal
          canvasId={canvasId}
          widgets={widgets.map(w => ({ id: w.id, title: w.title, chart_type: w.chart_type }))}
          onClose={() => setShowAlerts(false)}
        />
      )}
      {explainTarget && (
        <ExplainPopover
          canvasId={canvasId}
          widgetId={explainTarget.widgetId}
          column={explainTarget.column}
          value={explainTarget.value}
          onClose={() => setExplainTarget(null)}
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
