'use client'
import React, { useState, useRef, useCallback } from 'react'
import { X, Upload, FileArchive, CheckCircle2, Loader2, AlertCircle, ExternalLink, Database } from 'lucide-react'
import { vlyApi, projectApi } from '@/lib/api'
import { useRouter } from 'next/navigation'

interface Conn {
  id: string
  name: string
  db_type: string
  host?: string | null
  database_name?: string | null
}

interface Props {
  projectId: string
  /** If provided, auto-link widgets to this DB connection on import */
  connectionId?: string
  onClose: () => void
  onImported?: (dashboardId: string) => void
}

type State = 'idle' | 'dragging' | 'importing' | 'binding' | 'done' | 'error'

export function VlyImportModal({ projectId, connectionId, onClose, onImported }: Props) {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const [state, setState] = useState<State>('idle')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<{
    dashboard_id: string
    name: string
    widget_count: number
    connection_linked: boolean
    original_name: string | null
    data_mode?: 'live' | 'offline' | 'cached'
    has_table_data?: boolean
    table_count?: number
    connection_test?: { ok: boolean; message: string } | null
  } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [connections, setConnections] = useState<Conn[]>([])
  const [selectedConn, setSelectedConn] = useState('')
  const [bindMsg, setBindMsg] = useState('')
  const [retrying, setRetrying] = useState(false)

  const handleFile = useCallback(async (f: File) => {
    if (!f.name.endsWith('.vly') && !f.name.endsWith('.zip')) {
      setErrorMsg('Please select a .vly file exported from Visually.')
      setState('error')
      return
    }
    setFile(f)
    setState('importing')
    setErrorMsg('')
    try {
      const resp = await vlyApi.importVly(f, projectId, connectionId)
      setResult(resp.data)
      setState('done')
      // If the import didn't auto-link a connection, load the project's connections
      // so the user can bind one and turn cached data into live data.
      if (!resp.data.connection_linked) {
        try {
          const cr = await projectApi.listConnections(projectId)
          setConnections((cr.data as Conn[]) || [])
        } catch { /* non-fatal — user can still open with cached data */ }
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErrorMsg(msg || 'Import failed — the file may be corrupted or from an incompatible version.')
      setState('error')
    }
  }, [projectId, connectionId])

  const bindLiveConnection = useCallback(async () => {
    if (!result || !selectedConn) return
    setState('binding')
    setBindMsg('')
    try {
      await vlyApi.bindConnection(result.dashboard_id, selectedConn, { crawl: true, refresh: true })
      setResult(r => (r ? { ...r, connection_linked: true, data_mode: 'live', connection_test: { ok: true, message: 'Connected' } } : r))
      setState('done')
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setBindMsg(msg || 'Could not connect to live data. You can still open the canvas with cached data.')
      setState('done')
    }
  }, [result, selectedConn])

  // Re-probe a configured-but-unreachable live connection (complaint: no retry today).
  const retryTest = useCallback(async () => {
    if (!result) return
    setRetrying(true)
    try {
      const r = await vlyApi.testConnection(result.dashboard_id)
      setResult(prev => (prev ? { ...prev, connection_test: { ok: r.data.ok, message: r.data.message } } : prev))
    } catch {
      setResult(prev => (prev ? { ...prev, connection_test: { ok: false, message: 'Still unreachable' } } : prev))
    } finally {
      setRetrying(false)
    }
  }, [result])

  // Lock the canvas into offline mode (query the bundled tables, no DB) and open it.
  const continueOffline = useCallback(async () => {
    if (!result) return
    try { await vlyApi.setDataMode(result.dashboard_id, 'offline') } catch { /* non-fatal */ }
    if (onImported) onImported(result.dashboard_id)
    else router.push(`/projects/${projectId}/canvas/${result.dashboard_id}`)
    onClose()
  }, [result, onImported, router, projectId, onClose])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setState('idle')
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }, [handleFile])

  const openCanvas = () => {
    if (!result) return
    if (onImported) onImported(result.dashboard_id)
    else router.push(`/projects/${projectId}/canvas/${result.dashboard_id}`)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget && state !== 'importing') onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #2563EB22, #7C3AED22)' }}>
              <FileArchive size={16} style={{ color: '#6366F1' }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Import .vly Canvas</h2>
              <p className="text-xs text-gray-400">Restore a canvas from a Visually export file</p>
            </div>
          </div>
          {state !== 'importing' && (
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="p-5 overflow-y-auto">
          {state === 'idle' || state === 'dragging' ? (
            <div
              onDragOver={e => { e.preventDefault(); setState('dragging') }}
              onDragLeave={() => setState('idle')}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className="border-2 border-dashed rounded-xl flex flex-col items-center justify-center py-10 cursor-pointer transition-all"
              style={{
                borderColor: state === 'dragging' ? '#6366F1' : '#E5E7EB',
                background: state === 'dragging' ? '#F5F3FF' : '#FAFAFA',
              }}
            >
              <Upload size={28} style={{ color: state === 'dragging' ? '#6366F1' : '#D1D5DB', marginBottom: 10 }} />
              <p className="text-sm font-medium text-gray-600">
                {state === 'dragging' ? 'Drop to import' : 'Drop your .vly file here'}
              </p>
              <p className="text-xs text-gray-400 mt-1">or click to browse</p>
              <input ref={inputRef} type="file" accept=".vly,.zip" className="hidden" onChange={onFileChange} />
            </div>
          ) : state === 'importing' || state === 'binding' ? (
            <div className="flex flex-col items-center justify-center py-10 gap-4">
              <Loader2 size={28} className="animate-spin text-blue-500" />
              <div className="text-center">
                <p className="text-sm font-medium text-gray-700">
                  {state === 'binding' ? 'Connecting to live data…' : 'Importing canvas…'}
                </p>
                <p className="text-xs text-gray-400 mt-1">
                  {state === 'binding' ? 'Crawling schema & refreshing all widgets' : file?.name}
                </p>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-purple-500 animate-pulse" style={{ width: '60%' }} />
              </div>
            </div>
          ) : state === 'done' && result ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3 p-4 bg-green-50 rounded-xl border border-green-200">
                <CheckCircle2 size={20} className="text-green-500 flex-shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-green-800">Canvas imported successfully</p>
                  <p className="text-xs text-green-600 mt-0.5">{result.widget_count} widget{result.widget_count !== 1 ? 's' : ''} restored</p>
                </div>
              </div>

              <div className="border border-gray-100 rounded-xl p-4 space-y-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">New canvas name</span>
                  <span className="font-medium text-gray-800">{result.name}</span>
                </div>
                {result.original_name && result.original_name !== result.name && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">Original name</span>
                    <span className="text-gray-600">{result.original_name}</span>
                  </div>
                )}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">Widgets</span>
                  <span className="font-medium text-gray-800">{result.widget_count}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">Data source</span>
                  <span className={`font-medium ${
                    result.connection_linked && result.connection_test?.ok !== false ? 'text-green-600'
                    : result.has_table_data ? 'text-indigo-600'
                    : 'text-amber-600'}`}>
                    {result.connection_linked && result.connection_test?.ok !== false
                      ? '✓ Live database connected'
                      : result.connection_linked
                        ? '✗ Database unreachable'
                        : result.has_table_data
                          ? `● Offline — ${result.table_count ?? 0} bundled table${(result.table_count ?? 0) !== 1 ? 's' : ''}`
                          : '⚠ Not connected — cached snapshot only'}
                  </span>
                </div>
                {result.connection_linked && result.connection_test && !result.connection_test.ok && (
                  <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2 border border-amber-200">
                    {result.connection_test.message}
                  </p>
                )}
              </div>

              {/* ── Database unreachable → Retry / Continue offline ── */}
              {result.connection_linked && result.connection_test && !result.connection_test.ok && (
                <div className="border border-amber-100 bg-amber-50/40 rounded-xl p-4 space-y-3">
                  <p className="text-xs text-gray-600 leading-relaxed">
                    The linked database didn’t respond. Retry the connection, or
                    {result.has_table_data ? ' open the canvas offline using the bundled table data.' : ' open with the cached snapshot.'}
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={retryTest}
                      disabled={retrying}
                      className="flex-1 py-2 rounded-lg text-xs font-semibold border border-gray-200 text-gray-700 hover:bg-white flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                      {retrying ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />} Retry connection
                    </button>
                    {result.has_table_data && (
                      <button
                        onClick={continueOffline}
                        className="flex-1 py-2 rounded-lg text-xs font-semibold text-white flex items-center justify-center gap-2 hover:opacity-90"
                        style={{ background: 'linear-gradient(135deg, #6366F1, #7C3AED)' }}
                      >
                        Continue offline
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* ── Offline canvas (bundled tables, no live DB) ── */}
              {!result.connection_linked && result.has_table_data && (
                <div className="border border-indigo-100 bg-indigo-50/40 rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <Database size={14} className="text-indigo-500" />
                    <p className="text-xs font-semibold text-gray-800">Offline mode available</p>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed">
                    This archive bundles {result.table_count ?? 0} full table{(result.table_count ?? 0) !== 1 ? 's' : ''}.
                    You can open it and run the report + AI copilot entirely offline — no database connection needed.
                    {connections.length > 0 ? ' Or connect a live database below.' : ''}
                  </p>
                  <button
                    onClick={continueOffline}
                    className="w-full py-2 rounded-lg text-xs font-semibold text-white flex items-center justify-center gap-2 hover:opacity-90"
                    style={{ background: 'linear-gradient(135deg, #6366F1, #7C3AED)' }}
                  >
                    <Database size={12} /> Open offline with bundled data
                  </button>
                </div>
              )}

              {/* ── Connect-to-live-data step (shown only when not auto-linked) ── */}
              {!result.connection_linked && (
                <div className="border border-blue-100 bg-blue-50/40 rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <Database size={14} className="text-blue-500" />
                    <p className="text-xs font-semibold text-gray-800">Connect to live data</p>
                  </div>
                  <p className="text-xs text-gray-500 leading-relaxed">
                    Link a database connection to refresh every widget with live results and let
                    the AI copilot query the real data.
                  </p>

                  {connections.length > 0 ? (
                    <>
                      <select
                        value={selectedConn}
                        onChange={e => setSelectedConn(e.target.value)}
                        className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-2 bg-white text-gray-700 outline-none focus:border-blue-400"
                      >
                        <option value="">Select a database connection…</option>
                        {connections.map(c => (
                          <option key={c.id} value={c.id}>
                            {c.name} — {c.db_type}{c.database_name ? ` · ${c.database_name}` : ''}
                          </option>
                        ))}
                      </select>
                      <button
                        onClick={bindLiveConnection}
                        disabled={!selectedConn}
                        className="w-full py-2 rounded-lg text-xs font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-40 transition-opacity hover:opacity-90"
                        style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                      >
                        <Database size={12} /> Connect &amp; load live data
                      </button>
                    </>
                  ) : (
                    <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2 border border-amber-200">
                      No database connections in this project yet. Add one in project settings, then
                      use “Connect to live data” — or continue with cached data for now.
                    </p>
                  )}

                  {bindMsg && (
                    <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-200">{bindMsg}</p>
                  )}
                </div>
              )}

              <button
                onClick={openCanvas}
                className="w-full py-2.5 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-opacity hover:opacity-90"
                style={result.connection_linked
                  ? { background: 'linear-gradient(135deg, #2563EB, #7C3AED)', color: 'white' }
                  : { background: '#F3F4F6', color: '#4B5563' }}
              >
                {result.connection_linked ? 'Open Canvas' : 'Open with cached data'} <ExternalLink size={13} />
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center py-8 gap-4">
              <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
                <AlertCircle size={20} className="text-red-500" />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-gray-700">Import failed</p>
                <p className="text-xs text-gray-500 mt-1 max-w-xs">{errorMsg}</p>
              </div>
              <button
                onClick={() => { setState('idle'); setFile(null); setErrorMsg('') }}
                className="px-4 py-2 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                Try again
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
