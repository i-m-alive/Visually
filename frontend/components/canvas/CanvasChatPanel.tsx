'use client'
import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Bot, Loader2, X, Plus, Sparkles } from 'lucide-react'
import { chatApi, canvasApi, type WidgetCreate } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  inlineChart?: ChartResult
  newWidget?: {
    title: string
    chart_type: string
    sql: string
    chart_data?: Record<string, unknown>
  }
}

interface CanvasPage {
  id: string
  name: string
  order: number
}

interface Props {
  projectId: string
  canvasId: string
  widgets: CanvasWidgetData[]          // ALL widgets across all pages
  pages?: CanvasPage[]
  activePageId?: string
  onClose: () => void
  onWidgetAdded: () => void
}

function inlineRender(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
    if (p.startsWith('*') && p.endsWith('*')) return <em key={i}>{p.slice(1, -1)}</em>
    if (p.startsWith('`') && p.endsWith('`')) return <code key={i} style={{ fontFamily: 'monospace', fontSize: '0.88em', background: 'rgba(0,0,0,0.08)', padding: '1px 4px', borderRadius: 3 }}>{p.slice(1, -1)}</code>
    return p
  })
}

function MarkdownText({ text }: { text: string }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    const h3 = line.match(/^###\s+(.+)/)
    const h2 = line.match(/^##\s+(.+)/)
    const h1 = line.match(/^#\s+(.+)/)
    if (h1 || h2 || h3) {
      const txt = (h1?.[1] ?? h2?.[1] ?? h3?.[1] ?? '').trim()
      elements.push(<p key={i} style={{ fontSize: h1 ? 13 : 12, fontWeight: 700, color: 'inherit', margin: '8px 0 3px', lineHeight: 1.3 }}>{inlineRender(txt)}</p>)
      i++; continue
    }
    if (/^[•\-\*]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[•\-\*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[•\-\*]\s+/, '').trim()); i++ }
      elements.push(<ul key={`ul-${i}`} style={{ margin: '3px 0', paddingLeft: 16, color: 'inherit' }}>{items.map((it, j) => <li key={j} style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 1 }}>{inlineRender(it)}</li>)}</ul>)
      continue
    }
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s+/, '').trim()); i++ }
      elements.push(<ol key={`ol-${i}`} style={{ margin: '3px 0', paddingLeft: 18, color: 'inherit' }}>{items.map((it, j) => <li key={j} style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 1 }}>{inlineRender(it)}</li>)}</ol>)
      continue
    }
    elements.push(<p key={i} style={{ fontSize: 12, lineHeight: 1.65, color: 'inherit', margin: '2px 0' }}>{inlineRender(line)}</p>)
    i++
  }
  return <div>{elements}</div>
}

function buildRecommendations(widgets: CanvasWidgetData[], pages: CanvasPage[]): string[] {
  const qs: string[] = []
  const kpis   = widgets.filter(w => ['kpi', 'kpi_card'].includes(w.chart_type))
  const charts  = widgets.filter(w => ['bar_vertical', 'bar', 'line', 'area'].includes(w.chart_type))
  const pies    = widgets.filter(w => ['pie', 'donut'].includes(w.chart_type))
  const tables  = widgets.filter(w => ['table', 'data_table'].includes(w.chart_type))

  if (kpis[0])   qs.push(`What is driving "${kpis[0].title}"?`)
  if (kpis[1])   qs.push(`How has "${kpis[1].title}" changed recently?`)
  if (charts[0]) qs.push(`Summarize trends in "${charts[0].title}"`)
  if (pies[0])   qs.push(`Which segment is highest in "${pies[0].title}"?`)
  if (tables[0]) qs.push(`Top 5 rows in "${tables[0].title}"`)
  if (pages.length > 1) qs.push(`Summarize all ${pages.length} pages of this report`)
  qs.push('Which metrics need the most attention?')
  qs.push('Create a chart showing overall performance')

  return Array.from(new Set(qs)).slice(0, 5)
}

