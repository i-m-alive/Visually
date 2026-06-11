'use client'
import { useState, useRef, useEffect } from 'react'
import { Send, Loader2, BarChart3, Sparkles } from 'lucide-react'
import { analystApi } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'

const PAGE_CHIPS: Record<string, string[]> = {
  overview:   ['What are the top 3 trends?', 'Summarize key metrics', 'Any anomalies?'],
  revenue:    ['Who grew fastest?', 'Revenue by firm type', 'Compare YoY growth'],
  customer:   ['Top 10 customers', 'Which accounts are at risk?', 'Momentum leaders'],
  customers:  ['Top 10 customers', 'Which accounts are at risk?', 'Momentum leaders'],
  firm:       ['Revenue concentration', 'Fastest growing firm type', 'Show gross vs net'],
  firms:      ['Revenue concentration', 'Fastest growing firm type', 'Show gross vs net'],
  workforce:  ['Top producers', 'Bill rate trend', 'Utilization by customer'],
  new:        ['Net new vs lost', 'Draft save strategy', 'Top wins this quarter'],
  lost:       ['Net new vs lost', 'Draft save strategy', 'Top at-risk accounts'],
}

const DEFAULT_CHIPS = [
  'What are the top trends?',
  'Show me key metrics',
  'Any at-risk accounts?',
  'Compare this vs last period',
]

function getChips(pageName: string): string[] {
  const lower = pageName.toLowerCase()
  for (const [key, chips] of Object.entries(PAGE_CHIPS)) {
    if (lower.includes(key)) return chips
  }
  return DEFAULT_CHIPS
}

interface InlineChart {
  chart_type: string; title: string; x_axis_label: string; y_axis_label: string
  sql: string
  chart_data: { rows: unknown[]; columns: string[]; labels: string[]; values: number[] }
}

interface Msg {
  role: 'user' | 'assistant'
  text: string
  charts?: InlineChart[]
}

function toResult(c: InlineChart): ChartResult {
  return { chart_type: c.chart_type, title: c.title, sql: c.sql, score: 1, low_confidence: false, x_axis_label: c.x_axis_label, y_axis_label: c.y_axis_label, table_used: '', chart_data: { ...c.chart_data, rows: c.chart_data.rows as Record<string, unknown>[] } }
}

interface Props {
  token: string
  canvasName: string
  pageName: string
}

export function ExecutiveCopilot({ token, canvasName, pageName }: Props) {
  const [messages, setMessages] = useState<Msg[]>([
    { role: 'assistant', text: `Hi! I'm your executive analyst for **${canvasName}**. Ask me anything — I can query the data, surface trends, and draft action plans.` },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const chips = getChips(pageName)

  const send = async (text: string) => {
    const t = text.trim()
    if (!t || loading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: t }])
    setLoading(true)
    try {
      const resp = await analystApi.chat(token, { message: t, session_id: sessionId })
      const d = resp.data
      setSessionId(d.session_id)
      const rawCharts: InlineChart[] = (d.inline_charts as InlineChart[] | undefined) ?? (d.inline_chart ? [d.inline_chart as InlineChart] : [])
      setMessages(prev => [...prev, { role: 'assistant', text: d.text, charts: rawCharts.length ? rawCharts : undefined }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Something went wrong. Please try again.' }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full" style={{ background: '#FAFAFA' }}>

      {/* Header */}
      <div className="px-4 py-3 flex-shrink-0 border-b border-gray-100" style={{ background: '#fff' }}>
        <div className="flex items-center gap-2">
          {/* Animated conic-gradient orb */}
          <div className="relative w-7 h-7 flex-shrink-0">
            <div
              className="w-7 h-7 rounded-full"
              style={{ background: 'conic-gradient(from 0deg, #2563EB, #7C3AED, #0D9488, #2563EB)', animation: 'spin 6s linear infinite', opacity: 0.9 }}
            />
            <Sparkles size={11} className="absolute inset-0 m-auto text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold text-gray-800">Executive Copilot</p>
            <p className="text-[10px] text-gray-400 truncate">{pageName}</p>
          </div>
          {/* Live status */}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
            </span>
            <span className="text-[9px] text-gray-400">Live</span>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[92%] min-w-0 ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white rounded-2xl rounded-tr-sm px-3 py-2'
                : 'bg-white border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2 shadow-sm'
            }`}>
              {msg.role === 'assistant' ? (
                <div>
                  {msg.text.split('\n').map((line, j) => (
                    <p key={j} className="text-xs text-gray-700 mb-1 last:mb-0 leading-relaxed"
                      dangerouslySetInnerHTML={{ __html: line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*(.*?)\*/g, '<em>$1</em>') }}
                    />
                  ))}
                  {msg.charts && msg.charts.length > 0 && (
                    <div className="mt-2 rounded-xl overflow-hidden border border-gray-100">
                      {msg.charts.map((c, ci) => (
                        <div key={ci} className={ci > 0 ? 'border-t border-gray-100' : ''}>
                          <div className="px-2 py-1.5 flex items-center gap-1.5 border-b border-gray-100" style={{ background: '#F8FAFC' }}>
                            <BarChart3 size={10} className="text-blue-500" />
                            <span className="text-[10px] font-semibold text-gray-700">{c.title}</span>
                          </div>
                          <div className="h-36 p-1 bg-white">
                            <ChartRenderer result={toResult(c)} height={undefined} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs">{msg.text}</p>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2 shadow-sm">
              <Loader2 size={12} className="animate-spin text-blue-500" />
            </div>
          </div>
        )}
        {/* Context chips — only when idle and few messages */}
        {messages.length <= 2 && !loading && (
          <div className="space-y-1.5 pt-1">
            <p className="text-[10px] text-gray-400 text-center">Try asking:</p>
            {chips.map(chip => (
              <button
                key={chip}
                onClick={() => send(chip)}
                className="w-full text-left px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-100 rounded-xl hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 transition-all shadow-sm"
              >
                {chip}
              </button>
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-3 py-3 flex-shrink-0 border-t border-gray-100" style={{ background: '#fff' }}>
        <div className="flex items-end gap-2 bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 focus-within:border-blue-400 focus-within:bg-white transition-all">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
            placeholder="Ask about this report…"
            rows={1}
            className="flex-1 text-xs bg-transparent border-0 outline-none resize-none text-gray-800 placeholder-gray-400"
            style={{ minHeight: 20, maxHeight: 80 }}
          />
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || loading}
            className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center disabled:opacity-40 transition-all"
            style={{ background: input.trim() ? 'linear-gradient(135deg, #2563EB, #7C3AED)' : '#E5E7EB' }}
          >
            <Send size={11} className={input.trim() ? 'text-white' : 'text-gray-400'} />
          </button>
        </div>
        <p className="text-[9px] text-gray-400 mt-1 text-center">Enter to send · Shift+Enter for new line</p>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
