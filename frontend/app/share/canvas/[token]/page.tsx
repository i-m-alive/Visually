'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import { Responsive, WidthProvider } from 'react-grid-layout/legacy'
import type { LayoutItem } from 'react-grid-layout/legacy'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { RefreshCw, Loader2, AlertCircle, Sparkles } from 'lucide-react'
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

function widgetToResult(w: WidgetData): ChartResult {
  const cd = w.chart_data || { rows: [], columns: [] }
  const rows: Record<string, unknown>[] = (cd.rows as Record<string, unknown>[]) || []
  const cols: string[] = cd.columns || []
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

export default function ShareCanvasPage() {
  const { token } = useParams<{ token: string }>()
  const [canvas, setCanvas] = useState<CanvasData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [activePageId, setActivePageId] = useState('')
  const lastRefresh = useRef<Date | null>(null)

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

  const handleRefresh = async () => {
    if (!canvas || canvas.share_mode !== 'live' || refreshing) return
    setRefreshing(true)
    try {
      const resp = await publicCanvasApi.refresh(token)
      const freshWidgets: { widget_id: string; chart_data: WidgetData['chart_data'] }[] = resp.data.widgets
      setCanvas(prev => {
        if (!prev) return prev
        const byId = Object.fromEntries(freshWidgets.map(fw => [fw.widget_id, fw.chart_data]))
        return {
          ...prev,
          widgets: prev.widgets.map(w => byId[w.id] ? { ...w, chart_data: byId[w.id] } : w),
        }
      })
      lastRefresh.current = new Date()
    } catch { /* ignore refresh errors */ }
    finally { setRefreshing(false) }
  }

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
    <div className="min-h-screen flex flex-col" style={{ background: '#F8FAFC' }}>
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3.5 bg-white border-b border-gray-100 flex-shrink-0 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
            <Sparkles size={13} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-gray-900">{canvas.name}</h1>
            <p className="text-xs text-gray-400 flex items-center gap-1">
              <span className={`w-1.5 h-1.5 rounded-full inline-block ${canvas.share_mode === 'live' ? 'bg-green-400' : 'bg-gray-400'}`} />
              {canvas.share_mode === 'live' ? 'Live data' : 'Snapshot'}
              {lastRefresh.current && ` · Updated ${lastRefresh.current.toLocaleTimeString()}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {canvas.share_mode === 'live' && (
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} />
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          )}
          <span className="text-xs text-gray-300 px-2 py-1 rounded-lg bg-gray-50 border border-gray-100">
            Powered by Visually
          </span>
        </div>
      </header>

      {/* Page tabs */}
      {canvas.pages.length > 1 && (
        <div className="flex items-center gap-1 px-6 py-2 bg-white border-b border-gray-100 overflow-x-auto flex-shrink-0"
          style={{ scrollbarWidth: 'none' }}>
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

      {/* Grid */}
      <main className="flex-1 overflow-auto p-4">
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
            {pageWidgets.map(w => (
              <div key={w.id} className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden flex flex-col">
                <div className="px-3 pt-3 pb-1 flex-shrink-0">
                  <h3 className="text-xs font-semibold text-gray-700 truncate">{w.title}</h3>
                </div>
                <div className="flex-1 px-2 pb-2 min-h-0">
                  <ChartRenderer result={widgetToResult(w)} height={undefined} />
                </div>
              </div>
            ))}
          </ResponsiveGrid>
        )}
      </main>
    </div>
  )
}
