'use client'
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Plus, Pencil, Trash2, BadgeCheck, Loader2, Database, X, Sparkles,
} from 'lucide-react'
import { projectApi } from '@/lib/api'
import { metricsApi, type MetricDefinition } from '@/lib/metricsApi'

interface ConnItem { id: string; name: string; db_type: string }

const EMPTY: MetricDefinition = {
  name: '', synonyms: [], table: '', expression: 'COUNT(*)',
  date_column: '', filter: '', description: '',
}

export default function MetricsPage() {
  const params = useParams()
  const projectId = params.id as string

  const [connections, setConnections] = useState<ConnItem[]>([])
  const [connId, setConnId] = useState('')
  const [metrics, setMetrics] = useState<MetricDefinition[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<MetricDefinition | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [synonymsText, setSynonymsText] = useState('')

  useEffect(() => {
    projectApi.listConnections(projectId).then(r => {
      const conns: ConnItem[] = r.data?.connections || r.data || []
      setConnections(conns)
      if (conns.length) setConnId(conns[0].id)
      else setLoading(false)
    }).catch(() => setLoading(false))
  }, [projectId])

  const load = useCallback(async () => {
    if (!connId) return
    setLoading(true)
    try {
      const r = await metricsApi.list(projectId, connId)
      setMetrics(r.data.metrics || [])
      setError(null)
    } catch {
      setError('Failed to load metrics')
    } finally {
      setLoading(false)
    }
  }, [projectId, connId])

  useEffect(() => { load() }, [load])

  const openEditor = (m: MetricDefinition | null) => {
    setIsNew(!m)
    setEditing(m ? { ...m } : { ...EMPTY })
    setSynonymsText(m?.synonyms?.join(', ') || '')
  }

  const save = async () => {
    if (!editing || !editing.name.trim() || !editing.table.trim() || !editing.expression.trim()) {
      setError('Name, table, and expression are required'); return
    }
    setSaving(true)
    try {
      await metricsApi.upsert(projectId, connId, {
        ...editing,
        synonyms: synonymsText.split(',').map(s => s.trim()).filter(Boolean),
      })
      setEditing(null)
      setError(null)
      await load()
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err.response?.data?.detail || 'Failed to save metric')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (name: string) => {
    if (!confirm(`Delete metric "${name}"? The AI will stop using this definition.`)) return
    try {
      await metricsApi.remove(projectId, connId, name)
      await load()
    } catch { setError('Failed to delete') }
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Sparkles size={22} className="text-blue-600" /> Metrics
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-xl">
            Canonical definitions the AI uses to answer questions — define a metric once and every
            question about it answers identically, instantly, with zero hallucination.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {connections.length > 0 && (
            <select
              value={connId}
              onChange={e => setConnId(e.target.value)}
              className="text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white text-gray-700"
            >
              {connections.map(c => <option key={c.id} value={c.id}>{c.name} ({c.db_type})</option>)}
            </select>
          )}
          <button
            onClick={() => openEditor(null)}
            disabled={!connId}
            className="flex items-center gap-1.5 px-3.5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-40 transition-colors"
          >
            <Plus size={15} /> Add metric
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 px-4 py-2.5 bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg flex items-center justify-between">
          {error}
          <button onClick={() => setError(null)}><X size={14} /></button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-gray-400">
          <Loader2 size={22} className="animate-spin" />
        </div>
      ) : metrics.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center border-2 border-dashed border-gray-200 rounded-2xl">
          <Database size={32} className="text-gray-300 mb-3" />
          <p className="font-semibold text-gray-600">No metrics defined yet</p>
          <p className="text-sm text-gray-400 mt-1 max-w-md">
            Without definitions, the AI re-derives what &quot;revenue&quot; or &quot;placements&quot; means on every
            question. Pin your top metrics here so answers never drift.
          </p>
          <button
            onClick={() => openEditor(null)}
            disabled={!connId}
            className="mt-4 flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-40"
          >
            <Plus size={15} /> Add your first metric
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {metrics.map(m => (
            <div key={m.name} className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-1.5 min-w-0">
                  <h3 className="font-bold text-gray-900 truncate">{m.name}</h3>
                  <span title="Verified metric — used verbatim by the AI">
                    <BadgeCheck size={16} className="text-green-500 flex-shrink-0" />
                  </span>
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => openEditor(m)} className="p-1.5 text-gray-400 hover:text-blue-600 rounded" title="Edit">
                    <Pencil size={14} />
                  </button>
                  <button onClick={() => remove(m.name)} className="p-1.5 text-gray-400 hover:text-red-500 rounded" title="Delete">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              {m.description && <p className="text-sm text-gray-500 mt-1">{m.description}</p>}
              <div className="mt-3 space-y-1.5 text-xs font-mono">
                <div className="flex gap-2"><span className="text-gray-400 w-16 flex-shrink-0">table</span><span className="text-gray-700 bg-gray-50 px-1.5 rounded truncate">{m.table}</span></div>
                <div className="flex gap-2"><span className="text-gray-400 w-16 flex-shrink-0">expr</span><span className="text-blue-700 bg-blue-50 px-1.5 rounded truncate">{m.expression}</span></div>
                {m.date_column && <div className="flex gap-2"><span className="text-gray-400 w-16 flex-shrink-0">date col</span><span className="text-gray-700 bg-gray-50 px-1.5 rounded">{m.date_column}</span></div>}
                {m.filter && <div className="flex gap-2"><span className="text-gray-400 w-16 flex-shrink-0">filter</span><span className="text-amber-700 bg-amber-50 px-1.5 rounded truncate">{m.filter}</span></div>}
              </div>
              {m.synonyms?.length > 0 && (
                <div className="mt-3 flex gap-1.5 flex-wrap">
                  {m.synonyms.map(s => (
                    <span key={s} className="text-[11px] px-2 py-0.5 bg-gray-100 text-gray-500 rounded-full">{s}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Editor modal */}
      {editing && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={() => setEditing(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-gray-900 text-lg">{isNew ? 'Add metric' : `Edit "${editing.name}"`}</h2>
              <button onClick={() => setEditing(null)} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
            </div>
            <div className="space-y-3">
              {([
                ['Name *', 'name', 'placements', !isNew],
                ['Table *', 'table', 'staging.bullhorn_core_placement', false],
                ['Expression *', 'expression', 'COUNT(*) or SUM(amount)', false],
                ['Date column', 'date_column', 'date_added', false],
                ['Filter (WHERE fragment)', 'filter', "status = 'Approved'", false],
                ['Description', 'description', 'A candidate placed into a job', false],
              ] as [string, keyof MetricDefinition, string, boolean][]).map(([label, key, ph, disabled]) => (
                <div key={key}>
                  <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</label>
                  <input
                    value={(editing[key] as string) || ''}
                    disabled={disabled}
                    onChange={e => setEditing(prev => prev ? { ...prev, [key]: e.target.value } : prev)}
                    placeholder={ph}
                    className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 outline-none disabled:bg-gray-50 disabled:text-gray-400"
                  />
                </div>
              ))}
              <div>
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Synonyms (comma-separated)</label>
                <input
                  value={synonymsText}
                  onChange={e => setSynonymsText(e.target.value)}
                  placeholder="placement, placed candidates"
                  className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 outline-none"
                />
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">Cancel</button>
              <button
                onClick={save}
                disabled={saving}
                className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {saving && <Loader2 size={13} className="animate-spin" />} Save metric
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
