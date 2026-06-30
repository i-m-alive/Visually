'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Send, Loader2, AlertCircle, TrendingUp, MessageSquare, X, LayoutDashboard,
  Plus, Trash2, Edit2, Check, ChevronLeft, ChevronRight, Code, Download, RefreshCw,
  Copy, Volume2, VolumeX, FileText, Star, Maximize2, Square, Search, ChevronDown,
} from 'lucide-react'
import { agentApi, querySessionApi, streamChat, type ConversationTurn } from '@/lib/api'
import { usePipelineSocket } from '@/hooks/usePipelineSocket'
import { usePipelineStore } from '@/stores/pipelineStore'
import { IntentStatusBar } from '@/components/pipeline/IntentStatusBar'
import { ReasoningDrawer } from '@/components/pipeline/ReasoningDrawer'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult, DashboardResult, CandidateResult } from '@/stores/pipelineStore'

interface Message {
  id: string
  serverId?: string
  parentId?: string | null
  type: 'user' | 'agent'
  content: string
  jobId?: string
  loading?: boolean
  chartResult?: ChartResult
  dashboardResult?: DashboardResult
  // Present when multiple candidate tables scored similarly — user picks one.
  candidates?: CandidateResult[]
  candidatesMessage?: string
  error?: string
  jobType?: string
  created_at?: string
}

interface TreeNode {
  id: string
  parent_id: string | null
  role: 'user' | 'assistant'
  content: string
  result?: { kind?: string; chartResult?: ChartResult; dashboardResult?: DashboardResult; error?: string } | null
  job_id?: string | null
  created_at?: string | null
}

interface SessionMeta { id: string; title: string; updated_at?: string; message_count: number }

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  loading?: boolean
  inlineChart?: ChartResult
  candidates?: CandidateResult[]
  candidatesMessage?: string
}

// ── tree helpers ──────────────────────────────────────────────────────────────
function pathToLeaf(tree: Record<string, TreeNode>, leafId: string | null): TreeNode[] {
  const path: TreeNode[] = []
  let cur = leafId
  const guard = new Set<string>()
  while (cur && tree[cur] && !guard.has(cur)) {
    guard.add(cur)
    path.push(tree[cur])
    cur = tree[cur].parent_id
  }
  return path.reverse()
}

function deepestLeaf(tree: Record<string, TreeNode>, startId: string): string {
  let cur = startId
  const guard = new Set<string>()
  while (!guard.has(cur)) {
    guard.add(cur)
    const children = Object.values(tree)
      .filter((n) => n.parent_id === cur)
      .sort((a, b) => (a.created_at || '') < (b.created_at || '') ? -1 : 1)
    if (children.length === 0) return cur
    cur = children[children.length - 1].id
  }
  return cur
}

function nodeToMessage(n: TreeNode): Message {
  if (n.role === 'user') {
    return { id: n.id, serverId: n.id, parentId: n.parent_id, type: 'user', content: n.content, created_at: n.created_at ?? undefined }
  }
  const r = n.result || {}
  return {
    id: n.id, serverId: n.id, parentId: n.parent_id, type: 'agent', content: n.content,
    chartResult: r.kind === 'chart' ? r.chartResult : undefined,
    dashboardResult: r.kind === 'dashboard' ? r.dashboardResult : undefined,
    error: r.kind === 'error' ? r.error : undefined,
    created_at: n.created_at ?? undefined,
  }
}

function groupSessionsByDate(sessions: SessionMeta[]): Array<{ label: string; items: SessionMeta[] }> {
  const now = Date.now()
  const todayStr = new Date().toDateString()
  const yesterdayStr = new Date(now - 86400000).toDateString()
  const weekAgo = now - 7 * 86400000
  const buckets: Record<string, SessionMeta[]> = { Today: [], Yesterday: [], 'This week': [], Older: [] }
  for (const s of sessions) {
    const d = new Date(s.updated_at || '')
    const ds = d.toDateString()
    if (ds === todayStr) buckets.Today.push(s)
    else if (ds === yesterdayStr) buckets.Yesterday.push(s)
    else if (d.getTime() > weekAgo) buckets['This week'].push(s)
    else buckets.Older.push(s)
  }
  return Object.entries(buckets)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }))
}

function downloadCsv(rows: Record<string, unknown>[], columns: string[], filename: string) {
  const header = columns.join(',')
  const body = rows.map((r) => columns.map((c) => JSON.stringify(r[c] ?? '')).join(',')).join('\n')
  const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename.replace(/[^a-z0-9_\-]/gi, '_') + '.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

function chartKey(c: ChartResult): string {
  return (c.title || '') + '||' + (c.sql || '')
}

function relativeTime(iso?: string): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60000) return 'just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const ALL_CHART_TYPES = ['bar', 'line', 'area', 'pie', 'scatter', 'table'] as const
type ChartTypeOption = typeof ALL_CHART_TYPES[number]

function compatibleChartTypes(cr: ChartResult): ChartTypeOption[] {
  const rowCount = cr.chart_data?.rows?.length ?? 0
  return ALL_CHART_TYPES.filter((t) => {
    if (t === 'pie' && rowCount > 12) return false
    if (t === 'scatter' && (cr.chart_data?.columns?.length ?? 0) < 2) return false
    return true
  })
}

// ── Public API ────────────────────────────────────────────────────────────────
export interface QueryChatPanelProps {
  projectId: string
  /** Optional: label shown in the status bar, e.g. "PostgreSQL · my-db" */
  connectionLabel?: string
  /** Optional: callback to let the parent handle connection-switch UI */
  onSwitchConnection?: () => void
}

