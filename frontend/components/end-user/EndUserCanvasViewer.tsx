'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ArrowLeft, Loader2, AlertCircle, Sparkles, X, MessageSquare,
  Download, Maximize2, AlertTriangle, Lightbulb, ChevronDown, ChevronUp,
  BarChart2, RefreshCw,
} from 'lucide-react'
import { dashboardApi, aiInsightsApi, chatApi } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { CanvasChatPanel } from '@/components/canvas/CanvasChatPanel'

interface Widget {
  id: string
  title: string
  chart_type: string
  chart_data: { rows: any[]; columns: string[] } | null
  sql_query?: string
  connection_id?: string
  width: number
  height: number
  position_x: number
  position_y: number
  config?: Record<string, unknown>
}

interface Dashboard {
  id: string
  name: string
  theme: string
  project_id: string
  description?: string
  widgets?: Widget[]
}

interface Anomaly {
  widget_id: string
  severity: 'warning' | 'critical'
  message: string
}

interface Props {
  dashboardId: string
  onClose: () => void
}

const DARK_THEMES = new Set(['slate', 'obsidian'])

function downloadWidgetAsPNG(el: HTMLElement | null, title: string) {
  if (!el) return
  const svg = el.querySelector('svg')
  if (!svg) return
  const serializer = new XMLSerializer()
  const svgStr = serializer.serializeToString(svg)
  const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const img = new window.Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = svg.clientWidth || 800
    canvas.height = svg.clientHeight || 400
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0)
    const a = document.createElement('a')
    a.download = `${title.replace(/[^a-z0-9]/gi, '_')}.png`
    a.href = canvas.toDataURL('image/png')
    a.click()
    URL.revokeObjectURL(url)
  }
  img.src = url
}

