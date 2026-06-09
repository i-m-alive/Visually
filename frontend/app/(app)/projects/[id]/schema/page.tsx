'use client'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import { projectApi } from '@/lib/api'
import {
  ChevronDown, ChevronRight, RefreshCw, Database, Hash,
  Loader2, AlertCircle, Sparkles, Link2, Filter, BarChart2,
  Calendar, Layers, Tag, X,
} from 'lucide-react'

// ── Raw schema types ──────────────────────────────────────────────────────────
interface ColumnMeta {
  name: string
  type: string
  is_nullable: boolean
  is_primary_key: boolean
  description: string
  stats?: Record<string, unknown>
}
interface TableMeta {
  name: string
  schema: string
  row_count: number
  importance_rank: number
  description: string
  columns: ColumnMeta[]
  relationships: Array<{ column: string; references: string; inferred: boolean }>
}
interface SchemaData {
  tables: TableMeta[]
  important_tables: string[]
  total_tables: number
  version: number
  created_at: string
}

// ── AI metadata types ─────────────────────────────────────────────────────────
interface AiColumnMeta {
  column_name: string
  business_name: string | null
  description: string | null
  semantic_type: string | null
  fk_target_table: string | null
  fk_target_column: string | null
  fk_confirmed: boolean
  example_values: string[]
  is_kpi_metric: boolean | null
  is_dimension: boolean | null
  is_filter_eligible: boolean | null
}
interface AiTableMeta {
  table_name: string
  business_name: string | null
  description: string | null
  grain: string | null
  is_fact_table: boolean | null
  use_for: string[]
  never_use_for: string[]
  key_metric_cols: string[]
  key_dimension_cols: string[]
  key_date_cols: string[]
  generation_method: string
  generated_at: string | null
  columns: AiColumnMeta[]
}
interface MetadataResponse {
  total_tables: number
  tables: AiTableMeta[]
}

