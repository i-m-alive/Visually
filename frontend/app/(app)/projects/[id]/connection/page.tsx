'use client'
import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { projectApi } from '@/lib/api'
import {
  Database, Loader2, AlertCircle, CheckCircle2,
  Save, RefreshCw, Eye, EyeOff, Link2, Trash2,
} from 'lucide-react'

interface Connection {
  id: string
  name: string
  db_type: string
  host: string
  port: number | null
  database_name: string
  username: string
  ssl_enabled: boolean
  is_active: boolean
  last_tested_at: string | null
}

const DB_DEFAULTS: Record<string, number> = {
  postgresql: 5432,
  redshift: 5439,
  mysql: 3306,
}

export default function ConnectionPage() {
  const { id: projectId } = useParams<{ id: string }>()

  const [connections, setConnections] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState({
    name: '', db_type: 'redshift', host: '', port: '',
    database_name: '', username: '', password: '', ssl_enabled: true,
    iam_role_arn: '',
  })
  const [showPw, setShowPw] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string; latency?: number } | null>(null)
  const [testElapsed, setTestElapsed] = useState(0)

  const [deleting, setDeleting] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<{ id: string; msg: string } | null>(null)

  useEffect(() => {
    load()
  }, [projectId])

  const load = async () => {
    setLoading(true); setLoadError('')
    try {
      const resp = await projectApi.listConnections(projectId)
      setConnections(resp.data)
    } catch {
      setLoadError('Failed to load connections.')
    } finally {
      setLoading(false)
    }
  }

  const startEdit = (conn: Connection) => {
    setEditing(conn.id)
    setForm({
      name: conn.name,
      db_type: conn.db_type,
      host: conn.host,
      port: conn.port?.toString() ?? '',
      database_name: conn.database_name,
      username: conn.username,
      password: '',
      ssl_enabled: conn.ssl_enabled,
      iam_role_arn: '',
    })
    setSaveResult(null)
    setTestResult(null)
  }

  const handleSave = async () => {
    if (!editing) return
    setSaving(true); setSaveResult(null)
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        db_type: form.db_type,
        host: form.host.trim(),
        port: form.port ? parseInt(form.port) : DB_DEFAULTS[form.db_type] ?? 5432,
        database_name: form.database_name.trim(),
        username: form.username.trim(),
        ssl_enabled: form.ssl_enabled,
        connection_options: form.iam_role_arn ? { iam_role_arn: form.iam_role_arn } : null,
        password: form.password, // empty string = clear stored password (use IAM auth)
      }
      const resp = await projectApi.updateConnection(projectId, editing, payload)
      setConnections(prev => prev.map(c => c.id === editing ? resp.data : c))
      setSaveResult({ ok: true, msg: 'Connection saved!' })
      setForm(f => ({ ...f, password: '' }))
    } catch (err: any) {
      setSaveResult({ ok: false, msg: err?.response?.data?.detail ?? 'Save failed' })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async (connId: string) => {
    setTesting(connId); setTestResult(null); setTestElapsed(0)
    const timer = setInterval(() => setTestElapsed(s => s + 1), 1000)
    try {
      const resp = await projectApi.testConnection(projectId, connId)
      setTestResult({
        ok: resp.data.success,
        msg: resp.data.message,
        latency: resp.data.latency_ms,
      })
    } catch (err: any) {
      setTestResult({ ok: false, msg: err?.response?.data?.detail ?? 'Test failed' })
    } finally {
      clearInterval(timer)
      setTesting(null)
    }
  }

  const handleDelete = async (conn: Connection) => {
    if (!window.confirm(`Delete connection "${conn.name}"? This can't be undone. Reports using it must be removed or re-pointed first.`)) return
    setDeleting(conn.id); setDeleteError(null)
    try {
      await projectApi.deleteConnection(projectId, conn.id)
      setConnections(prev => prev.filter(c => c.id !== conn.id))
      if (editing === conn.id) setEditing(null)
    } catch (err: any) {
      // 409 = still in use by a report; surface the backend's explanation.
      setDeleteError({ id: conn.id, msg: err?.response?.data?.detail ?? 'Could not delete this connection.' })
    } finally {
      setDeleting(null)
    }
  }

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-100 bg-white flex items-center justify-between flex-shrink-0">
        <div>
          <h2 className="text-lg font-semibold font-display text-gray-900 flex items-center gap-2">
            <Link2 size={18} className="text-blue-500" /> Connection Settings
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">Edit your database connection credentials</p>
        </div>
        <button onClick={load} className="p-2 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors" title="Refresh">
          <RefreshCw size={15} />
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {loading && (
          <div className="flex items-center justify-center h-48">
            <Loader2 className="w-7 h-7 text-blue-500 animate-spin" />
          </div>
        )}

        {loadError && (
          <div className="flex items-center gap-2 text-red-600 text-sm p-4 bg-red-50 rounded-xl border border-red-100">
            <AlertCircle size={15} /> {loadError}
          </div>
        )}

        {!loading && !loadError && connections.length === 0 && (
          <div className="flex flex-col items-center justify-center h-48 gap-3 text-gray-400">
            <Database size={36} />
            <p className="text-sm text-gray-500">No connections configured for this project.</p>
          </div>
        )}

        {!loading && connections.map(conn => (
          <div key={conn.id} className="bg-white border border-gray-100 rounded-2xl overflow-hidden mb-4 shadow-sm">
            {/* Connection header */}
            <div className="px-5 py-4 flex items-center justify-between border-b border-gray-50">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center"
                  style={{ background: 'linear-gradient(135deg, #2563EB18, #7C3AED18)' }}>
                  <Database size={16} className="text-blue-600" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-900">{conn.name}</p>
                  <p className="text-xs text-gray-400 font-mono">
                    {conn.db_type} · {conn.host}:{conn.port} / {conn.database_name}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleTest(conn.id)}
                  disabled={testing === conn.id}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-60 transition-all min-w-[80px] justify-center"
                >
                  {testing === conn.id
                    ? <><Loader2 size={12} className="animate-spin" />{testElapsed > 10 ? `Waking… ${testElapsed}s` : 'Testing…'}</>
                    : <><RefreshCw size={12} />Test</>}
                </button>
                <button
                  onClick={() => editing === conn.id ? setEditing(null) : startEdit(conn)}
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-all ${
                    editing === conn.id
                      ? 'border-gray-300 text-gray-600 hover:bg-gray-50'
                      : 'border-blue-200 text-blue-600 bg-blue-50 hover:bg-blue-100'
                  }`}
                >
                  {editing === conn.id ? 'Cancel' : 'Edit'}
                </button>
                <button
                  onClick={() => handleDelete(conn)}
                  disabled={deleting === conn.id}
                  title="Delete connection"
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-60 transition-all"
                >
                  {deleting === conn.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                </button>
              </div>
            </div>

            {/* Delete error (e.g. 409 — still used by a report) */}
            {deleteError?.id === conn.id && (
              <div className="mx-5 mt-3 flex items-center gap-2 px-3 py-2 rounded-xl text-sm bg-red-50 text-red-700 border border-red-200">
                <AlertCircle size={14} /> {deleteError.msg}
              </div>
            )}

            {/* Test result */}
            {testResult && testing === null && (
              <div className={`mx-5 mt-3 flex items-center gap-2 px-3 py-2 rounded-xl text-sm ${
                testResult.ok ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
              }`}>
                {testResult.ok ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                {testResult.msg}
                {testResult.ok && testResult.latency && (
                  <span className="ml-1 text-xs opacity-60">{Math.round(testResult.latency)}ms</span>
                )}
              </div>
            )}

            {/* Edit form */}
            {editing === conn.id && (
              <div className="p-5 space-y-4 border-t border-gray-50 bg-gray-50/40">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Connection name</label>
                    <input value={form.name} onChange={e => set('name', e.target.value)} className="input-field text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Database type</label>
                    <select value={form.db_type} onChange={e => set('db_type', e.target.value)}
                      className="input-field text-sm">
                      <option value="redshift">Redshift</option>
                      <option value="postgresql">PostgreSQL</option>
                      <option value="mysql">MySQL</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-4">
                  <div className="col-span-2">
                    <label className="block text-xs font-medium text-gray-600 mb-1">Host</label>
                    <input value={form.host} onChange={e => set('host', e.target.value)}
                      className="input-field text-sm font-mono" placeholder="host.region.redshift-serverless.amazonaws.com" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Port</label>
                    <input value={form.port} onChange={e => set('port', e.target.value)}
                      className="input-field text-sm font-mono" placeholder={String(DB_DEFAULTS[form.db_type] ?? 5432)} />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Database name</label>
                  <input value={form.database_name} onChange={e => set('database_name', e.target.value)}
                    className="input-field text-sm font-mono" />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      Username
                      {conn.username && (
                        <span className="ml-2 text-gray-400 font-normal">current: <code className="text-gray-600">{conn.username}</code></span>
                      )}
                    </label>
                    <input value={form.username} onChange={e => set('username', e.target.value)}
                      className="input-field text-sm font-mono" placeholder={conn.username} />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">
                      Password <span className="text-gray-400 font-normal">(clear to use IAM auth)</span>
                    </label>
                    <div className="relative">
                      <input
                        type={showPw ? 'text' : 'password'}
                        value={form.password}
                        onChange={e => set('password', e.target.value)}
                        className="input-field text-sm pr-9"
                        placeholder="Leave blank for IAM / AWS credential auth"
                      />
                      <button type="button" onClick={() => setShowPw(v => !v)}
                        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                        {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>
                </div>

                {form.db_type === 'redshift' && (
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">IAM Role ARN <span className="text-gray-400 font-normal">(optional — leave blank for password auth)</span></label>
                    <input value={form.iam_role_arn} onChange={e => set('iam_role_arn', e.target.value)}
                      className="input-field text-sm font-mono" placeholder="arn:aws:iam::123456789:role/RedshiftRole" />
                  </div>
                )}

                <div className="flex items-center justify-between pt-1">
                  <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer select-none">
                    <input type="checkbox" checked={form.ssl_enabled} onChange={e => set('ssl_enabled', e.target.checked)}
                      className="w-4 h-4 accent-blue-600" />
                    SSL enabled
                  </label>
                  <div className="flex items-center gap-3">
                    {saveResult && (
                      <span className={`flex items-center gap-1.5 text-sm ${saveResult.ok ? 'text-green-600' : 'text-red-600'}`}>
                        {saveResult.ok ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                        {saveResult.msg}
                      </span>
                    )}
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white rounded-xl disabled:opacity-60 transition-all"
                      style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                    >
                      {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                      {saving ? 'Saving…' : 'Save changes'}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
