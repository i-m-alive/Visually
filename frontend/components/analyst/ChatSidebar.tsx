'use client'
import { useState, useRef, useEffect } from 'react'
import { Send, Loader2, Sparkles, BarChart3, RefreshCw, Database } from 'lucide-react'
import { analystApi } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'

type SchemaSource = 'live' | 'recent' | 'cached' | 'embedded' | 'none'

type InlineChartItem = {
  chart_type: string
  title: string
  x_axis_label: string
  y_axis_label: string
  chart_data: { rows: unknown[]; columns: string[]; labels: string[]; values: number[] }
  sql: string
}

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  inline_chart?: InlineChartItem
  inline_charts?: InlineChartItem[]
  schema_source?: SchemaSource
  schema_age_minutes?: number | null
  triggerMsg?: string
}

const CHART_CREATION_RE = /\b(create|make|build|generate|add|show|give|draw|produce)\b/i
const CHART_SUBJECT_RE  = /\b(chart|graph|pie|bar|kpi|table|visual|visualization|plot|donut|scatter|waterfall)\b/i
const isChartReq = (t: string) => CHART_CREATION_RE.test(t) && CHART_SUBJECT_RE.test(t)

interface ChatSidebarProps {
  token: string
  dashboardName: string
}

function chartToResult(chart: NonNullable<ChatMessage['inline_chart']>): ChartResult {
  return {
    chart_type: chart.chart_type,
    title: chart.title,
    sql: chart.sql,
    score: 1,
    low_confidence: false,
    x_axis_label: chart.x_axis_label,
    y_axis_label: chart.y_axis_label,
    table_used: '',
    chart_data: chart.chart_data,
  }
}

// Schema freshness badge config
const SCHEMA_BADGE: Record<SchemaSource, { dot: string; label: (age?: number | null) => string; bg: string; text: string }> = {
  live:     { dot: '#22C55E', label: ()    => 'Live schema',                          bg: '#F0FDF4', text: '#15803D' },
  recent:   { dot: '#F59E0B', label: (a)   => `Schema · ${Math.round(a ?? 0)}m ago`,  bg: '#FFFBEB', text: '#92400E' },
  cached:   { dot: '#F97316', label: (a)   => `Cached · ${Math.round((a ?? 0) / 60 > 1 ? (a ?? 0) / 60 : a ?? 0)}${(a ?? 0) > 60 ? 'h' : 'm'} ago`,
                                                                                       bg: '#FFF7ED', text: '#9A3412' },
  embedded: { dot: '#8B5CF6', label: ()    => 'Embedded schema',                      bg: '#F5F3FF', text: '#6D28D9' },
  none:     { dot: '#9CA3AF', label: ()    => 'No schema',                            bg: '#F9FAFB', text: '#6B7280' },
}

function SchemaBadge({ source, ageMin, refreshing }: { source: SchemaSource; ageMin?: number | null; refreshing: boolean }) {
  if (source === 'none') return null
  const cfg = SCHEMA_BADGE[source]
  return (
    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium" style={{ background: cfg.bg, color: cfg.text }}>
      {refreshing
        ? <RefreshCw size={8} className="animate-spin" />
        : <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: cfg.dot }} />
      }
      {refreshing ? 'Refreshing…' : cfg.label(ageMin)}
    </div>
  )
}

