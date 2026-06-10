'use client'
import { useState, useEffect } from 'react'
import { X, Loader2, Download, ZoomIn } from 'lucide-react'
import { analystApi } from '@/lib/api'
import type { FilterItem } from '@/lib/api'

interface DrilldownModalProps {
  token: string
  widgetId: string
  widgetTitle: string
  xColumn: string
  xValue: string
  activeFilters: FilterItem[]
  onClose: () => void
}

export function DrilldownModal({ token, widgetId, widgetTitle, xColumn, xValue, activeFilters, onClose }: DrilldownModalProps) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [columns, setColumns] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    analystApi.drilldown(token, widgetId, { x_column: xColumn, x_value: xValue, filters: activeFilters })
      .then(r => {
        setRows(r.data.rows as Record<string, unknown>[])
        setColumns(r.data.columns)
      })
      .catch(e => setError(e.response?.data?.detail ?? 'Failed to load drill-down data'))
      .finally(() => setLoading(false))
  }, [])

  const downloadCsv = () => {
    const header = columns.join(',')
    const body = rows.map(row => columns.map(c => JSON.stringify(row[c] ?? '')).join(',')).join('\n')
    const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `drilldown-${xValue}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.4)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <ZoomIn size={15} className="text-blue-500" />
              Drill-down: <span className="text-blue-600">{xValue}</span>
            </h3>
            <p className="text-xs text-gray-400 mt-0.5">{widgetTitle} · {xColumn} = {xValue}</p>
          </div>
          <div className="flex items-center gap-2">
            {!loading && rows.length > 0 && (
              <button onClick={downloadCsv} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
                <Download size={12} /> CSV
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center h-48">
              <Loader2 size={24} className="animate-spin text-blue-500" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-48 text-red-500 text-sm">{error}</div>
          ) : rows.length === 0 ? (
            <div className="flex items-center justify-center h-48 text-gray-400 text-sm">No rows found</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0 border-b border-gray-100">
                <tr>
                  {columns.map(c => (
                    <th key={c} className="px-4 py-2.5 text-left font-semibold text-gray-600 whitespace-nowrap border-r border-gray-100 last:border-0">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className={`border-b border-gray-50 ${i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'} hover:bg-blue-50/30 transition-colors`}>
                    {columns.map(c => (
                      <td key={c} className="px-4 py-2 text-gray-700 whitespace-nowrap border-r border-gray-50 last:border-0 max-w-[200px] truncate">
                        {String(row[c] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="px-5 py-2.5 border-t border-gray-100 flex-shrink-0 bg-gray-50">
          <span className="text-xs text-gray-400">{rows.length} row{rows.length !== 1 ? 's' : ''}</span>
        </div>
      </div>
    </div>
  )
}
