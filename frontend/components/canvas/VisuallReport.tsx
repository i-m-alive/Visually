'use client'
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  X, Send, Loader2, Plus, BarChart2, Table2, Sparkles,
  LayoutGrid, MessageSquare, ChevronRight, TrendingUp, TrendingDown,
  CheckCircle2, Copy, List, Sun, Moon, Zap, Info, ChevronDown, ChevronUp,
} from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { chatApi, canvasApi, type WidgetCreate } from '@/lib/api'
import type { ChartResult } from '@/stores/pipelineStore'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'

// ─── Theme System ──────────────────────────────────────────────────────────────

type ThemeId = 'executive' | 'lightpro' | 'midnight'

interface Theme {
  id: ThemeId; label: string
  bg: string; surface: string; border: string; text: string; muted: string
  accent: string; accentBg: string
  rail: string; railText: string; railActive: string
  heroFrom: string; heroMid: string; heroTo: string
}

const THEMES: Theme[] = [
  {
    id: 'executive', label: 'Executive',
    bg: '#EEF2F7', surface: '#FFFFFF', border: 'rgba(0,0,0,0.07)', text: '#0a2540', muted: '#6B7280',
    accent: '#2563EB', accentBg: '#EFF6FF',
    rail: '#0a2540', railText: 'rgba(255,255,255,0.45)', railActive: 'rgba(96,165,250,0.18)',
    heroFrom: '#0a2540', heroMid: '#1a4080', heroTo: '#7C3AED',
  },
  {
    id: 'lightpro', label: 'Light Pro',
    bg: '#F4F6FB', surface: '#FFFFFF', border: 'rgba(0,0,0,0.06)', text: '#111827', muted: '#9CA3AF',
    accent: '#7C3AED', accentBg: '#F5F3FF',
    rail: '#1E1B4B', railText: 'rgba(255,255,255,0.4)', railActive: 'rgba(167,139,250,0.2)',
    heroFrom: '#1E1B4B', heroMid: '#4F46E5', heroTo: '#EC4899',
  },
  {
    id: 'midnight', label: 'Midnight',
    bg: '#070D1A', surface: 'rgba(255,255,255,0.05)', border: 'rgba(255,255,255,0.09)', text: '#E8F4FF', muted: 'rgba(255,255,255,0.4)',
    accent: '#00D4FF', accentBg: 'rgba(0,212,255,0.08)',
    rail: '#040A14', railText: 'rgba(255,255,255,0.3)', railActive: 'rgba(0,212,255,0.12)',
    heroFrom: '#040A14', heroMid: '#0D2137', heroTo: '#1A3A6B',
  },
]

// ─── Micro-components ──────────────────────────────────────────────────────────

function AnimatedCounter({ value, duration = 900 }: { value: number; duration?: number }) {
  const [disp, setDisp] = useState(0)
  const raf = useRef(0)
  const t0 = useRef<number | null>(null)
  useEffect(() => {
    t0.current = null
    const eoe = (t: number) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t))
    const tick = (ts: number) => {
      if (!t0.current) t0.current = ts
      const p = Math.min((ts - t0.current) / duration, 1)
      setDisp(Math.round(value * eoe(p)))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf.current); t0.current = null }
  }, [value, duration])
  return <>{disp.toLocaleString()}</>
}

function Sparkline({ data, color, width = 64, height = 28 }: { data: number[]; color: string; width?: number; height?: number }) {
  if (data.length < 2) return null
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1
  const pts = data.map((v, i) =>
    `${(i / (data.length - 1)) * width},${height - ((v - mn) / rng) * (height * 0.85) - height * 0.075}`
  ).join(' ')
  return (
    <svg width={width} height={height} style={{ overflow: 'visible', display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TypewriterText({ text, active, speed = 10 }: { text: string; active: boolean; speed?: number }) {
  const [shown, setShown] = useState(active ? '' : text)
  useEffect(() => {
    if (!active) { setShown(text); return }
    setShown('')
    let i = 0
    const id = setInterval(() => { i++; setShown(text.slice(0, i)); if (i >= text.length) clearInterval(id) }, speed)
    return () => clearInterval(id)
  }, [text, active, speed])
  return <>{shown}</>
}

function SqlToggleSection({ sql, theme }: { sql: string; theme: Theme }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginBottom: 14 }}>
      <button onClick={() => setOpen(v => !v)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px',
        background: 'transparent', border: `1px solid ${theme.border}`, borderRadius: 8,
        fontSize: 11, fontWeight: 600, color: theme.muted, cursor: 'pointer', width: '100%', textAlign: 'left',
      }}>
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        View SQL Query
      </button>
      {open && (
        <pre style={{
          marginTop: 6, padding: '10px 12px', background: theme.bg, border: `1px solid ${theme.border}`,
          borderRadius: 8, fontSize: 10.5, color: theme.text, overflowX: 'auto', overflowY: 'auto',
          lineHeight: 1.55, fontFamily: '"SF Mono","JetBrains Mono",monospace', maxHeight: 200,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0,
        }}>{sql}</pre>
      )}
    </div>
  )
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

type SectionId = 'overview' | 'charts' | 'data'

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  typing?: boolean
  inlineChart?: ChartResult
  followUps?: string[]
  newWidget?: { title: string; chart_type: string; sql: string; chart_data?: Record<string, unknown> }
}

interface Props {
  canvas: { id: string; name: string; project_id: string }
  widgets: CanvasWidgetData[]
  projectId: string
  onClose: () => void
  onWidgetAdded: () => void
}

function getRecommendedQuestions(widgets: CanvasWidgetData[]): string[] {
  const qs: string[] = []
  const kpis   = widgets.filter(w => ['kpi', 'kpi_card'].includes(w.chart_type))
  const charts  = widgets.filter(w => ['bar', 'line'].includes(w.chart_type))
  const pies    = widgets.filter(w => ['pie', 'donut'].includes(w.chart_type))
  const tables  = widgets.filter(w => ['table', 'data_table'].includes(w.chart_type))
  if (kpis[0])   qs.push(`What is driving "${kpis[0].title}"?`)
  if (kpis[1])   qs.push(`How has "${kpis[1].title}" changed recently?`)
  if (charts[0]) qs.push(`Summarize trends in "${charts[0].title}"`)
  if (pies[0])   qs.push(`Which segment dominates in "${pies[0].title}"?`)
  if (tables[0]) qs.push(`Top 5 rows in "${tables[0].title}"`)
  qs.push('Summarize this dashboard in 3 bullet points')
  qs.push('Which metrics need the most attention?')
  qs.push('Create a chart showing month-over-month trends')
  return Array.from(new Set(qs)).slice(0, 6)
}