// ── Semantic type badge ───────────────────────────────────────────────────────
const SEM_COLORS: Record<string, string> = {
  pk:         'bg-amber-100 text-amber-700',
  fk:         'bg-blue-100 text-blue-700',
  metric:     'bg-green-100 text-green-700',
  dimension:  'bg-purple-100 text-purple-700',
  date:       'bg-teal-100 text-teal-700',
  identifier: 'bg-gray-100 text-gray-600',
  text:       'bg-gray-100 text-gray-600',
  flag:       'bg-orange-100 text-orange-700',
}
function SemBadge({ type }: { type: string | null }) {
  if (!type) return null
  const cls = SEM_COLORS[type] ?? 'bg-gray-100 text-gray-600'
  return <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wide ${cls}`}>{type}</span>
}

// ── Chip list ─────────────────────────────────────────────────────────────────
function Chips({ items, color = 'gray' }: { items: string[]; color?: string }) {
  if (!items.length) return <span className="text-xs text-gray-300">—</span>
  const cls: Record<string, string> = {
    gray:   'bg-gray-100 text-gray-600',
    green:  'bg-green-50 text-green-700',
    purple: 'bg-purple-50 text-purple-700',
    teal:   'bg-teal-50 text-teal-700',
    red:    'bg-red-50 text-red-600',
    blue:   'bg-blue-50 text-blue-700',
  }
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((v) => (
        <span key={v} className={`text-xs px-2 py-0.5 rounded-full ${cls[color] ?? cls.gray}`}>{v}</span>
      ))}
    </div>
  )
}

export default function SchemaPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const [tab, setTab] = useState<'schema' | 'metadata'>('schema')

  // Raw schema state
  const [schema, setSchema] = useState<SchemaData | null>(null)
  const [schemaLoading, setSchemaLoading] = useState(true)
  const [schemaError, setSchemaError] = useState('')
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())

  // Crawl state
  const [crawling, setCrawling] = useState(false)
  const [crawlStatus, setCrawlStatus] = useState('')
  const [crawlError, setCrawlError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Metadata state
  const [metadata, setMetadata] = useState<MetadataResponse | null>(null)
  const [metaLoading, setMetaLoading] = useState(false)
  const [metaError, setMetaError] = useState('')
  const [expandedMeta, setExpandedMeta] = useState<Set<string>>(new Set())
  const [metaSearch, setMetaSearch] = useState('')

  // ── Data fetching ───────────────────────────────────────────────────────────
  const fetchSchema = async () => {
    try {
      const resp = await projectApi.getSchema(projectId)
      setSchema(resp.data.schema)
      setSchemaError('')
    } catch {
      setSchemaError('No schema found. Please crawl first.')
    } finally {
      setSchemaLoading(false)
    }
  }

  const fetchMetadata = async () => {
    setMetaLoading(true)
    setMetaError('')
    try {
      const resp = await projectApi.getSchemaMetadata(projectId)
      setMetadata(resp.data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string }; status?: number } }
      if (err.response?.status === 404) {
        setMetaError(err.response.data?.detail ?? 'No metadata yet.')
      } else {
        setMetaError('Failed to load metadata.')
      }
    } finally {
      setMetaLoading(false)
    }
  }

  useEffect(() => { fetchSchema() }, [projectId])
  useEffect(() => { if (tab === 'metadata' && !metadata && !metaLoading) fetchMetadata() }, [tab])

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  const handleRecrawl = async () => {
    setCrawling(true); setCrawlError(''); setCrawlStatus('Starting crawl...')
    stopPoll()
    try {
      const resp = await projectApi.triggerCrawl(projectId)
      const jobId: string = resp.data.job_id
      if (!jobId) { setCrawlError('No job ID returned'); setCrawling(false); return }
      setCrawlStatus('Crawling database schema...')
      pollRef.current = setInterval(async () => {
        try {
          const s = await projectApi.getCrawlStatus(projectId, jobId)
          if (s.data.status === 'completed') {
            stopPoll(); setCrawling(false); setCrawlStatus('')
            await fetchSchema()
            // Refresh metadata after a short delay (background extraction takes ~30-60s)
            setTimeout(() => { setMetadata(null); if (tab === 'metadata') fetchMetadata() }, 5000)
          } else if (s.data.status === 'failed') {
            stopPoll(); setCrawling(false); setCrawlStatus('')
            setCrawlError(s.data.error || 'Crawl failed')
          } else {
            setCrawlStatus(`Crawl status: ${s.data.status}`)
          }
        } catch { /* keep waiting */ }
      }, 3000)
      setTimeout(() => {
        if (pollRef.current) { stopPoll(); setCrawling(false); setCrawlStatus(''); setCrawlError('Crawl timed out after 10 min') }
      }, 600_000)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setCrawlError(err.response?.data?.detail || 'Failed to start crawl')
      setCrawling(false); setCrawlStatus('')
    }
  }

  useEffect(() => () => stopPoll(), [])

  const toggleTable = (name: string) =>
    setExpandedTables(prev => { const n = new Set(prev); n.has(name) ? n.delete(name) : n.add(name); return n })
  const toggleMeta = (name: string) =>
    setExpandedMeta(prev => { const n = new Set(prev); n.has(name) ? n.delete(name) : n.add(name); return n })

  const filteredMeta = metadata?.tables.filter(t => {
    if (!metaSearch) return true
    const q = metaSearch.toLowerCase()
    return (
      t.table_name.toLowerCase().includes(q) ||
      (t.business_name ?? '').toLowerCase().includes(q) ||
      (t.description ?? '').toLowerCase().includes(q)
    )
  }) ?? []

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 bg-white">
        <div>
          <h2 className="text-lg font-semibold font-display text-gray-900">Schema Explorer</h2>
          {schema && (
            <p className="text-sm text-gray-500">
              {schema.total_tables} tables · v{schema.version} · {new Date(schema.created_at).toLocaleString()}
            </p>
          )}
          {crawlStatus && (
            <p className="text-sm text-brand flex items-center gap-1 mt-0.5">
              <Loader2 size={12} className="animate-spin" />{crawlStatus}
            </p>
          )}
          {crawlError && (
            <p className="text-sm text-red-600 flex items-center gap-1 mt-0.5">
              <AlertCircle size={12} />{crawlError}
            </p>
          )}
        </div>
        <button onClick={handleRecrawl} disabled={crawling} className="btn-secondary flex items-center gap-2 text-sm">
          <RefreshCw size={14} className={crawling ? 'animate-spin' : ''} />
          {crawling ? 'Crawling...' : 'Re-crawl'}
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 px-6 pt-3 bg-white border-b border-gray-100">
        <button
          onClick={() => setTab('schema')}
          className={`px-4 py-2 text-sm font-medium rounded-t transition-colors ${
            tab === 'schema'
              ? 'bg-white border border-b-white border-gray-200 text-gray-900 -mb-px'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <span className="flex items-center gap-1.5"><Database size={14} />Raw Schema</span>
        </button>
        <button
          onClick={() => setTab('metadata')}
          className={`px-4 py-2 text-sm font-medium rounded-t transition-colors ${
            tab === 'metadata'
              ? 'bg-white border border-b-white border-gray-200 text-gray-900 -mb-px'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <span className="flex items-center gap-1.5"><Sparkles size={14} />AI Metadata</span>
        </button>
      </div>

      {/* ── Raw Schema tab ───────────────────────────────────────────────── */}
      {tab === 'schema' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {schemaLoading && <p className="text-gray-400 text-sm p-4">Loading schema...</p>}
          {schemaError && !schemaLoading && (
            <div className="text-center py-12">
              <Database size={40} className="mx-auto text-gray-200 mb-3" />
              <p className="text-gray-500 text-sm">{schemaError}</p>
              <button onClick={handleRecrawl} disabled={crawling} className="btn-primary mt-3 text-sm flex items-center gap-2 mx-auto">
                {crawling && <Loader2 size={14} className="animate-spin" />}
                {crawling ? 'Crawling...' : 'Start crawl'}
              </button>
            </div>
          )}
          {schema?.tables.map((table) => {
            const isImportant = schema.important_tables.includes(table.name)
            const isExpanded = expandedTables.has(table.name)
            return (
              <div key={table.name} className="card overflow-hidden">
                <button
                  onClick={() => toggleTable(table.name)}
                  className="w-full flex items-center gap-3 p-4 text-left hover:bg-gray-50 transition-colors"
                >
                  {isExpanded ? <ChevronDown size={16} className="text-gray-400" /> : <ChevronRight size={16} className="text-gray-400" />}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900 font-mono text-sm">{table.name}</span>
                      {isImportant && <span className="text-xs px-1.5 py-0.5 bg-brand-light text-brand rounded font-medium">Important</span>}
                      <span className="text-xs text-gray-400">#{table.importance_rank}</span>
                    </div>
                    <p className="text-xs text-gray-500 mt-0.5 truncate">{table.description}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm font-medium text-gray-700">{table.row_count.toLocaleString()}</p>
                    <p className="text-xs text-gray-400">rows</p>
                  </div>
                </button>
                {isExpanded && (
                  <div className="border-t border-gray-100">
                    <div className="p-4 space-y-1">
                      {table.columns.map((col) => (
                        <div key={col.name} className="flex items-start gap-3 py-1.5 border-b border-gray-50 last:border-0">
                          <div className="flex items-center gap-1.5 min-w-0 flex-1">
                            {col.is_primary_key
                              ? <Hash size={12} className="text-amber-500 shrink-0" />
                              : <div className="w-3 h-3 rounded-sm bg-gray-200 shrink-0" />}
                            <span className="font-mono text-xs font-medium text-gray-800">{col.name}</span>
                            <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{col.type}</span>
                            {col.is_nullable && <span className="text-xs text-gray-300">nullable</span>}
                          </div>
                          <p className="text-xs text-gray-500 text-right max-w-xs">{col.description}</p>
                        </div>
                      ))}
                    </div>
                    {table.relationships.length > 0 && (
                      <div className="px-4 pb-4">
                        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Relationships</p>
                        {table.relationships.map((rel, i) => (
                          <div key={i} className="text-xs text-gray-500 flex items-center gap-2">
                            <span className="font-mono text-gray-700">{rel.column}</span>
                            <span className="text-gray-300">→</span>
                            <span className="font-mono text-brand">{rel.references}</span>
                            {rel.inferred && <span className="text-gray-300">(inferred)</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ── AI Metadata tab ──────────────────────────────────────────────── */}
      {tab === 'metadata' && (
        <div className="flex-1 overflow-y-auto">
          {metaLoading && (
            <div className="flex items-center justify-center py-16 gap-2 text-gray-400">
              <Loader2 size={18} className="animate-spin" />
              <span className="text-sm">Loading AI metadata...</span>
            </div>
          )}
          {metaError && !metaLoading && (
            <div className="text-center py-12 px-6">
              <Sparkles size={40} className="mx-auto text-gray-200 mb-3" />
              <p className="text-gray-500 text-sm max-w-sm mx-auto">{metaError}</p>
              <button onClick={fetchMetadata} className="btn-secondary mt-3 text-sm mx-auto flex items-center gap-2">
                <RefreshCw size={13} />Retry
              </button>
            </div>
          )}
          {metadata && !metaLoading && (
            <>
              {/* Toolbar */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50">
                <div className="relative flex-1 max-w-xs">
                  <input
                    value={metaSearch}
                    onChange={e => setMetaSearch(e.target.value)}
                    placeholder="Filter tables..."
                    className="w-full text-sm border border-gray-200 rounded-lg px-3 py-1.5 pr-7 focus:outline-none focus:ring-2 focus:ring-brand/30"
                  />
                  {metaSearch && (
                    <button onClick={() => setMetaSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                      <X size={13} />
                    </button>
                  )}
                </div>
                <span className="text-xs text-gray-400">
                  {filteredMeta.length} / {metadata.total_tables} tables
                </span>
                <button onClick={fetchMetadata} className="ml-auto text-gray-400 hover:text-gray-600">
                  <RefreshCw size={14} />
                </button>
              </div>

              <div className="p-4 space-y-3">
                {filteredMeta.map((table) => {
                  const isExpanded = expandedMeta.has(table.table_name)
                  return (
                    <div key={table.table_name} className="card overflow-hidden">
                      {/* Table header row */}
                      <button
                        onClick={() => toggleMeta(table.table_name)}
                        className="w-full flex items-start gap-3 p-4 text-left hover:bg-gray-50 transition-colors"
                      >
                        {isExpanded
                          ? <ChevronDown size={16} className="text-gray-400 mt-0.5 shrink-0" />
                          : <ChevronRight size={16} className="text-gray-400 mt-0.5 shrink-0" />}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-sm font-semibold text-gray-900">{table.table_name}</span>
                            {table.business_name && (
                              <span className="text-sm text-gray-500">· {table.business_name}</span>
                            )}
                            {table.is_fact_table === true && (
                              <span className="text-xs px-1.5 py-0.5 bg-green-100 text-green-700 rounded font-medium">Fact</span>
                            )}
                            {table.is_fact_table === false && (
                              <span className="text-xs px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded font-medium">Dimension</span>
                            )}
                          </div>
                          {table.description && (
                            <p className="text-xs text-gray-500 mt-1">{table.description}</p>
                          )}
                          {table.grain && (
                            <p className="text-xs text-gray-400 mt-0.5 italic">↳ {table.grain}</p>
                          )}
                        </div>
                        <div className="text-xs text-gray-400 shrink-0">
                          {table.columns.length} cols
                        </div>
                      </button>

                      {isExpanded && (
                        <div className="border-t border-gray-100 divide-y divide-gray-50">
                          {/* Use for / Never use for */}
                          {(table.use_for.length > 0 || table.never_use_for.length > 0) && (
                            <div className="px-4 py-3 grid grid-cols-2 gap-4">
                              {table.use_for.length > 0 && (
                                <div>
                                  <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                                    <Tag size={11} />Use for
                                  </p>
                                  <Chips items={table.use_for} color="blue" />
                                </div>
                              )}
                              {table.never_use_for.length > 0 && (
                                <div>
                                  <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                                    <X size={11} />Never use for
                                  </p>
                                  <Chips items={table.never_use_for} color="red" />
                                </div>
                              )}
                            </div>
                          )}

                          {/* Key column groups */}
                          {(table.key_metric_cols.length > 0 || table.key_dimension_cols.length > 0 || table.key_date_cols.length > 0) && (
                            <div className="px-4 py-3 grid grid-cols-3 gap-4">
                              <div>
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                                  <BarChart2 size={11} />Metrics
                                </p>
                                <Chips items={table.key_metric_cols} color="green" />
                              </div>
                              <div>
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                                  <Layers size={11} />Dimensions
                                </p>
                                <Chips items={table.key_dimension_cols} color="purple" />
                              </div>
                              <div>
                                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                                  <Calendar size={11} />Dates
                                </p>
                                <Chips items={table.key_date_cols} color="teal" />
                              </div>
                            </div>
                          )}

                          {/* Columns table */}
                          <div className="px-4 py-3">
                            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Columns</p>
                            <div className="space-y-1">
                              {table.columns.map((col) => (
                                <div key={col.column_name} className="flex items-start gap-2 py-1.5 border-b border-gray-50 last:border-0">
                                  <div className="flex items-center gap-1.5 w-40 shrink-0">
                                    <span className="font-mono text-xs font-medium text-gray-800 truncate">{col.column_name}</span>
                                  </div>
                                  <div className="flex items-center gap-1.5 shrink-0">
                                    <SemBadge type={col.semantic_type} />
                                    {col.is_kpi_metric && (
                                      <span title="KPI metric"><BarChart2 size={11} className="text-green-500" /></span>
                                    )}
                                    {col.is_filter_eligible && (
                                      <span title="Filter-eligible"><Filter size={11} className="text-blue-400" /></span>
                                    )}
                                    {col.fk_confirmed && col.fk_target_table && (
                                      <span className="flex items-center gap-0.5 text-[10px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded" title={`FK → ${col.fk_target_table}.${col.fk_target_column}`}>
                                        <Link2 size={9} />
                                        {col.fk_target_table.split('.').pop()}
                                      </span>
                                    )}
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    {col.business_name && (
                                      <p className="text-xs font-medium text-gray-700">{col.business_name}</p>
                                    )}
                                    {col.description && (
                                      <p className="text-xs text-gray-500">{col.description}</p>
                                    )}
                                    {col.example_values.length > 0 && (
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {col.example_values.slice(0, 6).map((v) => (
                                          <span key={v} className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{v}</span>
                                        ))}
                                        {col.example_values.length > 6 && (
                                          <span className="text-[10px] text-gray-400">+{col.example_values.length - 6} more</span>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* Footer */}
                          <div className="px-4 py-2 bg-gray-50 flex items-center gap-3">
                            <span className="text-[10px] text-gray-400">
                              Generated {table.generated_at ? new Date(table.generated_at).toLocaleString() : '—'}
                            </span>
                            <span className="text-[10px] bg-gray-200 text-gray-500 px-1.5 py-0.5 rounded">{table.generation_method}</span>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
