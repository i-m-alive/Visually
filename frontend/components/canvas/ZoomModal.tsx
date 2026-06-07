'use client'
import { useEffect } from 'react'
import { X, Download } from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'

interface Props {
  widget: {
    id: string
    title: string
    chart_type: string
    chart_data: Record<string, unknown> | null
    config?: Record<string, unknown>
  }
  colors?: string[]
  onClose: () => void
}

export function ZoomModal({ widget, colors, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const result: ChartResult = {
    chart_type: widget.chart_type,
    title: widget.title,
    chart_data: {
      rows: (widget.chart_data?.rows as Record<string, unknown>[]) || [],
      columns: (widget.chart_data?.columns as string[]) || [],
      labels: (widget.chart_data?.labels as string[]) || [],
      values: (widget.chart_data?.values as number[]) || [],
    },
    x_axis_label: (widget.config?.x_axis_label as string) || '',
    y_axis_label: (widget.config?.y_axis_label as string) || '',
    sql: '',
    score: 0,
    low_confidence: false,
    table_used: '',
  }

  const handleDownload = async () => {
    const el = document.getElementById(`zoom-chart-${widget.id}`)
    if (!el) return
    try {
      const html2canvas = (await import('html2canvas')).default
      const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2 })
      const link = document.createElement('a')
      link.download = `${widget.title.replace(/\s+/g, '_')}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
    } catch {
      // fallback: skip download
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl flex flex-col" style={{ maxHeight: '90vh' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <h2 className="text-base font-semibold text-gray-900 truncate">{widget.title}</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handleDownload}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
            >
              <Download size={13} /> PNG
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <X size={18} />
            </button>
          </div>
        </div>
        {/* Chart — fills modal body; height measured by ResizeObserver */}
        <div id={`zoom-chart-${widget.id}`} className="flex-1 overflow-auto p-6 min-h-0">
          <ChartRenderer result={result} colors={colors} height={520} />
        </div>
      </div>
    </div>
  )
}