export function CanvasChatPanel({ projectId, canvasId, widgets, pages = [], activePageId = '', onClose, onWidgetAdded }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([{
    role: 'assistant',
    content: 'Hi! I have full access to your canvas report (all pages) and your live database. Ask me to explore data, explain trends, or generate new charts.',
  }])
  const [input, setInput]     = useState('')
  const [sending, setSending]   = useState(false)
  const [sessionId]             = useState(() => `canvas-${canvasId}-${Date.now()}`)
  const [showSuggestions, setShowSuggestions] = useState(true)
  const endRef                  = useRef<HTMLDivElement>(null)
  const textareaRef             = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const connectionId = widgets.find(w => w.connection_id)?.connection_id
  const recommended  = buildRecommendations(widgets, pages)

  const send = useCallback(async (quickText?: string) => {
    const text = (quickText ?? input).trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setShowSuggestions(false)

    setMessages(prev => [...prev, { role: 'user', content: text }])

    try {
      const resp = await chatApi.send({
        session_id:    sessionId,
        message:       text,
        project_id:    projectId,
        dashboard_id:  canvasId,
        connection_id: connectionId,
        active_page_id: activePageId || undefined,
      })

      const data         = resp.data
      const responseText = data?.text || 'I could not generate a response.'

      // Use inline_chart returned by the backend (already executed SQL + data)
      let inlineChart: ChartResult | undefined
      if (data?.inline_chart) {
        const ic = data.inline_chart
        inlineChart = {
          chart_type:     ic.chart_type,
          title:          ic.title,
          sql:            ic.sql ?? '',
          score:          1,
          low_confidence: false,
          x_axis_label:   ic.x_axis_label ?? 'x',
          y_axis_label:   ic.y_axis_label ?? 'y',
          table_used:     '',
          chart_data:     ic.chart_data ?? { rows: [], columns: [], labels: [], values: [] },
        }
      }

      // Fallback: detect JSON chart spec in text
      let newWidget: ChatMsg['newWidget'] | undefined
      if (!inlineChart) {
        const jsonMatch = responseText.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/)
        if (jsonMatch) {
          try {
            const p = JSON.parse(jsonMatch[1])
            if (p.sql || p.chart_type) {
              newWidget = {
                title:      p.title      || 'New Chart',
                chart_type: p.chart_type || 'bar',
                sql:        p.sql        || '',
                chart_data: p.chart_data,
              }
            }
          } catch { /* not a chart JSON */ }
        }
      }

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: responseText,
        inlineChart,
        newWidget: inlineChart ? undefined : newWidget,
      }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Sorry, something went wrong. Please try again.' }])
    } finally {
      setSending(false)
    }
  }, [input, sending, sessionId, projectId, canvasId, connectionId, activePageId])

  const handleAddWidget = useCallback(async (src: ChartResult | ChatMsg['newWidget']) => {
    if (!src) return
    const widgetData: WidgetCreate = {
      title:         src.title,
      chart_type:    src.chart_type,
      sql_query:     src.sql,
      chart_data:    (src.chart_data as Record<string, unknown>) ?? undefined,
      config:        activePageId ? { page_id: activePageId } : undefined,
      width:         6,
      height:        5,
      connection_id: connectionId,
    }
    const activePage = pages.find(p => p.id === activePageId)
    const pageLabel  = activePage ? ` on "${activePage.name}"` : ''
    try {
      await canvasApi.addWidget(canvasId, widgetData)
      onWidgetAdded()
      setMessages(prev => [...prev, { role: 'assistant', content: `✓ Added "${src.title}"${pageLabel} to your canvas!` }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Failed to add the chart. Please try again.' }])
    }
  }, [canvasId, connectionId, onWidgetAdded, activePageId, pages])

  return (
    <div className="w-80 bg-white border-l border-gray-100 flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
            <Sparkles size={12} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-gray-900">Canvas Assistant</span>
        </div>
        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700 rounded transition-colors">
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-2 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div
              className="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-white text-xs"
              style={msg.role === 'assistant'
                ? { background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }
                : { background: '#9CA3AF' }}
            >
              {msg.role === 'user' ? <Bot size={11} /> : <Sparkles size={10} />}
            </div>
            <div className="flex flex-col gap-2 max-w-[85%]">
              <div className={`px-3 py-2 rounded-2xl text-xs leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-brand text-white rounded-tr-sm'
                  : 'bg-gray-100 text-gray-800 rounded-tl-sm'
              }`}>
                {msg.role === 'assistant'
                  ? <MarkdownText text={msg.content} />
                  : msg.content}
              </div>

              {/* Inline chart preview */}
              {msg.inlineChart && (
                <div className="bg-gray-50 border border-gray-200 rounded-xl p-3">
                  <p className="text-xs font-semibold text-gray-700 mb-2">{msg.inlineChart.title}</p>
                  <ChartRenderer result={msg.inlineChart} height={150} />
                  <button
                    onClick={() => handleAddWidget(msg.inlineChart!)}
                    className="mt-2.5 w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-brand rounded-lg transition-colors"
                    style={{ background: 'linear-gradient(135deg, #EFF6FF, #F5F3FF)', border: '1px solid #BFDBFE' }}
                  >
                    <Plus size={11} /> Add to Canvas
                  </button>
                </div>
              )}

              {/* JSON widget add button */}
              {msg.newWidget && (
                <button
                  onClick={() => handleAddWidget(msg.newWidget!)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-brand bg-brand/10 rounded-lg hover:bg-brand/20 transition-colors self-start"
                >
                  <Plus size={12} /> Add to canvas
                </button>
              )}
            </div>
          </div>
        ))}

        {sending && (
          <div className="flex gap-2">
            <div className="w-6 h-6 rounded-full flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
              <Sparkles size={10} className="text-white" />
            </div>
            <div className="px-3 py-2 bg-gray-100 rounded-2xl rounded-tl-sm">
              <Loader2 size={12} className="animate-spin text-gray-400" />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Suggested questions — visible until user sends first message */}
      {showSuggestions && recommended.length > 0 && (
        <div className="px-3 py-2.5 border-t border-gray-100 flex-shrink-0 space-y-1.5">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Suggested</p>
          {recommended.map((q, i) => (
            <button
              key={i}
              onClick={() => send(q)}
              className="w-full text-left px-2.5 py-1.5 text-xs text-gray-600 bg-gray-50 hover:bg-blue-50 hover:text-brand border border-gray-100 hover:border-blue-200 rounded-lg transition-colors leading-snug"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="px-3 py-3 border-t border-gray-100 flex-shrink-0">
        <div className="flex items-end gap-2 bg-gray-50 rounded-xl px-3 py-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => {
              setInput(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 80) + 'px'
            }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Ask about your data or create a chart…"
            rows={1}
            className="flex-1 bg-transparent text-xs text-gray-800 placeholder-gray-400 outline-none resize-none leading-relaxed"
            style={{ maxHeight: 80, overflowY: 'auto' }}
          />
          <button
            onClick={() => send()}
            disabled={!input.trim() || sending}
            className="p-1.5 text-white rounded-lg disabled:opacity-40 transition-colors flex-shrink-0"
            style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
          >
            <Send size={12} />
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-1.5 text-center">Full DB access · All pages · Enter to send</p>
      </div>
    </div>
  )
}
