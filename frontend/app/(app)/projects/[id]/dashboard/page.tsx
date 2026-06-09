'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import {
  Download, LayoutDashboard, BarChart2,
  Loader2, AlertCircle, ChevronRight, Trash2, Image as ImageIcon, X, Pencil, Check,
  SlidersHorizontal, Maximize2, Image,
} from 'lucide-react'
import { dashboardApi } from '@/lib/api'
import { ExportModal } from '@/components/export/ExportModal'
import { ThemeSwitcher } from '@/components/export/ThemeSwitcher'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { DashboardFilters, type FilterConfig } from '@/components/dashboard/DashboardFilters'
import { useDashboardFilterStore } from '@/stores/dashboardFilterStore'

interface Widget {
  id: string
  title: string
  chart_type: string
  chart_data: { rows: any[]; columns: string[] } | null
  base_sql?: string
  sql_query?: string
  connection_id?: string
  width: number
  height: number
  position_x: number
  position_y: number
  config?: Record<string, unknown>
}

interface PageTab { name: string; active: boolean }

interface Dashboard {
  id: string
  name: string
  theme: string
  created_at: string
  filter_config?: FilterConfig[]
  report_title?: string
  page_tabs?: PageTab[]
  colour_theme?: string
  widgets?: Widget[]
}

const DARK_THEMES = new Set(['slate', 'obsidian'])

