'use client'
import React, { useEffect, useState } from 'react'
import { X, Database, Loader2, ExternalLink, Plus } from 'lucide-react'
import { projectApi, vlyApi } from '@/lib/api'

interface Conn {
  id: string
  name: string
  db_type: string
  host?: string | null
  database_name?: string | null
}

/** Source-DB fingerprint carried in the .vly (host/port/database/user, no password). */
export interface ConnHint {
  db_type?: string
  host?: string | null
  port?: number | null
  database_name?: string | null
  username?: string | null
}

interface Props {
  projectId: string
  dashboardId: string
  onClose: () => void
  onConnected?: () => void
  /** The report's original DB fingerprint — used to pre-fill the new-connection form. */
  hint?: ConnHint | null
}

const DB_TYPES = ['postgresql', 'mysql', 'redshift', 'mssql', 'snowflake', 'bigquery']
const DEFAULT_PORTS: Record<string, number> = { postgresql: 5432, mysql: 3306, redshift: 5439, mssql: 1433 }

/**
 * Connect an offline / imported canvas to a live database. The report may point at a
 * DIFFERENT database than any already in the project, so this offers two paths:
 *   1) pick an existing project connection, or
 *   2) enter NEW credentials (host/port/database/user/password) — pre-filled from the
 *      .vly's connection hint — which creates the connection and binds it.
 * Binding flips the canvas to live (crawl schema + refresh every widget).
 */
