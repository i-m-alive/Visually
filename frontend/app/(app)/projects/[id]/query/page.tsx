'use client'
import { useState, useRef, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { Send, Loader2, AlertCircle, TrendingUp, MessageSquare, X, LayoutDashboard } from 'lucide-react'
import { agentApi, chatApi } from '@/lib/api'
import { usePipelineSocket } from '@/hooks/usePipelineSocket'
import { usePipelineStore } from '@/stores/pipelineStore'
import { IntentStatusBar } from '@/components/pipeline/IntentStatusBar'
import { ReasoningDrawer } from '@/components/pipeline/ReasoningDrawer'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult, DashboardResult } from '@/stores/pipelineStore'

interface Message {
  id: string
  type: 'user' | 'agent'
  content: string
  jobId?: string
  loading?: boolean
  chartResult?: ChartResult
  dashboardResult?: DashboardResult
  error?: string
  jobType?: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  loading?: boolean
  inlineChart?: ChartResult
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

  // Chat panel state
  const [chatOpen, setChatOpen] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [chatSessionId, setChatSessionId] = useState<string | undefined>(undefined)
  const [activeChartContext, setActiveChartContext] = useState<ChartResult | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)

  usePipelineSocket(activeJobId)

  useEffect(() => {
    if (!activeJobId) return
    const job = jobs[activeJobId]
    if (!job) return

    setMessages((prev) => prev.map((m) => {
      if (m.jobId !== activeJobId) return m
      if (job.chartResult) {
        return { ...m, loading: false, chartResult: job.chartResult }
      }
      if (job.dashboardResult) {
        return { ...m, loading: false, dashboardResult: job.dashboardResult }
      }
      if (job.error) {
        return { ...m, loading: false, error: job.error }
      }
      return m
    }))

    if (job.chartResult || job.dashboardResult || job.error) {
      setActiveJobId(null)
      setSubmitting(false)
    }
  }, [jobs, activeJobId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages])

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!input.trim() || submitting) return

    const userText = input.trim()
    setInput('')
    setSubmitting(true)

    const userMsgId = crypto.randomUUID()
    const agentMsgId = crypto.randomUUID()

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, type: 'user', content: userText },
      { id: agentMsgId, type: 'agent', content: '', loading: true, jobId: '' },
    ])

    try {
      const resp = await agentApi.submitIntent({ text: userText, project_id: projectId })
      const jobId = resp.data.job_id
      const jobType = resp.data.job_type || 'SINGLE_VIZ'

      setActiveJobId(jobId)
      setStoreActiveJob(jobId)

      setMessages((prev) => prev.map((m) =>
        m.id === agentMsgId ? { ...m, jobId, jobType } : m
      ))

      usePipelineStore.getState().resetJob(jobId)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setMessages((prev) => prev.map((m) =>
        m.id === agentMsgId
          ? { ...m, loading: false, error: e.response?.data?.detail || 'Failed to start pipeline' }
          : m
      ))
      setSubmitting(false)
    }
  }

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
      const resp = await chatApi.send({
        session_id: chatSessionId,
        message: userText,
        project_id: projectId,
      })

      const data = resp.data
      if (data.session_id && !chatSessionId) setChatSessionId(data.session_id)

      // Map inline_chart from API to ChartResult shape
      let inlineChart: ChartResult | undefined = undefined
      if (data.inline_chart) {
        const ic = data.inline_chart
        inlineChart = {
          chart_type: ic.chart_type,
          title: ic.title,
          sql: ic.sql || '',
          score: 1,
          low_confidence: false,
          x_axis_label: ic.x_axis_label || 'x',
          y_axis_label: ic.y_axis_label || 'y',
          table_used: '',
          chart_data: ic.chart_data || { rows: [], columns: [], labels: [], values: [] },
        }
      }

      setChatMessages((prev) => prev.map((m) =>
        m.id === assistantMsgId ? { ...m, content: data.text, loading: false, inlineChart } : m
      ))

      // Handle dashboard_action: fill input with modify instruction
      if (data.dashboard_action?.action === 'filter_widget' && data.dashboard_action?.params?.instruction) {
        setInput(data.dashboard_action.params.instruction)
      }
    } catch {
      setChatMessages((prev) => prev.map((m) =>
        m.id === assistantMsgId ? { ...m, content: 'Sorry, I could not process your request.', loading: false } : m
      ))
    } finally {
      setChatLoading(false)
    }
  }

  const openChatForChart = (chartResult: ChartResult) => {
    setActiveChartContext(chartResult)
    setChatOpen(true)
    if (chatMessages.length === 0) {
      setChatMessages([{
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `I can help you explore the "${chartResult.title}" chart. Ask me anything about the data, or say "filter by region" to refine it.`,
      }])
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Main query panel */}
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

          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.type === 'user' ? 'justify-end' : 'justify-start'}`}>
              {msg.type === 'user' ? (
                <div className="max-w-sm bg-brand text-white px-4 py-2.5 rounded-2xl rounded-tr-sm text-sm">
                  {msg.content}
                </div>
              ) : (
                <div className="max-w-2xl w-full">
                  {msg.loading && (
                    <div className="card p-4 flex items-center gap-3 text-gray-500">
                      <Loader2 size={16} className="animate-spin text-brand" />
                      <span className="text-sm">
                        {msg.jobId && jobs[msg.jobId]
                          ? getLoadingText(jobs[msg.jobId], msg.jobType)
                          : 'Starting pipeline...'}
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
                            <MessageSquare size={10} />
                            Ask about this
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
                              <button
                                onClick={() => openChatForChart(chart)}
                                className="text-xs text-gray-400 hover:text-brand flex-shrink-0 ml-2"
                              >
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
          ))}

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
                  {m.loading ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    m.content
                  )}
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
              <button
                onClick={handleChatSend}
                disabled={chatLoading || !chatInput.trim()}
                className="btn-primary px-3 py-2"
              >
                <Send size={13} />
              </button>
            </div>
          </div>
        </div>
      )}

      <ReasoningDrawer jobId={activeJobId} />
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