export function ChatSidebar({ token, dashboardName }: ChatSidebarProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'assistant', text: `Hi! I'm your AI analyst for **${dashboardName}**. Ask me anything about this data — I can answer questions, run queries, and create charts.` },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [schemaSource, setSchemaSource] = useState<SchemaSource>('none')
  const [schemaAge, setSchemaAge] = useState<number | null>(null)
  const [schemaRefreshing, setSchemaRefreshing] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text }])
    setLoading(true)
    try {
      const resp = await analystApi.chat(token, { message: text, session_id: sessionId })
      const d = resp.data
      setSessionId(d.session_id)

      // Update schema freshness state from response
      const src = d.schema_source ?? 'none'
      setSchemaSource(src)
      setSchemaAge(d.schema_age_minutes ?? null)
      // If cached/embedded the backend already triggered a background refresh
      if (src === 'cached' || src === 'embedded') {
        setSchemaRefreshing(true)
        // Optimistically clear after 15 seconds (crawler typically takes ~10s)
        setTimeout(() => setSchemaRefreshing(false), 15_000)
      } else {
        setSchemaRefreshing(false)
      }

      const rawCharts: InlineChartItem[] = d.inline_charts ?? (d.inline_chart ? [d.inline_chart] : [])
      setMessages(prev => [...prev, {
        role: 'assistant',
        text: d.text,
        inline_chart: rawCharts[0],
        inline_charts: rawCharts.length > 0 ? rawCharts : undefined,
        schema_source: src,
        schema_age_minutes: d.schema_age_minutes,
        triggerMsg: text,
      }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Sorry, something went wrong. Please try again.' }])
    } finally {
      setLoading(false)
    }
  }

  const SUGGESTIONS = [
    'What are the top trends in this data?',
    'Show me a summary of key metrics',
    'What anomalies do you see?',
    'Compare this month vs last month',
  ]

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-lg flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
            <Sparkles size={12} className="text-white" />
          </div>
          <span className="text-xs font-semibold text-gray-800">AI Analyst</span>
          <div className="ml-auto flex items-center gap-1.5">
            <SchemaBadge source={schemaSource} ageMin={schemaAge} refreshing={schemaRefreshing} />
          </div>
        </div>

        {/* Schema staleness hint */}
        {(schemaSource === 'cached' || schemaSource === 'embedded') && (
          <div className="mt-2 flex items-start gap-1.5 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
            <Database size={10} className="text-amber-500 mt-0.5 flex-shrink-0" />
            <p className="text-[10px] text-amber-700 leading-relaxed">
              {schemaSource === 'cached'
                ? `Schema snapshot is ${Math.round((schemaAge ?? 0) / 60 > 1 ? (schemaAge ?? 0) / 60 : schemaAge ?? 0)}${(schemaAge ?? 0) > 60 ? 'h' : 'm'} old. Refreshing in background — next response will use live schema.`
                : 'Using embedded schema from .vly file. Connect to a database for live schema.'
              }
            </p>
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[90%] min-w-0 break-words ${msg.role === 'user' ? 'bg-blue-600 text-white rounded-2xl rounded-tr-sm px-3 py-2 text-xs' : 'bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2 text-xs text-gray-700'}`}>
              {msg.role === 'assistant' ? (
                <div className="prose prose-xs max-w-none">
                  {msg.text.split('\n').map((line, j) => (
                    <p key={j} className="mb-1 last:mb-0 text-xs text-gray-700"
                       dangerouslySetInnerHTML={{ __html: line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*(.*?)\*/g, '<em>$1</em>') }}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-xs">{msg.text}</p>
              )}
              {msg.inline_charts && msg.inline_charts.length > 0 && (
                <div className="mt-2 bg-white border border-gray-200 rounded-lg overflow-hidden">
                  {msg.inline_charts.length > 1 && (
                    <div className="px-2 py-1.5 border-b border-gray-100">
                      <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{msg.inline_charts.length} charts</span>
                    </div>
                  )}
                  {msg.inline_charts.map((chart, ci) => (
                    <div key={ci} className={ci > 0 ? 'border-t border-gray-100' : ''}>
                      <div className="px-2 py-1.5 border-b border-gray-100 flex items-center gap-1.5">
                        <BarChart3 size={11} className="text-blue-500" />
                        <span className="text-xs font-medium text-gray-700">{chart.title}</span>
                        <span className="ml-auto text-[10px] text-gray-400">{chart.chart_type}</span>
                      </div>
                      <div className="h-40 p-1">
                        <ChartRenderer result={chartToResult(chart)} height={undefined} />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Retry — when AI described a chart but didn't generate one */}
              {msg.role === 'assistant' && !msg.inline_charts && msg.triggerMsg && isChartReq(msg.triggerMsg) && (
                <button
                  onClick={() => setInput(`GENERATE NOW as sql_execute block only, no description: ${msg.triggerMsg}`)}
                  className="mt-1.5 flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded-lg hover:bg-amber-100 transition-colors"
                >
                  <Sparkles size={11} className="text-amber-500" /> Generate chart
                </button>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-3 py-2">
              <Loader2 size={12} className="animate-spin text-blue-500" />
            </div>
          </div>
        )}
        {messages.length === 1 && !loading && (
          <div className="space-y-1.5 mt-2">
            <p className="text-xs text-gray-400 text-center">Try asking:</p>
            {SUGGESTIONS.map(s => (
              <button key={s} onClick={() => setInput(s)}
                className="w-full text-left px-2.5 py-1.5 text-xs text-gray-600 bg-gray-50 border border-gray-100 rounded-lg hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 transition-all">
                {s}
              </button>
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-3 py-3 border-t border-gray-100 flex-shrink-0">
        <div className="flex items-end gap-2 bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 focus-within:border-blue-400 focus-within:bg-white transition-all">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Ask about this data…"
            rows={1}
            className="flex-1 text-xs bg-transparent border-0 outline-none resize-none text-gray-800 placeholder-gray-400 max-h-24"
            style={{ minHeight: '20px' }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || loading}
            className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center disabled:opacity-40 transition-all"
            style={{ background: input.trim() ? 'linear-gradient(135deg, #2563EB, #7C3AED)' : '#E5E7EB' }}
          >
            <Send size={12} className={input.trim() ? 'text-white' : 'text-gray-400'} />
          </button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1.5 text-center">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>
  )
}
