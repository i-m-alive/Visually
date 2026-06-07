'use client'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import { projectApi } from '@/lib/api'
import { ChevronDown, ChevronRight, RefreshCw, Database, Hash, Loader2, AlertCircle } from 'lucide-react'

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

export default function SchemaPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const [schema, setSchema] = useState<SchemaData | null>(null)
  const [loading, setLoading] = useState(true)
  const [crawling, setCrawling] = useState(false)
  const [crawlStatus, setCrawlStatus] = useState('')
  const [crawlError, setCrawlError] = useState('')
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchSchema = async () => {
    try {
      const resp = await projectApi.getSchema(projectId)
      setSchema(resp.data.schema)
      setError('')
    } catch {
      setError('No schema found. Please crawl first.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchSchema() }, [projectId])

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const handleRecrawl = async () => {
    setCrawling(true)
    setCrawlError('')
    setCrawlStatus('Starting crawl...')
    stopPoll()
    try {
      const resp = await projectApi.triggerCrawl(projectId)
      const jobId: string = resp.data.job_id
      if (!jobId) {
        setCrawlError('No job ID returned from crawler')
        setCrawling(false)
        return
      }

      setCrawlStatus('Crawling database schema...')

      pollRef.current = setInterval(async () => {
        try {
          const statusResp = await projectApi.getCrawlStatus(projectId, jobId)
          const job = statusResp.data
          if (job.status === 'completed') {
            stopPoll()
            setCrawling(false)
            setCrawlStatus('')
            await fetchSchema()
          } else if (job.status === 'failed') {
            stopPoll()
            setCrawling(false)
            setCrawlStatus('')
            setCrawlError(job.error || 'Crawl failed — check backend logs')
          } else {
            setCrawlStatus(`Crawl status: ${job.status}`)
          }
        } catch {
          // crawler unreachable, keep waiting
        }
      }, 3000)

      // Safety timeout: 10 minutes
      setTimeout(() => {
        if (pollRef.current) {
          stopPoll()
          setCrawling(false)
          setCrawlStatus('')
          setCrawlError('Crawl timed out after 10 minutes — check schema_crawler terminal for errors')
        }
      }, 600_000)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setCrawlError(err.response?.data?.detail || 'Failed to start crawl')
      setCrawling(false)
      setCrawlStatus('')
    }
  }

  useEffect(() => () => stopPoll(), [])

  const toggleTable = (name: string) => {
    setExpandedTables((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full">
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
              <Loader2 size={12} className="animate-spin" />
              {crawlStatus}
            </p>
          )}
          {crawlError && (
            <p className="text-sm text-red-600 flex items-center gap-1 mt-0.5">
              <AlertCircle size={12} />
              {crawlError}
            </p>
          )}
        </div>
        <button onClick={handleRecrawl} disabled={crawling} className="btn-secondary flex items-center gap-2 text-sm">
          <RefreshCw size={14} className={crawling ? 'animate-spin' : ''} />
          {crawling ? 'Crawling...' : 'Re-crawl'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {loading && <p className="text-gray-400 text-sm p-4">Loading schema...</p>}
        {error && !loading && (
          <div className="text-center py-12">
            <Database size={40} className="mx-auto text-gray-200 mb-3" />
            <p className="text-gray-500 text-sm">{error}</p>
            {crawlError && (
              <div className="mt-3 max-w-sm mx-auto bg-red-50 border border-red-100 rounded-lg p-3 text-left">
                <p className="text-xs font-semibold text-red-700 mb-1">Last crawl error:</p>
                <p className="text-xs text-red-600 font-mono break-all">{crawlError}</p>
              </div>
            )}
            <button onClick={handleRecrawl} disabled={crawling} className="btn-primary mt-3 text-sm flex items-center gap-2 mx-auto">
              {crawling ? <Loader2 size={14} className="animate-spin" /> : null}
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
                    {isImportant && (
                      <span className="text-xs px-1.5 py-0.5 bg-brand-light text-brand rounded font-medium">Important</span>
                    )}
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
                          {col.is_primary_key ? (
                            <Hash size={12} className="text-amber-500 shrink-0" />
                          ) : (
                            <div className="w-3 h-3 rounded-sm bg-gray-200 shrink-0" />
                          )}
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
    </div>
  )
}
