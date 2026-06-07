'use client'
import { useState, useEffect, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Download, LayoutDashboard, BarChart2,
  Loader2, AlertCircle, ChevronRight, Trash2, Image as ImageIcon, X
} from 'lucide-react'
import { dashboardApi } from '@/lib/api'
import { ExportModal } from '@/components/export/ExportModal'
import { ThemeSwitcher } from '@/components/export/ThemeSwitcher'
import { ChartRenderer } from '@/components/charts/ChartRenderer'

interface Widget {
  id: string
  title: string
  chart_type: string
  chart_data: { rows: any[]; columns: string[] } | null
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
  created_at: string
  widgets?: Widget[]
}

export default function DashboardPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const router = useRouter()
  const [dashboards, setDashboards] = useState<Dashboard[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showExport, setShowExport] = useState(false)
  const [themeUpdating, setThemeUpdating] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [originalImage, setOriginalImage] = useState<string | null>(null)

  // Load dashboard list
  useEffect(() => {
    const fetchDashboards = async () => {
      setLoading(true)
      setError(null)
      try {
        const resp = await dashboardApi.list(projectId)
        const list: Dashboard[] = resp.data?.dashboards ?? resp.data ?? []
        setDashboards(list)
        if (list.length > 0) setSelectedId(list[0].id)
      } catch {
        setError('Failed to load dashboards')
      } finally {
        setLoading(false)
      }
    }
    fetchDashboards()
  }, [projectId])

  // Load selected dashboard detail
  useEffect(() => {
    if (!selectedId) return
    const fetchDetail = async () => {
      try {
        const resp = await dashboardApi.get(selectedId)
        setDashboard(resp.data)
      } catch {
        setError('Failed to load dashboard')
      }
    }
    fetchDetail()
  }, [selectedId])

  const handleThemeChange = useCallback(async (newTheme: string) => {
    if (!dashboard) return
    setThemeUpdating(true)
    try {
      await dashboardApi.updateTheme(dashboard.id, newTheme)
      setDashboard((d) => d ? { ...d, theme: newTheme } : d)
    } catch {
      // silently ignore theme update failures
    } finally {
      setThemeUpdating(false)
    }
  }, [dashboard])

  const handleDeleteDashboard = async (id: string) => {
    if (confirmDeleteId !== id) {
      setConfirmDeleteId(id)
      return
    }
    setDeletingId(id)
    setConfirmDeleteId(null)
    try {
      await dashboardApi.delete(id)
      const updated = dashboards.filter(d => d.id !== id)
      setDashboards(updated)
      if (selectedId === id) {
        setSelectedId(updated[0]?.id ?? null)
        setDashboard(null)
      }
    } catch {
      setError('Failed to delete dashboard')
    } finally {
      setDeletingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500">
        <AlertCircle className="w-10 h-10 text-red-400" />
        <p className="text-sm">{error}</p>
      </div>
    )
  }

  if (dashboards.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 text-gray-400 p-8">
        <LayoutDashboard className="w-12 h-12" />
        <div className="text-center">
          <p className="font-600 text-gray-600">No dashboards yet</p>
          <p className="text-sm mt-1">
            Ask a question in the{' '}
            <a href={`/projects/${projectId}/query`} className="text-blue-600 hover:underline">
              Query page
            </a>{' '}
            to create your first dashboard.
          </p>
        </div>
      </div>
    )
  }

  const widgets = dashboard?.widgets ?? []
  const sortedWidgets = [...widgets].sort((a, b) => {
    if (a.position_y !== b.position_y) return a.position_y - b.position_y
    return a.position_x - b.position_x
  })

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-gray-50">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-6 py-3 bg-white border-b border-gray-100 flex-shrink-0">
        {/* Dashboard selector */}
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <BarChart2 className="w-4 h-4 text-gray-400 flex-shrink-0" />
          {dashboards.length === 1 ? (
            <span className="text-sm font-600 text-gray-800 truncate">
              {dashboard?.name ?? dashboards[0].name}
            </span>
          ) : (
            <select
              value={selectedId ?? ''}
              onChange={(e) => setSelectedId(e.target.value)}
              className="text-sm font-500 text-gray-800 border-0 bg-transparent outline-none cursor-pointer max-w-xs"
            >
              {dashboards.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          )}
          {dashboard && (
            <ChevronRight className="w-3 h-3 text-gray-300 flex-shrink-0" />
          )}
        </div>

        {/* Theme switcher */}
        {dashboard && (
          <ThemeSwitcher
            currentTheme={dashboard.theme ?? 'frost'}
            onSelect={handleThemeChange}
            disabled={themeUpdating}
          />
        )}

        {/* Export button */}
        {dashboard && (
          <button
            onClick={() => setShowExport(true)}
            className="flex items-center gap-2 px-4 py-1.5 bg-blue-600 text-white text-sm font-600 rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Download size={14} />
            Export
          </button>
        )}

        {/* Delete dashboard button */}
        {dashboard && (
          confirmDeleteId === dashboard.id ? (
            <div className="flex items-center gap-1.5 border border-red-200 rounded-lg px-2 py-1">
              <span className="text-xs text-red-600 font-medium">Delete dashboard?</span>
              <button
                onClick={() => handleDeleteDashboard(dashboard.id)}
                disabled={deletingId === dashboard.id}
                className="text-xs font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded transition-colors"
              >
                {deletingId === dashboard.id ? <Loader2 size={10} className="animate-spin" /> : 'Delete'}
              </button>
              <button
                onClick={() => setConfirmDeleteId(null)}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => handleDeleteDashboard(dashboard.id)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-500 hover:bg-red-50 rounded-lg transition-colors border border-transparent hover:border-red-200"
              title="Delete this dashboard"
            >
              <Trash2 size={14} /> Delete
            </button>
          )
        )}
      </div>

      {/* Dashboard grid */}
      <div className="flex-1 overflow-auto p-6">
        {!dashboard ? (
          <div className="flex items-center justify-center h-40">
            <Loader2 className="w-6 h-6 text-blue-400 animate-spin" />
          </div>
        ) : widgets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-gray-400 gap-2">
            <LayoutDashboard className="w-10 h-10" />
            <p className="text-sm">This dashboard has no widgets yet.</p>
          </div>
        ) : (
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: 'repeat(12, 1fr)' }}
          >
            {sortedWidgets.map((widget) => {
              const colSpan = Math.max(1, Math.min(12, widget.width))
              const chartHeight = Math.max(200, widget.height * 55)
              const config = (widget.config as Record<string, string>) ?? {}
              const imageData = (widget.chart_data as Record<string, unknown>)?.image_data as string | undefined

              return (
                <div
                  key={widget.id}
                  className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow"
                  style={{ gridColumn: `span ${colSpan}` }}
                >
                  <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                    <h3 className="text-xs font-600 text-gray-500 uppercase tracking-wide truncate">
                      {widget.title}
                    </h3>
                    {imageData && (
                      <button
                        onClick={() => setOriginalImage(imageData)}
                        className="flex items-center gap-1 text-xs text-gray-400 hover:text-brand transition-colors flex-shrink-0 ml-2"
                        title="View original chart image"
                      >
                        <ImageIcon size={12} /> Original
                      </button>
                    )}
                  </div>
                  <div className="p-3" style={{ height: `${chartHeight}px` }}>
                    {widget.chart_data ? (
                      <ChartRenderer
                        result={{
                          chart_type: widget.chart_type,
                          title: widget.title,
                          chart_data: {
                            rows: (widget.chart_data as Record<string, unknown>)?.rows as Record<string, unknown>[] || [],
                            columns: (widget.chart_data as Record<string, unknown>)?.columns as string[] || [],
                            labels: (widget.chart_data as Record<string, unknown>)?.labels as string[] || [],
                            values: (widget.chart_data as Record<string, unknown>)?.values as number[] || [],
                          },
                          x_axis_label: config.x_axis_label || '',
                          y_axis_label: config.y_axis_label || '',
                          sql: '',
                          score: 0,
                          low_confidence: false,
                          table_used: '',
                        }}
                      />
                    ) : (
                      <div className="h-full flex items-center justify-center text-gray-300 text-sm">
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

      {/* Export modal */}
      {showExport && dashboard && (
        <ExportModal
          dashboardId={dashboard.id}
          projectId={projectId}
          dashboardName={dashboard.name}
          onClose={() => setShowExport(false)}
        />
      )}

      {/* Original screenshot modal */}
      {originalImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setOriginalImage(null)}
        >
          <div
            className="relative bg-white rounded-2xl shadow-2xl max-w-4xl max-h-[90vh] overflow-auto p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold text-gray-700">Original Chart Image</span>
              <button
                onClick={() => setOriginalImage(null)}
                className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <X size={16} />
              </button>
            </div>
            <img
              src={`data:image/png;base64,${originalImage}`}
              alt="Original chart"
              className="max-w-full rounded-lg border border-gray-100"
            />
          </div>
        </div>
      )}
    </div>
  )
}