function toChartResult(w: CanvasWidgetData): ChartResult {
  return {
    chart_type: w.chart_type, title: '',
    chart_data: {
      rows:    (w.chart_data?.rows    as Record<string, unknown>[]) ?? [],
      columns: (w.chart_data?.columns as string[])                  ?? [],
      labels:  (w.chart_data?.labels  as string[])                  ?? [],
      values:  (w.chart_data?.values  as (number | null)[])         ?? [],
    },
    x_axis_label: (w.config?.x_axis_label as string) ?? '',
    y_axis_label: (w.config?.y_axis_label as string) ?? '',
    sql: w.sql_query ?? '', score: w.validation_score ?? 0,
    low_confidence: false, table_used: '',
  }
}

function getKpiMeta(w: CanvasWidgetData): { num: number; spark: number[]; delta: { pct: number; up: boolean } | null } {
  const vs = ((w.chart_data?.values as (number | null)[]) ?? []).filter((v): v is number => v !== null)
  const num  = vs[vs.length - 1] ?? 0
  const prev = vs.length >= 2 ? vs[vs.length - 2] : null
  const delta = prev !== null && prev !== 0
    ? { pct: Math.abs(((num - prev) / prev) * 100), up: num >= prev }
    : null
  return { num, spark: vs.slice(-7), delta }
}

