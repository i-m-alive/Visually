'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { Layout, LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import {
  Layers, MessageSquare, Plus, Save, ChevronLeft,
  Loader2, AlertCircle, CheckCircle2, LayoutGrid, Sparkles
} from 'lucide-react'
import { canvasApi, widgetApi } from '@/lib/api'
import { CanvasWidget, type CanvasWidgetData } from '@/components/canvas/CanvasWidget'
import { ZoomModal } from '@/components/canvas/ZoomModal'
import { CanvasChatPanel } from '@/components/canvas/CanvasChatPanel'
import { VisuallReport } from '@/components/canvas/VisuallReport'

const ResponsiveGrid = WidthProvider(Responsive)

// LayoutItem is imported from react-grid-layout/legacy
type GridItem = LayoutItem

// ── Smart auto-arrange: size by chart type, pack left→right then wrap ─────────
const CHART_SIZES: Record<string, { w: number; h: number }> = {
  // KPI variants — small
  kpi:                    { w: 3, h: 4 },
  kpi_card:               { w: 3, h: 4 },
  gauge:                  { w: 3, h: 5 },
  multi_row_card:         { w: 4, h: 6 },
  // Round charts
  pie:                    { w: 4, h: 6 },
  donut:                  { w: 4, h: 6 },
  sunburst:               { w: 5, h: 6 },
  // Standard charts
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
  // Multi-series — need more width
  stacked_bar:            { w: 7, h: 6 },
  stacked_bar_100:        { w: 7, h: 6 },
  grouped_bar:            { w: 7, h: 6 },
  stacked_area:           { w: 7, h: 6 },
  // Horizontal bars — widest
  bar_horizontal:         { w: 7, h: 6 },
  stacked_bar_horizontal: { w: 8, h: 6 },
  // Heatmap — large grid
  heatmap:                { w: 8, h: 7 },
  // Tables — full width
  table:                  { w: 8, h: 7 },
  data_table:             { w: 8, h: 7 },
  pivot_table:            { w: 9, h: 7 },
  // ── New chart types ──────────────────────────────────────────────────────────
  // Statistical
  box_plot:               { w: 7, h: 6 },
  // Comparison
  bullet:                 { w: 7, h: 6 },
  scorecard:              { w: 5, h: 7 },
  dot_plot:               { w: 5, h: 7 },
  radar:                  { w: 6, h: 6 },
  // Trend / rank
  ribbon:                 { w: 7, h: 6 },
  // Flow / relational
  sankey:                 { w: 8, h: 7 },
  chord:                  { w: 6, h: 6 },
  network:                { w: 7, h: 7 },
  // Time-based
  gantt:                  { w: 10, h: 7 },
  timeline:               { w: 10, h: 5 },
  calendar_heatmap:       { w: 10, h: 5 },
  // Text
  word_cloud:             { w: 6, h: 6 },
  // Hierarchical
  org_chart:              { w: 8, h: 7 },
  // Part-to-whole (advanced)
  marimekko:              { w: 8, h: 7 },
  // Geographic
  choropleth:             { w: 7, h: 8 },
}
const DEFAULT_SIZE = { w: 6, h: 6 }
const COLS = 12

// Priority groups: KPIs together, then compact charts, then wide charts
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
    if (x + size.w > COLS) {
      x = 0
      y += rowHeight
      rowHeight = 0
    }
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

interface CanvasDetail {
  id: string
  name: string
  theme: string
  project_id: string
  widgets: WidgetWithPosition[]
}

