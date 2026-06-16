'use client'
import { useState } from 'react'
import { X, Database, Loader2, AlertCircle } from 'lucide-react'

type DbType = 'postgresql' | 'mysql' | 'redshift'

interface ConnectionDetails {
  host: string
  port: string
  database_name: string
  username: string
  password: string
  db_type: DbType
  ssl_enabled: boolean
  iam_role_arn?: string
}

const DEFAULT_PORT: Record<DbType, string> = {
  postgresql: '5432',
  mysql: '3306',
  redshift: '5439',
}

interface Props {
  fileName: string
  connectionHint?: { host?: string; database_name?: string; db_type?: string; username?: string; port?: number }
  /** Async — should create/verify the connection and proceed. Throw to surface an error here. */
  onConnect: (details: ConnectionDetails) => Promise<void>
  onClose: () => void
}

export function ConnectionPromptModal({ fileName, connectionHint, onConnect, onClose }: Props) {
  const initialType: DbType =
    connectionHint?.db_type === 'mysql' ? 'mysql'
    : connectionHint?.db_type === 'redshift' ? 'redshift'
    : 'postgresql'

  const [form, setForm] = useState<ConnectionDetails>({
    host: connectionHint?.host ?? '',
    port: connectionHint?.port ? String(connectionHint.port) : DEFAULT_PORT[initialType],
    database_name: connectionHint?.database_name ?? '',
    username: connectionHint?.username ?? '',
    password: '',
    db_type: initialType,
    ssl_enabled: initialType === 'redshift',   // Redshift requires SSL
    iam_role_arn: '',
  })

  // Switching DB type resets the port to that engine's default (unless the user
  // typed a custom one) and turns SSL on for Redshift.
  const changeDbType = (next: DbType) => {
    setForm(p => ({
      ...p,
      db_type: next,
      port: (!p.port || Object.values(DEFAULT_PORT).includes(p.port)) ? DEFAULT_PORT[next] : p.port,
      ssl_enabled: next === 'redshift' ? true : p.ssl_enabled,
    }))
  }
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const field = (
    label: string,
    key: 'host' | 'port' | 'database_name' | 'username' | 'password' | 'iam_role_arn',
    type: string = 'text',
    placeholder: string = '',
  ) => (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        value={form[key] ?? ''}
        onChange={e => setForm(p => ({ ...p, [key]: e.target.value }))}
        placeholder={placeholder}
        className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
      />
    </div>
  )

  const handleSubmit = async () => {
    if (!form.host || !form.database_name || !form.username) {
      setError('Host, database name and username are required.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await onConnect(form)
      // success → parent closes the modal
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg || 'Could not connect to the database. Check the host, port and credentials.')
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      {/* NOTE: intentionally NO onClick={onClose} on the backdrop. This is a
          credential-entry form — a stray click outside (or a tall modal that
          overflows so the "outside" sits under your cursor) must NOT dismiss it
          mid-typing. Close only via the explicit X / Cancel buttons. */}
      <div
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md p-6 max-h-[90vh] overflow-y-auto"
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
              onChange={e => changeDbType(e.target.value as DbType)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20"
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
              <option value="redshift">Amazon Redshift</option>
            </select>
          </div>
          {field('Host', 'host', 'text', form.db_type === 'redshift' ? 'my-cluster.xxxx.region.redshift.amazonaws.com' : 'localhost')}
          <div className="grid grid-cols-2 gap-3">
            {field('Port', 'port', 'text', DEFAULT_PORT[form.db_type])}
            {field('Database name', 'database_name', 'text', form.db_type === 'redshift' ? 'dev' : 'mydb')}
          </div>
          {field('Username', 'username', 'text', form.db_type === 'redshift' ? 'awsuser' : 'postgres')}
          {field('Password', 'password', 'password', form.db_type === 'redshift' ? 'leave blank to use IAM role' : '••••••••')}

          {form.db_type === 'redshift' && (
            <>
              {field('IAM Role ARN (optional)', 'iam_role_arn', 'text', 'arn:aws:iam::123456789012:role/MyRedshiftRole')}
              <label className="flex items-center gap-2 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={form.ssl_enabled}
                  onChange={e => setForm(p => ({ ...p, ssl_enabled: e.target.checked }))}
                  className="w-3.5 h-3.5 rounded accent-blue-600"
                />
                Use SSL (recommended for Redshift)
              </label>
            </>
          )}
        </div>

        <div className="flex gap-3 mt-5">
          <button
            onClick={onClose}
            disabled={submitting}
            className="flex-1 px-4 py-2.5 text-sm font-medium text-gray-600 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors disabled:opacity-40"
          >
            Cancel
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