export function QueryChatPanel({ projectId, connectionLabel, onSwitchConnection }: QueryChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const isAtBottomRef = useRef(true)
  const jobs = usePipelineStore((s) => s.jobs)
  const setStoreActiveJob = usePipelineStore((s) => s.setActiveJob)

  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [tree, setTree] = useState<Record<string, TreeNode>>({})
  const [activeLeafId, setActiveLeafId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameText, setRenameText] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  const sessionIdRef = useRef<string | null>(null)
  const activeLeafRef = useRef<string | null>(null)
  const pendingParentRef = useRef<string | null>(null)
  const persistedRef = useRef<Set<string>>(new Set())

  const [sidebarSearch, setSidebarSearch] = useState('')
  const [outputMode, setOutputMode] = useState<'auto' | 'chart' | 'table' | 'text'>('auto')
  const [showScrollFab, setShowScrollFab] = useState(false)
  const [hasNewMsg, setHasNewMsg] = useState(false)
  const [expandedChart, setExpandedChart] = useState<ChartResult | null>(null)
  const [chartTypeOverrides, setChartTypeOverrides] = useState<Record<string, string>>({})

  const [chatOpen, setChatOpen] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chartSessionMap, setChartSessionMap] = useState<Map<string, string>>(new Map())
  const [activeChartContext, setActiveChartContext] = useState<ChartResult | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)
  const chatAbortRef = useRef<AbortController | null>(null)
  const activeChartContextRef = useRef<ChartResult | null>(null)

  const [starredIds, setStarredIds] = useState<Set<string>>(new Set())
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [speakingId, setSpeakingId] = useState<string | null>(null)

  usePipelineSocket(activeJobId)

  useEffect(() => {
    return () => {
      chatAbortRef.current?.abort()
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel()
    }
  }, [])

  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight
      const atBottom = dist < 80
      isAtBottomRef.current = atBottom
      setShowScrollFab(!atBottom)
      if (atBottom) setHasNewMsg(false)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey
      if (meta && e.key === 'k') { e.preventDefault(); newChat() }
      if (meta && e.key === '.') { e.preventDefault(); if (submitting) cancelPipeline() }
      if (e.key === 'Escape' && editingId) setEditingId(null)
      if (e.key === 'Escape' && expandedChart) setExpandedChart(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitting, editingId, expandedChart])

  const refreshSessions = useCallback(async () => {
    try {
      const r = await querySessionApi.list(projectId)
      setSessions(r.data || [])
    } catch { /* ignore */ }
  }, [projectId])

  useEffect(() => { refreshSessions() }, [refreshSessions])

  const loadSession = useCallback(async (sid: string) => {
    try {
      const r = await querySessionApi.get(sid)
      const data = r.data
      const t: Record<string, TreeNode> = {}
      for (const n of (data.messages || [])) t[n.id] = n
      setTree(t)
      setActiveLeafId(data.active_leaf_id)
      activeLeafRef.current = data.active_leaf_id
      sessionIdRef.current = sid
      setActiveSessionId(sid)
      setMessages(pathToLeaf(t, data.active_leaf_id).map(nodeToMessage))
    } catch { /* ignore */ }
  }, [])

  const newChat = () => {
    setActiveSessionId(null); sessionIdRef.current = null
    setMessages([]); setTree({}); setActiveLeafId(null); activeLeafRef.current = null
    setEditingId(null)
  }

  const ensureSession = async (): Promise<string> => {
    if (sessionIdRef.current) return sessionIdRef.current
    const r = await querySessionApi.create(projectId)
    const sid = r.data.id
    sessionIdRef.current = sid; setActiveSessionId(sid)
    refreshSessions()
    return sid
  }

  const cancelPipeline = useCallback(() => {
    setActiveJobId(null); setSubmitting(false)
    setMessages((prev) => prev.map((m) => m.loading ? { ...m, loading: false, error: 'Cancelled' } : m))
  }, [])

  const autoResize = useCallback(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 140) + 'px'
  }, [])

  const runTurn = async (text: string, branchParentId?: string | null) => {
    if (!text.trim() || submitting) return
    setSubmitting(true); setEditingId(null)
    const sid = await ensureSession()
    const parentId = branchParentId !== undefined ? branchParentId : activeLeafRef.current

    let userServerId: string | undefined
    try {
      const r = await querySessionApi.addMessage(sid, { role: 'user', content: text, parent_id: parentId })
      userServerId = r.data.id
      setActiveLeafId(userServerId!); activeLeafRef.current = userServerId!
      refreshSessions()
    } catch { /* best-effort */ }
    pendingParentRef.current = userServerId || null

    const agentMsgId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), serverId: userServerId, parentId, type: 'user', content: text, created_at: new Date().toISOString() },
      { id: agentMsgId, type: 'agent', content: '', loading: true, jobId: '' },
    ])

    const conversationHistory: ConversationTurn[] = messages
      .filter((m) => !m.loading && (m.type === 'user' ? !!m.content : !!(m.chartResult || m.dashboardResult)))
      .slice(-6)
      .map((m): ConversationTurn => {
        if (m.type === 'user') return { role: 'user', content: m.content }
        const cr = m.chartResult
        return {
          role: 'assistant',
          content: cr?.narrative || cr?.title || (m.dashboardResult ? 'Dashboard with multiple charts' : ''),
          chart_title: cr?.title, sql: cr?.sql,
        }
      })

    try {
      const resp = await agentApi.submitIntent({
        text, project_id: projectId,
        conversation_history: conversationHistory.length > 0 ? conversationHistory : undefined,
        ...(outputMode !== 'auto' ? { output_mode: outputMode } : {}),
      })
      const jobId = resp.data.job_id
      const jobType = resp.data.job_type || 'SINGLE_VIZ'
      setActiveJobId(jobId); setStoreActiveJob(jobId)
      setMessages((prev) => prev.map((m) => m.id === agentMsgId ? { ...m, jobId, jobType } : m))
      usePipelineStore.getState().resetJob(jobId)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setMessages((prev) => prev.map((m) =>
        m.id === agentMsgId ? { ...m, loading: false, error: e.response?.data?.detail || 'Failed to start pipeline' } : m))
      setSubmitting(false)
    }
  }

  const handleSubmit = async () => {
    const text = input.trim()
    if (!text) return
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    await runTurn(text)
  }

  const finalizeTurn = useCallback(async (jid: string, job: { chartResult?: ChartResult; dashboardResult?: DashboardResult; error?: string }) => {
    const sid = sessionIdRef.current
    if (!sid) { setSubmitting(false); return }
    const content = job.chartResult?.narrative || job.error || ''
    const result = job.chartResult ? { kind: 'chart', chartResult: job.chartResult }
      : job.dashboardResult ? { kind: 'dashboard', dashboardResult: job.dashboardResult }
      : job.error ? { kind: 'error', error: job.error } : { kind: 'empty' }
    try {
      await querySessionApi.addMessage(sid, { role: 'assistant', content, parent_id: pendingParentRef.current, result, job_id: jid })
    } catch { /* ignore */ }
    await loadSession(sid)
    refreshSessions()
  }, [loadSession, refreshSessions])

  useEffect(() => {
    if (!activeJobId) return
    const job = jobs[activeJobId]
    if (!job) return
    const terminal = !!(job.chartResult || job.dashboardResult || job.error)
    setMessages((prev) => prev.map((m) => m.jobId === activeJobId
      ? {
          ...m,
          loading: !terminal,
          chartResult: job.chartResult,
          dashboardResult: job.dashboardResult,
          candidates: job.candidates,
          candidatesMessage: job.candidatesMessage,
          error: job.error,
        }
      : m))
    if (terminal && !persistedRef.current.has(activeJobId)) {
      persistedRef.current.add(activeJobId)
      const jid = activeJobId
      setActiveJobId(null); setSubmitting(false)
      void finalizeTurn(jid, job)
    }
  }, [jobs, activeJobId, finalizeTurn])

  useEffect(() => {
    if (isAtBottomRef.current) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    else setHasNewMsg(true)
  }, [messages])

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [chatMessages])

  const siblingsOf = (serverId?: string): TreeNode[] => {
    if (!serverId || !tree[serverId]) return []
    const node = tree[serverId]
    return Object.values(tree)
      .filter((n) => n.parent_id === node.parent_id && n.role === node.role)
      .sort((a, b) => (a.created_at || '') < (b.created_at || '') ? -1 : 1)
  }

  const switchSibling = async (serverId: string, dir: -1 | 1) => {
    const sibs = siblingsOf(serverId)
    if (sibs.length < 2) return
    const idx = sibs.findIndex((s) => s.id === serverId)
    const next = sibs[idx + dir]
    if (!next) return
    const sid = sessionIdRef.current
    if (!sid) return
    const leaf = deepestLeaf(tree, next.id)
    try { await querySessionApi.setActiveLeaf(sid, leaf) } catch { /* ignore */ }
    await loadSession(sid)
  }

  const startEdit = (msg: Message) => { setEditingId(msg.serverId || msg.id); setEditText(msg.content) }
  const submitEdit = async (msg: Message) => {
    const text = editText.trim()
    if (!text) return
    setEditingId(null)
    await runTurn(text, msg.parentId ?? null)
  }

  const regenerateFromAssistant = async (msg: Message) => {
    if (!msg.serverId || !tree[msg.serverId]) return
    const assistantNode = tree[msg.serverId]
    const userNode = assistantNode.parent_id ? tree[assistantNode.parent_id] : null
    if (!userNode || userNode.role !== 'user') return
    await runTurn(userNode.content, userNode.parent_id ?? null)
  }

  const deleteSession = async (sid: string) => {
    try { await querySessionApi.remove(sid) } catch { /* ignore */ }
    if (sid === sessionIdRef.current) newChat()
    refreshSessions()
  }

  const commitRename = async (sid: string) => {
    const t = renameText.trim(); setRenamingId(null)
    if (!t) return
    try { await querySessionApi.rename(sid, t) } catch { /* ignore */ }
    refreshSessions()
  }

  const openChatForChart = (chartResult: ChartResult) => {
    const prevKey = activeChartContext ? chartKey(activeChartContext) : null
    const newKey = chartKey(chartResult)
    const isNewChart = prevKey !== newKey
    setActiveChartContext(chartResult); activeChartContextRef.current = chartResult; setChatOpen(true)
    if (isNewChart) {
      chatAbortRef.current?.abort(); chatAbortRef.current = null
      setChatMessages([{ id: crypto.randomUUID(), role: 'assistant', content: `I can help you explore the "${chartResult.title}" chart. Ask me anything about the data, patterns, or trends.` }])
      setChatInput(''); setChatLoading(false)
    }
  }

  const handleChatSend = async () => {
    if (!chatInput.trim() || chatLoading) return
    const userText = chatInput.trim(); setChatInput(''); setChatLoading(true)
    const userMsgId = crypto.randomUUID(); const assistantMsgId = crypto.randomUUID()
    setChatMessages((prev) => [...prev, { id: userMsgId, role: 'user', content: userText }, { id: assistantMsgId, role: 'assistant', content: '', loading: true }])
    const ctx = activeChartContextRef.current
    let messageToSend = userText
    if (ctx) {
      const rowSample = ctx.chart_data?.rows?.slice(0, 8) ?? []
      messageToSend = `[Chart context: "${ctx.title}"${ctx.sql ? ` | SQL: ${ctx.sql}` : ''}${rowSample.length > 0 ? ` | Data sample: ${JSON.stringify(rowSample)}` : ''}]\nUser question: ${userText}`
    }
    const currentKey = ctx ? chartKey(ctx) : 'default'
    const abort = new AbortController(); chatAbortRef.current = abort
    let accumulated = ''
    try {
      await streamChat(
        { session_id: chartSessionMap.get(currentKey), message: messageToSend, project_id: projectId },
        {
          onText: (delta) => { accumulated += delta; setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, content: accumulated, loading: false } : m)) },
          onChart: (rawChart) => {
            const ic = rawChart as Record<string, unknown>
            const inlineChart: ChartResult = { chart_type: ic.chart_type as string, title: ic.title as string, sql: (ic.sql as string) || '', score: 1, low_confidence: false, x_axis_label: (ic.x_axis_label as string) || 'x', y_axis_label: (ic.y_axis_label as string) || 'y', table_used: '', chart_data: (ic.chart_data as ChartResult['chart_data']) || { rows: [], columns: [], labels: [], values: [] } }
            setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, inlineChart } : m))
          },
          onCandidates: (rawCandidates, msg) => {
            const candidates = rawCandidates as unknown as CandidateResult[]
            setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, candidates, candidatesMessage: msg } : m))
          },
          onAction: (action) => {
            const act = action as { action?: string; params?: { instruction?: string } }
            if (act.action === 'filter_widget' && act.params?.instruction) setInput(act.params.instruction)
          },
          onDone: (meta) => { setChartSessionMap((prev) => new Map(prev).set(currentKey, meta.session_id)); setChatLoading(false) },
          onError: () => { setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, content: 'Sorry, I could not process your request.', loading: false } : m)); setChatLoading(false) },
        },
        abort.signal,
      )
    } catch { if (!abort.signal.aborted) { setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, content: 'Sorry, I could not process your request.', loading: false } : m)); setChatLoading(false) } }
  }

  const closeChatPanel = () => { chatAbortRef.current?.abort(); chatAbortRef.current = null; setChatOpen(false) }
  const copyText = (text: string, id: string) => { navigator.clipboard.writeText(text).catch(() => {}); setCopiedId(id); setTimeout(() => setCopiedId((s) => (s === id ? null : s)), 1800) }
  const toggleStar = (id: string) => { setStarredIds((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next }) }
  const speakText = (text: string, id: string) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return
    window.speechSynthesis.cancel()
    if (speakingId === id) { setSpeakingId(null); return }
    const utt = new SpeechSynthesisUtterance(text)
    utt.rate = 1.0; utt.onend = () => setSpeakingId(null); utt.onerror = () => setSpeakingId(null)
    setSpeakingId(id); window.speechSynthesis.speak(utt)
  }
  const stopSpeaking = () => { if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel(); setSpeakingId(null) }

  const starredMessages = messages.filter((m) => starredIds.has(m.serverId || m.id))
  const sessionGroups = groupSessionsByDate(sessions)
  const filteredSessionGroups = sidebarSearch
    ? sessionGroups.map(({ label, items }) => ({ label, items: items.filter((s) => s.title.toLowerCase().includes(sidebarSearch.toLowerCase())) })).filter(({ items }) => items.length > 0)
    : sessionGroups

  return (
    <div className="flex h-full overflow-hidden">

      {/* ── History sidebar ── */}
      <aside className="w-56 flex-shrink-0 border-r border-gray-100 bg-gray-50 flex flex-col">
        <div className="p-2.5 pb-2">
          <button onClick={newChat} className="btn-primary w-full flex items-center justify-center gap-2 py-2 text-sm">
            <Plus size={14} /> New chat
          </button>
        </div>
        <div className="px-2.5 pb-2">
          <div className="relative">
            <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input value={sidebarSearch} onChange={(e) => setSidebarSearch(e.target.value)} placeholder="Search…" className="w-full pl-7 pr-3 py-1.5 text-xs bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-brand/30 focus:border-brand/40" />
          </div>
        </div>
        {starredMessages.length > 0 && (
          <div className="px-1.5 pb-2 border-b border-gray-200">
            <p className="px-2 py-1 text-[11px] uppercase tracking-wide text-amber-600 font-semibold flex items-center gap-1"><Star size={10} fill="currentColor" /> Starred</p>
            <div className="space-y-0.5">
              {starredMessages.map((m) => (
                <div key={m.serverId || m.id} className="flex items-start gap-1.5 rounded-lg px-2 py-1.5 cursor-pointer hover:bg-amber-50 text-xs text-gray-700" onClick={() => { const el = document.getElementById(`msg-${m.serverId || m.id}`); el?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>
                  <span className={`mt-0.5 flex-shrink-0 w-1.5 h-1.5 rounded-full ${m.type === 'user' ? 'bg-brand' : 'bg-green-500'}`} />
                  <span className="truncate leading-snug">{m.type === 'user' ? m.content : (m.chartResult?.title || m.chartResult?.narrative || 'AI response')}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-1.5 pb-3 space-y-0.5">
          {sessions.length === 0 && !sidebarSearch && <p className="px-2 text-xs text-gray-400 mt-2">No chats yet</p>}
          {sidebarSearch && filteredSessionGroups.length === 0 && <p className="px-2 text-xs text-gray-400 mt-2">No results</p>}
          {filteredSessionGroups.map(({ label, items }) => (
            <div key={label}>
              <p className="px-2 py-1 text-[11px] uppercase tracking-wide text-gray-400 mt-1">{label}</p>
              {items.map((s) => (
                <div key={s.id} className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 cursor-pointer text-xs ${s.id === activeSessionId ? 'bg-brand-light text-brand' : 'text-gray-700 hover:bg-gray-100'}`} onClick={() => s.id !== renamingId && loadSession(s.id)}>
                  {renamingId === s.id ? (
                    <input autoFocus value={renameText} onChange={(e) => setRenameText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(s.id); if (e.key === 'Escape') setRenamingId(null) }} onBlur={() => commitRename(s.id)} className="flex-1 bg-white border border-gray-200 rounded px-1 py-0.5 text-xs" onClick={(e) => e.stopPropagation()} />
                  ) : (
                    <>
                      <span className="flex-1 truncate">{s.title}</span>
                      <button onClick={(e) => { e.stopPropagation(); setRenamingId(s.id); setRenameText(s.title) }} className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600"><Edit2 size={11} /></button>
                      <button onClick={(e) => { e.stopPropagation(); deleteSession(s.id) }} className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500"><Trash2 size={11} /></button>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        {/* Connection label if provided */}
        {connectionLabel && (
          <div className="border-t border-gray-100 px-2.5 py-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
            <span className="text-[11px] text-gray-500 truncate flex-1">{connectionLabel}</span>
            {onSwitchConnection && (
              <button onClick={onSwitchConnection} className="text-[11px] text-brand hover:underline flex-shrink-0">Switch</button>
            )}
          </div>
        )}
      </aside>

      {/* ── Main + chat panel ── */}
      <div className="flex flex-1 overflow-hidden">
        <div className={`flex flex-col ${chatOpen ? 'flex-1' : 'w-full'} transition-all duration-300`}>
          <IntentStatusBar jobId={activeJobId} />

          <div className="relative flex-1 overflow-hidden">
            <div ref={scrollContainerRef} className="h-full overflow-y-auto p-4 space-y-4">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <TrendingUp size={40} className="text-brand-light mb-3" />
                  <h3 className="text-lg font-semibold font-display text-gray-700 mb-1.5">Ask anything about your data</h3>
                  <p className="text-gray-400 text-sm max-w-sm">Try a single chart, or ask for a full &quot;dashboard overview&quot;.</p>
                  <div className="mt-3 flex flex-wrap gap-2 justify-center">
                    {['Show monthly revenue trend', 'Top 10 products by sales', 'Give me a dashboard overview'].map((s) => (
                      <button key={s} onClick={() => setInput(s)} className="text-xs px-3 py-1.5 bg-brand-light text-brand rounded-full hover:bg-blue-100 transition-colors">{s}</button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((msg) => {
                const sibs = msg.type === 'user' ? siblingsOf(msg.serverId) : []
                const sibIdx = sibs.findIndex((s) => s.id === msg.serverId)
                return (
                  <div key={msg.id} id={`msg-${msg.serverId || msg.id}`} className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}>
                    {msg.type === 'user' ? (
                      editingId === (msg.serverId || msg.id) ? (
                        <div className="max-w-sm w-full flex flex-col items-end gap-1">
                          <textarea autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitEdit(msg) } }} className="input-field w-full text-sm" rows={2} />
                          <div className="flex gap-2">
                            <button onClick={() => setEditingId(null)} className="text-xs text-gray-500 px-2 py-1">Cancel</button>
                            <button onClick={() => submitEdit(msg)} className="btn-primary text-xs px-3 py-1 flex items-center gap-1"><Check size={12} /> Send</button>
                          </div>
                        </div>
                      ) : (
                        <div className="group flex items-end gap-1.5">
                          <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-opacity mb-1">
                            {sibs.length > 1 && (
                              <div className="flex items-center gap-0.5 text-[11px] text-gray-400">
                                <button disabled={sibIdx <= 0} onClick={() => switchSibling(msg.serverId!, -1)} className="disabled:opacity-30 hover:text-gray-700"><ChevronLeft size={13} /></button>
                                <span>{sibIdx + 1}/{sibs.length}</span>
                                <button disabled={sibIdx >= sibs.length - 1} onClick={() => switchSibling(msg.serverId!, 1)} className="disabled:opacity-30 hover:text-gray-700"><ChevronRight size={13} /></button>
                              </div>
                            )}
                            <button onClick={() => copyText(msg.content, msg.serverId || msg.id)} className="p-1 text-gray-400 hover:text-gray-600 rounded">{copiedId === (msg.serverId || msg.id) ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}</button>
                            <button onClick={() => toggleStar(msg.serverId || msg.id)} className={`p-1 rounded ${starredIds.has(msg.serverId || msg.id) ? 'text-amber-400' : 'text-gray-400 hover:text-amber-400'}`}><Star size={12} fill={starredIds.has(msg.serverId || msg.id) ? 'currentColor' : 'none'} /></button>
                            {msg.serverId && <button onClick={() => startEdit(msg)} className="p-1 text-gray-400 hover:text-gray-600 rounded"><Edit2 size={12} /></button>}
                          </div>
                          <div className={`max-w-sm px-4 py-2.5 rounded-2xl rounded-tr-sm text-sm ${starredIds.has(msg.serverId || msg.id) ? 'bg-brand/90 text-white ring-2 ring-amber-300/60' : 'bg-brand text-white'}`}>{msg.content}</div>
                          {msg.created_at && <span className="opacity-0 group-hover:opacity-100 transition-opacity text-[10px] text-gray-400 self-end mb-0.5 whitespace-nowrap">{relativeTime(msg.created_at)}</span>}
                        </div>
                      )
                    ) : (
                      <div className="max-w-2xl w-full">
                        {msg.loading && (
                          <div className="card p-4 space-y-3">
                            <div className="h-3.5 rounded-md bg-gray-200 animate-pulse w-2/5" />
                            <div className="h-[100px] rounded-lg bg-gray-200 animate-pulse" />
                            <div className="flex items-center gap-2">
                              <span className={`w-2 h-2 rounded-full flex-shrink-0 animate-pulse ${msg.jobId && jobs[msg.jobId] ? 'bg-brand' : 'bg-gray-300'}`} />
                              <span className="text-xs text-gray-400">{msg.jobId && jobs[msg.jobId] ? getLoadingText(jobs[msg.jobId], msg.jobType) : 'Starting pipeline…'}</span>
                            </div>
                          </div>
                        )}
                        {msg.error && (
                          <div className="card p-4 space-y-2">
                            <div className="flex items-center gap-3 text-red-600"><AlertCircle size={16} /><span className="text-sm">{msg.error}</span></div>
                            {msg.error !== 'Cancelled' && msg.serverId && !submitting && (
                              <div className="flex justify-end gap-2">
                                <button onClick={() => copyText(msg.error!, `${msg.serverId}-err`)} className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">{copiedId === `${msg.serverId}-err` ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}</button>
                                <button onClick={() => regenerateFromAssistant(msg)} className="text-xs text-gray-400 hover:text-brand flex items-center gap-1"><RefreshCw size={11} /> Try again</button>
                              </div>
                            )}
                          </div>
                        )}
                        {msg.candidates && msg.candidates.length > 1 && (
                          <CandidatesCard
                            candidates={msg.candidates}
                            message={msg.candidatesMessage}
                            onSelect={(chosen) => {
                              const cr: ChartResult = {
                                chart_type: chosen.chart_type,
                                title: chosen.title,
                                sql: chosen.sql,
                                score: chosen.confidence,
                                low_confidence: chosen.confidence < 0.65,
                                x_axis_label: chosen.x_axis_label,
                                y_axis_label: chosen.y_axis_label,
                                table_used: chosen.table_used,
                                chart_data: chosen.chart_data,
                              }
                              setMessages((prev) => prev.map((m) =>
                                m.id === msg.id ? { ...m, chartResult: cr, candidates: undefined } : m
                              ))
                            }}
                          />
                        )}
                        {msg.chartResult && (() => {
                          const cr = msg.chartResult!
                          const msgKey = msg.serverId || msg.id
                          const isStarred = starredIds.has(msgKey)
                          const isSpeaking = speakingId === msgKey
                          const responseText = cr.narrative || cr.title || ''
                          const isTextMode = cr.output_mode === 'text'
                          const activeChartType = chartTypeOverrides[msgKey] || cr.chart_type
                          const compatTypes = compatibleChartTypes(cr)

                          const HoverActions = () => (
                            <div className="opacity-0 group-hover:opacity-100 transition-all duration-150 flex items-center gap-1 flex-wrap pt-2 border-t border-gray-100 mt-1">
                              {cr.low_confidence && <span className="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 rounded-full">Low confidence</span>}
                              {responseText && <button onClick={() => copyText(responseText, `${msgKey}-text`)} className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1">{copiedId === `${msgKey}-text` ? <Check size={10} className="text-green-500" /> : <Copy size={10} />} Copy</button>}
                              {responseText && <button onClick={() => isSpeaking ? stopSpeaking() : speakText(responseText, msgKey)} className={`text-xs px-2 py-0.5 rounded-full flex items-center gap-1 ${isSpeaking ? 'bg-brand text-white' : 'bg-gray-100 hover:bg-gray-200 text-gray-600'}`}>{isSpeaking ? <VolumeX size={10} /> : <Volume2 size={10} />}{isSpeaking ? 'Stop' : 'Listen'}</button>}
                              <button onClick={() => toggleStar(msgKey)} className={`text-xs px-2 py-0.5 rounded-full flex items-center gap-1 ${isStarred ? 'bg-amber-100 text-amber-600' : 'bg-gray-100 hover:bg-gray-200 text-gray-600'}`}><Star size={10} fill={isStarred ? 'currentColor' : 'none'} />{isStarred ? 'Starred' : 'Star'}</button>
                              {cr.sql && <button onClick={() => copyText(cr.sql, `${msgKey}-sql`)} className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1">{copiedId === `${msgKey}-sql` ? <Check size={10} className="text-green-500" /> : <Code size={10} />} SQL</button>}
                              {cr.chart_data?.rows?.length > 0 && <button onClick={() => downloadCsv(cr.chart_data.rows as Record<string, unknown>[], cr.chart_data.columns, cr.title)} className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1"><Download size={10} /> CSV</button>}
                              {!isTextMode && <button onClick={() => openChatForChart(cr)} className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1"><MessageSquare size={10} /> Ask about this</button>}
                              {msg.serverId && !submitting && <button onClick={() => regenerateFromAssistant(msg)} className="text-xs text-gray-400 hover:text-brand flex items-center gap-1 ml-auto"><RefreshCw size={11} /> Regenerate</button>}
                            </div>
                          )

                          if (isTextMode) {
                            return (
                              <div className={`group card p-4 space-y-2 ${isStarred ? 'ring-2 ring-amber-300/60' : ''}`}>
                                <div className="flex items-start gap-2">
                                  <div className="mt-0.5 p-1.5 bg-brand/10 rounded-lg flex-shrink-0"><FileText size={14} className="text-brand" /></div>
                                  <div className="flex-1 min-w-0">{cr.narrative ? <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-line">{cr.narrative}</p> : <ChartRenderer result={cr} />}</div>
                                </div>
                                <HoverActions />
                              </div>
                            )
                          }

                          return (
                            <div className={`group card p-4 space-y-3 ${isStarred ? 'ring-2 ring-amber-300/60' : ''}`}>
                              <div className="flex items-center gap-2">
                                <h4 className="font-semibold text-gray-900 font-display flex-1 min-w-0 truncate">{cr.title}</h4>
                                <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ${cr.score >= 0.65 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}`}>{(cr.score * 100).toFixed(0)}%</span>
                                <button onClick={() => setExpandedChart({ ...cr, chart_type: activeChartType })} className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-gray-400 hover:text-gray-600 rounded flex-shrink-0" title="Expand"><Maximize2 size={14} /></button>
                              </div>
                              <ChartRenderer result={{ ...cr, chart_type: activeChartType }} />
                              {compatTypes.length > 1 && (
                                <div className="flex gap-1.5 flex-wrap">
                                  {compatTypes.map((t) => (
                                    <button key={t} onClick={() => setChartTypeOverrides((prev) => ({ ...prev, [msgKey]: t }))} className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors capitalize ${activeChartType === t ? 'bg-brand/10 border-brand/30 text-brand font-medium' : 'bg-white border-gray-200 text-gray-400 hover:border-gray-300 hover:text-gray-600'}`}>{t}</button>
                                  ))}
                                </div>
                              )}
                              {cr.narrative && <p className="text-sm text-gray-600 leading-relaxed border-l-2 border-brand/30 pl-3">{cr.narrative}</p>}
                              <HoverActions />
                            </div>
                          )
                        })()}
                        {msg.dashboardResult && (
                          <div className="space-y-3">
                            <div className="flex items-center gap-2 text-sm text-gray-500"><LayoutDashboard size={14} /> Dashboard — {msg.dashboardResult.charts.length} charts</div>
                            <div className="grid grid-cols-2 gap-3">
                              {msg.dashboardResult.charts.map((chart, i) => (
                                <div key={i} className="group card p-3 space-y-2">
                                  <div className="flex items-center justify-between">
                                    <h5 className="text-sm font-medium text-gray-900 font-display truncate">{chart.title}</h5>
                                    <div className="flex items-center gap-1 flex-shrink-0 ml-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                      <button onClick={() => setExpandedChart(chart)} className="text-xs text-gray-400 hover:text-gray-600"><Maximize2 size={12} /></button>
                                      {chart.sql && <button onClick={() => navigator.clipboard.writeText(chart.sql)} className="text-xs text-gray-400 hover:text-gray-600"><Code size={11} /></button>}
                                      {chart.chart_data?.rows?.length > 0 && <button onClick={() => downloadCsv(chart.chart_data.rows as Record<string, unknown>[], chart.chart_data.columns, chart.title)} className="text-xs text-gray-400 hover:text-gray-600"><Download size={11} /></button>}
                                      <button onClick={() => openChatForChart(chart)} className="text-xs text-gray-400 hover:text-brand"><MessageSquare size={12} /></button>
                                    </div>
                                  </div>
                                  <ChartRenderer result={chart} compact />
                                </div>
                              ))}
                            </div>
                            {msg.serverId && !submitting && <div className="flex justify-end"><button onClick={() => regenerateFromAssistant(msg)} className="text-xs text-gray-400 hover:text-brand flex items-center gap-1"><RefreshCw size={11} /> Regenerate</button></div>}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
              <div ref={messagesEndRef} />
            </div>

            {showScrollFab && (
              <button onClick={() => { scrollContainerRef.current?.scrollTo({ top: scrollContainerRef.current.scrollHeight, behavior: 'smooth' }); setShowScrollFab(false); setHasNewMsg(false) }} className="absolute bottom-4 right-4 flex flex-col items-center gap-1 z-10">
                {hasNewMsg && <span className="bg-brand text-white text-[10px] font-semibold px-2 py-0.5 rounded-full shadow-md">New</span>}
                <span className="w-8 h-8 rounded-full bg-brand text-white flex items-center justify-center shadow-lg hover:bg-brand-dark transition-colors"><ChevronDown size={16} /></span>
              </button>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-gray-100 bg-white px-4 pt-3 pb-4">
            <div className="max-w-3xl mx-auto space-y-2">
              <div className="flex items-center gap-1.5">
                {(['auto', 'chart', 'table', 'text'] as const).map((m) => (
                  <button key={m} type="button" onClick={() => setOutputMode(m)} className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${outputMode === m ? 'bg-brand text-white border-brand' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300'}`}>
                    {m === 'auto' ? 'Auto' : m === 'chart' ? 'Chart' : m === 'table' ? 'Table' : 'Text'}
                  </button>
                ))}
                <span className="ml-auto text-[10px] text-gray-300 hidden sm:flex items-center gap-3"><span>⌘↵ send</span><span>⌘K new</span><span>⌘. stop</span></span>
              </div>
              <div className="flex gap-2 items-end">
                <textarea ref={inputRef} value={input} onChange={(e) => { setInput(e.target.value); autoResize() }} onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void handleSubmit() } else if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void handleSubmit() } }} className="input-field flex-1 resize-none overflow-y-auto" style={{ minHeight: '42px', maxHeight: '140px', lineHeight: '1.5' }} placeholder="Ask about your data…" disabled={submitting} rows={1} />
                {submitting ? (
                  <button type="button" onClick={cancelPipeline} className="flex-shrink-0 h-[42px] px-4 bg-red-500 hover:bg-red-600 text-white rounded-lg transition-colors flex items-center gap-1.5" title="Cancel (⌘.)"><Square size={14} fill="white" /></button>
                ) : (
                  <button type="button" onClick={() => void handleSubmit()} disabled={!input.trim()} className="btn-primary flex-shrink-0 h-[42px] px-4 disabled:opacity-40 disabled:cursor-not-allowed"><Send size={16} /></button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* "Ask about this" panel */}
        {chatOpen && (
          <div className="w-72 border-l border-gray-100 flex flex-col bg-white">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <div>
                <p className="text-sm font-semibold text-gray-900">AI Assistant</p>
                {activeChartContext && <p className="text-xs text-gray-400 truncate max-w-52">{activeChartContext.title}</p>}
              </div>
              <button onClick={closeChatPanel} className="text-gray-400 hover:text-gray-600"><X size={16} /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {chatMessages.map((m) => (
                <div key={m.id} className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                  <div className={`max-w-[85%] px-3 py-2 rounded-xl text-xs leading-relaxed ${m.role === 'user' ? 'bg-brand text-white rounded-tr-sm' : 'bg-gray-100 text-gray-800 rounded-tl-sm'}`}>
                    {m.loading && !m.content ? <Loader2 size={12} className="animate-spin" /> : m.content || <Loader2 size={12} className="animate-spin" />}
                  </div>
                  {m.inlineChart && <div className="mt-2 w-full border border-gray-200 rounded-lg p-2 bg-white"><p className="text-xs font-medium text-gray-700 mb-1">{m.inlineChart.title}</p><ChartRenderer result={m.inlineChart} compact /></div>}
                  {m.candidates && m.candidates.length > 1 && (
                    <div className="mt-2 w-full">
                      <CandidatesCard
                        candidates={m.candidates}
                        message={m.candidatesMessage}
                        compact
                        onSelect={(chosen) => {
                          const cr: ChartResult = {
                            chart_type: chosen.chart_type, title: chosen.title, sql: chosen.sql,
                            score: chosen.confidence, low_confidence: chosen.confidence < 0.65,
                            x_axis_label: chosen.x_axis_label, y_axis_label: chosen.y_axis_label,
                            table_used: chosen.table_used, chart_data: chosen.chart_data,
                          }
                          setChatMessages((prev) => prev.map((cm) =>
                            cm.id === m.id ? { ...cm, inlineChart: cr, candidates: undefined } : cm
                          ))
                        }}
                      />
                    </div>
                  )}
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>
            <div className="border-t border-gray-100 p-3">
              <div className="flex gap-2">
                <input value={chatInput} onChange={(e) => setChatInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleChatSend()} className="input-field flex-1 text-xs py-2" placeholder="Ask a follow-up…" disabled={chatLoading} />
                <button onClick={handleChatSend} disabled={chatLoading || !chatInput.trim()} className="btn-primary px-3 py-2"><Send size={13} /></button>
              </div>
            </div>
          </div>
        )}

        <ReasoningDrawer jobId={activeJobId} />
      </div>

      {/* Chart expand modal */}
      {expandedChart && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-6" onClick={() => setExpandedChart(null)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-4 p-5 border-b border-gray-100">
              <h3 className="text-base font-semibold font-display text-gray-900">{expandedChart.title}</h3>
              <button onClick={() => setExpandedChart(null)} className="text-gray-400 hover:text-gray-600 flex-shrink-0"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-4">
              <ChartRenderer result={expandedChart} />
              {expandedChart.narrative && <p className="text-sm text-gray-600 leading-relaxed border-l-2 border-brand/30 pl-3">{expandedChart.narrative}</p>}
              {expandedChart.sql && <details><summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">View SQL</summary><pre className="mt-2 p-3 bg-gray-50 rounded-lg text-xs font-mono text-gray-700 overflow-x-auto whitespace-pre-wrap">{expandedChart.sql}</pre></details>}
              <div className="flex gap-2 flex-wrap pt-1">
                {expandedChart.chart_data?.rows?.length > 0 && <button onClick={() => downloadCsv(expandedChart.chart_data.rows as Record<string, unknown>[], expandedChart.chart_data.columns, expandedChart.title)} className="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1.5"><Download size={12} /> Download CSV</button>}
                {expandedChart.sql && <button onClick={() => navigator.clipboard.writeText(expandedChart.sql)} className="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1.5"><Code size={12} /> Copy SQL</button>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── CandidatesCard ────────────────────────────────────────────────────────────
// Shown when 2-3 table candidates scored similarly and the agent can't auto-pick.

interface CandidatesCardProps {
  candidates: CandidateResult[]
  message?: string
  compact?: boolean
  onSelect: (candidate: CandidateResult) => void
}

function CandidatesCard({ candidates, message, compact, onSelect }: CandidatesCardProps) {
  const sorted = [...candidates].sort((a, b) => b.confidence - a.confidence)
  return (
    <div className={`border border-amber-200 bg-amber-50 rounded-xl ${compact ? 'p-3' : 'p-4'} space-y-3`}>
      <div className="flex items-start gap-2">
        <span className="text-amber-500 text-base flex-shrink-0">⚡</span>
        <div>
          <p className={`font-semibold text-gray-800 ${compact ? 'text-xs' : 'text-sm'}`}>
            {message || 'I found multiple possible answers — please choose one:'}
          </p>
          <p className={`text-gray-500 mt-0.5 ${compact ? 'text-[11px]' : 'text-xs'}`}>
            Each option uses a different table. Pick the one that looks right for your question.
          </p>
        </div>
      </div>
      <div className={`grid gap-2 ${compact ? '' : 'sm:grid-cols-2'}`}>
        {sorted.map((c) => (
          <button
            key={c.table}
            onClick={() => onSelect(c)}
            className="text-left border border-gray-200 bg-white hover:border-brand/60 hover:bg-brand-light/30 rounded-lg p-3 transition-all group"
          >
            <div className="flex items-center justify-between mb-1.5">
              <span className={`font-semibold text-gray-900 group-hover:text-brand ${compact ? 'text-xs' : 'text-sm'}`}>
                {c.label}
              </span>
              <span className={`rounded-full px-1.5 py-0.5 font-medium flex-shrink-0 ml-2 ${
                c.confidence >= 0.65 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
              } ${compact ? 'text-[10px]' : 'text-xs'}`}>
                {(c.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <p className={`text-gray-500 ${compact ? 'text-[10px]' : 'text-xs'}`}>
              Table: <span className="font-mono">{c.table.split('.').pop()}</span>
              {' · '}{c.row_count} row{c.row_count !== 1 ? 's' : ''}
            </p>
            {!compact && c.chart_data?.rows?.length > 0 && (
              <div className="mt-2 h-16 overflow-hidden rounded opacity-80 group-hover:opacity-100 transition-opacity pointer-events-none">
                <ChartRenderer
                  result={{
                    chart_type: c.chart_type, title: '', sql: c.sql, score: c.confidence,
                    low_confidence: false, x_axis_label: c.x_axis_label,
                    y_axis_label: c.y_axis_label, table_used: c.table_used,
                    chart_data: c.chart_data,
                  }}
                  compact
                />
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

function getLoadingText(job: unknown, jobType?: string): string {
  if (!job) return 'Processing…'
  const steps = ((job as { steps?: Record<string, unknown> }).steps) || {}
  if (jobType === 'DASHBOARD') {
    if (steps.dashboard_chart_done) return 'Building dashboard charts…'
    if (steps.dashboard_decomposed) return 'Spawning chart pipelines…'
    return 'Decomposing dashboard request…'
  }
  if (steps.validate === 'active') return 'Validating result…'
  if (steps.render === 'active') return 'Rendering chart…'
  if (steps.execute === 'active') return 'Executing query…'
  if (steps.query === 'active') return 'Generating SQL…'
  if (steps.schema === 'active') return 'Loading schema…'
  return 'Classifying intent…'
}
