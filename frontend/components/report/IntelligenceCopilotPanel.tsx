'use client'
/**
 * IntelligenceCopilotPanel — the "Report Copilot" on the intelligence page.
 *
 * FORKED FROM components/canvas/CanvasChatPanel.tsx on 2026-06-18.
 *
 * Functionally identical to the Canvas Assistant, but a standalone component so
 * the Report Copilot's UI/UX can diverge without touching the canvas builder.
 * The one wiring difference: it streams from streamIntelligenceChat() →
 * /intelligence/chat/stream (the forked backend) instead of the canvas
 * /agent/chat/stream, and uses an `intel-` session-id prefix.
 */
import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Bot, Loader2, X, Plus, Sparkles, FileText, Database } from 'lucide-react'
import { streamIntelligenceChat, canvasApi, type WidgetCreate } from '@/lib/api'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'

type InlineChart = ChartResult & { selected: boolean }

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  inlineCharts?: InlineChart[]
  newWidget?: {
    title: string
    chart_type: string
    sql: string
    chart_data?: Record<string, unknown>
  }
  triggerMsg?: string  // the user message that produced this response (for retry)
}

const CHART_CREATION_WORDS = /\b(create|make|build|generate|add|show|give|draw|produce)\b/i
const CHART_SUBJECT_WORDS  = /\b(chart|graph|pie|bar|kpi|table|visual|visualization|plot|donut|scatter|waterfall)\b/i