export function ConnectLiveDbModal({ projectId, dashboardId, onClose, onConnected, hint }: Props) {
  const [conns, setConns] = useState<Conn[]>([])
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<'existing' | 'new'>('new')
  const [selected, setSelected] = useState('')
  const [binding, setBinding] = useState(false)
  const [err, setErr] = useState('')

  // New-connection form, pre-filled from the report's hint.
  const dbType0 = (hint?.db_type && DB_TYPES.includes(hint.db_type)) ? hint.db_type : 'postgresql'
  const [form, setForm] = useState({
    name: hint?.database_name ? `${hint.database_name} (live)` : 'Imported report DB',
    db_type: dbType0,
    host: hint?.host ?? '',
    port: String(hint?.port ?? DEFAULT_PORTS[dbType0] ?? 5432),
    database_name: hint?.database_name ?? '',
    username: hint?.username ?? '',
    password: '',
    ssl_enabled: true,
  })
  const set = (k: keyof typeof form, v: string | boolean) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    let cancelled = false
    projectApi.listConnections(projectId)
      .then(r => {
        if (cancelled) return
        const list = (r.data as Conn[]) || []
        setConns(list)
        // Default to "pick existing" only when connections already exist.
        if (list.length > 0) setMode('existing')
      })
      .catch(() => { if (!cancelled) setConns([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectId])

  const bindAndFinish = async (connId: string) => {
    await vlyApi.bindConnection(dashboardId, connId, { crawl: true, refresh: true })
    onConnected?.()
    onClose()
  }

  const connectExisting = async () => {
    if (!selected) return
    setBinding(true); setErr('')
    try {
      await bindAndFinish(selected)
    } catch (e: unknown) {
      setErr((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Could not connect.')
      setBinding(false)
    }
  }

  const connectNew = async () => {
    if (!form.host || !form.database_name || !form.username) {
      setErr('Host, database and username are required.')
      return
    }
    setBinding(true); setErr('')
    try {
      const resp = await projectApi.addConnection(projectId, {
        name: form.name || `${form.database_name} (live)`,
        db_type: form.db_type,
        host: form.host,
        port: form.port ? parseInt(form.port, 10) : null,
        database_name: form.database_name,
        username: form.username,
        password: form.password,
        ssl_enabled: form.ssl_enabled,
      })
      const newId = (resp.data as { id?: string })?.id
      if (!newId) throw new Error('Connection was not created')
      await bindAndFinish(newId)
    } catch (e: unknown) {
      setErr((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Could not create / connect to the database.')
      setBinding(false)
    }
  }

  const inputStyle = 'w-full text-xs border border-gray-200 rounded-lg px-2.5 py-2 bg-white text-gray-700 outline-none focus:border-blue-400'

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget && !binding) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #2563EB22, #7C3AED22)' }}>
              <Database size={16} style={{ color: '#2563EB' }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Connect to live database</h2>
              <p className="text-xs text-gray-400">Switch this offline canvas to real-time data</p>
            </div>
          </div>
          {!binding && (
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="p-5 space-y-4 overflow-y-auto">
          {/* Mode switch */}
          {conns.length > 0 && (
            <div className="flex gap-1.5 p-1 bg-gray-100 rounded-lg">
              <button
                onClick={() => setMode('existing')}
                className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${mode === 'existing' ? 'bg-white shadow text-gray-800' : 'text-gray-500'}`}
              >Use existing</button>
              <button
                onClick={() => setMode('new')}
                className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${mode === 'new' ? 'bg-white shadow text-gray-800' : 'text-gray-500'}`}
              >New database</button>
            </div>
          )}

          {hint?.host && mode === 'new' && (
            <p className="text-[11px] text-gray-500 bg-blue-50/50 border border-blue-100 rounded-lg px-3 py-2">
              Pre-filled from the report's source database (<strong>{hint.host}</strong>
              {hint.database_name ? <> · {hint.database_name}</> : null}). Enter the password to connect.
            </p>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-6 text-gray-400"><Loader2 size={18} className="animate-spin" /></div>
          ) : mode === 'existing' ? (
            <>
              <select value={selected} onChange={e => setSelected(e.target.value)} disabled={binding} className={inputStyle}>
                <option value="">Select a database connection…</option>
                {conns.map(c => (
                  <option key={c.id} value={c.id}>{c.name} — {c.db_type}{c.database_name ? ` · ${c.database_name}` : ''}</option>
                ))}
              </select>
              <button
                onClick={connectExisting}
                disabled={!selected || binding}
                className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-40 transition-opacity hover:opacity-90"
                style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
              >
                {binding ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />}
                {binding ? 'Connecting…' : 'Connect & load live data'}
              </button>
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2.5">
                <div className="col-span-2">
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Connection name</label>
                  <input value={form.name} onChange={e => set('name', e.target.value)} className={inputStyle} placeholder="My database" />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Type</label>
                  <select value={form.db_type} onChange={e => { set('db_type', e.target.value); if (DEFAULT_PORTS[e.target.value]) set('port', String(DEFAULT_PORTS[e.target.value])) }} className={inputStyle}>
                    {DB_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Port</label>
                  <input value={form.port} onChange={e => set('port', e.target.value)} className={inputStyle} placeholder="5432" />
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Host / URL</label>
                  <input value={form.host} onChange={e => set('host', e.target.value)} className={inputStyle} placeholder="db.example.com" />
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Database</label>
                  <input value={form.database_name} onChange={e => set('database_name', e.target.value)} className={inputStyle} placeholder="analytics" />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Username</label>
                  <input value={form.username} onChange={e => set('username', e.target.value)} className={inputStyle} placeholder="user" />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Password</label>
                  <input type="password" value={form.password} onChange={e => set('password', e.target.value)} className={inputStyle} placeholder="••••••••" />
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                <input type="checkbox" checked={form.ssl_enabled} onChange={e => set('ssl_enabled', e.target.checked)} className="w-3.5 h-3.5 accent-blue-600" />
                Use SSL
              </label>
              <button
                onClick={connectNew}
                disabled={binding}
                className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50 transition-opacity hover:opacity-90"
                style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
              >
                {binding ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {binding ? 'Connecting & loading live data…' : 'Connect & load live data'}
              </button>
            </>
          )}

          {err && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-200">{err}</p>}

          <p className="text-[11px] text-gray-400 flex items-center gap-1.5">
            <ExternalLink size={11} /> Connecting crawls the schema and re-runs every widget on live data.
          </p>
        </div>
      </div>
    </div>
  )
}
