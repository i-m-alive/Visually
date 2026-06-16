'use client'
import { useState, useEffect } from 'react'
import { Loader2, AlertCircle } from 'lucide-react'
import { dashboardApi } from '@/lib/api'
import { VisuallReport } from './VisuallReport'

interface Props {
  dashboardId: string
  projectId?: string
  canEdit?: boolean
  onClose: () => void
}

export function VisuallReportLoader({ dashboardId, projectId, canEdit = false, onClose }: Props) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    dashboardApi.get(dashboardId)
      .then(resp => { setData(resp.data); setLoading(false) })
      .catch(() => { setError('Failed to load report'); setLoading(false) })
  }, [dashboardId])

  if (loading) return (
    <div className="fixed inset-0 z-50 bg-gray-950 flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="w-10 h-10 text-blue-400 animate-spin" />
        <p className="text-sm text-gray-400">Loading report…</p>
      </div>
    </div>
  )

  if (error || !data) return (
    <div className="fixed inset-0 z-50 bg-white flex items-center justify-center">
      <div className="text-center">
        <AlertCircle className="w-10 h-10 text-red-400 mx-auto mb-3" />
        <p className="text-sm text-gray-500">{error ?? 'Report not found'}</p>
        <button
          onClick={onClose}
          className="mt-4 px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          Go back
        </button>
      </div>
    </div>
  )

  const resolvedProjectId = data.project_id ?? projectId ?? ''

  const canvas = {
    id: data.id,
    name: data.name,
    project_id: resolvedProjectId,
    filter_config: data.filter_config,
  }

  const widgets = (data.widgets ?? []).map((w: any) => ({
    id: w.id,
    title: w.title,
    chart_type: w.chart_type,
    chart_data: w.chart_data,
    config: w.config,
    connection_id: w.connection_id,
    sql_query: w.sql_query ?? w.base_sql,
    validation_score: w.validation_score,
  }))

  const pages = (data.page_tabs ?? []).map((p: any, i: number) => ({
    id: p.id ?? `page-${i}`,
    name: p.name,
    order: i,
  }))

  return (
    <VisuallReport
      canvas={canvas}
      widgets={widgets}
      pages={pages}
      projectId={resolvedProjectId}
      canEdit={canEdit}
      onClose={onClose}
      onWidgetAdded={() => {}}
    />
  )
}