function fmtNum(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function generateFollowUps(text: string, widgets: CanvasWidgetData[]): string[] {
  const qs: string[] = []
  if (/revenue|sales/i.test(text)) qs.push('Show revenue by month')
  if (/trend|growth|increase/i.test(text)) qs.push('Compare this to last quarter')
  if (widgets[0]?.title) qs.push(`Tell me more about "${widgets[0].title}"`)
  qs.push('Create a chart for this')
  return qs.slice(0, 3)
}

// ─── Main Component ────────────────────────────────────────────────────────────

export function VisuallReport({ canvas, widgets, projectId, onClose, onWidgetAdded }: Props) {
  const [themeIdx, setThemeIdx] = useState(() => {
    try { return parseInt(localStorage.getItem(`visually-theme-${canvas.id}`) ?? '0', 10) || 0 } catch { return 0 }
  })
  const theme = THEMES[Math.min(themeIdx, THEMES.length - 1)]

  const [activeSection, setActiveSection] = useState<SectionId>('overview')
  const [layoutMode, setLayoutMode]       = useState<'grid' | 'list'>('grid')
  const [showChat, setShowChat]           = useState(true)
  const [messages, setMessages]           = useState<ChatMsg[]>([{
    role: 'assistant',
    content: `Welcome to "${canvas.name}" — I have full context of all ${widgets.length} chart${widgets.length !== 1 ? 's' : ''}. Ask me anything or request new visualizations.`,
  }])
  const [input, setInput]       = useState('')
  const [sending, setSending]   = useState(false)
  const [sessionId]             = useState(() => `visually-${canvas.id}-${Date.now()}`)
  const [toast, setToast]       = useState<string | null>(null)
  const [execSummary, setExecSummary]   = useState('')
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryExpanded, setSummaryExpanded] = useState(true)
  // Info panel state
  const [infoWidgetId, setInfoWidgetId]           = useState<string | null>(null)
  const [widgetDescriptions, setWidgetDescriptions] = useState<Record<string, string>>({})
  const [descLoading, setDescLoading]             = useState<Record<string, boolean>>({})
  // Table accordion state — tables are collapsed by default
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  const endRef    = useRef<HTMLDivElement>(null)
  const toastRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connectionId   = widgets.find(w => w.connection_id)?.connection_id
  const kpis           = widgets.filter(w => ['kpi', 'kpi_card'].includes(w.chart_type))
  const visualCharts   = widgets.filter(w => ['bar', 'line', 'scatter', 'pie', 'donut', 'bar_horizontal'].includes(w.chart_type))
  const tables         = widgets.filter(w => ['table', 'data_table', 'pivot_table'].includes(w.chart_type))
  const recommended    = useMemo(() => getRecommendedQuestions(widgets), [widgets])

  // ── Inject CSS animations ────────────────────────────────────────────────
  useEffect(() => {
    const id = 'visually-report-styles'
    if (document.getElementById(id)) return
    const s = document.createElement('style')
    s.id = id
    s.textContent = `
      @keyframes visually-slideUp { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }
      @keyframes visually-shimmer { 0%{background-position:-400px 0} 100%{background-position:400px 0} }
      @keyframes visually-pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
      @keyframes visually-gradient { 0%{background-position:0% 50%} 50%{background-position:100% 50%} 100%{background-position:0% 50%} }
      @keyframes visually-fadeIn { from{opacity:0} to{opacity:1} }
      @keyframes visually-spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
      .vis-card { transition: transform 0.18s ease, box-shadow 0.18s ease; cursor: default; }
      .vis-card:hover { transform: translateY(-3px); box-shadow: 0 12px 32px rgba(0,0,0,0.13) !important; }
      .vis-info-btn { opacity: 0; transition: opacity 0.15s, border-color 0.15s, color 0.15s; }
      .vis-card:hover .vis-info-btn { opacity: 1; }
      .vis-info-btn.active { opacity: 1; }
    `
    document.head.appendChild(s)
    return () => { document.getElementById(id)?.remove() }
  }, [])

  // ── Persist theme ────────────────────────────────────────────────────────
  useEffect(() => {
    try { localStorage.setItem(`visually-theme-${canvas.id}`, String(themeIdx)) } catch {}
  }, [themeIdx, canvas.id])

  // ── Keyboard shortcuts ───────────────────────────────────────────────────
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Escape') {
        if (infoWidgetId) { setInfoWidgetId(null); return }
        onClose()
      }
      if (e.key === '1') setActiveSection('overview')
      if (e.key === '2' && visualCharts.length > 0) setActiveSection('charts')
      if (e.key === '3' && tables.length > 0) setActiveSection('data')
      if (e.key === 'c' || e.key === 'C') { setInfoWidgetId(null); setShowChat(v => !v) }
      if (e.key === 't' || e.key === 'T') setThemeIdx(v => (v + 1) % THEMES.length)
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose, visualCharts.length, tables.length])

  // ── Auto-generate executive summary ─────────────────────────────────────
  useEffect(() => {
    if (widgets.length === 0) return
    let cancelled = false
    setSummaryLoading(true)
    chatApi.send({
      session_id:    `exec-${canvas.id}`,
      message:       `Canvas widgets:\n${widgets.map(w => `- "${w.title}" (${w.chart_type})`).join('\n')}\n\nUser: Summarize this dashboard in 3 concise executive bullet points. Start each bullet with •`,
      project_id:    projectId,
      dashboard_id:  canvas.id,
      connection_id: connectionId,
    }).then(r => { if (!cancelled) setExecSummary(r.data?.text ?? '') })
      .catch(() => { if (!cancelled) setExecSummary('• Dashboard ready with real-time data\n• All charts loaded and responding\n• Ask the AI Copilot for deeper insights') })
      .finally(() => { if (!cancelled) setSummaryLoading(false) })
    return () => { cancelled = true }
  }, []) // fire once on mount — deps intentionally empty

  // ── Toast helper ─────────────────────────────────────────────────────────
  const showToast = useCallback((msg: string) => {
    setToast(msg)
    if (toastRef.current) clearTimeout(toastRef.current)
    toastRef.current = setTimeout(() => setToast(null), 3000)
  }, [])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  // ── Send chat message ────────────────────────────────────────────────────
  const send = useCallback(async (quickText?: string) => {
    const text = (quickText ?? input).trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setMessages(prev => [...prev, { role: 'user', content: text }])

    const ctx = widgets.map(w => `- "${w.title}" (${w.chart_type})${w.sql_query ? `\n  SQL: ${w.sql_query}` : ''}`).join('\n')
    const msg = widgets.length > 0 ? `Canvas widgets:\n${ctx}\n\nUser: ${text}` : text

    try {
      const resp = await chatApi.send({
        session_id:    sessionId,
        message:       msg,
        project_id:    projectId,
        dashboard_id:  canvas.id,
        connection_id: connectionId,
      })
      const data         = resp.data
      const responseText = data?.text || 'I could not generate a response.'

      let inlineChart: ChartResult | undefined
      if (data?.inline_chart) {
        const ic = data.inline_chart
        inlineChart = {
          chart_type: ic.chart_type, title: ic.title, sql: ic.sql ?? '',
          score: 1, low_confidence: false,
          x_axis_label: ic.x_axis_label ?? '', y_axis_label: ic.y_axis_label ?? '',
          table_used: '',
          chart_data: ic.chart_data ?? { rows: [], columns: [], labels: [], values: [] },
        }
      }

      let newWidget: ChatMsg['newWidget'] | undefined
      if (!inlineChart) {
        const m = responseText.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/)
        if (m) {
          try {
            const p = JSON.parse(m[1])
            if (p.sql || p.chart_type) newWidget = { title: p.title || 'New Chart', chart_type: p.chart_type || 'bar', sql: p.sql || '', chart_data: p.chart_data }
          } catch {}
        }
      }

      const followUps = (data?.suggested_followups as string[] | undefined) ?? generateFollowUps(responseText, widgets)

      setMessages(prev => [...prev, {
        role: 'assistant', content: responseText, typing: true,
        inlineChart, followUps,
        newWidget: inlineChart ? undefined : newWidget,
      }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Sorry, something went wrong. Please try again.' }])
    } finally {
      setSending(false)
    }
  }, [input, sending, sessionId, projectId, canvas.id, connectionId, widgets])

  // ── Add to canvas ────────────────────────────────────────────────────────
  const handleAddToCanvas = useCallback(async (src: ChartResult | ChatMsg['newWidget']) => {
    if (!src) return
    const w: WidgetCreate = {
      title: src.title, chart_type: src.chart_type, sql_query: src.sql,
      chart_data: (src.chart_data as Record<string, unknown>) ?? undefined,
      width: 6, height: 5, connection_id: connectionId,
    }
    try {
      await canvasApi.addWidget(canvas.id, w)
      onWidgetAdded()
      showToast(`✓ "${src.title}" added to canvas`)
      setMessages(prev => [...prev, { role: 'assistant', content: `✓ Added "${src.title}" to your canvas!` }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Failed to add the chart. Please try again.' }])
    }
  }, [canvas.id, connectionId, onWidgetAdded, showToast])

  // ── Copy chart as PNG ────────────────────────────────────────────────────
  const copyAsPng = useCallback(async (elId: string) => {
    const el = document.getElementById(elId)
    if (!el) return
    try {
      const h2c = (await import('html2canvas')).default
      const c = await h2c(el, { backgroundColor: theme.id === 'midnight' ? '#070D1A' : '#ffffff', scale: 2 })
      c.toBlob(b => { if (b) navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]) })
      showToast('Chart copied to clipboard!')
    } catch { showToast('Could not copy — try a different browser') }
  }, [theme.id, showToast])

  // ── Open info panel for a widget ─────────────────────────────────────────
  const openInfo = useCallback(async (w: CanvasWidgetData) => {
    setInfoWidgetId(w.id)
    if (widgetDescriptions[w.id] || descLoading[w.id]) return
    setDescLoading(prev => ({ ...prev, [w.id]: true }))
    try {
      const kpiNote = ['kpi', 'kpi_card'].includes(w.chart_type) ? (() => {
        const { num, delta } = getKpiMeta(w)
        return ` Current value: ${num.toLocaleString()}.${delta ? ` ${delta.up ? 'Up' : 'Down'} ${delta.pct.toFixed(1)}% vs prior.` : ''}`
      })() : ''
      const resp = await chatApi.send({
        session_id: `info-${w.id}-${canvas.id}`,
        message: `Chart: "${w.title}" (type: ${w.chart_type}).${w.sql_query ? ` SQL: ${w.sql_query}` : ''}${kpiNote}\n\nWrite a 3-sentence insight note: (1) what does this ${w.chart_type} specifically measure, (2) what is the key signal or trend visible in the data, (3) what business action this metric suggests. Be specific to the numbers. Prose only — no bullet points or headers.`,
        project_id: projectId,
        connection_id: connectionId,
      })
      setWidgetDescriptions(prev => ({ ...prev, [w.id]: resp.data?.text ?? 'Unable to generate insight.' }))
    } catch {
      setWidgetDescriptions(prev => ({ ...prev, [w.id]: `This ${w.chart_type} displays ${w.title} from your connected database.` }))
    } finally {
      setDescLoading(prev => ({ ...prev, [w.id]: false }))
    }
  }, [widgetDescriptions, descLoading, projectId, connectionId, canvas.id])

  // ── Shared styles ────────────────────────────────────────────────────────
  const cardBase: React.CSSProperties = {
    background: theme.surface, borderRadius: 16, padding: '18px 20px',
    border: `1px solid ${theme.border}`,
    boxShadow: theme.id === 'midnight'
      ? '0 4px 24px rgba(0,0,0,0.5)'
      : '0 1px 4px rgba(0,0,0,0.06), 0 4px 14px rgba(0,0,0,0.04)',
    animation: 'visually-slideUp 0.4s ease both',
  }

  const secLabel: React.CSSProperties = {
    fontSize: 10, fontWeight: 700, color: theme.muted,
    textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 12px',
  }

  // ── KPI card renderer ────────────────────────────────────────────────────
  const renderKpi = (w: CanvasWidgetData, idx: number) => {
    const { num, spark, delta } = getKpiMeta(w)
    const hasValues = spark.length > 0
    const borderColor = delta ? (delta.up ? '#16A34A' : '#DC2626') : theme.accent
    const isInfoOpen = infoWidgetId === w.id
    return (
      <div key={w.id} id={`vr-kpi-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-kpi-${w.id}`) }}
        style={{ ...cardBase, animationDelay: `${idx * 60}ms`, borderLeft: `3px solid ${borderColor}`, position: 'relative' }}
      >
        {/* Info button — appears on hover or when panel is open */}
        <button
          onClick={(e) => { e.stopPropagation(); openInfo(w) }}
          title="Explain this metric"
          className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
          style={{
            position: 'absolute', top: 10, right: 10,
            width: 22, height: 22, borderRadius: '50%', padding: 0,
            background: isInfoOpen ? theme.accentBg : 'transparent',
            border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`,
            color: isInfoOpen ? theme.accent : theme.muted,
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        ><Info size={11} /></button>

        <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 6px', paddingRight: 26 }}>{w.title}</p>
        {hasValues ? (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
              <p style={{ fontSize: 28, fontWeight: 800, color: theme.text, margin: 0, fontVariantNumeric: 'tabular-nums', lineHeight: 1, fontFamily: '"SF Mono","JetBrains Mono",monospace' }}>
                <AnimatedCounter value={num} />
              </p>
              {spark.length >= 2 && <Sparkline data={spark} color={borderColor} />}
            </div>
            {delta && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8 }}>
                {delta.up ? <TrendingUp size={11} color="#16A34A" /> : <TrendingDown size={11} color="#DC2626" />}
                <span style={{ fontSize: 11, fontWeight: 600, color: delta.up ? '#16A34A' : '#DC2626' }}>
                  {delta.pct.toFixed(1)}% vs prior
                </span>
              </div>
            )}
          </>
        ) : (
          <ChartRenderer result={toChartResult(w)} height={72} />
        )}
      </div>
    )
  }

  // ── Chart card renderer ──────────────────────────────────────────────────
  const renderChart = (w: CanvasWidgetData, idx: number, h = 220) => {
    const isInfoOpen = infoWidgetId === w.id
    return (
      <div key={w.id} id={`vr-chart-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-chart-${w.id}`) }}
        style={{ ...cardBase, animationDelay: `${idx * 60}ms` }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 8 }}>
          <p style={{ fontSize: 13, fontWeight: 700, color: theme.text, margin: 0, flex: 1 }}>{w.title}</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {/* Info button */}
            <button onClick={(e) => { e.stopPropagation(); openInfo(w) }} title="Explain this chart"
              className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
              style={{
                width: 24, height: 24, borderRadius: '50%', padding: 0,
                background: isInfoOpen ? theme.accentBg : 'transparent',
                border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`,
                color: isInfoOpen ? theme.accent : theme.muted,
                cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            ><Info size={12} /></button>
            {/* Copy button */}
            <button onClick={() => copyAsPng(`vr-chart-${w.id}`)} title="Copy as PNG"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 3, color: theme.muted, opacity: 0.45 }}
              onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
              onMouseLeave={e => (e.currentTarget.style.opacity = '0.45')}
            ><Copy size={12} /></button>
          </div>
        </div>
        {w.chart_data
          ? <ChartRenderer result={toChartResult(w)} height={h} />
          : <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>}
      </div>
    )
  }

  // ── Section content ──────────────────────────────────────────────────────
  const renderContent = () => {
    if (widgets.length === 0) return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 320, gap: 14 }}>
        <div style={{ width: 64, height: 64, borderRadius: 20, background: theme.accentBg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Sparkles size={28} color={theme.accent} />
        </div>
        <p style={{ fontSize: 15, fontWeight: 700, color: theme.text, margin: 0 }}>No charts yet</p>
        <p style={{ fontSize: 13, color: theme.muted, margin: 0 }}>Ask the AI Copilot to create visualizations</p>
        <button onClick={() => setShowChat(true)} style={{
          padding: '10px 22px', background: theme.accent, border: 'none', borderRadius: 10,
          fontSize: 13, fontWeight: 600, color: 'white', cursor: 'pointer',
        }}>Open AI Copilot</button>
      </div>
    )

    const cols = layoutMode === 'list' ? '1fr' : 'repeat(auto-fill, minmax(340px, 1fr))'
    const chartH = layoutMode === 'list' ? 280 : 220

    return (
      <div style={{ padding: '20px 28px 32px' }}>
        {/* Key Findings Strip */}
        {(kpis.length > 0 || visualCharts.length > 0) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 22, overflowX: 'auto', paddingBottom: 2 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0 }}>Live</span>
            {kpis.map((w, i) => {
              const { num, delta } = getKpiMeta(w)
              const col = delta?.up === false ? '#DC2626' : delta?.up ? '#16A34A' : theme.accent
              return (
                <span key={i} style={{
                  flexShrink: 0, padding: '4px 12px',
                  background: delta?.up === false ? '#FEF2F2' : delta?.up ? '#F0FDF4' : theme.accentBg,
                  border: `1px solid ${col}44`,
                  borderRadius: 20, fontSize: 11, fontWeight: 600, color: col,
                  display: 'flex', alignItems: 'center', gap: 4,
                }}>
                  {delta?.up ? <TrendingUp size={10} /> : delta?.up === false ? <TrendingDown size={10} /> : null}
                  {w.title}: {fmtNum(num)}
                </span>
              )
            })}
            {visualCharts.length > 0 && (
              <span style={{ flexShrink: 0, padding: '4px 12px', background: theme.accentBg, border: `1px solid ${theme.border}`, borderRadius: 20, fontSize: 11, fontWeight: 600, color: theme.accent }}>
                {visualCharts.length} trend chart{visualCharts.length > 1 ? 's' : ''}
              </span>
            )}
          </div>
        )}

        {/* KPIs — overview only */}
        {activeSection === 'overview' && kpis.length > 0 && (
          <div style={{ marginBottom: 28 }}>
            <p style={secLabel}>Key Metrics</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
              {kpis.map((w, i) => renderKpi(w, i))}
            </div>
          </div>
        )}

        {/* Visual charts */}
        {(activeSection === 'overview' || activeSection === 'charts') && visualCharts.length > 0 && (
          <div style={{ marginBottom: 28 }}>
            {activeSection === 'overview' && <p style={secLabel}>Charts</p>}
            <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 14 }}>
              {visualCharts.map((w, i) => renderChart(w, i, chartH))}
            </div>
          </div>
        )}

        {/* Data tables — accordion: collapsed by default, click header to expand */}
        {(activeSection === 'overview' || activeSection === 'data') && tables.length > 0 && (
          <div>
            {activeSection === 'overview' && <p style={secLabel}>Data Tables</p>}
            {activeSection === 'data' && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                <p style={secLabel}>Data Tables ({tables.length})</p>
                <button
                  onClick={() => setExpandedTables(prev => {
                    if (prev.size === tables.length) return new Set()
                    return new Set(tables.map(t => t.id))
                  })}
                  style={{ fontSize: 11, fontWeight: 600, color: theme.accent, background: 'none', border: 'none', cursor: 'pointer', padding: '2px 0' }}
                >
                  {expandedTables.size === tables.length ? 'Collapse all' : 'Expand all'}
                </button>
              </div>
            )}
            {tables.map((w, i) => {
              const isExpanded = expandedTables.has(w.id)
              const isInfoOpen = infoWidgetId === w.id
              const rows = (w.chart_data?.rows as Record<string, unknown>[] | undefined) ?? []
              return (
                <div key={w.id} id={`vr-table-${w.id}`} className="vis-card"
                  style={{ ...cardBase, animationDelay: `${i * 80}ms`, marginBottom: 10, padding: 0, overflow: 'hidden' }}
                >
                  {/* Accordion header */}
                  <div
                    onClick={() => setExpandedTables(prev => {
                      const next = new Set(prev)
                      if (next.has(w.id)) next.delete(w.id); else next.add(w.id)
                      return next
                    })}
                    style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '13px 16px', cursor: 'pointer', userSelect: 'none',
                      borderBottom: isExpanded ? `1px solid ${theme.border}` : 'none',
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.bg)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Table2 size={14} color={theme.accent} />
                      <span style={{ fontSize: 13, fontWeight: 700, color: theme.text }}>{w.title}</span>
                      {rows.length > 0 && (
                        <span style={{ padding: '2px 7px', background: theme.accentBg, border: `1px solid ${theme.border}`, borderRadius: 10, fontSize: 10, fontWeight: 600, color: theme.accent }}>
                          {rows.length} rows
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} onClick={e => e.stopPropagation()}>
                      {/* Info button */}
                      <button onClick={() => openInfo(w)} title="Explain this table"
                        className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
                        style={{
                          width: 24, height: 24, borderRadius: '50%', padding: 0,
                          background: isInfoOpen ? theme.accentBg : 'transparent',
                          border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`,
                          color: isInfoOpen ? theme.accent : theme.muted,
                          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        }}
                      ><Info size={12} /></button>
                      <div onClick={e => { e.stopPropagation(); setExpandedTables(prev => { const next = new Set(prev); if (next.has(w.id)) next.delete(w.id); else next.add(w.id); return next }) }}
                        style={{ color: theme.muted, display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </div>
                    </div>
                  </div>
                  {/* Accordion body */}
                  {isExpanded && (
                    <div style={{ padding: '4px 0 8px' }}>
                      {w.chart_data
                        ? <ChartRenderer result={toChartResult(w)} height={260} />
                        : <div style={{ height: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  // ── Nav items ─────────────────────────────────────────────────────────────
  const navItems = [
    { id: 'overview' as SectionId, label: 'Overview', icon: <LayoutGrid size={16} />, count: null,                  show: true },
    { id: 'charts'  as SectionId, label: 'Charts',   icon: <BarChart2  size={16} />, count: visualCharts.length || null, show: visualCharts.length > 0 },
    { id: 'data'    as SectionId, label: 'Data',     icon: <Table2     size={16} />, count: tables.length || null,       show: tables.length > 0 },
  ]

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', fontFamily: "'Inter',system-ui,-apple-system,sans-serif", background: theme.bg }}>

      {/* ── Left Rail ──────────────────────────────────────────────────────── */}
      <nav style={{ width: 74, background: theme.rail, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '14px 0', flexShrink: 0, gap: 2, boxShadow: '2px 0 12px rgba(0,0,0,0.15)' }}>
        <div style={{ marginBottom: 14 }}>
          <div style={{ width: 38, height: 38, borderRadius: 11, background: 'linear-gradient(135deg,#2563EB,#7C3AED)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Sparkles size={17} color="white" />
          </div>
        </div>

        {navItems.filter(s => s.show).map(s => (
          <button key={s.id} onClick={() => setActiveSection(s.id)} style={{
            width: 52, minHeight: 54, borderRadius: 12, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 3, padding: '6px 4px',
            background: activeSection === s.id ? theme.railActive : 'transparent',
            color: activeSection === s.id ? '#93C5FD' : theme.railText,
            border: 'none', cursor: 'pointer', transition: 'all 0.15s', position: 'relative',
          }}>
            {s.icon}
            <span style={{ fontSize: 9, fontWeight: 500, lineHeight: 1 }}>{s.label}</span>
            {s.count !== null && (
              <span style={{ position: 'absolute', top: 4, right: 4, fontSize: 8, fontWeight: 700, background: theme.accent, color: 'white', borderRadius: 6, padding: '1px 4px' }}>{s.count}</span>
            )}
          </button>
        ))}

        <div style={{ flex: 1 }} />

        {/* Theme cycle */}
        <button onClick={() => setThemeIdx(v => (v + 1) % THEMES.length)}
          title={`Theme: ${theme.label} → ${THEMES[(themeIdx + 1) % THEMES.length].label} (T)`}
          style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: 'transparent', color: theme.railText, border: 'none', cursor: 'pointer' }}>
          {theme.id === 'executive' ? <Sun size={15} /> : theme.id === 'lightpro' ? <Moon size={15} /> : <Zap size={15} />}
          <span style={{ fontSize: 8, fontWeight: 500 }}>Theme</span>
        </button>

        {/* AI Chat */}
        <button onClick={() => { setInfoWidgetId(null); setShowChat(v => !v) }} style={{
          width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 3,
          background: showChat ? theme.railActive : 'transparent',
          color: showChat ? '#93C5FD' : theme.railText,
          border: 'none', cursor: 'pointer',
        }}>
          <MessageSquare size={16} />
          <span style={{ fontSize: 9, fontWeight: 500 }}>AI</span>
        </button>

        {/* Back */}
        <button onClick={onClose} title="Back to Canvas (Esc)" style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: 'transparent', color: theme.railText, border: 'none', cursor: 'pointer' }}>
          <X size={16} />
          <span style={{ fontSize: 9, fontWeight: 500 }}>Canvas</span>
        </button>
      </nav>

      {/* ── Main Content ──────────────────────────────────────────────────── */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Hero Header */}
        <div style={{
          background: `linear-gradient(-45deg,${theme.heroFrom},${theme.heroMid},${theme.heroTo},${theme.heroMid})`,
          backgroundSize: '400% 400%', animation: 'visually-gradient 10s ease infinite',
          padding: '18px 28px 20px', flexShrink: 0, position: 'relative', overflow: 'hidden',
        }}>
          <div style={{ position: 'absolute', top: -30, right: 80, width: 180, height: 180, borderRadius: '50%', background: 'rgba(124,58,237,0.22)', filter: 'blur(50px)', pointerEvents: 'none' }} />
          <div style={{ position: 'absolute', bottom: -20, left: '35%', width: 140, height: 140, borderRadius: '50%', background: 'rgba(96,165,250,0.18)', filter: 'blur(40px)', pointerEvents: 'none' }} />

          {/* Breadcrumb */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 10, position: 'relative' }}>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)', fontWeight: 500 }}>Project</span>
            <ChevronRight size={9} color="rgba(255,255,255,0.38)" />
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)', fontWeight: 500 }}>{canvas.name}</span>
            <ChevronRight size={9} color="rgba(255,255,255,0.38)" />
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.85)', fontWeight: 600 }}>Visually View</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', position: 'relative' }}>
            <div>
              <h1 style={{ fontSize: 22, fontWeight: 800, color: 'white', margin: '0 0 8px', letterSpacing: '-0.5px' }}>{canvas.name}</h1>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ padding: '3px 10px', background: 'rgba(255,255,255,0.14)', backdropFilter: 'blur(8px)', borderRadius: 20, fontSize: 10, color: 'white', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#4ADE80', display: 'inline-block', animation: 'visually-pulse 2s ease infinite' }} />
                  LIVE
                </span>
                <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.62)' }}>{widgets.length} charts · Real-time data</span>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* Layout toggle */}
              <div style={{ display: 'flex', background: 'rgba(255,255,255,0.12)', borderRadius: 8, padding: 2, gap: 2 }}>
                {(['grid', 'list'] as const).map(m => (
                  <button key={m} onClick={() => setLayoutMode(m)} title={`${m} layout`}
                    style={{ width: 28, height: 26, borderRadius: 6, border: 'none', cursor: 'pointer', background: layoutMode === m ? 'rgba(255,255,255,0.24)' : 'transparent', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {m === 'grid' ? <LayoutGrid size={13} /> : <List size={13} />}
                  </button>
                ))}
              </div>
              <button onClick={onClose} style={{ padding: '6px 14px', background: 'rgba(255,255,255,0.14)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 8, fontSize: 11, fontWeight: 600, color: 'white', cursor: 'pointer' }}>
                ← Canvas
              </button>
            </div>
          </div>
        </div>

        {/* AI Executive Summary */}
        {(summaryLoading || execSummary) && (
          <div style={{ margin: '14px 28px 0', background: theme.surface, border: `1px solid ${theme.accent}30`, borderRadius: 14, padding: '12px 16px', boxShadow: `0 0 0 2px ${theme.accent}0d`, animation: 'visually-fadeIn 0.5s ease both', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: summaryExpanded ? 10 : 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 22, height: 22, borderRadius: 6, background: `linear-gradient(135deg,${theme.accent},#7C3AED)`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Sparkles size={11} color="white" />
                </div>
                <span style={{ fontSize: 11, fontWeight: 700, color: theme.text, textTransform: 'uppercase', letterSpacing: '0.06em' }}>AI Executive Summary</span>
              </div>
              <button onClick={() => setSummaryExpanded(v => !v)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, fontSize: 11, padding: '2px 6px' }}>
                {summaryExpanded ? '▲ Hide' : '▼ Show'}
              </button>
            </div>
            {summaryExpanded && (
              summaryLoading
                ? <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <Loader2 size={12} className="animate-spin" style={{ color: theme.accent }} />
                    <span style={{ fontSize: 12, color: theme.muted }}>Generating summary…</span>
                  </div>
                : <div style={{ fontSize: 13, color: theme.text, lineHeight: 1.7, whiteSpace: 'pre-line' }}>{execSummary}</div>
            )}
          </div>
        )}

        {/* Section tabs */}
        <div style={{ display: 'flex', gap: 2, padding: '10px 28px 0', flexShrink: 0 }}>
          {navItems.filter(s => s.show).map(s => (
            <button key={s.id} onClick={() => setActiveSection(s.id)} style={{
              padding: '7px 16px', borderRadius: '8px 8px 0 0', border: 'none', cursor: 'pointer',
              background: activeSection === s.id ? theme.surface : 'transparent',
              color: activeSection === s.id ? theme.accent : theme.muted,
              fontSize: 12, fontWeight: activeSection === s.id ? 700 : 500,
              borderBottom: `2px solid ${activeSection === s.id ? theme.accent : 'transparent'}`,
              transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 5,
            }}>
              {s.icon}{s.label}
              {s.count !== null && (
                <span style={{ fontSize: 9, fontWeight: 700, background: activeSection === s.id ? theme.accent : theme.border, color: activeSection === s.id ? 'white' : theme.muted, borderRadius: 8, padding: '1px 5px' }}>{s.count}</span>
              )}
            </button>
          ))}
        </div>

        {/* Scrollable section content */}
        <div style={{ flex: 1, overflowY: 'auto', background: theme.bg }}>
          {renderContent()}
        </div>
      </main>

      {/* ── Right Panel: Info or Chat ─────────────────────────────────────── */}
      {(infoWidgetId !== null || showChat) && (
        <aside style={{ width: 360, background: theme.surface, borderLeft: `1px solid ${theme.border}`, display: 'flex', flexDirection: 'column', flexShrink: 0, boxShadow: '-4px 0 20px rgba(0,0,0,0.07)', animation: 'visually-slideUp 0.22s ease both' }}>

        {/* ── Info Panel (shown when a widget's ⓘ was clicked) ─────────── */}
        {infoWidgetId !== null && (() => {
          const iw = widgets.find(w => w.id === infoWidgetId)
          if (!iw) return null
          const isKpi = ['kpi', 'kpi_card'].includes(iw.chart_type)
          const kpiM = isKpi ? getKpiMeta(iw) : null
          const desc = widgetDescriptions[iw.id]
          const loading = descLoading[iw.id]
          return (
            <>
              {/* Header */}
              <div style={{ padding: '14px 16px', borderBottom: `1px solid ${theme.border}`, flexShrink: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ width: 34, height: 34, borderRadius: 10, background: theme.accentBg, border: `1px solid ${theme.accent}44`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <Info size={16} color={theme.accent} />
                    </div>
                    <div>
                      <p style={{ fontSize: 14, fontWeight: 700, color: theme.text, margin: 0 }}>Widget Insight</p>
                      <p style={{ fontSize: 10, color: theme.muted, margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{iw.chart_type}</p>
                    </div>
                  </div>
                  <button onClick={() => setInfoWidgetId(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, padding: 4 }}><X size={15} /></button>
                </div>
              </div>

              {/* Body */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '18px 16px' }}>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: theme.text, margin: '0 0 10px', lineHeight: 1.35 }}>{iw.title}</h3>
                <span style={{ padding: '3px 9px', background: theme.accentBg, border: `1px solid ${theme.border}`, borderRadius: 8, fontSize: 10, fontWeight: 700, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.06em', display: 'inline-block', marginBottom: 18 }}>{iw.chart_type}</span>

                {/* AI Description */}
                <div style={{ marginBottom: 20 }}>
                  <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 8px' }}>What this measures</p>
                  {loading ? (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '10px 0' }}>
                      <Loader2 size={13} style={{ color: theme.accent, animation: 'visually-spin 1s linear infinite' }} />
                      <span style={{ fontSize: 12, color: theme.muted }}>Generating insight…</span>
                    </div>
                  ) : desc ? (
                    <p style={{ fontSize: 13, color: theme.text, lineHeight: 1.7, margin: 0 }}>{desc}</p>
                  ) : (
                    <p style={{ fontSize: 13, color: theme.muted, lineHeight: 1.7, margin: 0 }}>Insight will appear here once loaded.</p>
                  )}
                </div>

                {/* KPI current state */}
                {isKpi && kpiM && (
                  <div style={{ marginBottom: 20, padding: '12px 14px', background: theme.bg, borderRadius: 12, border: `1px solid ${theme.border}` }}>
                    <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 8px' }}>Current Value</p>
                    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' }}>
                      <p style={{ fontSize: 30, fontWeight: 800, color: theme.text, margin: 0, fontFamily: '"SF Mono","JetBrains Mono",monospace', fontVariantNumeric: 'tabular-nums' }}>
                        {kpiM.num.toLocaleString()}
                      </p>
                      {kpiM.spark.length >= 2 && (
                        <Sparkline data={kpiM.spark} color={kpiM.delta?.up === false ? '#DC2626' : '#16A34A'} width={72} height={32} />
                      )}
                    </div>
                    {kpiM.delta && (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6 }}>
                        {kpiM.delta.up ? <TrendingUp size={11} color="#16A34A" /> : <TrendingDown size={11} color="#DC2626" />}
                        <span style={{ fontSize: 12, fontWeight: 600, color: kpiM.delta.up ? '#16A34A' : '#DC2626' }}>
                          {kpiM.delta.pct.toFixed(1)}% vs prior period
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* SQL toggle */}
                {iw.sql_query && <SqlToggleSection sql={iw.sql_query} theme={theme} />}
              </div>

              {/* Footer */}
              <div style={{ padding: '12px 16px', borderTop: `1px solid ${theme.border}`, display: 'flex', gap: 8, flexShrink: 0 }}>
                <button
                  onClick={() => {
                    const q = `Tell me more about "${iw.title}" — what's driving this and what should I do about it?`
                    setInfoWidgetId(null)
                    setShowChat(true)
                    setInput(q)
                  }}
                  style={{ flex: 1, padding: '9px 0', background: 'linear-gradient(135deg,#2563EB,#7C3AED)', border: 'none', borderRadius: 10, fontSize: 12, fontWeight: 700, color: 'white', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                >
                  <MessageSquare size={13} /> Ask AI about this
                </button>
              </div>
            </>
          )
        })()}

        {/* ── Chat Panel (shown when no info panel) ────────────────────── */}
        {infoWidgetId === null && showChat && <>

          {/* Chat header */}
          <div style={{ padding: '14px 16px', borderBottom: `1px solid ${theme.border}`, flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ width: 34, height: 34, borderRadius: 10, background: 'linear-gradient(135deg,#2563EB,#7C3AED)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Sparkles size={15} color="white" />
                </div>
                <div>
                  <p style={{ fontSize: 14, fontWeight: 700, color: theme.text, margin: 0 }}>AI Copilot</p>
                  <p style={{ fontSize: 10, color: theme.muted, margin: 0 }}>Real-time data · Press C to toggle</p>
                </div>
              </div>
              <button onClick={() => setShowChat(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, padding: 4 }}><X size={15} /></button>
            </div>
          </div>

          {/* Suggested questions */}
          {messages.length <= 1 && recommended.length > 0 && (
            <div style={{ padding: '12px 16px', borderBottom: `1px solid ${theme.border}`, flexShrink: 0 }}>
              <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Suggested</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {recommended.map((q, i) => (
                  <button key={i} onClick={() => send(q)} style={{
                    textAlign: 'left', padding: '7px 11px', background: theme.accentBg,
                    border: `1px solid ${theme.border}`, borderRadius: 8, fontSize: 12, color: theme.text,
                    cursor: 'pointer', lineHeight: 1.4, transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = theme.accent; e.currentTarget.style.color = theme.accent }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = theme.border; e.currentTarget.style.color = theme.text }}
                  >{q}</button>
                ))}
              </div>
            </div>
          )}

          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {messages.map((msg, i) => {
              const isLatestAI = msg.role === 'assistant' && msg.typing && i === messages.length - 1
              const bubbleBg = msg.role === 'user' ? '#2563EB' : (theme.id === 'midnight' ? 'rgba(255,255,255,0.08)' : '#F3F4F6')
              return (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start', gap: 8 }}>
                  <div style={{ maxWidth: '90%', padding: '9px 13px', borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '4px 16px 16px 16px', background: bubbleBg, color: msg.role === 'user' ? 'white' : theme.text, fontSize: 13, lineHeight: 1.55 }}>
                    {isLatestAI ? <TypewriterText text={msg.content} active speed={10} /> : msg.content}
                  </div>

                  {msg.inlineChart && (
                    <div style={{ width: '100%', background: theme.bg, border: `1px solid ${theme.border}`, borderRadius: 12, padding: 14 }}>
                      <p style={{ fontSize: 12, fontWeight: 700, color: theme.text, marginBottom: 10 }}>{msg.inlineChart.title}</p>
                      <ChartRenderer result={msg.inlineChart} height={180} />
                      <button onClick={() => handleAddToCanvas(msg.inlineChart!)} style={{ marginTop: 10, width: '100%', padding: '8px 0', background: theme.accentBg, border: `1px solid ${theme.accent}44`, borderRadius: 8, fontSize: 12, fontWeight: 700, color: theme.accent, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5 }}>
                        <Plus size={12} /> Add to Canvas
                      </button>
                    </div>
                  )}

                  {msg.newWidget && (
                    <button onClick={() => handleAddToCanvas(msg.newWidget!)} style={{ padding: '7px 14px', background: theme.accentBg, border: `1px solid ${theme.accent}44`, borderRadius: 8, fontSize: 12, fontWeight: 700, color: theme.accent, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5 }}>
                      <Plus size={12} /> Add &ldquo;{msg.newWidget.title}&rdquo; to Canvas
                    </button>
                  )}

                  {/* Follow-up chips — only on last message */}
                  {msg.followUps && msg.followUps.length > 0 && i === messages.length - 1 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, maxWidth: '95%' }}>
                      {msg.followUps.map((q, fi) => (
                        <button key={fi} onClick={() => send(q)} style={{ padding: '4px 10px', background: 'transparent', border: `1px solid ${theme.accent}55`, borderRadius: 16, fontSize: 11, color: theme.accent, cursor: 'pointer', transition: 'background 0.15s' }}
                          onMouseEnter={e => (e.currentTarget.style.background = theme.accentBg)}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                        >{q}</button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}

            {/* Typing indicator — animated dots */}
            {sending && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'linear-gradient(135deg,#2563EB,#7C3AED)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Sparkles size={12} color="white" />
                </div>
                <div style={{ padding: '10px 14px', background: theme.id === 'midnight' ? 'rgba(255,255,255,0.08)' : '#F3F4F6', borderRadius: '4px 16px 16px 16px', display: 'flex', gap: 4, alignItems: 'center' }}>
                  {[0, 1, 2].map(d => (
                    <span key={d} style={{ width: 5, height: 5, borderRadius: '50%', background: theme.muted, display: 'inline-block', animation: `visually-pulse 1.2s ease ${d * 0.2}s infinite` }} />
                  ))}
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
          <div style={{ padding: '12px 16px', borderTop: `1px solid ${theme.border}`, flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, background: theme.bg, border: `1px solid ${theme.border}`, borderRadius: 12, padding: '8px 10px 8px 14px' }}>
              <textarea
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Ask about your data or request a chart…"
                rows={1}
                style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 13, color: theme.text, resize: 'none', maxHeight: 80, lineHeight: 1.5, fontFamily: 'inherit' }}
              />
              <button onClick={() => send()} disabled={!input.trim() || sending} style={{ width: 34, height: 34, borderRadius: 9, flexShrink: 0, background: 'linear-gradient(135deg,#2563EB,#7C3AED)', border: 'none', cursor: input.trim() && !sending ? 'pointer' : 'not-allowed', opacity: !input.trim() || sending ? 0.45 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Send size={13} color="white" />
              </button>
            </div>
            <p style={{ fontSize: 10, color: theme.muted, marginTop: 6, textAlign: 'center' }}>
              1/2/3 sections · C copilot · T theme · Esc back
            </p>
          </div>
        </>}
        </aside>
      )}

      {/* ── Toast ─────────────────────────────────────────────────────────── */}
      {toast && (
        <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', background: theme.id === 'midnight' ? '#E8F4FF' : '#0a2540', color: theme.id === 'midnight' ? '#070D1A' : 'white', padding: '10px 20px', borderRadius: 30, fontSize: 13, fontWeight: 600, boxShadow: '0 4px 20px rgba(0,0,0,0.28)', animation: 'visually-slideUp 0.3s ease both', zIndex: 200, display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
          <CheckCircle2 size={14} />{toast}
        </div>
      )}
    </div>
  )
}
