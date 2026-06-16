'use client'
import { useState } from 'react'
import { X, Play, Loader2, Download, AlertCircle, Terminal } from 'lucide-react'
import { analystApi } from '@/lib/api'

interface QueryModalProps {
  token: string
  initialSql?: string
  onClose: () => void
}

export function QueryModal({ token, initialSql = '', onClose }: QueryModalProps) {
  const [sql, setSql] = useState(initialSql)
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [columns, setColumns] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [duration, setDuration] = useState<number | null>(null)
  const [hasRun, setHasRun] = useState(false)

  const run = async () => {
    if (!sql.trim() || loading) return
    setLoading(true); setError(''); setHasRun(false)
    try {
      const r = await analystApi.query(token, sql)
      setRows(r.data.rows as Record<string, unknown>[])
      setColumns(r.data.columns)
      setDuration(r.data.duration_ms)
      setHasRun(true)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err.response?.data?.detail ?? 'Query failed')
    } finally {
      setLoading(false)
    }
  }

  const downloadCsv = () => {
    const header = columns.join(',')
    const body = rows.map(row => columns.map(c => JSON.stringify(row[c] ?? '')).join(',')).join('\n')
    const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'query-result.csv'
    a.click()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.4)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <Terminal size={15} className="text-green-500" /> SQL Sandbox
          </h3>
          <div className="flex items-center gap-2">
            {hasRun && rows.length > 0 && (
              <button onClick={downloadCsv} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
                <Download size={12} /> CSV
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* SQL Editor */}
        <div className="px-4 pt-3 pb-2 border-b border-gray-100 flex-shrink-0">
          <div className="relative">
            <textarea
              value={sql}
              onChange={e => setSql(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run() }}
              placeholder="SELECT * FROM your_table LIMIT 100"
              rows={5}
              className="w-full font-mono text-xs p-3 bg-gray-950 text-green-400 rounded-xl border border-gray-800 outline-none resize-none placeholder-gray-600 leading-relaxed"
            />
            <button
              onClick={run}
              disabled={!sql.trim() || loading}
              className="absolute bottom-3 right-3 flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white rounded-lg disabled:opacity-50 transition-all"
              style={{ background: 'linear-gradient(135deg, #059669, #047857)' }}
            >
              {loading ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              {loading ? 'Running…' : 'Run (⌘↵)'}
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-1.5">SELECT queries only · restricted to canvas tables</p>
        </div>

        {/* Results */}
        <div className="flex-1 overflow-auto">
          {error ? (
            <div className="m-4 flex items-start gap-2 p-3 bg-red-50 border border-red-100 rounded-xl text-xs text-red-700">
              <AlertCircle size={13} className="flex-shrink-0 mt-0.5" />
              {error}
            </div>
          ) : hasRun && columns.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">No results</div>
          ) : hasRun ? (
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
                  <tr key={i} className={`border-b border-gray-50 ${i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}`}>
                    {columns.map(c => (
                      <td key={c} className="px-4 py-2 text-gray-700 whitespace-nowrap border-r border-gray-50 last:border-0 max-w-[200px] truncate">{String(row[c] ?? '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Run a query to see results</div>
          )}
        </div>

        {hasRun && (
          <div className="px-5 py-2.5 border-t border-gray-100 bg-gray-50 flex items-center gap-3 flex-shrink-0">
            <span className="text-xs text-gray-500">{rows.length} row{rows.length !== 1 ? 's' : ''}</span>
            {duration != null && <span className="text-xs text-gray-400">{Math.round(duration)}ms</span>}
          </div>
        )}
      </div>
    </div>
  )
}
