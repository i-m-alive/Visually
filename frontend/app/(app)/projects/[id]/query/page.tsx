'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Send, Loader2, AlertCircle, TrendingUp, MessageSquare, X, LayoutDashboard,
  Plus, Trash2, Edit2, Check, ChevronLeft, ChevronRight,
} from 'lucide-react'
import { agentApi, chatApi, querySessionApi } from '@/lib/api'
import { usePipelineSocket } from '@/hooks/usePipelineSocket'
import { usePipelineStore } from '@/stores/pipelineStore'
import { IntentStatusBar } from '@/components/pipeline/IntentStatusBar'
import { ReasoningDrawer } from '@/components/pipeline/ReasoningDrawer'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult, DashboardResult } from '@/stores/pipelineStore'

interface Message {
  id: string                  // local render key (= serverId once persisted)
  serverId?: string           // query_messages id
  parentId?: string | null    // tree parent (server id)
  type: 'user' | 'agent'
  content: string
  jobId?: string
  loading?: boolean
  chartResult?: ChartResult
  dashboardResult?: DashboardResult
  error?: string
  jobType?: string
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
    return { id: n.id, serverId: n.id, parentId: n.parent_id, type: 'user', content: n.content }
  }
  const r = n.result || {}
  return {
    id: n.id, serverId: n.id, parentId: n.parent_id, type: 'agent', content: n.content,
    chartResult: r.kind === 'chart' ? r.chartResult : undefined,
    dashboardResult: r.kind === 'dashboard' ? r.dashboardResult : undefined,
    error: r.kind === 'error' ? r.error : undefined,
  }
}

