'use client'
import { useState, useEffect } from 'react'
import { X, Download, FileText, Image, Globe, Loader2, CheckCircle2, AlertCircle, ExternalLink } from 'lucide-react'
import { exportApi } from '@/lib/api'

const THEMES = [
  { id: 'frost',    label: 'Frost',    description: 'Clean white with blue accents',        swatch: '#2563EB' },
  { id: 'slate',    label: 'Slate',    description: 'Dark mode with sky-blue highlights',    swatch: '#38bdf8' },
  { id: 'sage',     label: 'Sage',     description: 'Nature-inspired green palette',         swatch: '#16A34A' },
  { id: 'ember',    label: 'Ember',    description: 'Warm orange for executive reports',     swatch: '#E8520A' },
  { id: 'obsidian', label: 'Obsidian', description: 'Deep dark with teal highlights',        swatch: '#00E5C3' },
]

const EXPORT_TYPES = [
  {
    id: 'html',
    label: 'Interactive HTML',
    icon: Globe,
    description: 'Self-contained file with live charts, filtering, and AI chat. Works offline.',
  },
  {
    id: 'pdf',
    label: 'PDF Report',
    icon: FileText,
    description: 'A4 landscape PDF rendered by headless Chrome. Best for printing.',
  },
  {
    id: 'png',
    label: 'PNG Snapshot',
    icon: Image,
    description: 'High-DPI 2× full-page screenshot. Best for presentations.',
  },
] as const

type ExportType = 'html' | 'pdf' | 'png'

interface Props {
  dashboardId: string
  projectId: string
  dashboardName: string
  onClose: () => void
}

type Phase = 'config' | 'generating' | 'done' | 'error'

