'use client'
/**
 * Embed view — rendered inside an <iframe> by external websites.
 *
 * Usage:
 *   <iframe src="https://app.visually.ai/embed/canvas/{token}"
 *           width="1200" height="800" frameborder="0" />
 *
 * Differences from /share/canvas/[token]:
 *   - No Visually branding header (just a subtle footer badge)
 *   - height: 100dvh fills the iframe
 *   - Minimal chrome — only the page tabs and refresh button
 *   - X-Frame-Options is NOT set to DENY (Next.js default is same-origin;
 *     for true cross-origin embedding set ALLOW-FROM in next.config.js headers)
 */
import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { RefreshCw, Loader2, AlertCircle } from 'lucide-react'
import { publicCanvasApi } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'

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
}

interface CanvasData {
  id: string
  name: string
  theme: string
  layout_config: Record<string, unknown>
  pages: { id: string; name: string; order: number }[]
  share_mode: string
  widgets: WidgetData[]
}

function widgetToResult(w: WidgetData): ChartResult {
  const cd = w.chart_data || { rows: [], columns: [] }
  const rows = (cd.rows as Record<string, unknown>[]) || []
  const cols = cd.columns || []
  return {
    chart_type: w.chart_type,
    title: w.title,
    sql: '',
    score: 1,
    low_confidence: false,
    x_axis_label: cols[0] || 'x',
    y_axis_label: cols[1] || 'y',
    table_used: '',
    chart_data: {
      rows,
      columns: cols,
      labels: rows.map(r => String(r[cols[0]] ?? '')),
      values: rows.map(r => Number(r[cols[1]] ?? 0)),
    },
  }
}

export default function EmbedCanvasPage() {
  const { token } = useParams<{ token: string }>()
  const [canvas, setCanvas] = useState<CanvasData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [activePageId, setActivePageId] = useState('')

  const load = useCallback(async () => {
    try {
      const resp = await publicCanvasApi.get(token)
      const data: CanvasData = resp.data
      setCanvas(data)
      if (data.pages.length > 0) setActivePageId(data.pages[0].id)
    } catch { setError(true) }
    finally { setLoading(false) }
  }, [token])

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
    } catch { /* ignore */ }
    finally { setRefreshing(false) }
  }

  if (loading) {
    return (
      <div className="h-dvh flex items-center justify-center bg-white">
        <Loader2 size={22} className="animate-spin text-gray-300" />
      </div>
    )
  }

  if (error || !canvas) {
    return (
      <div className="h-dvh flex items-center justify-center bg-white">
        <div className="text-center">
          <AlertCircle size={20} className="text-red-300 mx-auto mb-2" />
          <p className="text-xs text-gray-400">Canvas unavailable</p>
        </div>
      </div>
    )
  }

  const defaultPageId = canvas.pages[0]?.id ?? ''
  const currentPageId = activePageId || defaultPageId

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

  return (
    <div className="h-dvh flex flex-col overflow-hidden" style={{ background: '#F8FAFC' }}>
      {/* Minimal top bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center gap-2">
          {canvas.pages.length > 1 &&
            [...canvas.pages].sort((a, b) => a.order - b.order).map(page => (
              <button
                key={page.id}
                onClick={() => setActivePageId(page.id)}
                className="px-3 py-1 rounded-full text-xs font-medium transition-all"
                style={{
                  background: page.id === currentPageId ? '#EFF6FF' : 'transparent',
                  color: page.id === currentPageId ? '#2563EB' : '#9CA3AF',
                }}
              >
                {page.name}
              </button>
            ))
          }
          {canvas.pages.length <= 1 && (
            <span className="text-xs font-medium text-gray-600">{canvas.name}</span>
          )}
        </div>
        {canvas.share_mode === 'live' && (
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-40"
            title="Refresh data"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          </button>
        )}
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-auto p-3">
        {pageWidgets.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-300 text-xs">No data</div>
        ) : (
          <ResponsiveGrid
            className="layout"
            layouts={{ lg: gridLayout }}
            breakpoints={{ lg: 1200, md: 768 }}
            cols={{ lg: 12, md: 8 }}
            rowHeight={56}
            isDraggable={false}
            isResizable={false}
            margin={[8, 8]}
          >
            {pageWidgets.map(w => (
              <div key={w.id} className="bg-white rounded-xl border border-gray-100 overflow-hidden flex flex-col shadow-sm">
                <div className="px-3 pt-2.5 pb-0.5 flex-shrink-0">
                  <h3 className="text-xs font-semibold text-gray-600 truncate">{w.title}</h3>
                </div>
                <div className="flex-1 px-2 pb-2 min-h-0">
                  <ChartRenderer result={widgetToResult(w)} height={undefined} />
                </div>
              </div>
            ))}
          </ResponsiveGrid>
        )}
      </div>

      {/* Footer badge */}
      <div className="flex justify-end px-3 py-1.5 bg-white border-t border-gray-50 flex-shrink-0">
        <span className="text-xs text-gray-300">Powered by Visually</span>
      </div>
    </div>
  )
}