// ── SVG → PNG download ────────────────────────────────────────────────────────
function downloadWidgetAsPNG(el: HTMLElement | null, title: string) {
  if (!el) return
  const svg = el.querySelector('svg')
  if (!svg) return
  const serializer = new XMLSerializer()
  const svgStr = serializer.serializeToString(svg)
  const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const img  = new window.Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width  = svg.clientWidth  || 800
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

export default function DashboardPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const [dashboards, setDashboards]   = useState<Dashboard[]>([])
  const [selectedId, setSelectedId]   = useState<string | null>(null)
  const [dashboard, setDashboard]     = useState<Dashboard | null>(null)
  const [widgetData, setWidgetData]   = useState<Record<string, any>>({})
  const [loading, setLoading]         = useState(true)
  const [requeryLoading, setRequeryLoading] = useState(false)
  const [lastRefreshed, setLastRefreshed]   = useState<Date | null>(null)
  const [error, setError]             = useState<string | null>(null)
  const [showExport, setShowExport]   = useState(false)
  const [themeUpdating, setThemeUpdating] = useState(false)
  const [deletingId, setDeletingId]   = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [originalImage, setOriginalImage] = useState<string | null>(null)
  const [renamingDash, setRenamingDash] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)
  const [showFilterPanel, setShowFilterPanel] = useState(true)
  const [fullscreenWidget, setFullscreenWidget] = useState<Widget | null>(null)

  // Refs for per-widget PNG export
  const widgetRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const { getFilters, setFilter, clearFilter, clearAll, hasActiveFilters } = useDashboardFilterStore()

  /* ── load dashboard list ── */
  useEffect(() => {
    const fetch = async () => {
      setLoading(true); setError(null)
      try {
        const resp = await dashboardApi.list(projectId)
        const list: Dashboard[] = resp.data?.dashboards ?? resp.data ?? []
        setDashboards(list)
        if (list.length > 0) setSelectedId(list[0].id)
      } catch { setError('Failed to load dashboards') }
      finally { setLoading(false) }
    }
    fetch()
  }, [projectId])

  /* ── load selected dashboard detail ── */
  useEffect(() => {
    if (!selectedId) return
    const fetch = async () => {
      try {
        const resp = await dashboardApi.get(selectedId)
        const d: Dashboard = resp.data
        setDashboard(d)
        const seed: Record<string, any> = {}
        for (const w of d.widgets ?? []) seed[w.id] = w.chart_data
        setWidgetData(seed)
        setLastRefreshed(new Date())
      } catch { setError('Failed to load dashboard') }
    }
    fetch()
  }, [selectedId])

  /* ── cross-filter: re-execute all widgets ── */
  const handleApplyFilters = useCallback(async () => {
    if (!dashboard) return
    const filters = getFilters(dashboard.id)
    setRequeryLoading(true)
    try {
      const resp = await dashboardApi.requery(dashboard.id, filters)
      const updates: Record<string, any> = {}
      for (const item of resp.data?.widgets ?? []) updates[item.widget_id] = item.chart_data
      setWidgetData(prev => ({ ...prev, ...updates }))
      setLastRefreshed(new Date())
    } catch (e) { console.error('[requery]', e) }
    finally { setRequeryLoading(false) }
  }, [dashboard, getFilters])

  /* ── cross-filter via chart click ── */
  const handleDataPointClick = useCallback((column: string, value: unknown) => {
    if (!dashboard) return
    setFilter(dashboard.id, column, [String(value)])
    // Trigger requery with new filter applied immediately
    // (state update is synchronous in Zustand, so getFilters will see the new value)
    const filters = { ...getFilters(dashboard.id), [column]: [String(value)] }
    setRequeryLoading(true)
    dashboardApi.requery(dashboard.id, filters)
      .then(resp => {
        const updates: Record<string, any> = {}
        for (const item of resp.data?.widgets ?? []) updates[item.widget_id] = item.chart_data
        setWidgetData(prev => ({ ...prev, ...updates }))
        setLastRefreshed(new Date())
      })
      .catch(e => console.error('[cross-filter]', e))
      .finally(() => setRequeryLoading(false))
  }, [dashboard, getFilters, setFilter])

  const handleThemeChange = useCallback(async (newTheme: string) => {
    if (!dashboard) return
    setThemeUpdating(true)
    try {
      await dashboardApi.updateTheme(dashboard.id, newTheme)
      setDashboard(d => d ? { ...d, theme: newTheme } : d)
    } catch { /* ignore */ }
    finally { setThemeUpdating(false) }
  }, [dashboard])

  const handleRenameDashboard = async () => {
    const trimmed = renameValue.trim()
    if (!trimmed || !dashboard) { setRenamingDash(false); return }
    setRenameSaving(true)
    try {
      await dashboardApi.rename(dashboard.id, trimmed)
      setDashboards(prev => prev.map(d => d.id === dashboard.id ? { ...d, name: trimmed } : d))
      setDashboard(prev => prev ? { ...prev, name: trimmed } : prev)
    } catch { /* ignore */ }
    setRenameSaving(false); setRenamingDash(false)
  }

  const handleDeleteDashboard = async (id: string) => {
    if (confirmDeleteId !== id) { setConfirmDeleteId(id); return }
    setDeletingId(id); setConfirmDeleteId(null)
    try {
      await dashboardApi.delete(id)
      const updated = dashboards.filter(d => d.id !== id)
      setDashboards(updated)
      if (selectedId === id) { setSelectedId(updated[0]?.id ?? null); setDashboard(null) }
    } catch { setError('Failed to delete dashboard') }
    finally { setDeletingId(null) }
  }

  /* ── loading / error / empty states ── */
  if (loading) return (
    <div className="flex-1 flex items-center justify-center">
      <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
    </div>
  )
  if (error) return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500">
      <AlertCircle className="w-10 h-10 text-red-400" /><p className="text-sm">{error}</p>
    </div>
  )
  if (dashboards.length === 0) return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 text-gray-400 p-8">
      <LayoutDashboard className="w-12 h-12" />
      <div className="text-center">
        <p className="font-600 text-gray-600">No dashboards yet</p>
        <p className="text-sm mt-1">
          Ask a question in the{' '}
          <a href={`/projects/${projectId}/query`} className="text-blue-600 hover:underline">Query page</a>
          {' '}to create your first dashboard.
        </p>
      </div>
    </div>
  )

  const theme       = dashboard?.theme ?? 'frost'
  const widgets     = dashboard?.widgets ?? []
  const sortedWidgets = [...widgets].sort((a, b) =>
    a.position_y !== b.position_y ? a.position_y - b.position_y : a.position_x - b.position_x
  )
  const filterConfigs: FilterConfig[] = dashboard?.filter_config ?? []
  const pageTabs: PageTab[]           = dashboard?.page_tabs ?? []
  const activeFilters                 = dashboard ? getFilters(dashboard.id) : {}
  const activeFilterEntries           = Object.entries(activeFilters).filter(([, v]) =>
    Array.isArray(v) ? v.length > 0 : !!(v as any).start
  )
  const activeCount = activeFilterEntries.length

  const cardStyle   = { background: 'var(--dash-card-bg, #fff)', borderColor: 'var(--dash-card-border, #e5e7eb)' }
  const headerStyle = { background: 'var(--dash-header-bg, #fff)', borderColor: 'var(--dash-header-border, #f3f4f6)' }
  const textStyle   = { color: 'var(--dash-text, #111827)' }
  const mutedStyle  = { color: 'var(--dash-text-muted, #6b7280)' }

  const refreshLabel = lastRefreshed
    ? `Refreshed ${lastRefreshed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : 'Not yet loaded'

  return (
    <div
      className="flex-1 flex flex-col min-h-0"
      data-theme={theme}
      style={{ background: 'var(--dash-bg, #F3F4F6)', color: 'var(--dash-text, #111827)' }}
    >
      {/* ── Top bar ── */}
      <div className="flex items-center gap-3 px-6 py-3 border-b flex-shrink-0" style={headerStyle}>
        {/* Filter toggle with badge */}
        {filterConfigs.length > 0 && (
          <button
            onClick={() => setShowFilterPanel(v => !v)}
            className={`relative p-1.5 rounded-lg transition-colors ${showFilterPanel ? 'bg-blue-50 text-blue-600' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'}`}
            title="Toggle filter panel"
          >
            <SlidersHorizontal size={15} />
            {activeCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-blue-600 text-white text-[9px] font-bold flex items-center justify-center">
                {activeCount}
              </span>
            )}
          </button>
        )}

        {/* Dashboard selector + rename */}
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <BarChart2 className="w-4 h-4 text-gray-400 flex-shrink-0" />
          {renamingDash && dashboard ? (
            <div className="flex items-center gap-1.5">
              <input autoFocus value={renameValue}
                onChange={e => setRenameValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleRenameDashboard(); if (e.key === 'Escape') setRenamingDash(false) }}
                className="text-sm font-semibold bg-transparent border-b-2 border-blue-500 outline-none w-48"
                style={textStyle}
              />
              <button onClick={handleRenameDashboard} disabled={!renameValue.trim() || renameSaving} className="p-1 text-blue-600 hover:bg-blue-50 rounded disabled:opacity-40">
                {renameSaving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
              </button>
              <button onClick={() => setRenamingDash(false)} className="p-1 text-gray-400 hover:text-gray-600 rounded"><X size={13} /></button>
            </div>
          ) : (
            <>
              {dashboards.length === 1 ? (
                <span className="text-sm font-600 truncate" style={textStyle}>{dashboard?.name ?? dashboards[0].name}</span>
              ) : (
                <select value={selectedId ?? ''}
                  onChange={e => { setSelectedId(e.target.value); setRenamingDash(false) }}
                  className="text-sm font-500 border-0 bg-transparent outline-none cursor-pointer max-w-xs"
                  style={textStyle}>
                  {dashboards.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              )}
              {dashboard && (
                <button onClick={() => { setRenameValue(dashboard.name); setRenamingDash(true) }}
                  className="p-1 text-gray-300 hover:text-blue-500 hover:bg-blue-50 rounded transition-colors">
                  <Pencil size={12} />
                </button>
              )}
            </>
          )}
          {dashboard && !renamingDash && <ChevronRight className="w-3 h-3 text-gray-300 flex-shrink-0" />}
          {dashboard?.report_title && (
            <span className="text-xs truncate hidden sm:block" style={mutedStyle}>{dashboard.report_title}</span>
          )}
        </div>

        {requeryLoading && <Loader2 className="w-4 h-4 text-blue-400 animate-spin flex-shrink-0" />}

        {dashboard && <ThemeSwitcher currentTheme={theme} onSelect={handleThemeChange} disabled={themeUpdating} />}

        {dashboard && (
          <button onClick={() => setShowExport(true)}
            className="flex items-center gap-2 px-4 py-1.5 bg-blue-600 text-white text-sm font-600 rounded-lg hover:bg-blue-700 transition-colors">
            <Download size={14} /> Export
          </button>
        )}

        {dashboard && (
          confirmDeleteId === dashboard.id ? (
            <div className="flex items-center gap-1.5 border border-red-200 rounded-lg px-2 py-1">
              <span className="text-xs text-red-600 font-medium">Delete?</span>
              <button onClick={() => handleDeleteDashboard(dashboard.id)} disabled={deletingId === dashboard.id}
                className="text-xs font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded">
                {deletingId === dashboard.id ? <Loader2 size={10} className="animate-spin" /> : 'Yes'}
              </button>
              <button onClick={() => setConfirmDeleteId(null)} className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
            </div>
          ) : (
            <button onClick={() => handleDeleteDashboard(dashboard.id)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-500 hover:bg-red-50 rounded-lg transition-colors border border-transparent hover:border-red-200">
              <Trash2 size={14} /> Delete
            </button>
          )
        )}
      </div>

      {/* ── Page tabs ── */}
      {pageTabs.length > 0 && (
        <div className="flex items-center gap-0 px-6 border-b overflow-x-auto flex-shrink-0" style={headerStyle}>
          {pageTabs.map(tab => (
            <div key={tab.name}
              className={`px-4 py-2 text-xs font-medium border-b-2 whitespace-nowrap cursor-default ${tab.active ? 'border-blue-600 text-blue-600' : 'border-transparent'}`}
              style={tab.active ? {} : mutedStyle}>
              {tab.name}
            </div>
          ))}
        </div>
      )}

      {/* ── Stats ribbon ── */}
      {dashboard && (
        <div
          className="flex items-center gap-4 px-6 py-2 border-b text-xs flex-shrink-0 overflow-x-auto"
          style={{ ...headerStyle, borderTopColor: 'transparent' }}
        >
          <span style={mutedStyle}><b style={textStyle}>{widgets.length}</b> widget{widgets.length !== 1 ? 's' : ''}</span>
          <span style={{ color: 'var(--dash-card-border, #e5e7eb)' }}>·</span>
          <span style={mutedStyle}>{refreshLabel}</span>
          {activeCount > 0 && (
            <>
              <span style={{ color: 'var(--dash-card-border, #e5e7eb)' }}>·</span>
              <span className="text-blue-600 font-medium">{activeCount} filter{activeCount !== 1 ? 's' : ''} active</span>
            </>
          )}
          {activeCount > 0 && (
            <button onClick={() => { clearAll(dashboard.id); handleApplyFilters() }}
              className="text-blue-500 hover:underline">Clear</button>
          )}
        </div>
      )}

      {/* ── Active filter chips ── */}
      {activeCount > 0 && (
        <div className="flex items-center gap-2 px-6 py-2 border-b flex-wrap flex-shrink-0" style={headerStyle}>
          {activeFilterEntries.map(([col, val]) => {
            const label  = filterConfigs.find(f => f.column === col)?.display_name ?? col
            const valStr = Array.isArray(val) ? val.join(', ') : `${(val as any).start} – ${(val as any).end}`
            return (
              <span key={col} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
                <span className="font-semibold">{label}:</span> {valStr}
                <button onClick={() => { clearFilter(dashboard!.id, col); handleApplyFilters() }} className="ml-0.5 hover:text-blue-900">
                  <X size={10} />
                </button>
              </span>
            )
          })}
        </div>
      )}

      {/* ── Content: filter panel + chart grid ── */}
      <div className="flex-1 flex min-h-0 overflow-hidden">

        {/* Left filter panel */}
        {filterConfigs.length > 0 && showFilterPanel && (
          <div className="w-60 shrink-0 overflow-y-auto border-r p-4"
            style={{ background: 'var(--dash-filter-bg, #fff)', borderColor: 'var(--dash-card-border, #e5e7eb)' }}>
            <DashboardFilters
              dashboardId={dashboard?.id ?? ''}
              filters={filterConfigs}
              onApply={handleApplyFilters}
            />
          </div>
        )}

        {/* Chart grid */}
        <div className="flex-1 overflow-auto p-6">
          {!dashboard ? (
            <div className="flex items-center justify-center h-40">
              <Loader2 className="w-6 h-6 text-blue-400 animate-spin" />
            </div>
          ) : widgets.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 gap-2" style={mutedStyle}>
              <LayoutDashboard className="w-10 h-10" />
              <p className="text-sm">This dashboard has no widgets yet.</p>
            </div>
          ) : (
            <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(12, 1fr)' }}>
              {sortedWidgets.map(widget => {
                const colSpan     = Math.max(1, Math.min(12, widget.width))
                const chartHeight = Math.max(200, widget.height * 55)
                const config      = (widget.config as Record<string, string>) ?? {}
                const cd          = widgetData[widget.id] ?? widget.chart_data
                const imageData   = (cd as Record<string, unknown>)?.image_data as string | undefined

                return (
                  <div
                    key={widget.id}
                    ref={el => { widgetRefs.current[widget.id] = el }}
                    className="rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow border group"
                    style={{ gridColumn: `span ${colSpan}`, ...cardStyle }}
                  >
                    {/* Widget header */}
                    <div className="px-4 py-3 border-b flex items-center justify-between"
                      style={{ borderColor: 'var(--dash-card-border, #e5e7eb)' }}>
                      <h3 className="text-xs font-600 uppercase tracking-wide truncate" style={mutedStyle}>
                        {widget.title}
                      </h3>

                      {/* Action buttons — visible on hover */}
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 ml-2">
                        {/* Fullscreen */}
                        <button
                          onClick={() => setFullscreenWidget(widget)}
                          className="p-1 rounded hover:bg-gray-100 transition-colors"
                          style={mutedStyle}
                          title="Expand"
                        >
                          <Maximize2 size={12} />
                        </button>

                        {/* Per-widget PNG download */}
                        <button
                          onClick={() => downloadWidgetAsPNG(widgetRefs.current[widget.id], widget.title)}
                          className="p-1 rounded hover:bg-gray-100 transition-colors"
                          style={mutedStyle}
                          title="Download as PNG"
                        >
                          <Download size={12} />
                        </button>

                        {/* Original image */}
                        {imageData && (
                          <button
                            onClick={() => setOriginalImage(imageData)}
                            className="p-1 rounded hover:bg-gray-100 transition-colors flex items-center gap-1"
                            style={mutedStyle}
                            title="View original"
                          >
                            <ImageIcon size={12} />
                          </button>
                        )}
                      </div>
                    </div>

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
                          onDataPointClick={handleDataPointClick}
                        />
                      ) : (
                        <div className="h-full flex items-center justify-center text-sm" style={mutedStyle}>No data</div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Fullscreen widget modal ── */}
      {fullscreenWidget && (() => {
        const cd = widgetData[fullscreenWidget.id] ?? fullscreenWidget.chart_data
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
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => downloadWidgetAsPNG(document.getElementById('fullscreen-chart'), fullscreenWidget.title)}
                    className="flex items-center gap-1.5 text-xs px-2 py-1 rounded hover:bg-gray-100 transition-colors"
                    style={mutedStyle}
                  >
                    <Download size={12} /> Download
                  </button>
                  <button onClick={() => setFullscreenWidget(null)}
                    className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors" style={mutedStyle}>
                    <X size={16} />
                  </button>
                </div>
              </div>
              <div id="fullscreen-chart" className="flex-1 p-4 overflow-auto">
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
                    onDataPointClick={handleDataPointClick}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center" style={mutedStyle}>No data</div>
                )}
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Export modal ── */}
      {showExport && dashboard && (
        <ExportModal dashboardId={dashboard.id} projectId={projectId} dashboardName={dashboard.name} onClose={() => setShowExport(false)} />
      )}

      {/* ── Original screenshot modal ── */}
      {originalImage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={() => setOriginalImage(null)}>
          <div className="relative bg-white rounded-2xl shadow-2xl max-w-4xl max-h-[90vh] overflow-auto p-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold text-gray-700">Original Chart Image</span>
              <button onClick={() => setOriginalImage(null)} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors">
                <X size={16} />
              </button>
            </div>
            <img src={`data:image/png;base64,${originalImage}`} alt="Original chart" className="max-w-full rounded-lg border border-gray-100" />
          </div>
        </div>
      )}
    </div>
  )
}
