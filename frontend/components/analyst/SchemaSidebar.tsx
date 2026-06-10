'use client'
import { useState, useEffect } from 'react'
import { Database, ChevronDown, ChevronRight, Search, Eye, Table, Loader2, X, Play } from 'lucide-react'
import { analystApi } from '@/lib/api'
import type { TableInfo } from '@/lib/api'

interface SchemaSidebarProps {
  token: string
  onQueryOpen: (sql: string) => void
}

export function SchemaSidebar({ token, onQueryOpen }: SchemaSidebarProps) {
  const [tables, setTables] = useState<TableInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [preview, setPreview] = useState<{ table: string; rows: unknown[]; columns: string[] } | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => {
    analystApi.getSchema(token).then(r => {
      setTables(r.data.tables)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [token])

  const toggleExpand = (name: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }

  const loadPreview = async (tableName: string) => {
    setPreviewLoading(true)
    setPreview(null)
    try {
      const r = await analystApi.previewTable(token, tableName, 50)
      setPreview({ table: tableName, rows: r.data.rows, columns: r.data.columns })
    } catch { /* ignore */ } finally {
      setPreviewLoading(false)
    }
  }

  const filtered = tables.filter(t => t.name.toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-3 py-3 border-b border-gray-100">
        <div className="flex items-center gap-2 mb-2">
          <Database size={14} className="text-blue-500" />
          <span className="text-xs font-semibold text-gray-700">Schema Explorer</span>
        </div>
        <div className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search tables..."
            className="w-full pl-7 pr-3 py-1.5 text-xs border border-gray-200 rounded-lg bg-gray-50 focus:outline-none focus:border-blue-400"
          />
        </div>
      </div>

      {/* Table list */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-24">
            <Loader2 size={16} className="animate-spin text-blue-400" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-xs text-gray-400 text-center py-6">No tables found</div>
        ) : (
          <div className="py-1">
            {filtered.map(table => (
              <div key={table.name}>
                <div
                  className="flex items-center gap-1.5 px-3 py-2 hover:bg-gray-50 cursor-pointer group"
                  onClick={() => toggleExpand(table.name)}
                >
                  {expanded.has(table.name) ? <ChevronDown size={12} className="text-gray-400 flex-shrink-0" /> : <ChevronRight size={12} className="text-gray-400 flex-shrink-0" />}
                  <Table size={12} className="text-blue-500 flex-shrink-0" />
                  <span className="text-xs text-gray-700 font-medium truncate flex-1">{table.name}</span>
                  <span className="text-xs text-gray-400 flex-shrink-0">{table.column_count}</span>
                  <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={e => { e.stopPropagation(); loadPreview(table.name) }}
                      className="p-0.5 text-gray-400 hover:text-blue-600 rounded"
                      title="Preview data"
                    >
                      <Eye size={11} />
                    </button>
                    <button
                      onClick={e => { e.stopPropagation(); onQueryOpen(`SELECT * FROM ${table.name} LIMIT 100`) }}
                      className="p-0.5 text-gray-400 hover:text-green-600 rounded"
                      title="Query in sandbox"
                    >
                      <Play size={11} />
                    </button>
                  </div>
                </div>
                {expanded.has(table.name) && table.columns.length > 0 && (
                  <div className="ml-6 border-l border-gray-100 pl-2 pb-1">
                    {table.columns.map(col => (
                      <div key={col.name} className="flex items-center gap-2 py-0.5 px-2 hover:bg-gray-50 rounded cursor-pointer group"
                        onClick={() => onQueryOpen(`SELECT ${col.name} FROM ${table.name} GROUP BY ${col.name} ORDER BY COUNT(*) DESC LIMIT 20`)}>
                        <span className="text-xs text-gray-600 truncate flex-1">{col.name}</span>
                        <span className="text-xs text-gray-300 font-mono flex-shrink-0">{col.type}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Preview panel */}
      {(preview || previewLoading) && (
        <div className="border-t border-gray-100 bg-gray-50">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
            <span className="text-xs font-semibold text-gray-600 truncate">
              {previewLoading ? 'Loading…' : preview?.table}
            </span>
            <button onClick={() => setPreview(null)} className="text-gray-400 hover:text-gray-600">
              <X size={12} />
            </button>
          </div>
          {previewLoading ? (
            <div className="flex items-center justify-center h-16">
              <Loader2 size={14} className="animate-spin text-blue-400" />
            </div>
          ) : preview && (
            <div className="overflow-auto max-h-48">
              <table className="w-full text-xs">
                <thead className="bg-gray-100 sticky top-0">
                  <tr>
                    {preview.columns.map(c => (
                      <th key={c} className="px-2 py-1 text-left font-medium text-gray-600 whitespace-nowrap border-r border-gray-200 last:border-0">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(preview.rows as Record<string, unknown>[]).slice(0, 20).map((row, i) => (
                    <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                      {preview.columns.map(c => (
                        <td key={c} className="px-2 py-0.5 text-gray-600 whitespace-nowrap border-r border-gray-100 last:border-0 max-w-[120px] truncate">
                          {String(row[c] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
