'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { api, dashboardApi, shareApi, aiInsightsApi, vlyApi, endUserApi, BACKGROUND_REQ } from '@/lib/api'
import { ConnectionPromptModal } from '@/components/end-user/ConnectionPromptModal'
import {
  Loader2, AlertCircle, Search, X,
  BarChart2, Clock, RefreshCw, Share2, Heart, TrendingUp, Zap,
  ChevronRight, Sparkles, UserCircle2, Link2, Upload, FileArchive,
  Database, CheckCircle2, Package, Brain, Calendar, Wifi, WifiOff, Trash2,
} from 'lucide-react'

interface DashCard {
  id: string
  name: string
  description: string
  theme: string
  project_name: string
  project_id: string
  created_at: string
  updated_at: string
  widget_count: number
  // import provenance
  is_imported: boolean
  imported_at: string | null
  imported_by: string | null
  has_intelligence: boolean
  connection_hint: Record<string, string>
  // Real live-DB status (from an active bound connection on the project)
  live_connection?: boolean
  connection_label?: string
  connection_synced_at?: string | null
}

// Render a short AI summary as clean text — strip markdown markers (**bold**,
// *italic*, `code`, # headings, bullets) so no raw asterisks leak into the card.
function cleanSummary(s: string): string {
  if (!s) return ''
  return s
    .replace(/```[\s\S]*?```/g, '')      // code fences
    .replace(/`([^`]+)`/g, '$1')          // inline code
    .replace(/\*\*([^*]+)\*\*/g, '$1')    // bold
    .replace(/\*([^*]+)\*/g, '$1')        // italic
    .replace(/__([^_]+)__/g, '$1')        // bold (underscores)
    .replace(/_([^_]+)_/g, '$1')          // italic (underscores)
    .replace(/^#{1,6}\s+/gm, '')          // headings
    .replace(/^\s*[-*+]\s+/gm, '')        // bullet markers
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links → text
    .replace(/\s{2,}/g, ' ')
    .trim()
}

const THEME_COLORS: Record<string, { bg: string; border: string; accent: string }> = {
  frost:    { bg: '#EFF6FF', border: '#BFDBFE', accent: '#2563EB' },
  slate:    { bg: '#1E293B', border: '#334155', accent: '#94A3B8' },
  obsidian: { bg: '#111827', border: '#1F2937', accent: '#6B7280' },
  rose:     { bg: '#FFF1F2', border: '#FECDD3', accent: '#E11D48' },
  emerald:  { bg: '#ECFDF5', border: '#A7F3D0', accent: '#059669' },
  amber:    { bg: '#FFFBEB', border: '#FDE68A', accent: '#D97706' },
}

function computeHealthScore(widgetCount: number): number {
  if (widgetCount === 0) return 0
  if (widgetCount <= 2) return 35
  if (widgetCount <= 4) return 58
  if (widgetCount <= 7) return 75
  if (widgetCount <= 12) return 88
  return 95
}

function extractToken(input: string): string | null {
  const cleaned = input.trim()
  const m = cleaned.match(/\/share\/canvas\/([A-Za-z0-9_-]+)/)
  if (m) return m[1]
  if (/^[A-Za-z0-9_-]{20,}$/.test(cleaned)) return cleaned
  return null
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z').getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 2) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export default function EndUserDashboardPage() {
  const router = useRouter()
  const fileRef = useRef<HTMLInputElement>(null)

  const [dashboards, setDashboards]       = useState<DashCard[]>([])
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [search, setSearch]               = useState('')
  const [aiSummaries, setAiSummaries]     = useState<Record<string, string>>({})
  const [aiLoading, setAiLoading]         = useState<Record<string, boolean>>({})
  const [shareToast, setShareToast]       = useState<string | null>(null)
  const loadedSummaries                   = useRef(new Set<string>())

  // Paste link modal
  const [linkModal, setLinkModal]   = useState(false)
  const [linkInput, setLinkInput]   = useState('')
  const [linkError, setLinkError]   = useState('')

  // .vly import state
  const [importing, setImporting]         = useState(false)
  const [importError, setImportError]     = useState<string | null>(null)
  const [importSuccess, setImportSuccess] = useState<{ name: string; id: string; hasIntel: boolean } | null>(null)
  // Connect-a-DB step (required before import — analysts have no projects/connections UI)
  const [pendingFile, setPendingFile]     = useState<File | null>(null)
  const [connHint, setConnHint]           = useState<Record<string, string> | undefined>(undefined)
  const [showConnPrompt, setShowConnPrompt] = useState(false)
  const [hasTableData, setHasTableData]   = useState(false)
  const [showImportChoice, setShowImportChoice] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await dashboardApi.sharedWithMe()
      setDashboards(resp.data.dashboards ?? [])
    } catch {
      setError('Failed to load reports')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Continuously re-check live-DB / sync status so the card badge updates after a
  // connection is bound or refreshed (silent — no full-page spinner).
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        // Background poll: refresh the token silently if needed, but NEVER let a
        // 401 here redirect — the user may be mid-way through the connect modal.
        const resp = await dashboardApi.sharedWithMe(BACKGROUND_REQ)
        setDashboards(resp.data.dashboards ?? [])
      } catch { /* ignore */ }
    }, 30000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (dashboards.length === 0) return
    const toLoad = dashboards.filter(d => !loadedSummaries.current.has(d.id))
    toLoad.forEach(async (d, i) => {
      await new Promise(r => setTimeout(r, i * 300))
      if (loadedSummaries.current.has(d.id)) return
      loadedSummaries.current.add(d.id)
      setAiLoading(prev => ({ ...prev, [d.id]: true }))
      try {
        const resp = await aiInsightsApi.summary(d.id, BACKGROUND_REQ)
        setAiSummaries(prev => ({ ...prev, [d.id]: resp.data.summary }))
      } catch { /* ignore */ }
      finally { setAiLoading(prev => ({ ...prev, [d.id]: false })) }
    })
  }, [dashboards])

  const handleDelete = async (id: string, name: string, isImported: boolean) => {
    const msg = isImported
      ? `Delete "${name}"? This permanently removes the imported report and its charts.`
      : `Remove "${name}" from your reports? (The original stays with the person who shared it.)`
    if (!window.confirm(msg)) return
    // optimistic removal
    setDashboards(prev => prev.filter(d => d.id !== id))
    try {
      const resp = await endUserApi.deleteReport(id)
      setShareToast(resp.data?.mode === 'deleted' ? 'Report deleted' : 'Removed from your reports')
    } catch {
      setShareToast('Failed to delete — refreshing')
      await load()
    }
    setTimeout(() => setShareToast(null), 2500)
  }

  const handleShare = async (id: string) => {
    try {
      const resp = await shareApi.create(id, { mode: 'view', expires_days: 30 })
      const token = resp.data?.token ?? resp.data?.id ?? ''
      const url = `${window.location.origin}/share/canvas/${token}`
      await navigator.clipboard.writeText(url)
      setShareToast('Share link copied!')
      setTimeout(() => setShareToast(null), 2500)
    } catch {
      setShareToast('Failed to create share link')
      setTimeout(() => setShareToast(null), 2500)
    }
  }

  const handleOpenLink = () => {
    setLinkError('')
    const token = extractToken(linkInput)
    if (!token) { setLinkError('Paste a share link like: …/share/canvas/TOKEN'); return }
    setLinkModal(false)
    setLinkInput('')
    router.push(`/share/canvas/${token}`)
  }

  // Step 1 — a file was picked: peek its connection fingerprint and require a DB
  // connection before importing (analysts get live data, never a stale snapshot).
  const handleVlyFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setImportError(null)
    setImportSuccess(null)
    let hint: Record<string, string> | undefined
    let tablesBundled = false
    try {
      const { default: JSZip } = await import('jszip')
      const zip = await JSZip.loadAsync(file)
      const metaFile = zip.file('meta.json')
      if (metaFile) {
        const meta = JSON.parse(await metaFile.async('string'))
        if (meta?.connection_hint?.host) hint = meta.connection_hint
      }
      // Does the archive bundle full tables for offline use?
      const manFile = zip.file('tables_manifest.json')
      if (manFile) {
        const man = JSON.parse(await manFile.async('string'))
        tablesBundled = (man?.tables || []).some((t: { included?: boolean }) => t.included)
      }
    } catch { /* no hint/manifest — proceed without */ }
    setConnHint(hint)
    setHasTableData(tablesBundled)
    setPendingFile(file)
    // .ovly is the offline export → let the analyst choose offline (bundled data) vs
    // connecting a live DB. A .vly is a live export → go straight to the connect form.
    const isOffline = /\.ovly$/i.test(file.name)
    if (isOffline && tablesBundled) setShowImportChoice(true)
    else setShowConnPrompt(true)
  }

  // Step 2 — analyst supplied credentials: create + verify the connection, import the
  // .vly, then bind the connection (crawl schema + refresh widgets with live data).
  const connectImportAndBind = useCallback(async (details: {
    db_type: string; host: string; port: string; database_name: string; username: string
    password: string; ssl_enabled?: boolean; iam_role_arn?: string
  }) => {
    if (!pendingFile) return
    // 2a — create + test the connection (throws on failure → modal shows the error)
    const connResp = await endUserApi.createConnection({
      db_type: details.db_type,
      host: details.host,
      port: details.port ? Number(details.port) : undefined,
      database_name: details.database_name,
      username: details.username,
      password: details.password,
      ssl_enabled: details.ssl_enabled,
      iam_role_arn: details.iam_role_arn || undefined,
    })
    const connectionId = connResp.data.connection_id

    setShowConnPrompt(false)
    setImporting(true)
    try {
      // 2b — import the canvas
      const form = new FormData()
      form.append('file', pendingFile)
      const resp = await api.post('/end-user/import-vly', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const { dashboard_id, name, has_intelligence } = resp.data
      // 2c — bind: sets connection on widgets + layout, crawls schema, refreshes live data
      await vlyApi.bindConnection(dashboard_id, connectionId, { crawl: true, refresh: true })
      await load()
      setImportSuccess({ name, id: dashboard_id, hasIntel: has_intelligence })
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Imported, but failed to connect live data. Open the report to retry.'
      setImportError(msg)
    } finally {
      setImporting(false)
      setPendingFile(null)
    }
  }, [pendingFile, load])

  // Import WITHOUT a live connection — offline (query the bundled tables) or a
  // cached snapshot. No credentials needed.
  const importWithoutConnection = useCallback(async (preferOffline: boolean) => {
    if (!pendingFile) return
    setShowImportChoice(false)
    setImporting(true)
    try {
      const form = new FormData()
      form.append('file', pendingFile)
      if (preferOffline) form.append('prefer_offline', 'true')
      const resp = await api.post('/end-user/import-vly', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const { dashboard_id, name, has_intelligence } = resp.data
      await load()
      setImportSuccess({ name, id: dashboard_id, hasIntel: has_intelligence })
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Import failed. Please try again.'
      setImportError(msg)
    } finally {
      setImporting(false)
      setPendingFile(null)
    }
  }, [pendingFile, load])

  const filtered = dashboards.filter(d => {
    if (!search) return true
    const q = search.toLowerCase()
    return d.name.toLowerCase().includes(q) || d.project_name.toLowerCase().includes(q)
  })

  const sharedReports   = filtered.filter(d => !d.is_imported)
  const importedReports = filtered.filter(d => d.is_imported)

  return (
    <div className="flex flex-col h-full" style={{ background: '#F8FAFC' }}>

      {/* Toast */}
      {shareToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-5 py-3 bg-gray-900 text-white text-sm font-medium rounded-xl shadow-xl">
          {shareToast}
        </div>
      )}

      {/* Paste link modal */}
      {linkModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm px-4">
          <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-md">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
                <Link2 size={14} className="text-white" />
              </div>
              <h2 className="text-base font-bold text-gray-900">Open via Share Link</h2>
              <button onClick={() => { setLinkModal(false); setLinkInput(''); setLinkError('') }} className="ml-auto text-gray-400 hover:text-gray-600">
                <X size={16} />
              </button>
            </div>
            <p className="text-xs text-gray-500 mb-3">Paste a share link you received from your team.</p>
            <input
              autoFocus value={linkInput}
              onChange={e => { setLinkInput(e.target.value); setLinkError('') }}
              onKeyDown={e => e.key === 'Enter' && handleOpenLink()}
              placeholder="https://…/share/canvas/TOKEN"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-xl bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
            />
            {linkError && <p className="text-xs text-red-500 mt-1.5">{linkError}</p>}
            <div className="flex gap-2 mt-4">
              <button onClick={handleOpenLink} className="flex-1 py-2 text-sm font-semibold text-white rounded-xl" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
                Open Report
              </button>
              <button onClick={() => { setLinkModal(false); setLinkInput(''); setLinkError('') }} className="px-4 py-2 text-sm text-gray-500 border border-gray-200 rounded-xl hover:bg-gray-50">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Hidden file input */}
      <input ref={fileRef} type="file" accept=".vly,.ovly" className="hidden" onChange={handleVlyFile} />

      {/* Import choice — offline (bundled data) vs connect to a live DB */}
      {showImportChoice && pendingFile && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-indigo-50 flex items-center justify-center flex-shrink-0">
                  <Database size={16} className="text-indigo-600" />
                </div>
                <div>
                  <p className="text-sm font-bold text-gray-900">How do you want to open this report?</p>
                  <p className="text-xs text-gray-500 mt-0.5 font-medium">{pendingFile.name}</p>
                </div>
              </div>
              <button onClick={() => { setShowImportChoice(false); setPendingFile(null) }} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50"><X size={15} /></button>
            </div>

            {hasTableData ? (
              <p className="text-xs text-gray-500 mb-4 leading-relaxed">
                This report bundles its full table data, so it can run <strong>entirely offline</strong> — no database needed.
                Or connect a live database for real-time data.
              </p>
            ) : (
              <p className="text-xs text-gray-500 mb-4 leading-relaxed">
                Connect a live database for real-time data, or open with the cached snapshot bundled in the file.
              </p>
            )}

            <div className="space-y-2.5">
              {hasTableData && (
                <button
                  onClick={() => importWithoutConnection(true)}
                  disabled={importing}
                  className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50 hover:opacity-90"
                  style={{ background: 'linear-gradient(135deg, #6366F1, #7C3AED)' }}
                >
                  {importing ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />} Open offline with bundled data
                </button>
              )}
              <button
                onClick={() => { setShowImportChoice(false); setShowConnPrompt(true) }}
                disabled={importing}
                className="w-full py-2.5 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-50 border"
                style={{ borderColor: '#2563EB55', color: '#2563EB', background: '#2563EB10' }}
              >
                <Database size={14} /> Connect to live database
              </button>
              {!hasTableData && (
                <button
                  onClick={() => importWithoutConnection(false)}
                  disabled={importing}
                  className="w-full py-2.5 rounded-xl text-sm font-medium text-gray-600 border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
                >
                  Open with cached snapshot
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Connect-a-DB step (chosen explicitly, or when no bundled data + a DB hint) */}
      {showConnPrompt && pendingFile && (
        <ConnectionPromptModal
          fileName={pendingFile.name}
          connectionHint={connHint}
          onConnect={connectImportAndBind}
          onClose={() => { setShowConnPrompt(false); setPendingFile(null) }}
        />
      )}

      {/* Header */}
      <div className="px-6 py-4 bg-white border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold font-display text-gray-900">My Reports</h1>
            <p className="text-sm text-gray-500 mt-0.5">Reports shared with you · your imported canvases</p>
          </div>
          <button onClick={load} className="p-2 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors" title="Refresh">
            <RefreshCw size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="relative mt-3 max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search reports…"
            className="w-full pl-9 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
          />
          {search && <button onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"><X size={13} /></button>}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 mt-3 flex-wrap">
          <span className="text-xs text-gray-400 font-medium mr-1">Add report:</span>
          <button onClick={() => setLinkModal(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-blue-200 text-blue-600 bg-blue-50 hover:bg-blue-100 transition-colors">
            <Link2 size={12} /> Paste share link
          </button>
          <button onClick={() => fileRef.current?.click()} disabled={importing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-purple-200 text-purple-600 bg-purple-50 hover:bg-purple-100 transition-colors disabled:opacity-50">
            {importing ? <><Loader2 size={12} className="animate-spin" /> Importing…</> : <><Upload size={12} /> Import</>}
          </button>
        </div>

        {/* Import success banner */}
        {importSuccess && (
          <div className="mt-3 flex items-center gap-3 px-4 py-3 rounded-xl border border-green-200 bg-green-50">
            <CheckCircle2 size={16} className="text-green-600 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-green-800 truncate">"{importSuccess.name}" imported successfully</p>
              <p className="text-xs text-green-600 mt-0.5">Saved permanently to your reports · {importSuccess.hasIntel ? 'AI analysis bundled' : 'Open to run AI analysis'}</p>
            </div>
            <div className="flex gap-2 flex-shrink-0">
              <button onClick={() => router.push(`/intelligence/${importSuccess.id}`)}
                className="px-3 py-1.5 text-xs font-semibold text-white rounded-lg"
                style={{ background: 'linear-gradient(135deg, #059669, #10b981)' }}>
                Open
              </button>
              <button onClick={() => setImportSuccess(null)} className="text-green-500 hover:text-green-700"><X size={14} /></button>
            </div>
          </div>
        )}

        {/* Import error */}
        {importError && (
          <div className="mt-2 flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            <AlertCircle size={12} />
            <span>{importError}</span>
            <button onClick={() => setImportError(null)} className="ml-auto"><X size={11} /></button>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6 space-y-8">
        {loading && (
          <div className="flex items-center justify-center h-48"><Loader2 className="w-8 h-8 text-blue-500 animate-spin" /></div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <AlertCircle className="w-10 h-10 text-red-400" />
            <p className="text-sm text-gray-500">{error}</p>
            <button onClick={load} className="btn-secondary text-sm flex items-center gap-2"><RefreshCw size={13} /> Retry</button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 gap-4 text-gray-400">
            <UserCircle2 className="w-12 h-12 text-gray-300" />
            <div className="text-center">
              <p className="font-medium text-gray-600">{search ? 'No reports match your search.' : 'No reports yet.'}</p>
              {!search && (
                <div className="mt-2 space-y-1 text-sm text-gray-400">
                  <p>Ask your builder to share a report, or:</p>
                  <p>
                    <button onClick={() => setLinkModal(true)} className="text-blue-500 hover:underline font-medium">Paste a share link</button>
                    {' · '}
                    <button onClick={() => fileRef.current?.click()} className="text-purple-500 hover:underline font-medium">Import a .vly file</button>
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Shared With Me ─────────────────────────────────────────────── */}
        {!loading && !error && sharedReports.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-4">
              <Share2 size={14} className="text-blue-500" />
              <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Shared with me</h2>
              <span className="text-xs text-gray-400">({sharedReports.length})</span>
            </div>
            <ReportGrid reports={sharedReports} aiSummaries={aiSummaries} aiLoading={aiLoading} onShare={handleShare} onDelete={handleDelete} router={router} />
          </section>
        )}

        {/* ── Imported Reports ───────────────────────────────────────────── */}
        {!loading && !error && importedReports.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-4">
              <Package size={14} className="text-purple-500" />
              <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider">My Imported Canvases</h2>
              <span className="text-xs text-gray-400">({importedReports.length})</span>
            </div>
            <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
              {importedReports.map(dash => (
                <ImportedCard key={dash.id} dash={dash} aiSummary={aiSummaries[dash.id]} aiLoading={!!aiLoading[dash.id]} onShare={() => handleShare(dash.id)} onDelete={() => handleDelete(dash.id, dash.name, true)} router={router} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

// ── Shared report card grid ────────────────────────────────────────────────────

function ReportGrid({ reports, aiSummaries, aiLoading, onShare, onDelete, router }: {
  reports: DashCard[]
  aiSummaries: Record<string, string>
  aiLoading: Record<string, boolean>
  onShare: (id: string) => void
  onDelete: (id: string, name: string, isImported: boolean) => void
  router: ReturnType<typeof useRouter>
}) {
  return (
    <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
      {reports.map(dash => {
        const colors  = THEME_COLORS[dash.theme] ?? THEME_COLORS.frost
        const health  = computeHealthScore(dash.widget_count)
        const updated = new Date(
          (dash.updated_at.includes('T') ? dash.updated_at : dash.updated_at.replace(' ', 'T'))
          + (/[Zz]|[+-]\d{2}/.test(dash.updated_at) ? '' : 'Z')
        ).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

        return (
          <div key={dash.id} className="group relative rounded-2xl border border-gray-100 overflow-hidden bg-white hover:shadow-xl hover:-translate-y-1 transition-all duration-200">
            <div className="h-20 relative flex items-end px-4 pb-3" style={{ background: `linear-gradient(135deg, ${colors.border}, ${colors.accent})` }}>
              <div className="flex items-center gap-1.5 opacity-60">
                <BarChart2 size={14} className="text-white" /><TrendingUp size={14} className="text-white" /><Zap size={12} className="text-white" />
              </div>
              <div className="absolute top-3 right-3 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold"
                style={{ background: 'rgba(0,0,0,0.4)', color: health >= 80 ? '#4ADE80' : health >= 55 ? '#FCD34D' : '#F87171' }}>
                <Heart size={9} fill="currentColor" />{health}
              </div>
            </div>
            <div className="p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-bold text-gray-900 truncate">{dash.name}</h3>
                  <p className="text-xs text-gray-500 mt-0.5 truncate">{dash.project_name}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-gray-400 flex items-center gap-0.5"><BarChart2 size={9} /> {dash.widget_count} charts</span>
                    <span className="text-[10px] text-gray-400 flex items-center gap-0.5"><Clock size={9} /> {updated}</span>
                  </div>
                </div>
                <ChevronRight size={14} className="text-gray-300 group-hover:text-blue-400 transition-colors mt-1 flex-shrink-0" />
              </div>
              <div className="mt-3 min-h-[36px]">
                {aiLoading[dash.id] ? (
                  <div className="flex items-center gap-1.5 text-[11px] text-gray-400"><Loader2 size={10} className="animate-spin text-purple-400" /><span>AI briefing…</span></div>
                ) : aiSummaries[dash.id] ? (
                  <div className="flex items-start gap-1.5">
                    <Sparkles size={11} className="text-purple-500 mt-0.5 flex-shrink-0" />
                    <p className="text-[11px] text-gray-600 leading-relaxed line-clamp-2">{cleanSummary(aiSummaries[dash.id])}</p>
                  </div>
                ) : dash.description ? (
                  <p className="text-[11px] text-gray-500 line-clamp-2">{dash.description}</p>
                ) : null}
              </div>
              <div className="flex items-center gap-2 mt-4 pt-3 border-t border-gray-50">
                <button onClick={() => router.push(`/canvas/${dash.id}/analyst`)}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-semibold text-white rounded-xl transition-colors"
                  style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
                  <Sparkles size={12} /> Open Report
                </button>
                <button onClick={e => { e.stopPropagation(); onShare(dash.id) }}
                  className="p-2 text-gray-400 hover:text-blue-500 rounded-lg hover:bg-blue-50 transition-colors border border-gray-100" title="Copy share link">
                  <Share2 size={14} />
                </button>
                <button onClick={e => { e.stopPropagation(); onDelete(dash.id, dash.name, false) }}
                  className="p-2 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors border border-gray-100" title="Remove from my reports">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
            <div className="absolute inset-0 rounded-2xl border-2 border-transparent group-hover:border-blue-400 transition-colors pointer-events-none" />
          </div>
        )
      })}
    </div>
  )
}

// ── Imported canvas card ───────────────────────────────────────────────────────

function ImportedCard({ dash, aiSummary, aiLoading, onShare, onDelete, router }: {
  dash: DashCard
  aiSummary?: string
  aiLoading: boolean
  onShare: () => void
  onDelete: () => void
  router: ReturnType<typeof useRouter>
}) {
  const colors    = THEME_COLORS[dash.theme] ?? THEME_COLORS.frost
  const health    = computeHealthScore(dash.widget_count)
  // Live = an active DB connection is actually bound on the project (not the
  // import-time hint). Falls back to the hint for older records.
  const hasDB     = dash.live_connection ?? !!(dash.connection_hint?.host)
  const dbLabel   = dash.connection_label
    || (dash.connection_hint?.db_type ? `${dash.connection_hint.db_type} · ${dash.connection_hint.database_name ?? ''}` : 'Live database')
  const syncedAgo = dash.connection_synced_at ? timeAgo(dash.connection_synced_at) : ''
  const importedAgo = dash.imported_at ? timeAgo(dash.imported_at) : ''

  return (
    <div className="group relative rounded-2xl overflow-hidden bg-white border border-purple-100 hover:shadow-xl hover:-translate-y-1 transition-all duration-200">
      {/* Header band */}
      <div className="h-20 relative flex items-end px-4 pb-3" style={{ background: `linear-gradient(135deg, #7c3aed22, #7c3aed44)` }}>
        <div className="flex items-center gap-1.5 opacity-70">
          <Package size={14} className="text-purple-500" />
          <span className="text-[10px] font-bold text-purple-600 uppercase tracking-wide">Imported</span>
        </div>
        <div className="absolute top-3 right-3 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold"
          style={{ background: 'rgba(0,0,0,0.25)', color: health >= 80 ? '#4ADE80' : health >= 55 ? '#FCD34D' : '#F87171' }}>
          <Heart size={9} fill="currentColor" />{health}
        </div>
        {/* AI badge */}
        {dash.has_intelligence && (
          <div className="absolute top-3 left-12 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-100 text-purple-700">
            <Brain size={9} /> AI
          </div>
        )}
      </div>

      <div className="p-4">
        {/* Title row */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-bold text-gray-900 truncate">{dash.name}</h3>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              <span className="text-[10px] text-gray-400 flex items-center gap-0.5"><BarChart2 size={9} /> {dash.widget_count} charts</span>
              {importedAgo && <span className="text-[10px] text-gray-400 flex items-center gap-0.5"><Calendar size={9} /> Imported {importedAgo}</span>}
            </div>
          </div>
          <ChevronRight size={14} className="text-gray-300 group-hover:text-purple-400 transition-colors mt-1 flex-shrink-0" />
        </div>

        {/* DB status pill */}
        <div className={`mt-2 flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-medium w-fit ${
          hasDB ? 'bg-green-50 text-green-700 border border-green-100' : 'bg-amber-50 text-amber-700 border border-amber-100'
        }`}>
          {hasDB ? <Wifi size={9} /> : <WifiOff size={9} />}
          <span className="truncate max-w-[200px]">
            {hasDB ? `Live · ${dbLabel}${syncedAgo ? ` · synced ${syncedAgo}` : ''}` : 'Cached data only — no live DB'}
          </span>
        </div>

        {/* AI summary */}
        <div className="mt-3 min-h-[32px]">
          {aiLoading ? (
            <div className="flex items-center gap-1.5 text-[11px] text-gray-400"><Loader2 size={10} className="animate-spin text-purple-400" /><span>AI briefing…</span></div>
          ) : aiSummary ? (
            <div className="flex items-start gap-1.5">
              <Sparkles size={11} className="text-purple-500 mt-0.5 flex-shrink-0" />
              <p className="text-[11px] text-gray-600 leading-relaxed line-clamp-2">{cleanSummary(aiSummary)}</p>
            </div>
          ) : dash.description ? (
            <p className="text-[11px] text-gray-500 line-clamp-2">{dash.description}</p>
          ) : null}
        </div>

        {/* Action row — Open goes to the AI Intelligence page */}
        <div className="flex items-center gap-2 mt-4 pt-3 border-t border-gray-50">
          <button
            onClick={() => router.push(`/intelligence/${dash.id}`)}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-semibold text-white rounded-xl"
            style={{ background: 'linear-gradient(135deg, #7c3aed, #a855f7)' }}>
            <Brain size={12} /> Open
          </button>
          <button
            onClick={e => { e.stopPropagation(); onShare() }}
            title="Copy share link"
            className="p-2 rounded-lg border border-gray-100 text-gray-400 hover:text-blue-500 hover:bg-blue-50 transition-colors"
          >
            <Share2 size={14} />
          </button>
          <button
            onClick={e => { e.stopPropagation(); onDelete() }}
            title="Delete report"
            className="p-2 rounded-lg border border-gray-100 text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      <div className="absolute inset-0 rounded-2xl border-2 border-transparent group-hover:border-purple-300 transition-colors pointer-events-none" />
    </div>
  )
}
