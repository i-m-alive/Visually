'use client'
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Loader2, AlertCircle, Search, X, BarChart2, Clock,
  FileUp, RefreshCw, Sparkles, Download, Share2,
  Pin, PinOff, LayoutDashboard, Plus,
  Trash2, RotateCcw, CheckCircle2, Copy, List,
  LayoutGrid, SlidersHorizontal, ChevronDown, Tag,
  AlertTriangle, Command, ArrowRight, Folder, Users,
} from 'lucide-react'
import { dashboardApi, canvasApi, vlyApi, shareApi, aiInsightsApi } from '@/lib/api'
import { VlyImportZone } from '@/components/end-user/VlyImportZone'
import { VisuallReportLoader } from '@/components/canvas/VisuallReportLoader'
import { useAuthStore } from '@/stores/authStore'

interface DashCard {
  id: string
  name: string
  description?: string
  theme: string
  project_id: string
  created_at: string
  updated_at: string
  widget_count: number
  has_schedule?: boolean
}

const THEME_GRADIENTS: Record<string, { from: string; to: string }> = {
  executive:    { from: '#0a2540', to: '#2563EB' },
  lightpro:     { from: '#1E1B4B', to: '#7C3AED' },
  midnight:     { from: '#040A14', to: '#0ea5e9' },
  senior:       { from: '#000080', to: '#4169E1' },
  maturepro:    { from: '#3E2723', to: '#8B4513' },
  digitalnative:{ from: '#050507', to: '#00b894' },
  genz:         { from: '#6B21A8', to: '#EC4899' },
  accessible:   { from: '#000000', to: '#0057A8' },
  frost:        { from: '#1d4ed8', to: '#7c3aed' },
  slate:        { from: '#1E293B', to: '#475569' },
  obsidian:     { from: '#111827', to: '#374151' },
  rose:         { from: '#BE123C', to: '#E11D48' },
  emerald:      { from: '#065F46', to: '#059669' },
  amber:        { from: '#92400E', to: '#D97706' },
}
function getGrad(t: string) { return THEME_GRADIENTS[t] ?? THEME_GRADIENTS.frost }