export default function CanvasEditorPage() {
  const { id: projectId, canvasId } = useParams<{ id: string; canvasId: string }>()
  const router = useRouter()

  const [canvas, setCanvas] = useState<CanvasDetail | null>(null)
  const [widgets, setWidgets] = useState<WidgetWithPosition[]>([])
  const [layout, setLayout] = useState<GridItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedOk, setSavedOk] = useState(false)
  const [showChat, setShowChat]     = useState(false)
  const [showReport, setShowReport] = useState(false)
  const [zoomTarget, setZoomTarget] = useState<{ widget: CanvasWidgetData; colors: string[] } | null>(null)
  const [isDirty, setIsDirty]       = useState(false)

  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await canvasApi.get(canvasId)
      const data: CanvasDetail = resp.data
      setCanvas(data)
      const ws: WidgetWithPosition[] = (data.widgets || []) as WidgetWithPosition[]
      setWidgets(ws)
      // If every widget sits at (0,0) the canvas is fresh — auto-arrange smartly
      const allZero = ws.every(w => (w.position_x ?? 0) === 0 && (w.position_y ?? 0) === 0)
      const computed: GridItem[] = allZero
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
      // Persist auto-arrangement so future loads preserve positions
      if (allZero && ws.length > 0) {
        void canvasApi.updateLayout(canvasId, computed.map(l => ({
          widget_id: l.i, x: l.x, y: l.y, w: l.w, h: l.h,
        })))
      }
    } catch {
      setError('Failed to load canvas')
    } finally {
      setLoading(false)
    }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const persistLayout = useCallback(async (items: LayoutItem[]) => {
    setSaving(true)
    try {
      await canvasApi.updateLayout(canvasId, items.map(l => ({
        widget_id: l.i,
        x: l.x,
        y: l.y,
        w: l.w,
        h: l.h,
      })))
      setSavedOk(true)
      setIsDirty(false)
      setTimeout(() => setSavedOk(false), 2000)
    } catch {
      // silently ignore
    } finally {
      setSaving(false)
    }
  }, [canvasId])

  const handleLayoutChange = useCallback((newLayout: Layout, _allLayouts: Partial<Record<string, Layout>>) => {
    setLayout([...newLayout])
    setIsDirty(true)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    saveTimeoutRef.current = setTimeout(() => persistLayout([...newLayout]), 1200)
  }, [persistLayout])

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

  const handleManualSave = () => {
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    persistLayout(layout)
  }

  const handleAutoArrange = useCallback(() => {
    const arranged = autoArrange(widgets)
    setLayout(arranged)
    setIsDirty(false)
    persistLayout(arranged)
  }, [widgets, persistLayout])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500">
        <AlertCircle className="w-10 h-10 text-red-400" />
        <p className="text-sm">{error}</p>
        <button onClick={() => router.back()} className="text-sm text-brand hover:underline">Go back</button>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-gray-50">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-white border-b border-gray-100 flex-shrink-0">
        <button
          onClick={() => router.push(`/projects/${projectId}/canvas`)}
          className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
        >
          <ChevronLeft size={18} />
        </button>

        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Layers size={16} className="text-brand flex-shrink-0" />
          <h1 className="text-sm font-semibold text-gray-900 truncate">{canvas?.name}</h1>
        </div>

        {/* Save state */}
        <div className="flex items-center gap-1.5 text-xs flex-shrink-0">
          {saving && (
            <span className="flex items-center gap-1 text-gray-400">
              <Loader2 size={12} className="animate-spin" /> Saving…
            </span>
          )}
          {savedOk && (
            <span className="flex items-center gap-1 text-green-600">
              <CheckCircle2 size={12} /> Saved
            </span>
          )}
          {isDirty && !saving && !savedOk && (
            <button
              onClick={handleManualSave}
              className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-white bg-brand rounded-lg hover:bg-brand/90 transition-colors"
            >
              <Save size={12} /> Save
            </button>
          )}
        </div>

        {/* Auto-arrange */}
        {widgets.length > 0 && (
          <button
            onClick={handleAutoArrange}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
            title="Auto-arrange all charts by type"
          >
            <LayoutGrid size={13} /> Auto-arrange
          </button>
        )}

        {/* Add chart via AI */}
        <button
          onClick={() => setShowChat(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
        >
          <Plus size={13} /> Add chart
        </button>

        {/* Chat toggle */}
        <button
          onClick={() => setShowChat(v => !v)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
            showChat ? 'bg-brand text-white' : 'text-gray-600 bg-gray-100 hover:bg-gray-200'
          }`}
        >
          <MessageSquare size={13} /> AI Chat
        </button>

        {/* Visually report mode */}
        <button
          onClick={() => setShowReport(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white rounded-lg transition-all hover:opacity-90"
          style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
          title="View as rich report"
        >
          <Sparkles size={13} /> Visually
        </button>
      </div>

      {/* Body: canvas + optional chat panel */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Canvas grid */}
        <div className="flex-1 overflow-auto p-4 min-w-0">
          {widgets.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-400 gap-4">
              <Layers className="w-12 h-12 text-gray-200" />
              <div className="text-center">
                <p className="font-medium text-gray-600">Canvas is empty</p>
                <p className="text-sm mt-1">Use AI Chat to add charts, or run a screenshot pipeline to populate this canvas.</p>
              </div>
              <button
                onClick={() => setShowChat(true)}
                className="flex items-center gap-2 px-4 py-2 bg-brand text-white text-sm rounded-lg hover:bg-brand/90"
              >
                <MessageSquare size={14} /> Open AI Chat
              </button>
            </div>
          ) : (
            <ResponsiveGrid
              className="layout"
              layouts={{ lg: layout, md: layout, sm: layout }}
              breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
              cols={{ lg: 12, md: 12, sm: 6, xs: 4, xxs: 2 }}
              rowHeight={70}
              onLayoutChange={handleLayoutChange}
              draggableHandle=".drag-handle"
              isDraggable
              isResizable
              margin={[12, 12]}
              containerPadding={[4, 4]}
              resizeHandles={['se', 'e', 's']}
            >
              {widgets.map(widget => (
                <div key={widget.id} className="overflow-hidden rounded-xl">
                  <CanvasWidget
                    widget={widget}
                    onDelete={handleDeleteWidget}
                    onUpdate={handleUpdateWidget}
                    onZoom={(w, cols) => setZoomTarget({ widget: w, colors: cols })}
                  />
                </div>
              ))}
            </ResponsiveGrid>
          )}
        </div>

        {/* Chat panel */}
        {showChat && (
          <CanvasChatPanel
            projectId={projectId}
            canvasId={canvasId}
            widgets={widgets}
            onClose={() => setShowChat(false)}
            onWidgetAdded={load}
          />
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
          canvas={{ id: canvas.id, name: canvas.name, project_id: canvas.project_id }}
          widgets={widgets}
          projectId={projectId}
          onClose={() => setShowReport(false)}
          onWidgetAdded={load}
        />
      )}
    </div>
  )
}