export function ExportModal({ dashboardId, projectId, dashboardName, onClose }: Props) {
  const [exportType, setExportType] = useState<ExportType>('html')
  const [theme, setTheme] = useState('frost')
  const [includeChat, setIncludeChat] = useState(true)
  const [tokenExpiry, setTokenExpiry] = useState(30)

  const [phase, setPhase] = useState<Phase>('config')
  const [jobId, setJobId] = useState<string | null>(null)
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [pollCount, setPollCount] = useState(0)

  // Poll for job completion
  useEffect(() => {
    if (phase !== 'generating' || !jobId) return
    const interval = setInterval(async () => {
      try {
        const resp = await exportApi.getJob(jobId)
        const data = resp.data
        if (data.pipeline_status === 'completed' && data.export?.status === 'completed') {
          setDownloadUrl(exportApi.downloadUrl(jobId))
          setPhase('done')
          clearInterval(interval)
        } else if (data.pipeline_status === 'failed' || data.export?.status === 'failed') {
          setErrorMsg(data.export?.error_message || 'Export failed')
          setPhase('error')
          clearInterval(interval)
        }
        setPollCount((c) => c + 1)
      } catch {
        // keep polling
      }
    }, 2500)
    return () => clearInterval(interval)
  }, [phase, jobId])

  const handleStartExport = async () => {
    setPhase('generating')
    setErrorMsg(null)
    try {
      const resp = await exportApi.trigger({
        dashboard_id: dashboardId,
        project_id: projectId,
        export_type: exportType,
        theme,
        include_chat: exportType === 'html' ? includeChat : false,
        token_expiry_days: tokenExpiry,
      })
      setJobId(resp.data.pipeline_job_id || resp.data.job_id)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErrorMsg(msg || 'Failed to start export')
      setPhase('error')
    }
  }

  const selected = EXPORT_TYPES.find((t) => t.id === exportType)!

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-lg font-700 text-gray-900">Export Dashboard</h2>
            <p className="text-sm text-gray-500 mt-0.5 truncate max-w-xs">{dashboardName}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X size={20} />
          </button>
        </div>

        {phase === 'config' && (
          <div className="px-6 py-5 space-y-6">
            {/* Export type */}
            <div>
              <label className="text-xs font-600 text-gray-500 uppercase tracking-wider mb-2 block">
                Format
              </label>
              <div className="space-y-2">
                {EXPORT_TYPES.map((et) => {
                  const Icon = et.icon
                  return (
                    <button
                      key={et.id}
                      onClick={() => setExportType(et.id)}
                      className={`w-full flex items-start gap-3 p-3 rounded-xl border-2 text-left transition-all ${
                        exportType === et.id
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-gray-200 hover:border-gray-300 bg-white'
                      }`}
                    >
                      <Icon
                        size={18}
                        className={`mt-0.5 flex-shrink-0 ${exportType === et.id ? 'text-blue-600' : 'text-gray-400'}`}
                      />
                      <div>
                        <div className={`text-sm font-600 ${exportType === et.id ? 'text-blue-700' : 'text-gray-800'}`}>
                          {et.label}
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5">{et.description}</div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Theme picker */}
            <div>
              <label className="text-xs font-600 text-gray-500 uppercase tracking-wider mb-2 block">
                Theme
              </label>
              <div className="grid grid-cols-5 gap-2">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setTheme(t.id)}
                    title={`${t.label} — ${t.description}`}
                    className={`flex flex-col items-center gap-1.5 p-2 rounded-lg border-2 transition-all ${
                      theme === t.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div
                      className="w-6 h-6 rounded-full ring-2 ring-white ring-offset-1"
                      style={{ background: t.swatch }}
                    />
                    <span className={`text-xs font-500 ${theme === t.id ? 'text-blue-700' : 'text-gray-600'}`}>
                      {t.label}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* HTML-only options */}
            {exportType === 'html' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between p-3 bg-gray-50 rounded-xl">
                  <div>
                    <div className="text-sm font-600 text-gray-800">Include AI chat</div>
                    <div className="text-xs text-gray-500">Embed a chat panel connected to the live database</div>
                  </div>
                  <button
                    onClick={() => setIncludeChat((v) => !v)}
                    className={`relative w-10 h-6 rounded-full transition-colors ${includeChat ? 'bg-blue-500' : 'bg-gray-300'}`}
                  >
                    <span
                      className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                        includeChat ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>

                {includeChat && (
                  <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl">
                    <label className="text-sm font-500 text-gray-700 flex-1">Chat token valid for</label>
                    <select
                      value={tokenExpiry}
                      onChange={(e) => setTokenExpiry(Number(e.target.value))}
                      className="text-sm border border-gray-200 rounded-lg px-2 py-1 bg-white"
                    >
                      <option value={7}>7 days</option>
                      <option value={14}>14 days</option>
                      <option value={30}>30 days</option>
                      <option value={90}>90 days</option>
                    </select>
                  </div>
                )}
              </div>
            )}

            <button
              onClick={handleStartExport}
              className="w-full flex items-center justify-center gap-2 py-3 bg-blue-600 text-white font-600 rounded-xl hover:bg-blue-700 transition-colors"
            >
              <Download size={16} />
              Generate {selected.label}
            </button>
          </div>
        )}

        {phase === 'generating' && (
          <div className="px-6 py-12 flex flex-col items-center gap-4">
            <Loader2 size={40} className="text-blue-500 animate-spin" />
            <div className="text-center">
              <p className="text-gray-800 font-600">Generating your {selected.label}…</p>
              <p className="text-sm text-gray-500 mt-1">
                Refreshing widget data and building the export file.
              </p>
              {pollCount > 0 && (
                <p className="text-xs text-gray-400 mt-2">Still working… ({pollCount * 2.5}s elapsed)</p>
              )}
            </div>
          </div>
        )}

        {phase === 'done' && downloadUrl && (
          <div className="px-6 py-8 flex flex-col items-center gap-5">
            <CheckCircle2 size={48} className="text-green-500" />
            <div className="text-center">
              <p className="text-gray-800 font-700 text-lg">Export ready!</p>
              <p className="text-sm text-gray-500 mt-1">Your {selected.label.toLowerCase()} has been generated.</p>
            </div>
            <div className="flex gap-3 w-full">
              <a
                href={downloadUrl}
                download
                className="flex-1 flex items-center justify-center gap-2 py-3 bg-green-600 text-white font-600 rounded-xl hover:bg-green-700 transition-colors"
              >
                <Download size={16} />
                Download
              </a>
              {exportType === 'html' && (
                <a
                  href={downloadUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 px-4 py-3 border border-gray-200 text-gray-700 font-600 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  <ExternalLink size={16} />
                  Preview
                </a>
              )}
            </div>
            <button onClick={onClose} className="text-sm text-gray-400 hover:text-gray-600 transition-colors">
              Close
            </button>
          </div>
        )}

        {phase === 'error' && (
          <div className="px-6 py-8 flex flex-col items-center gap-4">
            <AlertCircle size={48} className="text-red-500" />
            <div className="text-center">
              <p className="text-gray-800 font-700">Export failed</p>
              <p className="text-sm text-gray-500 mt-1">{errorMsg}</p>
            </div>
            <button
              onClick={() => setPhase('config')}
              className="px-6 py-2 bg-gray-100 text-gray-700 font-600 rounded-xl hover:bg-gray-200 transition-colors"
            >
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
