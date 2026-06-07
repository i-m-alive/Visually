'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { projectApi } from '@/lib/api'
import { CheckCircle, XCircle, Loader2 } from 'lucide-react'

type DbType = 'postgresql' | 'mysql' | 'redshift'
type Step = 'project' | 'connection' | 'crawling'

const DB_OPTIONS: { value: DbType; label: string; defaultPort: number }[] = [
  { value: 'postgresql', label: 'PostgreSQL', defaultPort: 5432 },
  { value: 'mysql', label: 'MySQL', defaultPort: 3306 },
  { value: 'redshift', label: 'Amazon Redshift', defaultPort: 5439 },
]

export default function NewProjectPage() {
  const router = useRouter()
  const [step, setStep] = useState<Step>('project')
  const [projectId, setProjectId] = useState('')
  const [connId, setConnId] = useState('')
  const [crawlJobId, setCrawlJobId] = useState('')

  // Project form
  const [projName, setProjName] = useState('')
  const [projDesc, setProjDesc] = useState('')

  // Connection form
  const [dbType, setDbType] = useState<DbType>('postgresql')
  const [connName, setConnName] = useState('My Database')
  const [host, setHost] = useState('localhost')
  const [port, setPort] = useState('5432')
  const [dbName, setDbName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [iamRoleArn, setIamRoleArn] = useState('')

  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleDbTypeChange = (value: DbType) => {
    setDbType(value)
    const opt = DB_OPTIONS.find(o => o.value === value)
    if (opt) setPort(String(opt.defaultPort))
  }

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSaving(true)
    try {
      const resp = await projectApi.create({ name: projName, description: projDesc || undefined })
      setProjectId(resp.data.id)
      setStep('connection')
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setError(e.response?.data?.detail || 'Failed to create project')
    } finally {
      setSaving(false)
    }
  }

  const handleTestConnection = async () => {
    if (!connId) {
      await handleSaveConnection(true)
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const resp = await projectApi.testConnection(projectId, connId)
      setTestResult(resp.data)
    } catch {
      setTestResult({ success: false, message: 'Connection test failed' })
    } finally {
      setTesting(false)
    }
  }

  const handleSaveConnection = async (testOnly = false) => {
    setError('')
    setSaving(true)
    try {
      const connectionOptions = dbType === 'redshift' && iamRoleArn
        ? { iam_role_arn: iamRoleArn }
        : undefined

      const resp = await projectApi.addConnection(projectId, {
        name: connName,
        db_type: dbType,
        host,
        port: parseInt(port),
        database_name: dbName,
        username,
        password,
        ssl_enabled: dbType === 'redshift',
        connection_options: connectionOptions,
      })
      setConnId(resp.data.id)

      if (testOnly) {
        const testResp = await projectApi.testConnection(projectId, resp.data.id)
        setTestResult(testResp.data)
      } else {
        setStep('crawling')
        const crawlResp = await projectApi.triggerCrawl(projectId)
        setCrawlJobId(crawlResp.data.job_id)

        const poll = setInterval(async () => {
          try {
            await projectApi.getSchema(projectId)
            clearInterval(poll)
            router.push(`/projects/${projectId}/query`)
          } catch {}
        }, 2000)

        setTimeout(() => {
          clearInterval(poll)
          router.push(`/projects/${projectId}/query`)
        }, 120000)
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setError(e.response?.data?.detail || 'Failed to save connection')
    } finally {
      setSaving(false)
    }
  }

  if (step === 'crawling') {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <Loader2 size={40} className="mx-auto text-brand animate-spin mb-4" />
          <h3 className="text-lg font-semibold font-display text-gray-900">Crawling schema...</h3>
          <p className="text-gray-500 text-sm mt-1">Analyzing your database structure with AI</p>
          <p className="text-gray-400 text-xs mt-2">Job: {crawlJobId}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-lg mx-auto">
        <div className="flex items-center gap-2 mb-6">
          {['project', 'connection'].map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                step === s ? 'bg-brand text-white' : step === 'connection' && s === 'project' ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-500'
              }`}>{i + 1}</div>
              <span className="text-sm text-gray-600 capitalize">{s}</span>
              {i === 0 && <span className="text-gray-300">→</span>}
            </div>
          ))}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-100 text-red-700 text-sm p-3 rounded-lg mb-4">{error}</div>
        )}

        {step === 'project' && (
          <form onSubmit={handleCreateProject} className="card p-6 space-y-4">
            <h2 className="text-xl font-semibold font-display">Name your project</h2>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Project name</label>
              <input value={projName} onChange={(e) => setProjName(e.target.value)}
                className="input-field" placeholder="My Analytics Project" required />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description (optional)</label>
              <input value={projDesc} onChange={(e) => setProjDesc(e.target.value)}
                className="input-field" placeholder="Sales data, customer metrics..." />
            </div>
            <button type="submit" disabled={saving} className="btn-primary w-full">
              {saving ? 'Creating...' : 'Continue →'}
            </button>
          </form>
        )}

        {step === 'connection' && (
          <div className="card p-6 space-y-4">
            <h2 className="text-xl font-semibold font-display">Connect your database</h2>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Database type</label>
              <select value={dbType} onChange={(e) => handleDbTypeChange(e.target.value as DbType)}
                className="input-field">
                {DB_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">Host</label>
                <input value={host} onChange={(e) => setHost(e.target.value)}
                  className="input-field" placeholder={dbType === 'redshift' ? 'cluster.region.redshift.amazonaws.com' : 'localhost'} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Port</label>
                <input value={port} onChange={(e) => setPort(e.target.value)}
                  className="input-field" type="number" />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Database name</label>
              <input value={dbName} onChange={(e) => setDbName(e.target.value)}
                className="input-field" placeholder="mydb" />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
                <input value={username} onChange={(e) => setUsername(e.target.value)}
                  className="input-field" placeholder={dbType === 'redshift' ? 'awsuser' : 'postgres'} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  className="input-field" placeholder="••••••••" />
              </div>
            </div>

            {dbType === 'redshift' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  IAM Role ARN <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <input value={iamRoleArn} onChange={(e) => setIamRoleArn(e.target.value)}
                  className="input-field" placeholder="arn:aws:iam::123456789012:role/RedshiftRole" />
                <p className="text-xs text-gray-400 mt-1">For IAM-based authentication instead of password</p>
              </div>
            )}

            {testResult && (
              <div className={`flex items-center gap-2 text-sm p-3 rounded-lg ${testResult.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                {testResult.success ? <CheckCircle size={16} /> : <XCircle size={16} />}
                {testResult.message}
              </div>
            )}

            <div className="flex gap-3">
              <button onClick={handleTestConnection} disabled={testing || saving} className="btn-secondary flex-1">
                {testing ? 'Testing...' : 'Test connection'}
              </button>
              <button onClick={() => handleSaveConnection(false)} disabled={saving || testing} className="btn-primary flex-1">
                {saving ? 'Saving...' : 'Save & crawl →'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
