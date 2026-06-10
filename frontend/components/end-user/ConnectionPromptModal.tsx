'use client'
import { useState } from 'react'
import { X, Database, Loader2, AlertCircle } from 'lucide-react'

interface ConnectionDetails {
  host: string
  port: string
  database_name: string
  username: string
  password: string
  db_type: 'postgresql' | 'mysql'
}

interface Props {
  fileName: string
  connectionHint?: { host?: string; database_name?: string; db_type?: string; username?: string; port?: number }
  onConnect: (details: ConnectionDetails) => void
  onSkip: () => void
  onClose: () => void
}

export function ConnectionPromptModal({ fileName, connectionHint, onConnect, onSkip, onClose }: Props) {
  const [form, setForm] = useState<ConnectionDetails>({
    host: connectionHint?.host ?? '',
    port: connectionHint?.port ? String(connectionHint.port) : '5432',
    database_name: connectionHint?.database_name ?? '',
    username: connectionHint?.username ?? '',
    password: '',
    db_type: (connectionHint?.db_type === 'mysql' ? 'mysql' : 'postgresql'),
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const field = (
    label: string,
    key: keyof ConnectionDetails,
    type: string = 'text',
    placeholder: string = '',
  ) => (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        value={form[key]}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))}
        placeholder={placeholder}
        className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
      />
    </div>
  )

  const handleSubmit = () => {
    if (!form.host || !form.database_name || !form.username) {
      setError('Host, database name and username are required.')
      return
    }
    setError(null)
    setSubmitting(true)
    onConnect(form)
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-blue-50 flex items-center justify-center flex-shrink-0">
              <Database size={16} className="text-blue-600" />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900">Connect a Database</p>
              <p className="text-xs text-gray-500 mt-0.5">
                <span className="font-medium text-gray-700">{fileName}</span> needs a database connection for live data.
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50">
            <X size={15} />
          </button>
        </div>

        {connectionHint?.host && (
          <div className="mb-4 px-3 py-2 bg-blue-50 rounded-lg border border-blue-100 text-xs text-blue-700">
            This report was built on <span className="font-mono font-semibold">{connectionHint.host}/{connectionHint.database_name}</span>.
            Enter credentials to connect to the same or a compatible database.
          </div>
        )}

        {error && (
          <div className="mb-4 flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-100 rounded-lg text-xs text-red-600">
            <AlertCircle size={13} className="flex-shrink-0" />
            {error}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Database type</label>
            <select
              value={form.db_type}
              onChange={e => setForm(p => ({ ...p, db_type: e.target.value as any }))}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </select>
          </div>
          {field('Host', 'host', 'text', 'localhost')}
          <div className="grid grid-cols-2 gap-3">
            {field('Port', 'port', 'text', '5432')}
            {field('Database name', 'database_name', 'text', 'mydb')}
          </div>
          {field('Username', 'username', 'text', 'postgres')}
          {field('Password', 'password', 'password', '••••••••')}
        </div>

        <div className="flex gap-3 mt-5">
          <button
            onClick={onSkip}
            className="flex-1 px-4 py-2.5 text-sm font-medium text-gray-600 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
          >
            Import without DB
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex-1 px-4 py-2.5 text-sm font-semibold text-white rounded-xl transition-colors disabled:opacity-40 flex items-center justify-center gap-2"
            style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
          >
            {submitting ? <><Loader2 size={14} className="animate-spin" /> Connecting…</> : 'Connect & Import'}
          </button>
        </div>
      </div>
    </div>
  )
}