function relativeDate(s: string): string {
  if (!s) return '—'
  const norm = s.includes('T') ? s : s.replace(' ', 'T')
  const tz = /[Zz]|[+-]\d{2}:?\d{2}$/.test(norm) ? norm : norm + 'Z'
  const d = new Date(tz)
  if (isNaN(d.getTime())) return '—'
  const diff = Date.now() - d.getTime()
  const m = Math.floor(diff / 60000), h = Math.floor(diff / 3600000), dy = Math.floor(diff / 86400000)
  if (m < 2) return 'just now'
  if (m < 60) return `${m}m ago`
  if (h < 24) return `${h}h ago`
  if (dy < 7) return `${dy}d ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
function isStale(s: string): boolean {
  if (!s) return false
  const norm = s.includes('T') ? s : s.replace(' ', 'T')
  const tz = /[Zz]|[+-]\d{2}:?\d{2}$/.test(norm) ? norm : norm + 'Z'
  const d = new Date(tz)
  return !isNaN(d.getTime()) && Date.now() - d.getTime() > 30 * 86400000
}
function isAiError(t: string) {
  return /unavailable|ResourceNotFound|error occurred|Exception|failed/i.test(t)
}

const lsGet = (k: string, fb: any) => { try { return JSON.parse(localStorage.getItem(k) ?? '') } catch { return fb } }
const lsSet = (k: string, v: any) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch {} }
function getPinned()   { return lsGet('visually-pinned', []) as string[] }
function setPinned(v: string[]) { lsSet('visually-pinned', v) }
function getRecent(pid: string) { return lsGet(`visually-recent-${pid}`, []) as string[] }
function pushRecent(pid: string, id: string) {
  const prev = getRecent(pid).filter((x: string) => x !== id)
  lsSet(`visually-recent-${pid}`, [id, ...prev].slice(0, 8))
}
function getFolders() { return lsGet('visually-folders', {}) as Record<string, string> }
function setFolders(v: Record<string, string>) { lsSet('visually-folders', v) }

export default function DashboardPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const router = useRouter()
  const user = useAuthStore(s => s.user)
  const isBuilder = (user as any)?.role === 'builder'

  const [dashboards,   setDashboards]   = useState<DashCard[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [search,       setSearch]       = useState('')
  const [viewingId,    setViewingId]    = useState<string | null>(null)
  const [showImport,   setShowImport]   = useState(false)
  const [importing,    setImporting]    = useState(false)
  const [importMsg,    setImportMsg]    = useState<string | null>(null)
  const [creatingCanvas, setCreatingCanvas] = useState(false)

  const [pinned,       setPinnedState]  = useState<string[]>([])
  const [recent,       setRecent]       = useState<string[]>([])
  const [viewMode,     setViewMode]     = useState<'grid' | 'list'>('grid')
  const [sortBy,       setSortBy]       = useState<'updated' | 'name' | 'count'>('updated')
  const [filterFolder, setFilterFolder] = useState<string>('')
  const [folders,      setFoldersState] = useState<Record<string, string>>({})
  const [showFolderEdit, setShowFolderEdit] = useState<string | null>(null)
  const [folderDraft,  setFolderDraft]  = useState('')
  const [showSortMenu, setShowSortMenu] = useState(false)

  const [aiSummaries, setAiSummaries] = useState<Record<string, string>>({})
  const [aiLoading,   setAiLoading]   = useState<Record<string, boolean>>({})
  const loadedSummaries = useRef(new Set<string>())

  const [deletingId,   setDeletingId]   = useState<string | null>(null)
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null)

  const [renamingId,   setRenamingId]   = useState<string | null>(null)
  const [renameDraft,  setRenameDraft]  = useState('')
  const [renaming,     setRenaming]     = useState(false)

  const [duplicating,  setDuplicating]  = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null)

  const [aiQuery,    setAiQuery]    = useState('')
  const [aiResults,  setAiResults]  = useState<string[] | null>(null)
  const [aiSearching, setAiSearching] = useState(false)

  const [showPalette, setShowPalette] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')

  const [shareModalId,     setShareModalId]     = useState<string | null>(null)
  const [shareModalEmail,  setShareModalEmail]  = useState('')
  const [sharingAnalyst,   setSharingAnalyst]   = useState(false)
  const [shareModalResult, setShareModalResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok }); setTimeout(() => setToast(null), 3000)
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const resp = await dashboardApi.list(projectId)
      const list: DashCard[] = resp.data?.dashboards ?? resp.data ?? []
      setDashboards(list)
    } catch { setError('Failed to load reports') }
    finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    setPinnedState(getPinned())
    setRecent(getRecent(projectId))
    setFoldersState(getFolders())
  }, [projectId])

  const loadSummary = useCallback(async (id: string) => {
    if (loadedSummaries.current.has(id)) return
    loadedSummaries.current.add(id)
    setAiLoading(prev => ({ ...prev, [id]: true }))
    try {
      const resp = await aiInsightsApi.summary(id)
      const text = resp.data.summary ?? ''
      if (!isAiError(text)) setAiSummaries(prev => ({ ...prev, [id]: text }))
    } catch {}
    finally { setAiLoading(prev => ({ ...prev, [id]: false })) }
  }, [])

  useEffect(() => {
    if (dashboards.length === 0) return
    dashboards.forEach((d, i) => setTimeout(() => loadSummary(d.id), i * 240))
  }, [dashboards, loadSummary])

  const refreshSummary = (id: string) => {
    loadedSummaries.current.delete(id)
    setAiSummaries(prev => { const n = { ...prev }; delete n[id]; return n })
    loadSummary(id)
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault(); setShowPalette(v => !v); setPaletteQuery('')
      }
      if (e.key === 'Escape') { setShowPalette(false); setConfirmDelId(null) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // ── Actions ──────────────────────────────────────────────────────────────────

  const handleNewCanvas = async () => {
    setCreatingCanvas(true)
    try {
      const resp = await canvasApi.create({ project_id: projectId, name: 'Untitled Report' })
      const newId = resp.data?.id ?? resp.data?.dashboard?.id
      if (newId) router.push(`/projects/${projectId}/canvas/${newId}`)
      else showToast('Created but could not navigate', false)
    } catch { showToast('Failed to create canvas', false) }
    finally { setCreatingCanvas(false) }
  }

  const togglePin = (id: string) => {
    const next = pinned.includes(id) ? pinned.filter(x => x !== id) : [id, ...pinned]
    setPinnedState(next); setPinned(next)
  }

  const openReport = (id: string) => {
    pushRecent(projectId, id); setRecent(getRecent(projectId)); setViewingId(id)
  }

  const handleDelete = async (id: string) => {
    if (confirmDelId !== id) { setConfirmDelId(id); return }
    setDeletingId(id); setConfirmDelId(null)
    try {
      await dashboardApi.delete(id)
      setDashboards(prev => prev.filter(d => d.id !== id))
      showToast('Report deleted')
    } catch (err: any) {
      showToast(err?.response?.data?.detail ?? 'Delete failed — please try again', false)
    } finally { setDeletingId(null) }
  }

  const handleRename = async (id: string) => {
    const name = renameDraft.trim()
    if (!name) { setRenamingId(null); return }
    setRenaming(true)
    try {
      await dashboardApi.rename(id, name)
      setDashboards(prev => prev.map(d => d.id === id ? { ...d, name } : d))
      showToast('Renamed')
    } catch { showToast('Rename failed', false) }
    finally { setRenaming(false); setRenamingId(null) }
  }

  const handleDuplicate = async (id: string) => {
    setDuplicating(id)
    try {
      const resp = await dashboardApi.duplicate(id)
      showToast(`Duplicated as "${resp.data.name}"`)
      await load()
    } catch { showToast('Duplicate failed', false) }
    finally { setDuplicating(null) }
  }

  const handleExport = (id: string) => { vlyApi.exportVly(id); showToast('Export started') }

  const handleShare = async (id: string) => {
    try {
      const resp = await shareApi.create(id, { mode: 'view', expires_days: 30 })
      const token = resp.data?.token ?? resp.data?.id ?? ''
      await navigator.clipboard.writeText(`${window.location.origin}/share/canvas/${token}`)
      showToast('Share link copied!')
    } catch { showToast('Failed to create share link', false) }
  }

  const handleShareWithAnalyst = async (email: string) => {
    if (!shareModalId || !email.trim()) return
    setSharingAnalyst(true)
    setShareModalResult(null)
    try {
      await shareApi.addCollaborator(shareModalId, { email: email.trim(), role: 'viewer' })
      setShareModalResult({ ok: true, msg: `Shared with ${email.trim()}` })
      setShareModalEmail('')
      setTimeout(() => { setShareModalId(null); setShareModalResult(null) }, 1500)
    } catch (err: any) {
      setShareModalResult({ ok: false, msg: err?.response?.data?.detail ?? 'User not found or already shared' })
    } finally {
      setSharingAnalyst(false)
    }
  }

  const handleImport = async (file: File, connectionId?: string) => {
    setImporting(true); setImportMsg(null)
    try {
      const resp = await vlyApi.importVly(file, projectId, connectionId)
      setImportMsg(`Imported "${resp.data.name}" successfully!`)
      setShowImport(false); await load()
    } catch { setImportMsg('Import failed. Please try again.') }
    finally { setImporting(false) }
  }

  const saveFolder = (id: string, folder: string) => {
    const next = folder.trim()
      ? { ...folders, [id]: folder.trim() }
      : (() => { const n = { ...folders }; delete n[id]; return n })()
    setFoldersState(next); setFolders(next); setShowFolderEdit(null)
  }

  const handleAiSearch = async () => {
    if (!aiQuery.trim()) return
    setAiSearching(true); setAiResults(null)
    await new Promise(r => setTimeout(r, 320))
    const q = aiQuery.toLowerCase()
    const matched = dashboards
      .filter(d => {
        const text = [d.name, d.description, aiSummaries[d.id]].filter(Boolean).join(' ').toLowerCase()
        return text.includes(q) || q.split(' ').some(w => w.length > 2 && text.includes(w))
      })
      .map(d => d.id)
    setAiResults(matched)
    setAiSearching(false)
  }

  // ── Derived ───────────────────────────────────────────────────────────────────

  const sorted = useMemo(() => {
    const list = [...dashboards]
    if (sortBy === 'name') list.sort((a, b) => a.name.localeCompare(b.name))
    else if (sortBy === 'count') list.sort((a, b) => b.widget_count - a.widget_count)
    else list.sort((a, b) => {
      const ta = new Date(((a.updated_at || a.created_at).replace(' ', 'T')) + (/(Z|[+-]\d{2})/.test(a.updated_at) ? '' : 'Z')).getTime()
      const tb = new Date(((b.updated_at || b.created_at).replace(' ', 'T')) + (/(Z|[+-]\d{2})/.test(b.updated_at) ? '' : 'Z')).getTime()
      return tb - ta
    })
    return list
  }, [dashboards, sortBy])

  const filtered = useMemo(() => {
    let list = sorted
    if (aiResults !== null) return list.filter(d => aiResults.includes(d.id))
    if (search) {
      const q = search.toLowerCase()
      list = list.filter(d =>
        d.name.toLowerCase().includes(q) ||
        (d.description ?? '').toLowerCase().includes(q) ||
        (aiSummaries[d.id] ?? '').toLowerCase().includes(q)
      )
    }
    if (filterFolder) list = list.filter(d => folders[d.id] === filterFolder)
    return list
  }, [sorted, search, filterFolder, aiResults, aiSummaries, folders])

  const pinnedCards = filtered.filter(d => pinned.includes(d.id))
  const recentCards = recent.flatMap(rid => { const f = dashboards.find(d => d.id === rid); return f ? [f] : [] }).slice(0, 6)
  const folderNames = [...new Set(Object.values(folders))].filter(Boolean)

  const paletteItems = useMemo(() => {
    const q = paletteQuery.toLowerCase()
    return dashboards.filter(d => !q || d.name.toLowerCase().includes(q)).slice(0, 8)
  }, [dashboards, paletteQuery])

  const hasActiveFilters = !!(search || filterFolder || aiResults !== null)

  if (viewingId) return (
    <VisuallReportLoader
      dashboardId={viewingId}
      projectId={projectId}
      canEdit={isBuilder}
      onClose={() => setViewingId(null)}
    />
  )

  return (
    <>
      <style>{`
        @keyframes slideUp   { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
        @keyframes fadeIn    { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
        @keyframes shimmer   { 0%{background-position:-500px 0} 100%{background-position:500px 0} }
        @keyframes popSpring { 0%{transform:scale(.75);opacity:0} 65%{transform:scale(1.08)} 100%{transform:scale(1);opacity:1} }
        @keyframes toastUp   { from{opacity:0;transform:translateX(-50%) translateY(14px)} to{opacity:1;transform:translateX(-50%) translateY(0)} }
        @keyframes backdropIn{ from{opacity:0} to{opacity:1} }
        @keyframes paletteIn { from{opacity:0;transform:translate(-50%,-48%) scale(.96)} to{opacity:1;transform:translate(-50%,-50%) scale(1)} }
        @keyframes flipHint  { 0%,100%{opacity:.6;transform:translateY(0)} 50%{opacity:1;transform:translateY(-3px)} }
        .shimmer-line{background:linear-gradient(90deg,#f1f5f9 25%,#e2e8f0 50%,#f1f5f9 75%);background-size:500px 100%;animation:shimmer 1.5s ease-in-out infinite;border-radius:6px}
        .toast-enter{animation:toastUp .3s cubic-bezier(.21,1.02,.73,1) both}
        .palette-enter{animation:paletteIn .2s cubic-bezier(.21,1.02,.73,1) both}
        .backdrop-enter{animation:backdropIn .2s ease both}
        .flip-hint{animation:flipHint 2s ease-in-out infinite}
      `}</style>

      <div className="flex flex-col h-full" style={{ background: '#F4F6FB' }}>

        {/* ── Header ── */}
        <div className="px-6 pt-4 pb-3 bg-white border-b border-gray-100 flex-shrink-0" style={{ animation: 'fadeIn .35s ease both' }}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h1 className="text-lg font-bold text-gray-900">Reports</h1>
              <p className="text-xs text-gray-500 mt-0.5">{dashboards.length} reports · project {projectId.slice(0, 8)}</p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => { setShowPalette(true); setPaletteQuery('') }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-all"
              >
                <Command size={12} /> Quick open
                <span className="ml-1 text-[10px] text-gray-400 bg-gray-100 px-1 rounded">⌘K</span>
              </button>
              <button onClick={load} className="p-2 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-all" title="Refresh">
                <RefreshCw size={15} />
              </button>
              <button onClick={() => setShowImport(true)} className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 transition-all">
                <FileUp size={14} /> Import .vly
              </button>
              {isBuilder && (
                <button
                  onClick={handleNewCanvas}
                  disabled={creatingCanvas}
                  className="flex items-center gap-1.5 px-3 py-2 text-sm font-semibold text-white rounded-lg disabled:opacity-60 transition-all"
                  style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                  onMouseEnter={e => { const el = e.currentTarget as HTMLElement; el.style.filter = 'brightness(1.1)'; el.style.transform = 'translateY(-1px)' }}
                  onMouseLeave={e => { const el = e.currentTarget as HTMLElement; el.style.filter = ''; el.style.transform = '' }}
                >
                  {creatingCanvas ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  New Canvas
                </button>
              )}
            </div>
          </div>

          {/* Search + Sort + View toggle */}
          <div className="flex items-center gap-2 mt-3">
            <div className="relative flex-1 max-w-xs">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={search} onChange={e => { setSearch(e.target.value); setAiResults(null) }}
                placeholder="Search reports…"
                className="w-full pl-9 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all"
              />
              {search && <button onClick={() => { setSearch(''); setAiResults(null) }} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400"><X size={13} /></button>}
            </div>

            <div className="relative">
              <button onClick={() => setShowSortMenu(v => !v)} className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-all">
                <SlidersHorizontal size={13} />
                {sortBy === 'updated' ? 'Last updated' : sortBy === 'name' ? 'Name' : 'Widget count'}
                <ChevronDown size={11} />
              </button>
              {showSortMenu && (
                <div className="absolute top-full left-0 mt-1 z-30 bg-white rounded-xl border border-gray-100 shadow-lg py-1 min-w-[160px]" style={{ animation: 'popSpring .18s ease both' }}>
                  {(['updated', 'name', 'count'] as const).map(s => (
                    <button key={s} onClick={() => { setSortBy(s); setShowSortMenu(false) }}
                      className={`w-full text-left px-4 py-2 text-sm transition-colors ${sortBy === s ? 'text-blue-600 bg-blue-50' : 'text-gray-700 hover:bg-gray-50'}`}
                    >
                      {s === 'updated' ? 'Last updated' : s === 'name' ? 'Name (A–Z)' : 'Widget count'}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {folderNames.length > 0 && (
              <select value={filterFolder} onChange={e => setFilterFolder(e.target.value)}
                className="px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg bg-white focus:outline-none hover:bg-gray-50 cursor-pointer"
              >
                <option value="">All folders</option>
                {folderNames.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            )}

            <div className="flex border border-gray-200 rounded-lg overflow-hidden ml-auto">
              <button onClick={() => setViewMode('grid')} className={`px-2.5 py-2 transition-colors ${viewMode === 'grid' ? 'bg-blue-50 text-blue-600' : 'text-gray-400 hover:bg-gray-50'}`} title="Grid view">
                <LayoutGrid size={14} />
              </button>
              <button onClick={() => setViewMode('list')} className={`px-2.5 py-2 transition-colors border-l border-gray-200 ${viewMode === 'list' ? 'bg-blue-50 text-blue-600' : 'text-gray-400 hover:bg-gray-50'}`} title="List view">
                <List size={14} />
              </button>
            </div>
          </div>

          {/* AI Search */}
          <div className="flex items-center gap-2 mt-2.5">
            <div className="relative flex-1 max-w-sm">
              <Sparkles size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-purple-400" />
              <input
                value={aiQuery}
                onChange={e => setAiQuery(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAiSearch()}
                placeholder='Ask AI: "Which report shows hiring data?"'
                className="w-full pl-9 pr-3 py-2 text-sm border border-purple-200 rounded-lg bg-purple-50/40 focus:outline-none focus:ring-2 focus:ring-purple-400/20 focus:border-purple-400 transition-all text-gray-700 placeholder:text-purple-300"
              />
            </div>
            <button onClick={handleAiSearch} disabled={!aiQuery.trim() || aiSearching}
              className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-white rounded-lg disabled:opacity-50 transition-all"
              style={{ background: 'linear-gradient(135deg, #7C3AED, #2563EB)' }}
            >
              {aiSearching ? <Loader2 size={12} className="animate-spin" /> : <><Sparkles size={12} /> Ask</>}
            </button>
            {aiResults !== null && (
              <button onClick={() => { setAiResults(null); setAiQuery('') }} className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 px-2 py-1 rounded hover:bg-purple-50">
                <X size={11} /> Clear ({aiResults.length})
              </button>
            )}
          </div>

          {/* Folder chips */}
          {folderNames.length > 0 && (
            <div className="flex items-center gap-2 mt-2.5 flex-wrap">
              <span className="text-[10px] text-gray-400 font-medium">FOLDERS</span>
              <button onClick={() => setFilterFolder('')}
                className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-medium transition-all ${!filterFolder ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-600 border-gray-200 hover:border-gray-400'}`}
              >All</button>
              {folderNames.map(f => (
                <button key={f} onClick={() => setFilterFolder(f === filterFolder ? '' : f)}
                  className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-medium transition-all ${filterFolder === f ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-indigo-600 border-indigo-200 hover:border-indigo-400'}`}
                >
                  <Folder size={9} /> {f}
                </button>
              ))}
            </div>
          )}
        </div>

        {importMsg && (
          <div className={`mx-6 mt-4 flex-shrink-0 px-4 py-3 rounded-xl text-sm font-medium flex items-center gap-2 ${importMsg.includes('successfully') ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`} style={{ animation: 'fadeIn .3s ease both' }}>
            {importMsg.includes('successfully') ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
            {importMsg}
            <button onClick={() => setImportMsg(null)} className="ml-auto"><X size={13} /></button>
          </div>
        )}

        {toast && (
          <div className={`toast-enter fixed bottom-6 left-1/2 z-50 px-5 py-3 text-white text-sm font-medium rounded-xl shadow-2xl flex items-center gap-2 ${toast.ok ? 'bg-gray-900' : 'bg-red-600'}`} style={{ transform: 'translateX(-50%)' }}>
            {toast.ok ? <CheckCircle2 size={15} className="text-green-400" /> : <AlertCircle size={15} />}
            {toast.msg}
          </div>
        )}

        {/* ── Content ── */}
        <div className="flex-1 overflow-auto p-6 space-y-8">
          {loading && <div className="flex items-center justify-center h-48"><Loader2 className="w-8 h-8 text-blue-500 animate-spin" /></div>}
          {error && !loading && (
            <div className="flex flex-col items-center justify-center h-48 gap-3" style={{ animation: 'fadeIn .3s ease both' }}>
              <AlertCircle className="w-10 h-10 text-red-400" />
              <p className="text-sm text-gray-500">{error}</p>
              <button onClick={load} className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"><RefreshCw size={13} /> Retry</button>
            </div>
          )}
          {!loading && !error && dashboards.length === 0 && (
            <div className="flex flex-col items-center justify-center h-64 gap-4 text-gray-400" style={{ animation: 'fadeIn .3s ease both' }}>
              <LayoutDashboard className="w-12 h-12" />
              <div className="text-center">
                <p className="font-medium text-gray-600">No reports yet</p>
                {isBuilder && <p className="text-sm mt-1">Click <strong>New Canvas</strong> to create your first report.</p>}
              </div>
            </div>
          )}

          {!loading && !error && dashboards.length > 0 && (
            <>
              {pinnedCards.length > 0 && (
                <section style={{ animation: 'fadeIn .35s ease both' }}>
                  <SH icon={<Pin size={13} className="text-amber-500" />} label="Pinned" />
                  <CardGrid cards={pinnedCards} viewMode={viewMode} idx0={0} {...sharedProps({ aiSummaries, aiLoading, deletingId, confirmDelId, duplicating, renamingId, renameDraft, renaming, pinned, folders, showFolderEdit, folderDraft, openReport, handleDelete, handleDuplicate, handleExport, handleShare, setConfirmDelId, setRenamingId, setRenameDraft, handleRename, setRenaming, saveFolder, setShowFolderEdit, setFolderDraft, refreshSummary, togglePin, setShareModalId, setShareModalEmail })} />
                </section>
              )}

              {recentCards.length > 0 && !search && !filterFolder && aiResults === null && (
                <section style={{ animation: 'fadeIn .4s ease both' }}>
                  <SH icon={<Clock size={13} className="text-blue-500" />} label="Recently Viewed" />
                  <div className="flex gap-3 mt-4 overflow-x-auto pb-2">
                    {recentCards.map((d, i) => <RecentChip key={d.id} dash={d} idx={i} onClick={() => openReport(d.id)} />)}
                  </div>
                </section>
              )}

              <section>
                <div className="flex items-center justify-between">
                  <SH
                    icon={<BarChart2 size={13} className="text-gray-400" />}
                    label={aiResults !== null ? `AI Results (${filtered.length})` : hasActiveFilters ? `Filtered (${filtered.length})` : `All Reports (${filtered.length})`}
                  />
                  {hasActiveFilters && (
                    <button onClick={() => { setSearch(''); setFilterFolder(''); setAiResults(null); setAiQuery('') }} className="text-xs text-blue-500 hover:underline flex items-center gap-1">
                      <X size={11} /> Clear filters
                    </button>
                  )}
                </div>
                {filtered.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-12 mt-4" style={{ animation: 'fadeIn .3s ease both' }}>
                    {aiResults !== null ? 'No reports match your query.' : `No reports match "${search || filterFolder}"`}
                  </p>
                ) : (
                  <CardGrid cards={filtered} viewMode={viewMode} idx0={pinnedCards.length} {...sharedProps({ aiSummaries, aiLoading, deletingId, confirmDelId, duplicating, renamingId, renameDraft, renaming, pinned, folders, showFolderEdit, folderDraft, openReport, handleDelete, handleDuplicate, handleExport, handleShare, setConfirmDelId, setRenamingId, setRenameDraft, handleRename, setRenaming, saveFolder, setShowFolderEdit, setFolderDraft, refreshSummary, togglePin, setShareModalId, setShareModalEmail })} />
                )}
              </section>
            </>
          )}
        </div>

        {showImport && <VlyImportZone importing={importing} onImport={handleImport} onClose={() => setShowImport(false)} />}

        {/* Share with Analyst modal */}
        {shareModalId && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => { setShareModalId(null); setShareModalEmail(''); setShareModalResult(null) }}>
            <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-sm mx-4" style={{ animation: 'popSpring .2s ease both' }} onClick={e => e.stopPropagation()}>
              <div className="flex items-center gap-2 mb-1">
                <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
                  <Users size={15} className="text-white" />
                </div>
                <h3 className="text-base font-bold text-gray-900">Share with Analyst</h3>
              </div>
              <p className="text-sm text-gray-500 mb-4 mt-1">
                Enter the email of the analyst. They must already have a Visually account.
              </p>
              <input
                autoFocus
                type="email"
                value={shareModalEmail}
                onChange={e => setShareModalEmail(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleShareWithAnalyst(shareModalEmail)}
                placeholder="analyst@company.com"
                className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 mb-3"
              />
              {shareModalResult && (
                <p className={`text-sm mb-3 flex items-center gap-1.5 ${shareModalResult.ok ? 'text-green-600' : 'text-red-600'}`}>
                  {shareModalResult.ok ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                  {shareModalResult.msg}
                </p>
              )}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleShareWithAnalyst(shareModalEmail)}
                  disabled={sharingAnalyst || !shareModalEmail.trim()}
                  className="flex-1 flex items-center justify-center gap-2 py-2.5 text-sm font-semibold text-white rounded-xl disabled:opacity-60 transition-all"
                  style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                >
                  {sharingAnalyst ? <Loader2 size={13} className="animate-spin" /> : <Users size={13} />}
                  {sharingAnalyst ? 'Sharing…' : 'Share Report'}
                </button>
                <button
                  onClick={() => { setShareModalId(null); setShareModalEmail(''); setShareModalResult(null) }}
                  className="px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-xl hover:bg-gray-50 transition-all"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ⌘K Palette */}
        {showPalette && (
          <>
            <div className="backdrop-enter fixed inset-0 z-40 bg-black/40 backdrop-blur-sm" onClick={() => setShowPalette(false)} />
            <div className="palette-enter fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 bg-white rounded-2xl shadow-2xl border border-gray-100 overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100">
                <Search size={16} className="text-gray-400" />
                <input autoFocus value={paletteQuery} onChange={e => setPaletteQuery(e.target.value)} placeholder="Open a report…" className="flex-1 text-sm text-gray-900 outline-none" />
                <kbd className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">ESC</kbd>
              </div>
              <div className="max-h-72 overflow-auto">
                {paletteItems.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">No reports found</p>
                ) : paletteItems.map(d => {
                  const g = getGrad(d.theme)
                  return (
                    <button key={d.id} onClick={() => { openReport(d.id); setShowPalette(false) }}
                      className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors text-left"
                    >
                      <div className="w-6 h-6 rounded-lg flex-shrink-0" style={{ background: `linear-gradient(135deg, ${g.from}, ${g.to})` }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">{d.name}</p>
                        <p className="text-[10px] text-gray-400">{d.widget_count} charts · {relativeDate(d.updated_at)}</p>
                      </div>
                      <ArrowRight size={13} className="text-gray-300 flex-shrink-0" />
                    </button>
                  )
                })}
              </div>
              <div className="px-4 py-2.5 border-t border-gray-50 flex items-center gap-4 text-[10px] text-gray-400">
                <span>↵ open</span><span>ESC close</span>
                <span className="ml-auto">{dashboards.length} reports</span>
              </div>
            </div>
          </>
        )}

        {showSortMenu && <div className="fixed inset-0 z-20" onClick={() => setShowSortMenu(false)} />}
      </div>
    </>
  )
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function sharedProps(p: any) {
  return {
    aiSummaries: p.aiSummaries, aiLoading: p.aiLoading,
    deletingId: p.deletingId, confirmDelId: p.confirmDelId,
    duplicating: p.duplicating,
    renamingId: p.renamingId, renameDraft: p.renameDraft, renaming: p.renaming,
    pinned: p.pinned, folders: p.folders,
    showFolderEdit: p.showFolderEdit, folderDraft: p.folderDraft,
    onOpen: p.openReport, onExport: p.handleExport, onShare: p.handleShare,
    onTogglePin: p.togglePin, onDelete: p.handleDelete, onDuplicate: p.handleDuplicate,
    onCancelDel: () => p.setConfirmDelId(null),
    onStartRename: (id: string, name: string) => { p.setRenamingId(id); p.setRenameDraft(name) },
    onRename: p.handleRename,
    onRenameChange: p.setRenameDraft,
    onRefreshAi: p.refreshSummary,
    onEditFolder: (id: string, cur: string) => { p.setShowFolderEdit(id); p.setFolderDraft(cur ?? '') },
    onSaveFolder: p.saveFolder,
    onFolderDraftChange: p.setFolderDraft,
    onOpenShareModal: (id: string) => { p.setShareModalId(id); p.setShareModalEmail('') },
  }
}

function SH({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{label}</h2>
    </div>
  )
}

function CardGrid({ cards, viewMode, idx0, ...props }: any) {
  if (viewMode === 'list') {
    return (
      <div className="mt-4 rounded-xl border border-gray-100 overflow-hidden bg-white divide-y divide-gray-50">
        {cards.map((d: DashCard, i: number) => <ListRow key={d.id} dash={d} idx={idx0 + i} {...props} />)}
      </div>
    )
  }
  return (
    <div className="grid gap-5 mt-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
      {cards.map((d: DashCard, i: number) => <FlipCard key={d.id} dash={d} idx={idx0 + i} {...props} />)}
    </div>
  )
}

// ─── Recent Chip ──────────────────────────────────────────────────────────────
function RecentChip({ dash, idx, onClick }: { dash: DashCard; idx: number; onClick: () => void }) {
  const [hov, setHov] = useState(false)
  const g = getGrad(dash.theme)
  return (
    <button onClick={onClick} onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      className="flex-shrink-0 w-44 rounded-xl border border-gray-100 overflow-hidden text-left bg-white"
      style={{ animation: `slideUp .38s ${idx * 50}ms cubic-bezier(.21,1.02,.73,1) both`, transform: hov ? 'translateY(-4px)' : '', boxShadow: hov ? '0 10px 28px -8px rgba(0,0,0,0.14)' : '0 1px 3px rgba(0,0,0,0.05)', transition: 'transform .2s cubic-bezier(.34,1.56,.64,1), box-shadow .2s ease' }}
    >
      <div className="h-1.5" style={{ background: `linear-gradient(90deg, ${g.from}, ${g.to})` }} />
      <div className="p-3">
        <p className="text-xs font-semibold text-gray-900 truncate">{dash.name}</p>
        <p className="text-[10px] text-gray-400 mt-0.5">{dash.widget_count} charts · {relativeDate(dash.updated_at)}</p>
      </div>
    </button>
  )
}

// ─── List Row ─────────────────────────────────────────────────────────────────
function ListRow({ dash, idx, aiSummaries, onOpen, onExport, onShare, onDelete, onDuplicate, confirmDelId, deletingId, duplicating, onCancelDel, onOpenShareModal }: any) {
  const [hov, setHov] = useState(false)
  const g = getGrad(dash.theme)
  const stale = isStale(dash.updated_at)
  return (
    <div
      onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      className="flex items-center gap-4 px-4 py-3 cursor-pointer transition-colors"
      style={{ background: hov ? '#F8FAFF' : 'white', animation: `slideUp .35s ${idx * 40}ms ease both` }}
      onClick={() => onOpen(dash.id)}
    >
      <div className="w-2 h-8 rounded-full flex-shrink-0" style={{ background: `linear-gradient(180deg, ${g.from}, ${g.to})` }} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-900 truncate">{dash.name}</span>
          {stale && <span className="text-[10px] font-medium text-amber-600 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded-full flex items-center gap-0.5"><AlertTriangle size={8} /> Stale</span>}
          {dash.has_schedule && <span className="text-[10px] font-medium text-blue-600 bg-blue-50 border border-blue-200 px-1.5 py-0.5 rounded-full">⏰ Scheduled</span>}
        </div>
        {aiSummaries[dash.id] ? (
          <p className="text-[11px] text-gray-500 truncate mt-0.5">{aiSummaries[dash.id]}</p>
        ) : dash.description ? (
          <p className="text-[11px] text-gray-400 truncate mt-0.5">{dash.description}</p>
        ) : null}
      </div>
      <span className="text-[10px] text-gray-400 flex-shrink-0">{dash.widget_count} charts</span>
      <span className="text-[10px] text-gray-400 flex-shrink-0">{relativeDate(dash.updated_at)}</span>
      <div className="flex items-center gap-1 flex-shrink-0" onClick={e => e.stopPropagation()}>
        <button onClick={() => onOpenShareModal(dash.id)} className="p-1.5 text-gray-400 hover:text-purple-500 rounded-lg hover:bg-purple-50 transition-all" title="Share with analyst"><Users size={13} /></button>
        <button onClick={() => onShare(dash.id)} className="p-1.5 text-gray-400 hover:text-blue-500 rounded-lg hover:bg-blue-50 transition-all"><Share2 size={13} /></button>
        <button onClick={() => onExport(dash.id)} className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-50 transition-all"><Download size={13} /></button>
        <button onClick={() => onDuplicate(dash.id)} disabled={duplicating === dash.id} className="p-1.5 text-gray-400 hover:text-green-600 rounded-lg hover:bg-green-50 transition-all">
          {duplicating === dash.id ? <Loader2 size={13} className="animate-spin" /> : <Copy size={13} />}
        </button>
        {confirmDelId === dash.id ? (
          <div className="flex gap-1" style={{ animation: 'popSpring .2s ease both' }}>
            <button onClick={() => onDelete(dash.id)} disabled={deletingId === dash.id} className="px-2 py-1 text-[10px] font-bold bg-red-500 text-white rounded-lg">
              {deletingId === dash.id ? <Loader2 size={9} className="animate-spin" /> : 'Delete?'}
            </button>
            <button onClick={onCancelDel} className="px-2 py-1 text-[10px] text-gray-500 border border-gray-200 rounded-lg">No</button>
          </div>
        ) : (
          <button onClick={() => onDelete(dash.id)} className="p-1.5 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-all"><Trash2 size={13} /></button>
        )}
      </div>
    </div>
  )
}

// ─── Flip Card ────────────────────────────────────────────────────────────────
function FlipCard({ dash, idx, aiSummaries, aiLoading, deletingId, confirmDelId, duplicating, renamingId, renameDraft, renaming, pinned, folders, showFolderEdit, folderDraft, onOpen, onExport, onShare, onTogglePin, onDelete, onDuplicate, onCancelDel, onStartRename, onRename, onRenameChange, onRefreshAi, onEditFolder, onSaveFolder, onFolderDraftChange, onOpenShareModal }: any) {
  const [flipped,  setFlipped]  = useState(false)
  const renameRef = useRef<HTMLInputElement>(null)
  const g         = getGrad(dash.theme)
  const stale     = isStale(dash.updated_at)
  const isDeleting = deletingId === dash.id
  const isConfirm  = confirmDelId === dash.id
  const isDupl     = duplicating === dash.id
  const isRenaming = renamingId === dash.id
  const folder     = folders[dash.id]
  const summary    = aiSummaries[dash.id]
  const loading    = aiLoading[dash.id]

  useEffect(() => { if (isRenaming) setTimeout(() => renameRef.current?.focus(), 30) }, [isRenaming])

  return (
    <div
      style={{
        perspective: '1200px',
        height: 260,
        animation: `slideUp .42s ${idx * 50}ms cubic-bezier(.21,1.02,.73,1) both`,
      }}
      onMouseEnter={() => setFlipped(true)}
      onMouseLeave={() => setFlipped(false)}
    >
      <div style={{
        position: 'relative', width: '100%', height: '100%',
        transformStyle: 'preserve-3d',
        transform: flipped ? 'rotateY(180deg)' : 'rotateY(0deg)',
        transition: 'transform .55s cubic-bezier(.4,0,.2,1)',
      }}>

        {/* ── FRONT ── */}
        <div style={{
          position: 'absolute', inset: 0,
          backfaceVisibility: 'hidden',
          WebkitBackfaceVisibility: 'hidden',
          borderRadius: 16,
          overflow: 'hidden',
          background: 'white',
          border: '1px solid #f1f5f9',
          boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
          display: 'flex', flexDirection: 'column',
        }}>
          {/* Gradient header */}
          <div className="relative px-4 pt-3.5 pb-3 flex-shrink-0" style={{ background: `linear-gradient(135deg, ${g.from}, ${g.to})`, minHeight: 88 }}>
            {isRenaming ? (
              <input
                ref={renameRef}
                value={renameDraft}
                onChange={e => onRenameChange(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') onRename(dash.id)
                  if (e.key === 'Escape') onStartRename(null, '')
                }}
                onBlur={() => onRename(dash.id)}
                onClick={e => e.stopPropagation()}
                className="w-full bg-white/20 text-white font-bold text-sm rounded-lg px-2 py-0.5 outline-none border border-white/50 pr-16"
              />
            ) : (
              <h3
                onDoubleClick={e => { e.stopPropagation(); onStartRename(dash.id, dash.name) }}
                className="text-[15px] font-bold text-white leading-snug pr-16 cursor-text select-none"
                style={{ textShadow: '0 1px 4px rgba(0,0,0,0.28)' }}
                title="Double-click to rename"
              >
                {dash.name}
              </h3>
            )}

            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <span className="text-[10px] text-white/65 flex items-center gap-0.5"><BarChart2 size={9} /> {dash.widget_count} charts</span>
              <span className="text-[10px] text-white/65 flex items-center gap-0.5"><Clock size={9} /> {relativeDate(dash.updated_at)}</span>
              {stale && <span className="text-[10px] font-medium text-amber-300 bg-amber-400/20 px-1.5 py-0.5 rounded-full flex items-center gap-0.5"><AlertTriangle size={8} /> Stale</span>}
              {dash.has_schedule && <span className="text-[10px] text-white/70 bg-white/15 px-1.5 py-0.5 rounded-full">⏰ Auto</span>}
              {folder && <span className="text-[10px] text-white/80 bg-white/15 px-1.5 py-0.5 rounded-full flex items-center gap-0.5"><Folder size={8} /> {folder}</span>}
            </div>

            {/* Top-right action cluster */}
            <div className="absolute top-2.5 right-2.5 flex items-center gap-1" onClick={e => e.stopPropagation()}>
              {renaming && isRenaming && <Loader2 size={10} className="animate-spin text-white/70" />}
              <button
                onClick={() => onTogglePin(dash.id)}
                className="p-1.5 rounded-lg transition-all"
                style={{ background: 'rgba(0,0,0,0.22)', color: pinned.includes(dash.id) ? '#fcd34d' : 'rgba(255,255,255,0.55)' }}
                title={pinned.includes(dash.id) ? 'Unpin' : 'Pin'}
              >
                {pinned.includes(dash.id) ? <Pin size={10} fill="currentColor" /> : <PinOff size={10} />}
              </button>
              {isConfirm ? (
                <div className="flex items-center gap-1" style={{ animation: 'popSpring .2s ease both' }}>
                  <button onClick={() => onDelete(dash.id)} disabled={isDeleting} className="px-2 py-0.5 rounded-lg text-[10px] font-bold bg-red-500 text-white hover:bg-red-600 flex items-center gap-0.5">
                    {isDeleting ? <Loader2 size={9} className="animate-spin" /> : 'Confirm?'}
                  </button>
                  <button onClick={() => onCancelDel()} className="px-1.5 py-0.5 rounded-lg text-[10px] font-medium bg-white/20 text-white hover:bg-white/30">No</button>
                </div>
              ) : (
                <button onClick={() => onDelete(dash.id)} className="p-1.5 rounded-lg" style={{ background: 'rgba(0,0,0,0.22)', color: 'rgba(255,255,255,0.5)' }} title="Delete">
                  <Trash2 size={10} />
                </button>
              )}
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 px-4 pt-3 pb-3 flex flex-col gap-2">
            {/* AI snippet */}
            <div className="flex-1">
              {loading ? (
                <div className="space-y-2 pt-1">
                  <div className="shimmer-line h-2.5 w-full" /><div className="shimmer-line h-2.5 w-3/4" />
                </div>
              ) : summary ? (
                <p className="text-[11px] text-gray-600 leading-relaxed line-clamp-2">{summary}</p>
              ) : (
                <p className="text-[11px] text-gray-400 italic">{dash.description || 'Hover to see AI description'}</p>
              )}
            </div>

            {/* Folder + actions */}
            {showFolderEdit === dash.id ? (
              <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                <input
                  autoFocus value={folderDraft} onChange={e => onFolderDraftChange(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') onSaveFolder(dash.id, folderDraft); if (e.key === 'Escape') onEditFolder(null, '') }}
                  placeholder="Folder name…"
                  className="flex-1 text-xs px-2 py-1 border border-indigo-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-400"
                />
                <button onClick={() => onSaveFolder(dash.id, folderDraft)} className="text-[10px] px-2 py-1 bg-indigo-600 text-white rounded-lg">Save</button>
                <button onClick={() => onSaveFolder(dash.id, '')} className="text-[10px] px-1.5 py-1 text-gray-500 border border-gray-200 rounded-lg">Clear</button>
              </div>
            ) : (
              <div className="flex items-center justify-between" onClick={e => e.stopPropagation()}>
                <button onClick={() => onEditFolder(dash.id, folder)} className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-indigo-600 transition-colors">
                  <Tag size={9} /> {folder || 'Add to folder'}
                </button>
                <div className="flex items-center gap-1">
                  <SmallBtn onClick={() => onOpenShareModal(dash.id)} title="Share with analyst"><Users size={12} /></SmallBtn>
                  <SmallBtn onClick={() => onShare(dash.id)} title="Copy share link"><Share2 size={12} /></SmallBtn>
                  <SmallBtn onClick={() => onExport(dash.id)} title="Export"><Download size={12} /></SmallBtn>
                  <SmallBtn onClick={() => onDuplicate(dash.id)} title="Duplicate" disabled={isDupl}>
                    {isDupl ? <Loader2 size={12} className="animate-spin" /> : <Copy size={12} />}
                  </SmallBtn>
                  {!summary && (
                    <SmallBtn onClick={() => onRefreshAi(dash.id)} title="Generate AI summary">
                      <Sparkles size={12} />
                    </SmallBtn>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── BACK ── click anywhere to open */}
        <div
          onClick={() => onOpen(dash.id)}
          style={{
            position: 'absolute', inset: 0,
            backfaceVisibility: 'hidden',
            WebkitBackfaceVisibility: 'hidden',
            transform: 'rotateY(180deg)',
            borderRadius: 16,
            overflow: 'hidden',
            background: `linear-gradient(160deg, ${g.from} 0%, ${g.to} 100%)`,
            cursor: 'pointer',
            display: 'flex', flexDirection: 'column',
          }}
        >
          {/* Top strip with name */}
          <div className="px-4 pt-4 pb-2 flex-shrink-0 border-b border-white/10">
            <p className="text-[11px] font-semibold text-white/60 uppercase tracking-widest">AI Summary</p>
            <h3 className="text-[15px] font-bold text-white leading-tight mt-0.5" style={{ textShadow: '0 1px 4px rgba(0,0,0,0.3)' }}>{dash.name}</h3>
          </div>

          {/* Full AI description */}
          <div className="flex-1 px-4 py-3 overflow-hidden">
            {loading ? (
              <div className="space-y-2.5 pt-1">
                {[1, 0.8, 0.9, 0.7].map((w, i) => (
                  <div key={i} className="h-2.5 rounded-full" style={{ width: `${w * 100}%`, background: 'rgba(255,255,255,0.2)', animation: `shimmer 1.5s ${i * 200}ms ease-in-out infinite`, backgroundSize: '200px 100%' }} />
                ))}
              </div>
            ) : summary ? (
              <p className="text-[12px] text-white/90 leading-relaxed">{summary}</p>
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-2 opacity-70">
                <Sparkles size={20} className="text-white/60" />
                <p className="text-[11px] text-white/60 text-center">No AI summary yet.<br/>Generating soon…</p>
              </div>
            )}
          </div>

          {/* Meta + click hint */}
          <div className="px-4 pb-4 flex-shrink-0 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-[10px] text-white/60 flex items-center gap-0.5"><BarChart2 size={9} /> {dash.widget_count} charts</span>
              <span className="text-[10px] text-white/60 flex items-center gap-0.5"><Clock size={9} /> {relativeDate(dash.updated_at)}</span>
              {stale && <span className="text-[10px] text-amber-300 bg-amber-400/20 px-1.5 py-0.5 rounded-full flex items-center gap-0.5"><AlertTriangle size={8} /> Stale</span>}
            </div>
            <div className="flip-hint flex items-center gap-1 text-[11px] font-semibold text-white/80">
              Open <ArrowRight size={11} />
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}

function SmallBtn({ onClick, title, disabled, children }: any) {
  return (
    <button
      onClick={e => { e.stopPropagation(); onClick() }}
      title={title}
      disabled={disabled}
      className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-all disabled:opacity-40"
    >
      {children}
    </button>
  )
}