export default function QueryPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const jobs = usePipelineStore((s) => s.jobs)
  const setStoreActiveJob = usePipelineStore((s) => s.setActiveJob)

  // Session / history state
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [tree, setTree] = useState<Record<string, TreeNode>>({})
  const [activeLeafId, setActiveLeafId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameText, setRenameText] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  // Refs for the async pipeline-completion closure
  const sessionIdRef = useRef<string | null>(null)
  const activeLeafRef = useRef<string | null>(null)
  const pendingParentRef = useRef<string | null>(null)
  const persistedRef = useRef<Set<string>>(new Set())

  // Chat panel state ("Ask about this")
  const [chatOpen, setChatOpen] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatSessionId, setChatSessionId] = useState<string | undefined>(undefined)
  const [activeChartContext, setActiveChartContext] = useState<ChartResult | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)

  usePipelineSocket(activeJobId)

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

  // ── run one turn (optionally branching from a given parent message) ──────────
  const runTurn = async (text: string, branchParentId?: string | null) => {
    if (!text.trim() || submitting) return
    setSubmitting(true)
    setEditingId(null)
    const sid = await ensureSession()
    const parentId = branchParentId !== undefined ? branchParentId : activeLeafRef.current

    let userServerId: string | undefined
    try {
      const r = await querySessionApi.addMessage(sid, { role: 'user', content: text, parent_id: parentId })
      userServerId = r.data.id
      setActiveLeafId(userServerId!); activeLeafRef.current = userServerId!
      // Backend auto-titles the session from the first question — reflect it in
      // the sidebar immediately rather than waiting for the answer to finish.
      refreshSessions()
    } catch { /* persist best-effort */ }
    pendingParentRef.current = userServerId || null

    const agentMsgId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), serverId: userServerId, parentId, type: 'user', content: text },
      { id: agentMsgId, type: 'agent', content: '', loading: true, jobId: '' },
    ])

    try {
      const resp = await agentApi.submitIntent({ text, project_id: projectId })
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

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    const text = input.trim()
    if (!text) return
    setInput('')
    await runTurn(text)
  }

  // ── persist assistant result when the pipeline finishes, then reload tree ─────
  const finalizeTurn = useCallback(async (jid: string, job: { chartResult?: ChartResult; dashboardResult?: DashboardResult; error?: string }) => {
    const sid = sessionIdRef.current
    if (!sid) { setSubmitting(false); return }
    const content = job.chartResult?.narrative || job.error || ''
    const result = job.chartResult ? { kind: 'chart', chartResult: job.chartResult }
      : job.dashboardResult ? { kind: 'dashboard', dashboardResult: job.dashboardResult }
      : job.error ? { kind: 'error', error: job.error } : { kind: 'empty' }
    try {
      await querySessionApi.addMessage(sid, {
        role: 'assistant', content, parent_id: pendingParentRef.current, result, job_id: jid,
      })
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
      ? { ...m, loading: !terminal, chartResult: job.chartResult, dashboardResult: job.dashboardResult, error: job.error }
      : m))
    if (terminal && !persistedRef.current.has(activeJobId)) {
      persistedRef.current.add(activeJobId)
      const jid = activeJobId
      setActiveJobId(null); setSubmitting(false)
      void finalizeTurn(jid, job)
    }
  }, [jobs, activeJobId, finalizeTurn])

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [chatMessages])

  // ── branching: edit a user message + switch between sibling versions ─────────
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
    // Branch: new user message under the SAME parent as the edited one.
    await runTurn(text, msg.parentId ?? null)
  }

  // ── session sidebar actions ──────────────────────────────────────────────────
  const deleteSession = async (sid: string) => {
    try { await querySessionApi.remove(sid) } catch { /* ignore */ }
    if (sid === sessionIdRef.current) newChat()
    refreshSessions()
  }
  const commitRename = async (sid: string) => {
    const t = renameText.trim()
    setRenamingId(null)
    if (!t) return
    try { await querySessionApi.rename(sid, t) } catch { /* ignore */ }
    refreshSessions()
  }

  // ── "Ask about this" chat panel (unchanged) ──────────────────────────────────
  const handleChatSend = async () => {
    if (!chatInput.trim() || chatLoading) return
    const userText = chatInput.trim()
    setChatInput('')
    setChatLoading(true)
    const userMsgId = crypto.randomUUID()
    const assistantMsgId = crypto.randomUUID()
    setChatMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: userText },
      { id: assistantMsgId, role: 'assistant', content: '', loading: true },
    ])
    try {
      const resp = await chatApi.send({ session_id: chatSessionId, message: userText, project_id: projectId })
      const data = resp.data
      if (data.session_id && !chatSessionId) setChatSessionId(data.session_id)
      let inlineChart: ChartResult | undefined = undefined
      if (data.inline_chart) {
        const ic = data.inline_chart
        inlineChart = {
          chart_type: ic.chart_type, title: ic.title, sql: ic.sql || '', score: 1, low_confidence: false,
          x_axis_label: ic.x_axis_label || 'x', y_axis_label: ic.y_axis_label || 'y', table_used: '',
          chart_data: ic.chart_data || { rows: [], columns: [], labels: [], values: [] },
        }
      }
      setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, content: data.text, loading: false, inlineChart } : m))
      if (data.dashboard_action?.action === 'filter_widget' && data.dashboard_action?.params?.instruction) {
        setInput(data.dashboard_action.params.instruction)
      }
    } catch {
      setChatMessages((prev) => prev.map((m) => m.id === assistantMsgId ? { ...m, content: 'Sorry, I could not process your request.', loading: false } : m))
    } finally {
      setChatLoading(false)
    }
  }

  const openChatForChart = (chartResult: ChartResult) => {
    setActiveChartContext(chartResult)
    setChatOpen(true)
    if (chatMessages.length === 0) {
      setChatMessages([{
        id: crypto.randomUUID(), role: 'assistant',
        content: `I can help you explore the "${chartResult.title}" chart. Ask me anything about the data, or say "filter by region" to refine it.`,
      }])
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── History sidebar ── */}
      <aside className="w-64 flex-shrink-0 border-r border-gray-100 bg-gray-50 flex flex-col">
        <div className="p-3">
          <button onClick={newChat} className="btn-primary w-full flex items-center justify-center gap-2 py-2 text-sm">
            <Plus size={15} /> New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          <p className="px-2 py-1 text-[11px] uppercase tracking-wide text-gray-400">Recent</p>
          {sessions.length === 0 && <p className="px-2 text-xs text-gray-400">No chats yet</p>}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 cursor-pointer text-sm ${
                s.id === activeSessionId ? 'bg-brand-light text-brand' : 'text-gray-700 hover:bg-gray-100'
              }`}
              onClick={() => s.id !== renamingId && loadSession(s.id)}
            >
              {renamingId === s.id ? (
                <input
                  autoFocus value={renameText} onChange={(e) => setRenameText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') commitRename(s.id); if (e.key === 'Escape') setRenamingId(null) }}
                  onBlur={() => commitRename(s.id)}
                  className="flex-1 bg-white border border-gray-200 rounded px-1 py-0.5 text-xs"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <>
                  <span className="flex-1 truncate">{s.title}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); setRenamingId(s.id); setRenameText(s.title) }}
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600"
                    title="Rename"
                  ><Edit2 size={12} /></button>
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteSession(s.id) }}
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500"
                    title="Delete"
                  ><Trash2 size={12} /></button>
                </>
              )}
            </div>
          ))}
        </div>
      </aside>

      {/* ── Main + chat panel ── */}
      <div className="flex flex-1 overflow-hidden">
        <div className={`flex flex-col ${chatOpen ? 'flex-1' : 'w-full'} transition-all duration-300`}>
          <IntentStatusBar jobId={activeJobId} />

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center">
                <TrendingUp size={48} className="text-brand-light mb-4" />
                <h3 className="text-xl font-semibold font-display text-gray-700 mb-2">Ask anything about your data</h3>
                <p className="text-gray-400 text-sm max-w-md">
                  Try a single chart, or ask for a full &quot;dashboard overview&quot; to get multiple visualizations at once.
                </p>
                <div className="mt-4 flex flex-wrap gap-2 justify-center">
                  {[
                    'Show monthly revenue trend',
                    'Top 10 products by sales',
                    'Give me a dashboard overview of sales performance',
                  ].map((s) => (
                    <button key={s} onClick={() => setInput(s)} className="text-xs px-3 py-1.5 bg-brand-light text-brand rounded-full hover:bg-blue-100 transition-colors">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg) => {
              const sibs = msg.type === 'user' ? siblingsOf(msg.serverId) : []
              const sibIdx = sibs.findIndex((s) => s.id === msg.serverId)
              return (
                <div key={msg.id} className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}>
                  {msg.type === 'user' ? (
                    editingId === (msg.serverId || msg.id) ? (
                      <div className="max-w-sm w-full flex flex-col items-end gap-1">
                        <textarea
                          autoFocus value={editText} onChange={(e) => setEditText(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitEdit(msg) } }}
                          className="input-field w-full text-sm" rows={2}
                        />
                        <div className="flex gap-2">
                          <button onClick={() => setEditingId(null)} className="text-xs text-gray-500 px-2 py-1">Cancel</button>
                          <button onClick={() => submitEdit(msg)} className="btn-primary text-xs px-3 py-1 flex items-center gap-1">
                            <Check size={12} /> Send
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="group flex items-center gap-1.5">
                        {sibs.length > 1 && (
                          <div className="flex items-center gap-0.5 text-[11px] text-gray-400">
                            <button disabled={sibIdx <= 0} onClick={() => switchSibling(msg.serverId!, -1)} className="disabled:opacity-30 hover:text-gray-700"><ChevronLeft size={13} /></button>
                            <span>{sibIdx + 1}/{sibs.length}</span>
                            <button disabled={sibIdx >= sibs.length - 1} onClick={() => switchSibling(msg.serverId!, 1)} className="disabled:opacity-30 hover:text-gray-700"><ChevronRight size={13} /></button>
                          </div>
                        )}
                        {msg.serverId && (
                          <button onClick={() => startEdit(msg)} className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600" title="Edit & branch">
                            <Edit2 size={12} />
                          </button>
                        )}
                        <div className="max-w-sm bg-brand text-white px-4 py-2.5 rounded-2xl rounded-tr-sm text-sm">
                          {msg.content}
                        </div>
                      </div>
                    )
                  ) : (
                    <div className="max-w-2xl w-full">
                      {msg.loading && (
                        <div className="card p-4 flex items-center gap-3 text-gray-500">
                          <Loader2 size={16} className="animate-spin text-brand" />
                          <span className="text-sm">
                            {msg.jobId && jobs[msg.jobId] ? getLoadingText(jobs[msg.jobId], msg.jobType) : 'Starting pipeline...'}
                          </span>
                        </div>
                      )}

                      {msg.error && (
                        <div className="card p-4 flex items-center gap-3 text-red-600 bg-red-50 border border-red-100">
                          <AlertCircle size={16} />
                          <span className="text-sm">{msg.error}</span>
                        </div>
                      )}

                      {msg.chartResult && (
                        <div className="card p-4 space-y-3">
                          <div className="flex items-center justify-between">
                            <h4 className="font-semibold text-gray-900 font-display">{msg.chartResult.title}</h4>
                            <div className="flex items-center gap-2">
                              {msg.chartResult.low_confidence && (
                                <span className="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 rounded-full">Low confidence</span>
                              )}
                              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                msg.chartResult.score >= 0.8 ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
                              }`}>
                                Score: {(msg.chartResult.score * 100).toFixed(0)}%
                              </span>
                              <button
                                onClick={() => openChatForChart(msg.chartResult!)}
                                className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-full flex items-center gap-1 transition-colors"
                              >
                                <MessageSquare size={10} /> Ask about this
                              </button>
                            </div>
                          </div>

                          {msg.chartResult.output_mode === 'text' ? (
                            msg.chartResult.narrative ? (
                              <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-line">{msg.chartResult.narrative}</p>
                            ) : (
                              <ChartRenderer result={msg.chartResult} />
                            )
                          ) : (
                            <>
                              <ChartRenderer result={msg.chartResult} />
                              {msg.chartResult.narrative && (
                                <p className="text-sm text-gray-600 leading-relaxed border-l-2 border-brand/30 pl-3">
                                  {msg.chartResult.narrative}
                                </p>
                              )}
                            </>
                          )}
                        </div>
                      )}

                      {msg.dashboardResult && (
                        <div className="space-y-3">
                          <div className="flex items-center gap-2 text-sm text-gray-500">
                            <LayoutDashboard size={14} />
                            Dashboard — {msg.dashboardResult.charts.length} charts generated
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            {msg.dashboardResult.charts.map((chart, i) => (
                              <div key={i} className="card p-3 space-y-2">
                                <div className="flex items-center justify-between">
                                  <h5 className="text-sm font-medium text-gray-900 font-display truncate">{chart.title}</h5>
                                  <button onClick={() => openChatForChart(chart)} className="text-xs text-gray-400 hover:text-brand flex-shrink-0 ml-2">
                                    <MessageSquare size={12} />
                                  </button>
                                </div>
                                <ChartRenderer result={chart} compact />
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}

            <div ref={messagesEndRef} />
          </div>

          <form onSubmit={handleSubmit} className="border-t border-gray-100 bg-white p-4">
            <div className="flex gap-3 max-w-3xl mx-auto">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSubmit()}
                className="input-field flex-1"
                placeholder="Ask about your data... or 'give me a dashboard overview'"
                disabled={submitting}
              />
              <button type="submit" disabled={submitting || !input.trim()} className="btn-primary px-4">
                <Send size={16} />
              </button>
            </div>
          </form>
        </div>

        {/* Chat panel */}
        {chatOpen && (
          <div className="w-80 border-l border-gray-100 flex flex-col bg-white">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <div>
                <p className="text-sm font-semibold text-gray-900">AI Assistant</p>
                {activeChartContext && (
                  <p className="text-xs text-gray-400 truncate max-w-56">{activeChartContext.title}</p>
                )}
              </div>
              <button onClick={() => setChatOpen(false)} className="text-gray-400 hover:text-gray-600">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {chatMessages.map((m) => (
                <div key={m.id} className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                  <div className={`max-w-[85%] px-3 py-2 rounded-xl text-xs leading-relaxed ${
                    m.role === 'user' ? 'bg-brand text-white rounded-tr-sm' : 'bg-gray-100 text-gray-800 rounded-tl-sm'
                  }`}>
                    {m.loading ? <Loader2 size={12} className="animate-spin" /> : m.content}
                  </div>
                  {m.inlineChart && (
                    <div className="mt-2 w-full border border-gray-200 rounded-lg p-2 bg-white">
                      <p className="text-xs font-medium text-gray-700 mb-1">{m.inlineChart.title}</p>
                      <ChartRenderer result={m.inlineChart} compact />
                    </div>
                  )}
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>

            <div className="border-t border-gray-100 p-3">
              <div className="flex gap-2">
                <input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleChatSend()}
                  className="input-field flex-1 text-xs py-2"
                  placeholder="Ask a follow-up..."
                  disabled={chatLoading}
                />
                <button onClick={handleChatSend} disabled={chatLoading || !chatInput.trim()} className="btn-primary px-3 py-2">
                  <Send size={13} />
                </button>
              </div>
            </div>
          </div>
        )}

        <ReasoningDrawer jobId={activeJobId} />
      </div>
    </div>
  )
}

function getLoadingText(job: unknown, jobType?: string): string {
  if (!job) return 'Processing...'
  const steps = ((job as { steps?: Record<string, unknown> }).steps) || {}
  if (jobType === 'DASHBOARD') {
    if (steps.dashboard_chart_done) return 'Building dashboard charts...'
    if (steps.dashboard_decomposed) return 'Spawning chart pipelines...'
    return 'Decomposing dashboard request...'
  }
  if (steps.validate === 'active') return 'Validating result...'
  if (steps.render === 'active') return 'Rendering chart...'
  if (steps.execute === 'active') return 'Executing query...'
  if (steps.query === 'active') return 'Generating SQL...'
  if (steps.schema === 'active') return 'Loading schema...'
  return 'Classifying intent...'
}
