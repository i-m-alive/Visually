'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { dashboardApi, shareApi, aiInsightsApi } from '@/lib/api'
import {
  LayoutDashboard, Loader2, AlertCircle, Search, X,
  BarChart2, Clock, RefreshCw, Share2, Heart, TrendingUp, Zap,
  ChevronRight, Download, Sparkles, UserCircle2,
} from 'lucide-react'
import { VisuallReportLoader } from '@/components/canvas/VisuallReportLoader'

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

export default function EndUserDashboardPage() {
  const [dashboards, setDashboards] = useState<DashCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [viewingId, setViewingId] = useState<string | null>(null)
  const [aiSummaries, setAiSummaries] = useState<Record<string, string>>({})
  const [aiLoading, setAiLoading] = useState<Record<string, boolean>>({})
  const [shareToast, setShareToast] = useState<string | null>(null)
  const loadedSummaries = useRef(new Set<string>())

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

  useEffect(() => {
    if (dashboards.length === 0) return
    const toLoad = dashboards.filter(d => !loadedSummaries.current.has(d.id))
    toLoad.forEach(async (d, i) => {
      await new Promise(r => setTimeout(r, i * 300))
      if (loadedSummaries.current.has(d.id)) return
      loadedSummaries.current.add(d.id)
      setAiLoading(prev => ({ ...prev, [d.id]: true }))
      try {
        const resp = await aiInsightsApi.summary(d.id)
        setAiSummaries(prev => ({ ...prev, [d.id]: resp.data.summary }))
      } catch { /* ignore */ }
      finally { setAiLoading(prev => ({ ...prev, [d.id]: false })) }
    })
  }, [dashboards])

  const handleShare = async (id: string) => {
    try {
      const resp = await shareApi.create(id, { mode: 'view', expires_days: 30 })
      const token = resp.data?.token ?? resp.data?.id ?? ''
      const url = `${window.location.origin}/share/canvas/${token}`
      await navigator.clipboard.writeText(url)
      setShareToast('Share link copied!')
      setTimeout(() => setShareToast(null), 2500)
    } catch { setShareToast('Failed to create share link'); setTimeout(() => setShareToast(null), 2500) }
  }

  const filtered = dashboards.filter(d => {
    if (!search) return true
    const q = search.toLowerCase()
    return d.name.toLowerCase().includes(q) || d.project_name.toLowerCase().includes(q)
  })

  if (viewingId) {
    return (
      <VisuallReportLoader
        dashboardId={viewingId}
        canEdit={false}
        onClose={() => setViewingId(null)}
      />
    )
  }

  return (
    <div className="flex flex-col h-full" style={{ background: '#F8FAFC' }}>

      {/* Toast */}
      {shareToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-5 py-3 bg-gray-900 text-white text-sm font-medium rounded-xl shadow-xl">
          {shareToast}
        </div>
      )}

      {/* Header */}
      <div className="px-6 py-4 bg-white border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold font-display text-gray-900">My Reports</h1>
            <p className="text-sm text-gray-500 mt-0.5">Reports shared with you by your team</p>
          </div>
          <button
            onClick={load}
            className="p-2 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="relative mt-3 max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search reports…"
            className="w-full pl-9 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {loading && (
          <div className="flex items-center justify-center h-48">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <AlertCircle className="w-10 h-10 text-red-400" />
            <p className="text-sm text-gray-500">{error}</p>
            <button onClick={load} className="btn-secondary text-sm flex items-center gap-2">
              <RefreshCw size={13} /> Retry
            </button>
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 gap-4 text-gray-400">
            <UserCircle2 className="w-12 h-12 text-gray-300" />
            <div className="text-center">
              <p className="font-medium text-gray-600">
                {search ? 'No reports match your search.' : 'No reports shared with you yet.'}
              </p>
              {!search && (
                <p className="text-sm mt-1 text-gray-400">
                  Ask your builder to share a report with your email address.
                </p>
              )}
            </div>
          </div>
        )}

        {!loading && !error && filtered.length > 0 && (
          <>
            <p className="text-xs text-gray-400 mb-4 font-medium uppercase tracking-wide">
              {filtered.length} report{filtered.length !== 1 ? 's' : ''}
            </p>
            <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
              {filtered.map(dash => {
                const colors = THEME_COLORS[dash.theme] ?? THEME_COLORS.frost
                const health = computeHealthScore(dash.widget_count)
                const updatedDate = new Date(
                  (dash.updated_at.includes('T') ? dash.updated_at : dash.updated_at.replace(' ', 'T'))
                  + (/[Zz]|[+-]\d{2}/.test(dash.updated_at) ? '' : 'Z')
                ).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

                return (
                  <div
                    key={dash.id}
                    className="group relative rounded-2xl border border-gray-100 overflow-hidden bg-white hover:shadow-xl hover:-translate-y-1 transition-all duration-200"
                  >
                    {/* Gradient header */}
                    <div className="h-20 relative flex items-end px-4 pb-3" style={{ background: `linear-gradient(135deg, ${colors.border}, ${colors.accent})` }}>
                      <div className="flex items-center gap-1.5 opacity-60">
                        <BarChart2 size={14} className="text-white" />
                        <TrendingUp size={14} className="text-white" />
                        <Zap size={12} className="text-white" />
                      </div>
                      <div
                        className="absolute top-3 right-3 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold"
                        style={{ background: 'rgba(0,0,0,0.4)', color: health >= 80 ? '#4ADE80' : health >= 55 ? '#FCD34D' : '#F87171' }}
                      >
                        <Heart size={9} fill="currentColor" />
                        {health}
                      </div>
                    </div>

                    <div className="p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <h3 className="text-sm font-bold text-gray-900 truncate">{dash.name}</h3>
                          <p className="text-xs text-gray-500 mt-0.5 truncate">{dash.project_name}</p>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] text-gray-400 flex items-center gap-0.5">
                              <BarChart2 size={9} /> {dash.widget_count} charts
                            </span>
                            <span className="text-[10px] text-gray-400 flex items-center gap-0.5">
                              <Clock size={9} /> {updatedDate}
                            </span>
                          </div>
                        </div>
                        <ChevronRight size={14} className="text-gray-300 group-hover:text-blue-400 transition-colors mt-1 flex-shrink-0" />
                      </div>

                      {/* AI Briefing */}
                      <div className="mt-3 min-h-[36px]">
                        {aiLoading[dash.id] ? (
                          <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
                            <Loader2 size={10} className="animate-spin text-purple-400" />
                            <span>AI briefing…</span>
                          </div>
                        ) : aiSummaries[dash.id] ? (
                          <div className="flex items-start gap-1.5">
                            <Sparkles size={11} className="text-purple-500 mt-0.5 flex-shrink-0" />
                            <p className="text-[11px] text-gray-600 leading-relaxed line-clamp-2">{aiSummaries[dash.id]}</p>
                          </div>
                        ) : dash.description ? (
                          <p className="text-[11px] text-gray-500 line-clamp-2">{dash.description}</p>
                        ) : null}
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-2 mt-4 pt-3 border-t border-gray-50">
                        <button
                          onClick={() => setViewingId(dash.id)}
                          className="flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-semibold text-white rounded-xl transition-colors"
                          style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                        >
                          <Sparkles size={12} /> Open Report
                        </button>
                        <button
                          onClick={e => { e.stopPropagation(); handleShare(dash.id) }}
                          className="p-2 text-gray-400 hover:text-blue-500 rounded-lg hover:bg-blue-50 transition-colors border border-gray-100"
                          title="Copy share link"
                        >
                          <Share2 size={14} />
                        </button>
                      </div>
                    </div>

                    <div className="absolute inset-0 rounded-2xl border-2 border-transparent group-hover:border-blue-400 transition-colors pointer-events-none" />
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