function isChartCreationRequest(text: string) {
  return CHART_CREATION_WORDS.test(text) && CHART_SUBJECT_WORDS.test(text)
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
  // optional overrides — the intelligence page passes a "Report Copilot" identity
  title?: string
  subtitle?: string
  suggestedQuestions?: string[]
  initialWidth?: number
  onAddToPage?: (charts: Array<ChartResult | ChatMsg['newWidget']>) => void
  prefillMessage?: string
  isOffline?: boolean   // offline canvas: query bundled tables, hide DB/scope picker
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

export function IntelligenceCopilotPanel({ projectId, canvasId, widgets, pages = [], activePageId = '', onClose, onWidgetAdded, title, subtitle, suggestedQuestions, initialWidth, onAddToPage, prefillMessage, isOffline }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([{
    role: 'assistant',
    content: title
      ? `Hi! I'm your **${title}**. I have full access to your report data and live database. Ask me anything about the data, explore trends, or generate new charts.`
      : 'Hi! I have full access to your intelligence report (all pages) and your live database. Ask me to explore data, explain trends, or generate new charts.',
  }])
  const [input, setInput]     = useState('')
  const [sending, setSending]   = useState(false)
  // `intel-` prefix keeps Report-Copilot sessions distinct from canvas chat sessions.
  const [sessionId]             = useState(() => `intel-${canvasId}-${Date.now()}`)
  const [showSuggestions, setShowSuggestions] = useState(true)
  // Scope toggle — 'report' (default) limits the copilot to this report's tables +
  // their 2-hop FK neighbours; 'database' opens the full schema for free exploration.
  const [scope, setScope]       = useState<'report' | 'database'>('report')
  const endRef                  = useRef<HTMLDivElement>(null)
  const textareaRef             = useRef<HTMLTextAreaElement>(null)
  const [panelWidth, setPanelWidth] = useState(initialWidth ?? 320)
  const resizingRef  = useRef(false)
  const resizeStartX = useRef(0)
  const resizeStartW = useRef(initialWidth ?? 320)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])
  useEffect(() => {
    if (prefillMessage) { setInput(prefillMessage); setTimeout(() => textareaRef.current?.focus(), 100) }
  }, [prefillMessage])

  const connectionId = widgets.find(w => w.connection_id)?.connection_id
  const widgetRecs   = buildRecommendations(widgets, pages)
  const recommended  = suggestedQuestions?.length
    ? Array.from(new Set([...suggestedQuestions, ...widgetRecs])).slice(0, 6)
    : widgetRecs

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizingRef.current  = true
    resizeStartX.current = e.clientX
    resizeStartW.current = panelWidth
    const onMove = (ev: MouseEvent) => {
      if (!resizingRef.current) return
      const delta = resizeStartX.current - ev.clientX
      setPanelWidth(Math.min(640, Math.max(260, resizeStartW.current + delta)))
    }
    const onUp = () => {
      resizingRef.current = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [panelWidth])

  const send = useCallback(async (quickText?: string) => {
    const text = (quickText ?? input).trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setShowSuggestions(false)

    setMessages(prev => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', triggerMsg: text },
    ])

    const toInlineChart = (ic: Record<string, unknown>): InlineChart => {
      const extra: Record<string, unknown> = {}
      if (ic.slicer_type)   extra.slicer_type   = ic.slicer_type
      if (ic.slicer_column) extra.slicer_column = ic.slicer_column
      return {
        chart_type:     ic.chart_type as string,
        title:          ic.title as string,
        sql:            (ic.sql as string) ?? '',
        score:          1,
        low_confidence: false,
        x_axis_label:   (ic.x_axis_label as string) ?? 'x',
        y_axis_label:   (ic.y_axis_label as string) ?? 'y',
        table_used:     '',
        chart_data:     (ic.chart_data as ChartResult['chart_data']) ?? { rows: [], columns: [], labels: [], values: [] },
        extra_config:   Object.keys(extra).length ? extra : undefined,
        selected:       true,
      }
    }

    const updateAssistant = (mut: (m: ChatMsg) => ChatMsg) => {
      setMessages(prev => {
        const next = [...prev]
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === 'assistant') { next[i] = mut(next[i]); break }
        }
        return next
      })
    }

    let acc = ''
    try {
      await streamIntelligenceChat(
        {
          session_id:     sessionId,
          message:        text,
          project_id:     projectId,
          dashboard_id:   canvasId,
          connection_id:  connectionId,
          active_page_id: activePageId || undefined,
          scope: isOffline ? 'report' : scope,
        },
        {
          onText: (delta) => { acc += delta; updateAssistant(m => ({ ...m, content: acc })) },
          onChart: (chart) => { updateAssistant(m => ({ ...m, inlineCharts: [toInlineChart(chart)] })) },
          onError: (message) => {
            const detail = message && message !== 'stream error' ? message : 'Sorry, something went wrong. Please try again.'
            updateAssistant(m => ({
              ...m,
              content: (m.content || '') + (m.content ? '\n\n' : '') + detail,
            }))
          },
        },
      )
      updateAssistant(m =>
        (!m.content && !m.inlineCharts)
          ? { ...m, content: 'I could not generate a response.' }
          : m
      )
    } catch {
      updateAssistant(m => ({ ...m, content: m.content || 'Sorry, something went wrong. Please try again.' }))
    } finally {
      setSending(false)
    }
  }, [input, sending, sessionId, projectId, canvasId, connectionId, activePageId, scope])

  const handleAddWidgets = useCallback(async (srcs: Array<ChartResult | ChatMsg['newWidget']>) => {
    if (!srcs.length) return
    const activePage = pages.find(p => p.id === activePageId)
    const pageLabel  = activePage ? ` on "${activePage.name}"` : ''
    try {
      await Promise.all(srcs.map(src => {
        if (!src) return Promise.resolve()
        const extraCfg = (src as ChartResult).extra_config ?? {}
        const widgetData: WidgetCreate = {
          title:         src.title,
          chart_type:    src.chart_type,
          sql_query:     src.sql,
          chart_data:    (src.chart_data as Record<string, unknown>) ?? undefined,
          config:        { ...(activePageId ? { page_id: activePageId } : {}), ...extraCfg },
          width:         src.chart_type === 'slicer' ? 3 : 6,
          height:        src.chart_type === 'slicer' ? 2 : 5,
          connection_id: connectionId,
        }
        return canvasApi.addWidget(canvasId, widgetData)
      }))
      onWidgetAdded()
      const names = srcs.filter(Boolean).map(s => `"${s!.title}"`).join(', ')
      setMessages(prev => [...prev, { role: 'assistant', content: `✓ Added ${names}${pageLabel} to your report!` }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Failed to add charts. Please try again.' }])
    }
  }, [canvasId, connectionId, onWidgetAdded, activePageId, pages])

  const toggleChart = useCallback((msgIdx: number, chartIdx: number) => {
    setMessages(prev => prev.map((m, mi) => mi !== msgIdx ? m : {
      ...m,
      inlineCharts: m.inlineCharts?.map((c, ci) => ci !== chartIdx ? c : { ...c, selected: !c.selected }),
    }))
  }, [])

  const toggleAllCharts = useCallback((msgIdx: number, val: boolean) => {
    setMessages(prev => prev.map((m, mi) => mi !== msgIdx ? m : {
      ...m,
      inlineCharts: m.inlineCharts?.map(c => ({ ...c, selected: val })),
    }))
  }, [])

  return (
    <div className="relative flex h-full" style={{ width: panelWidth, flexShrink: 0 }}>
      {/* Resize handle — sits outside overflow:hidden so it's always clickable */}
      <div
        onMouseDown={startResize}
        className="absolute left-0 top-0 bottom-0 w-2 cursor-col-resize z-30 flex items-center justify-center group"
        style={{ left: -4 }}
      >
        <div className="w-1 h-10 rounded-full bg-gray-300 group-hover:bg-blue-400 transition-colors" />
      </div>
      <div className="relative bg-white flex flex-col h-full w-full" style={{ borderRadius: 16, overflow: 'hidden', border: '1px solid #e2eaf4' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 flex-shrink-0" style={{ background: 'linear-gradient(135deg, #0a213a, #0d3060)' }}>
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: 'linear-gradient(135deg, #00b4d8, #0077b6)' }}>
            <Sparkles size={13} className="text-white" />
          </div>
          <div>
            <span className="text-sm font-semibold text-white">{title ?? 'Report Copilot'}</span>
            {subtitle && <p className="text-[10px] leading-tight" style={{ color: 'rgba(255,255,255,0.45)', margin: 0 }}>{subtitle}</p>}
          </div>
        </div>
        <button onClick={onClose} className="p-1 rounded transition-colors" style={{ color: 'rgba(255,255,255,0.5)' }}
          onMouseEnter={e => (e.currentTarget.style.color = 'white')}
          onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.5)')}>
          <X size={16} />
        </button>
      </div>

      {/* Scope row — toggle when live; a fixed "offline" indicator when offline
          (no DB to switch to; the copilot queries the report's bundled tables). */}
      {isOffline ? (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100 flex-shrink-0" style={{ background: '#f8fafc' }}>
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold" style={{ background: '#eef2ff', color: '#4338ca', border: '1px solid #c7d2fe' }}>
            <Database size={11} /> Offline data
          </span>
          <span className="text-[10px] leading-tight" style={{ color: '#94a3b8' }}>
            Answering from the report’s bundled tables (no live database)
          </span>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100 flex-shrink-0" style={{ background: '#f8fafc' }}>
          <div className="flex items-center gap-1 p-0.5 rounded-lg flex-shrink-0" style={{ background: '#eef2f7', border: '1px solid #e2eaf4' }}>
            <button
              onClick={() => setScope('report')}
              title="Answer using only the tables/views that build this report (+ closely related ones). Faster and more focused."
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors"
              style={scope === 'report'
                ? { background: '#fff', color: '#0d3060', boxShadow: '0 1px 3px rgba(10,33,58,0.12)' }
                : { background: 'transparent', color: '#64748b' }}
            >
              <FileText size={11} /> Report
            </button>
            <button
              onClick={() => setScope('database')}
              title="Open the full database schema — the copilot can query any table or view, not just the report's."
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors"
              style={scope === 'database'
                ? { background: '#fff', color: '#0d3060', boxShadow: '0 1px 3px rgba(10,33,58,0.12)' }
                : { background: 'transparent', color: '#64748b' }}
            >
              <Database size={11} /> Full DB
            </button>
          </div>
          <span className="text-[10px] leading-tight" style={{ color: '#94a3b8' }}>
            {scope === 'report'
              ? 'Scoped to this report’s tables + related ones'
              : 'Full database — query any table or view'}
          </span>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {/* Suggested questions rendered inline after greeting — never push greeting off screen */}
        {showSuggestions && recommended.length > 0 && messages.length === 1 && (
          <div className="ml-8">
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Suggested</p>
            <div className="flex flex-col gap-1.5">
              {recommended.map((q, i) => (
                <button
                  key={i}
                  onClick={() => send(q)}
                  className="w-full text-left px-2.5 py-1.5 text-xs text-gray-600 bg-gray-50 hover:bg-blue-50 hover:text-blue-700 border border-gray-100 hover:border-blue-200 rounded-lg transition-colors leading-snug"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
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
            <div className="flex flex-col gap-2 max-w-[85%] min-w-0">
              <div className={`px-3 py-2 rounded-2xl text-xs leading-relaxed break-words min-w-0 ${
                msg.role === 'user'
                  ? 'bg-brand text-white rounded-tr-sm'
                  : 'bg-gray-100 text-gray-800 rounded-tl-sm'
              }`}>
                {msg.role === 'assistant'
                  ? (msg.content
                      ? <MarkdownText text={msg.content} />
                      : <Loader2 size={12} className="animate-spin text-gray-400" />)
                  : msg.content}
              </div>

              {/* Inline chart previews */}
              {msg.inlineCharts && msg.inlineCharts.length > 0 && (
                msg.inlineCharts.length === 1 ? (
                  // Single chart — compact preview
                  <div className="bg-gray-50 border border-gray-200 rounded-xl p-3">
                    <p className="text-xs font-semibold text-gray-700 mb-2">{msg.inlineCharts[0].title}</p>
                    <ChartRenderer result={msg.inlineCharts[0]} height={172} legend />
                    <div className="mt-2.5">
                      {onAddToPage ? (
                        <button
                          onClick={() => onAddToPage([msg.inlineCharts![0]])}
                          className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors"
                          style={{ background: 'linear-gradient(135deg, #ecfdf5, #d1fae5)', border: '1px solid #6ee7b7', color: '#065f46' }}
                        >
                          <Plus size={11} /> Add to Section
                        </button>
                      ) : (
                        <button
                          onClick={() => handleAddWidgets([msg.inlineCharts![0]])}
                          className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-brand rounded-lg transition-colors"
                          style={{ background: 'linear-gradient(135deg, #EFF6FF, #F5F3FF)', border: '1px solid #BFDBFE' }}
                        >
                          <Plus size={11} /> Add to Report
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  // Multiple charts — selectable list
                  <div className="bg-gray-50 border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-3 py-2 border-b border-gray-100 flex items-center justify-between">
                      <span className="text-xs font-semibold text-gray-600">{msg.inlineCharts.length} charts generated</span>
                      <div className="flex items-center gap-2">
                        <button onClick={() => toggleAllCharts(i, true)} className="text-[10px] text-indigo-500 hover:text-indigo-700">All</button>
                        <span className="text-gray-300 text-[10px]">·</span>
                        <button onClick={() => toggleAllCharts(i, false)} className="text-[10px] text-gray-400 hover:text-gray-600">None</button>
                      </div>
                    </div>
                    {msg.inlineCharts.map((chart, ci) => (
                      <div key={ci} className={`p-3 border-b border-gray-100 last:border-0 transition-opacity ${chart.selected ? '' : 'opacity-50'}`}>
                        <label className="flex items-center gap-2 mb-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={chart.selected}
                            onChange={() => toggleChart(i, ci)}
                            className="w-3.5 h-3.5 rounded accent-indigo-600 cursor-pointer"
                          />
                          <span className="text-xs font-semibold text-gray-700 truncate flex-1">{chart.title}</span>
                          <span className="text-[10px] text-gray-400 shrink-0 bg-gray-100 px-1.5 py-0.5 rounded">{chart.chart_type}</span>
                        </label>
                        <ChartRenderer result={chart} height={134} legend />
                      </div>
                    ))}
                    <div className="px-3 py-2.5 bg-white">
                      {onAddToPage ? (
                        <button
                          onClick={() => onAddToPage(msg.inlineCharts!.filter(c => c.selected))}
                          disabled={!msg.inlineCharts.some(c => c.selected)}
                          className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-semibold rounded-lg disabled:opacity-40"
                          style={{ background: 'linear-gradient(135deg, #ecfdf5, #d1fae5)', border: '1px solid #6ee7b7', color: '#065f46' }}
                        >
                          <Plus size={10} /> Add to Section ({msg.inlineCharts.filter(c => c.selected).length})
                        </button>
                      ) : (
                        <button
                          onClick={() => handleAddWidgets(msg.inlineCharts!.filter(c => c.selected))}
                          disabled={!msg.inlineCharts.some(c => c.selected)}
                          className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-semibold text-white rounded-lg disabled:opacity-40 transition-opacity"
                          style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
                        >
                          <Plus size={10} /> Add to Report ({msg.inlineCharts.filter(c => c.selected).length})
                        </button>
                      )}
                    </div>
                  </div>
                )
              )}

              {/* JSON widget add button */}
              {msg.newWidget && (
                onAddToPage ? (
                  <button
                    onClick={() => onAddToPage([msg.newWidget!])}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg self-start"
                    style={{ background: 'linear-gradient(135deg, #ecfdf5, #d1fae5)', border: '1px solid #6ee7b7', color: '#065f46' }}
                  >
                    <Plus size={12} /> Add to Section
                  </button>
                ) : (
                  <button
                    onClick={() => handleAddWidgets([msg.newWidget!])}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-brand bg-brand/10 rounded-lg hover:bg-brand/20 transition-colors self-start"
                  >
                    <Plus size={12} /> Add to Report
                  </button>
                )
              )}

              {/* Retry button — shown when AI described a chart but didn't generate it */}
              {msg.role === 'assistant' && !msg.inlineCharts && !msg.newWidget && msg.triggerMsg && isChartCreationRequest(msg.triggerMsg) && (
                <button
                  onClick={() => send(`GENERATE NOW as sql_execute block only, no description: ${msg.triggerMsg}`)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded-lg hover:bg-amber-100 transition-colors self-start"
                >
                  <Sparkles size={11} className="text-amber-500" /> Generate chart
                </button>
              )}
            </div>
          </div>
        ))}

        {/* "More coming" indicator — shown only after prose has started streaming */}
        {sending && messages[messages.length - 1]?.role === 'assistant' && !!messages[messages.length - 1]?.content && (
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
            placeholder="Ask about your report or create a chart…"
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
        <p className="text-xs text-gray-400 mt-1.5 text-center">{isOffline ? 'Offline data · All pages · Enter to send' : 'Full DB access · All pages · Enter to send'}</p>
      </div>
      </div>{/* end overflow:hidden inner panel */}
    </div>
  )
}