export function EndUserCanvasViewer({ dashboardId, onClose }: Props) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // AI states
  const [summary, setSummary] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryOpen, setSummaryOpen] = useState(true)
  const [insights, setInsights] = useState<Record<string, string>>({})
  const [insightLoading, setInsightLoading] = useState<string | null>(null)
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [showChat, setShowChat] = useState(false)
  const [fullscreenWidget, setFullscreenWidget] = useState<Widget | null>(null)

  const widgetRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await dashboardApi.get(dashboardId)
      setDashboard(resp.data)
    } catch {
      setError('Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [dashboardId])

  useEffect(() => { load() }, [load])

  // Auto-load AI summary and anomalies once dashboard loads
  useEffect(() => {
    if (!dashboard) return

    setSummaryLoading(true)
    aiInsightsApi.summary(dashboardId)
      .then(r => setSummary(r.data.summary))
      .catch(() => setSummary(null))
      .finally(() => setSummaryLoading(false))

    aiInsightsApi.anomalies(dashboardId)
      .then(r => setAnomalies(r.data.anomalies ?? []))
      .catch(() => setAnomalies([]))
  }, [dashboard, dashboardId])

  const fetchInsight = useCallback(async (widgetId: string) => {
    if (insights[widgetId] || insightLoading === widgetId) return
    setInsightLoading(widgetId)
    try {
      const r = await aiInsightsApi.insight(dashboardId, widgetId)
      setInsights(prev => ({ ...prev, [widgetId]: r.data.insight }))
    } catch {
      setInsights(prev => ({ ...prev, [widgetId]: 'Could not generate insight.' }))
    } finally {
      setInsightLoading(null)
    }
  }, [dashboardId, insights, insightLoading])

  if (loading) return (
    <div className="flex-1 flex items-center justify-center h-full">
      <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
    </div>
  )

  if (error || !dashboard) return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 h-full">
      <AlertCircle className="w-10 h-10 text-red-400" />
      <p className="text-sm text-gray-500">{error ?? 'Report not found'}</p>
      <button onClick={onClose} className="btn-secondary text-sm">Go back</button>
    </div>
  )

  const theme = dashboard.theme ?? 'frost'
  const isDark = DARK_THEMES.has(theme)
  const widgets = dashboard.widgets ?? []
  const sortedWidgets = [...widgets].sort((a, b) =>
    a.position_y !== b.position_y ? a.position_y - b.position_y : a.position_x - b.position_x
  )

  const cardStyle   = { background: 'var(--dash-card-bg, #fff)', borderColor: 'var(--dash-card-border, #e5e7eb)' }
  const headerStyle = { background: 'var(--dash-header-bg, #fff)', borderColor: 'var(--dash-header-border, #f3f4f6)' }
  const textStyle   = { color: 'var(--dash-text, #111827)' }
  const mutedStyle  = { color: 'var(--dash-text-muted, #6b7280)' }

  const anomalyMap: Record<string, Anomaly> = {}
  anomalies.forEach(a => { anomalyMap[a.widget_id] = a })

  return (
    <div
      className="flex flex-col h-full relative"
      data-theme={theme}
      style={{ background: 'var(--dash-bg, #F3F4F6)', color: 'var(--dash-text, #111827)' }}
    >
      {/* Top bar */}
      <div className="flex items-center gap-3 px-5 py-3 border-b flex-shrink-0" style={headerStyle}>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors flex-shrink-0"
          style={mutedStyle}
        >
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-bold truncate" style={textStyle}>{dashboard.name}</h2>
          {dashboard.description && (
            <p className="text-xs truncate" style={mutedStyle}>{dashboard.description}</p>
          )}
        </div>
        <button
          onClick={() => setShowChat(v => !v)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
            showChat ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100 border border-gray-200'
          }`}
        >
          <MessageSquare size={14} />
          Ask AI
        </button>
      </div>

      {/* AI Summary banner */}
      {(summaryLoading || summary) && (
        <div
          className="mx-4 mt-3 rounded-xl border flex-shrink-0 overflow-hidden"
          style={{ background: isDark ? '#1E3A5F' : '#EFF6FF', borderColor: isDark ? '#1E40AF' : '#BFDBFE' }}
        >
          <button
            className="w-full flex items-center gap-2.5 px-4 py-2.5 text-left"
            onClick={() => setSummaryOpen(v => !v)}
          >
            <div
              className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0"
              style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
            >
              <Sparkles size={11} className="text-white" />
            </div>
            <span className="text-xs font-semibold" style={{ color: isDark ? '#93C5FD' : '#1D4ED8' }}>
              AI Report Summary
            </span>
            <span className="ml-auto" style={{ color: isDark ? '#93C5FD' : '#1D4ED8' }}>
              {summaryOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </span>
          </button>
          {summaryOpen && (
            <div className="px-4 pb-3">
              {summaryLoading ? (
                <div className="flex items-center gap-2">
                  <Loader2 size={12} className="animate-spin" style={{ color: '#2563EB' }} />
                  <span className="text-xs" style={{ color: isDark ? '#93C5FD' : '#1D4ED8' }}>
                    Generating summary…
                  </span>
                </div>
              ) : (
                <p className="text-xs leading-relaxed" style={{ color: isDark ? '#BFDBFE' : '#1E40AF' }}>
                  {summary}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Content + optional chat side-panel */}
      <div className="flex-1 flex min-h-0 overflow-hidden relative">
        {/* Chart grid */}
        <div className="flex-1 overflow-auto p-4">
          {widgets.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-2" style={mutedStyle}>
              <BarChart2 className="w-10 h-10" />
              <p className="text-sm">This report has no charts yet.</p>
            </div>
          ) : (
            <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(12, 1fr)' }}>
              {sortedWidgets.map(widget => {
                const colSpan    = Math.max(1, Math.min(12, widget.width))
                const chartHeight = Math.max(200, widget.height * 55)
                const config     = (widget.config as Record<string, string>) ?? {}
                const cd         = widget.chart_data
                const anomaly    = anomalyMap[widget.id]
                const widgetInsight = insights[widget.id]
                const loadingInsight = insightLoading === widget.id

                return (
                  <div
                    key={widget.id}
                    ref={el => { widgetRefs.current[widget.id] = el }}
                    className="rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow border group"
                    style={{
                      gridColumn: `span ${colSpan}`,
                      ...cardStyle,
                      ...(anomaly ? { borderColor: anomaly.severity === 'critical' ? '#FCA5A5' : '#FDE68A' } : {}),
                    }}
                  >
                    {/* Anomaly banner */}
                    {anomaly && (
                      <div
                        className="px-3 py-1.5 flex items-center gap-2 text-xs font-medium"
                        style={{
                          background: anomaly.severity === 'critical' ? '#FEF2F2' : '#FFFBEB',
                          color: anomaly.severity === 'critical' ? '#DC2626' : '#D97706',
                        }}
                      >
                        <AlertTriangle size={12} />
                        {anomaly.message}
                      </div>
                    )}

                    {/* Widget header */}
                    <div className="px-4 py-3 border-b flex items-center justify-between"
                      style={{ borderColor: 'var(--dash-card-border, #e5e7eb)' }}>
                      <h3 className="text-xs font-600 uppercase tracking-wide truncate" style={mutedStyle}>
                        {widget.title}
                      </h3>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 ml-2">
                        {/* AI insight chip */}
                        <button
                          onClick={() => fetchInsight(widget.id)}
                          className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium transition-colors"
                          style={{
                            background: widgetInsight ? '#F0FDF4' : '#EFF6FF',
                            color: widgetInsight ? '#16A34A' : '#2563EB',
                          }}
                          title="Get AI insight"
                        >
                          {loadingInsight ? <Loader2 size={9} className="animate-spin" /> : <Lightbulb size={9} />}
                          {widgetInsight ? 'Insight' : 'Ask AI'}
                        </button>
                        <button
                          onClick={() => setFullscreenWidget(widget)}
                          className="p-1 rounded hover:bg-gray-100 transition-colors"
                          style={mutedStyle}
                          title="Expand"
                        >
                          <Maximize2 size={12} />
                        </button>
                        <button
                          onClick={() => downloadWidgetAsPNG(widgetRefs.current[widget.id], widget.title)}
                          className="p-1 rounded hover:bg-gray-100 transition-colors"
                          style={mutedStyle}
                          title="Download PNG"
                        >
                          <Download size={12} />
                        </button>
                      </div>
                    </div>

                    {/* AI insight bubble */}
                    {widgetInsight && (
                      <div
                        className="mx-3 mt-2 px-3 py-2 rounded-lg text-xs leading-relaxed"
                        style={{ background: '#F0FDF4', color: '#166534', border: '1px solid #BBF7D0' }}
                      >
                        <span className="font-semibold">AI: </span>{widgetInsight}
                      </div>
                    )}

                    {/* Chart body */}
                    <div className="p-3 overflow-hidden" style={{ height: `${chartHeight}px` }}>
                      {cd ? (
                        <ChartRenderer
                          result={{
                            chart_type: widget.chart_type,
                            title: widget.title,
                            chart_data: {
                              rows:    (cd as any)?.rows    ?? [],
                              columns: (cd as any)?.columns ?? [],
                              labels:  (cd as any)?.labels  ?? [],
                              values:  (cd as any)?.values  ?? [],
                            },
                            x_axis_label: config.x_axis_label || '',
                            y_axis_label: config.y_axis_label || '',
                            sql: '', score: 0, low_confidence: false, table_used: '',
                          }}
                        />
                      ) : (
                        <div className="h-full flex items-center justify-center text-sm" style={mutedStyle}>
                          No data
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Floating AI Chat panel (absolute overlay) */}
        {showChat && (
          <div style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, zIndex: 40,
            width: 320, display: 'flex', flexDirection: 'column',
            boxShadow: '-4px 0 24px rgba(0,0,0,0.10)',
          }}>
            <CanvasChatPanel
              projectId={dashboard.project_id ?? ''}
              canvasId={dashboardId}
              widgets={widgets as any}
              pages={[]}
              activePageId=""
              onClose={() => setShowChat(false)}
              onWidgetAdded={() => {}}
            />
          </div>
        )}
      </div>

      {/* Fullscreen widget modal */}
      {fullscreenWidget && (() => {
        const cd = fullscreenWidget.chart_data
        const config = (fullscreenWidget.config as Record<string, string>) ?? {}
        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-6"
            onClick={() => setFullscreenWidget(null)}
          >
            <div
              className="relative rounded-2xl shadow-2xl overflow-hidden flex flex-col"
              style={{ ...cardStyle, width: '85vw', height: '82vh' }}
              onClick={e => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-5 py-3 border-b flex-shrink-0"
                style={{ borderColor: 'var(--dash-card-border, #e5e7eb)' }}>
                <span className="text-sm font-semibold" style={textStyle}>{fullscreenWidget.title}</span>
                <button onClick={() => setFullscreenWidget(null)}
                  className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors" style={mutedStyle}>
                  <X size={16} />
                </button>
              </div>
              <div className="flex-1 p-4 overflow-auto">
                {cd ? (
                  <ChartRenderer
                    height={Math.floor(window.innerHeight * 0.65)}
                    result={{
                      chart_type: fullscreenWidget.chart_type,
                      title: fullscreenWidget.title,
                      chart_data: {
                        rows:    (cd as any)?.rows    ?? [],
                        columns: (cd as any)?.columns ?? [],
                        labels:  (cd as any)?.labels  ?? [],
                        values:  (cd as any)?.values  ?? [],
                      },
                      x_axis_label: config.x_axis_label || '',
                      y_axis_label: config.y_axis_label || '',
                      sql: '', score: 0, low_confidence: false, table_used: '',
                    }}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center" style={mutedStyle}>No data</div>
                )}
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
