'use client'
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  X, Send, Loader2, Plus, BarChart2, Table2, Sparkles,
  LayoutGrid, MessageSquare, ChevronRight, TrendingUp, TrendingDown,
  CheckCircle2, Copy, List, Sun, Moon, Zap, Info, ChevronDown, ChevronUp,
  Maximize2, Star, Printer, ChevronLeft, Play, Pause, Download,
} from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { chatApi, canvasApi, type WidgetCreate } from '@/lib/api'
import type { ChartResult } from '@/stores/pipelineStore'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'

// ─── Theme System ──────────────────────────────────────────────────────────────

type ThemeId = 'executive' | 'lightpro' | 'midnight' | 'senior' | 'maturepro' | 'digitalnative' | 'genz' | 'accessible'
type BgPattern = 'none' | 'dots' | 'mesh' | 'graph'
type LayoutMode = 'grid' | 'list' | 'newspaper'

interface Theme {
  id: ThemeId; label: string
  bg: string; surface: string; border: string; text: string; muted: string
  accent: string; accentBg: string
  rail: string; railText: string; railActive: string
  heroFrom: string; heroMid: string; heroTo: string
  fontSizeBase: number; kpiFontSize: number; fontFamily: string
  cardRadius: number; buttonMinH: number; animations: boolean; glassMorphism: boolean
}

function getPatternStyle(p: BgPattern, theme: Theme): React.CSSProperties {
  if (p === 'dots') return { backgroundImage: `radial-gradient(${theme.muted}22 1.5px, transparent 1.5px)`, backgroundSize: '24px 24px' }
  if (p === 'mesh') return { backgroundImage: [`radial-gradient(at 40% 20%,${theme.accent}0E 0,transparent 50%)`, `radial-gradient(at 80% 0%,${theme.heroTo}0B 0,transparent 50%)`, `radial-gradient(at 0% 50%,${theme.heroMid}09 0,transparent 50%)`, `radial-gradient(at 80% 80%,${theme.accent}09 0,transparent 50%)`].join(',') }
  if (p === 'graph') return { backgroundImage: [`linear-gradient(${theme.border} 1px,transparent 1px)`, `linear-gradient(90deg,${theme.border} 1px,transparent 1px)`].join(','), backgroundSize: '20px 20px' }
  return {}
}

const THEMES: Theme[] = [
  {
    id: 'executive', label: 'Executive',
    bg: '#EEF2F7', surface: '#FFFFFF', border: 'rgba(0,0,0,0.07)', text: '#0a2540', muted: '#6B7280',
    accent: '#2563EB', accentBg: '#EFF6FF',
    rail: '#0a2540', railText: 'rgba(255,255,255,0.45)', railActive: 'rgba(96,165,250,0.18)',
    heroFrom: '#0a2540', heroMid: '#1a4080', heroTo: '#7C3AED',
    fontSizeBase: 13, kpiFontSize: 28, fontFamily: "'Inter',system-ui,sans-serif",
    cardRadius: 16, buttonMinH: 32, animations: true, glassMorphism: false,
  },
  {
    id: 'lightpro', label: 'Light Pro',
    bg: '#F4F6FB', surface: '#FFFFFF', border: 'rgba(0,0,0,0.06)', text: '#111827', muted: '#9CA3AF',
    accent: '#7C3AED', accentBg: '#F5F3FF',
    rail: '#1E1B4B', railText: 'rgba(255,255,255,0.4)', railActive: 'rgba(167,139,250,0.2)',
    heroFrom: '#1E1B4B', heroMid: '#4F46E5', heroTo: '#EC4899',
    fontSizeBase: 13, kpiFontSize: 28, fontFamily: "'Inter',system-ui,sans-serif",
    cardRadius: 16, buttonMinH: 32, animations: true, glassMorphism: false,
  },
  {
    id: 'midnight', label: 'Midnight',
    bg: '#070D1A', surface: 'rgba(255,255,255,0.05)', border: 'rgba(255,255,255,0.09)', text: '#E8F4FF', muted: 'rgba(255,255,255,0.4)',
    accent: '#00D4FF', accentBg: 'rgba(0,212,255,0.08)',
    rail: '#040A14', railText: 'rgba(255,255,255,0.3)', railActive: 'rgba(0,212,255,0.12)',
    heroFrom: '#040A14', heroMid: '#0D2137', heroTo: '#1A3A6B',
    fontSizeBase: 13, kpiFontSize: 28, fontFamily: "'Inter',system-ui,sans-serif",
    cardRadius: 16, buttonMinH: 32, animations: true, glassMorphism: true,
  },
  {
    id: 'senior', label: 'Classic',
    bg: '#FFFFFF', surface: '#F8F9FA', border: 'rgba(0,0,0,0.15)', text: '#000000', muted: '#555555',
    accent: '#000080', accentBg: '#E8E8FF',
    rail: '#000080', railText: 'rgba(255,255,255,0.85)', railActive: 'rgba(0,0,128,0.2)',
    heroFrom: '#000080', heroMid: '#0000CD', heroTo: '#4169E1',
    fontSizeBase: 18, kpiFontSize: 48, fontFamily: "'Georgia','Times New Roman',serif",
    cardRadius: 8, buttonMinH: 48, animations: false, glassMorphism: false,
  },
  {
    id: 'maturepro', label: 'Warm Earth',
    bg: '#F5F0E8', surface: '#FFFEF8', border: 'rgba(0,0,0,0.1)', text: '#2C1810', muted: '#7A6A5A',
    accent: '#8B4513', accentBg: '#FFF3E0',
    rail: '#3E2723', railText: 'rgba(255,255,255,0.7)', railActive: 'rgba(139,69,19,0.2)',
    heroFrom: '#3E2723', heroMid: '#5D4037', heroTo: '#8B4513',
    fontSizeBase: 15, kpiFontSize: 36, fontFamily: "'Merriweather','Georgia',serif",
    cardRadius: 12, buttonMinH: 40, animations: false, glassMorphism: false,
  },
  {
    id: 'digitalnative', label: 'Neon',
    bg: '#09090B', surface: 'rgba(255,255,255,0.04)', border: 'rgba(0,255,200,0.12)', text: '#E2E8F0', muted: 'rgba(255,255,255,0.35)',
    accent: '#00FFC8', accentBg: 'rgba(0,255,200,0.06)',
    rail: '#050507', railText: 'rgba(255,255,255,0.3)', railActive: 'rgba(0,255,200,0.1)',
    heroFrom: '#050507', heroMid: '#0A1628', heroTo: '#12003E',
    fontSizeBase: 12, kpiFontSize: 30, fontFamily: "'JetBrains Mono','SF Mono',monospace",
    cardRadius: 10, buttonMinH: 30, animations: true, glassMorphism: true,
  },
  {
    id: 'genz', label: 'Vivid',
    bg: '#FDF4FF', surface: '#FFFFFF', border: 'rgba(168,85,247,0.12)', text: '#1A1A2E', muted: '#9CA3AF',
    accent: '#A855F7', accentBg: '#FAF5FF',
    rail: '#1A1A2E', railText: 'rgba(255,255,255,0.5)', railActive: 'rgba(168,85,247,0.15)',
    heroFrom: '#6B21A8', heroMid: '#A855F7', heroTo: '#EC4899',
    fontSizeBase: 13, kpiFontSize: 30, fontFamily: "'Inter','Poppins',sans-serif",
    cardRadius: 20, buttonMinH: 32, animations: true, glassMorphism: false,
  },
  {
    id: 'accessible', label: 'Accessible',
    bg: '#FFFFFF', surface: '#F7F7F7', border: '#767676', text: '#000000', muted: '#595959',
    accent: '#0057A8', accentBg: '#E6EFF8',
    rail: '#000000', railText: '#FFFFFF', railActive: 'rgba(0,87,168,0.15)',
    heroFrom: '#000000', heroMid: '#003F7F', heroTo: '#0057A8',
    fontSizeBase: 14, kpiFontSize: 32, fontFamily: "'Inter','Helvetica Neue',sans-serif",
    cardRadius: 6, buttonMinH: 44, animations: false, glassMorphism: false,
  },
]

// ─── Markdown renderer (no external deps) ─────────────────────────────────────

function MarkdownText({ text, color, fontSize = 13 }: { text: string; color: string; fontSize?: number }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    // Headings
    const h3 = line.match(/^###\s+(.+)/)
    const h2 = line.match(/^##\s+(.+)/)
    const h1 = line.match(/^#\s+(.+)/)
    if (h1 || h2 || h3) {
      const txt = (h1?.[1] ?? h2?.[1] ?? h3?.[1] ?? '').trim()
      elements.push(<p key={i} style={{ fontSize: h1 ? fontSize + 3 : h2 ? fontSize + 1 : fontSize, fontWeight: 800, color, margin: '10px 0 4px', lineHeight: 1.3 }}>{inlineRender(txt)}</p>)
      i++; continue
    }
    // Bullet lines: • - * at start
    if (/^[•\-\*]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[•\-\*]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[•\-\*]\s+/, '').trim())
        i++
      }
      elements.push(
        <ul key={`ul-${i}`} style={{ margin: '4px 0', paddingLeft: 18, color }}>
          {items.map((it, j) => <li key={j} style={{ fontSize, lineHeight: 1.65, marginBottom: 2 }}>{inlineRender(it)}</li>)}
        </ul>
      )
      continue
    }
    // Numbered list
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, '').trim())
        i++
      }
      elements.push(
        <ol key={`ol-${i}`} style={{ margin: '4px 0', paddingLeft: 20, color }}>
          {items.map((it, j) => <li key={j} style={{ fontSize, lineHeight: 1.65, marginBottom: 2 }}>{inlineRender(it)}</li>)}
        </ol>
      )
      continue
    }
    // Regular paragraph
    elements.push(<p key={i} style={{ fontSize, lineHeight: 1.7, color, margin: '3px 0' }}>{inlineRender(line)}</p>)
    i++
  }
  return <div>{elements}</div>
}

function inlineRender(text: string): React.ReactNode[] {
  // Split on **bold**, *italic*, `code`
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2,-2)}</strong>
    if (p.startsWith('*') && p.endsWith('*')) return <em key={i}>{p.slice(1,-1)}</em>
    if (p.startsWith('`') && p.endsWith('`')) return <code key={i} style={{ fontFamily: 'monospace', fontSize: '0.88em', background: 'rgba(0,0,0,0.07)', padding: '1px 4px', borderRadius: 3 }}>{p.slice(1,-1)}</code>
    return p
  })
}

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

function TrendBadge({ trend, small = false }: { trend: { pct: number; up: boolean }; small?: boolean }) {
  const color = trend.up ? '#16A34A' : '#DC2626'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: small ? '1px 5px' : '2px 7px', background: trend.up ? '#F0FDF4' : '#FEF2F2', borderRadius: 10, fontSize: small ? 9 : 10, fontWeight: 700, color, flexShrink: 0 }}>
      {trend.up ? '▲' : '▼'} {trend.pct.toFixed(1)}%
    </span>
  )
}

function KeyDriverCallout({ text, theme }: { text: string; theme: Theme }) {
  return (
    <div style={{ marginTop: 8, padding: '5px 10px', background: `${theme.accent}0D`, border: `1px solid ${theme.accent}22`, borderLeft: `3px solid ${theme.accent}`, borderRadius: 7, fontSize: 11, color: theme.text }}>
      {text}
    </div>
  )
}

function ChartTypeSwitcher({ baseType, active, onSelect, theme }: { baseType: string; active: string; onSelect: (t: string | undefined) => void; theme: Theme }) {
  const opts = [
    { t: baseType, label: baseType.replace(/_/g,' ') },
    { t: 'bar', label: 'Bar' },
    { t: 'line', label: 'Line' },
    { t: 'pie', label: 'Pie' },
    { t: 'table', label: 'Table' },
  ].filter((o,i,arr) => i === 0 || !arr.slice(0,i).some(p => p.t === o.t))
  return (
    <div style={{ display: 'flex', gap: 2, background: theme.bg, borderRadius: 8, padding: 2, marginBottom: 8 }}>
      {opts.map(o => (
        <button key={o.t} onClick={() => onSelect(o.t === baseType ? undefined : o.t)}
          style={{ padding: '2px 7px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: 9, fontWeight: active === o.t ? 700 : 400, background: active === o.t ? theme.surface : 'transparent', color: active === o.t ? theme.accent : theme.muted, transition: 'all 0.12s' }}>
          {o.label}
        </button>
      ))}
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
  onCanvasRename?: (newName: string) => void
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

// ─── Intelligence Utilities ───────────────────────────────────────────────────

function suggestAlternativeChart(w: CanvasWidgetData): string | null {
  const rows = (w.chart_data?.rows as Record<string,unknown>[]) ?? []
  const ct = w.chart_type.toLowerCase()
  const count = rows.length || ((w.chart_data?.labels as unknown[]) ?? []).length
  if (['bar','bar_vertical'].includes(ct)) return count < 6 ? 'pie' : count > 15 ? 'treemap' : 'line'
  if (ct === 'bar_horizontal') return 'treemap'
  if (ct === 'line') return 'area'
  if (ct === 'area') return 'line'
  if (['table','data_table'].includes(ct) && count > 0 && count <= 20) return 'bar'
  if (['pie','donut'].includes(ct)) return 'bar'
  return null
}

function computeCaption(w: CanvasWidgetData): string {
  const ct = w.chart_type.toLowerCase()
  const values = (w.chart_data?.values as (number|null)[]) ?? []
  const labels = (w.chart_data?.labels as string[]) ?? []
  const rows = (w.chart_data?.rows as Record<string,unknown>[]) ?? []
  const cols = (w.chart_data?.columns as string[]) ?? []
  const nums = values.length > 0
    ? values.filter((v): v is number => typeof v === 'number')
    : rows.map(r => Number(r[cols[1]] ?? Object.values(r)[1] ?? 0)).filter(n => !isNaN(n))
  const labArr = labels.length > 0 ? labels : rows.map(r => String(r[cols[0]] ?? Object.values(r)[0] ?? ''))
  if (nums.length === 0) return ''
  const total = nums.reduce((s,v) => s+v, 0)
  const max = Math.max(...nums)
  const maxIdx = nums.indexOf(max)
  const topLabel = labArr[maxIdx] ?? ''
  if (['bar','bar_vertical','bar_horizontal'].includes(ct) && topLabel)
    return `${topLabel} leads at ${max.toLocaleString()} (${total > 0 ? Math.round((max/total)*100) : 0}% of total)`
  if (['pie','donut'].includes(ct) && topLabel)
    return `${topLabel} dominates at ${total > 0 ? Math.round((max/total)*100) : 0}%`
  if (ct === 'line' && nums.length >= 2) {
    const first = nums[0], last = nums[nums.length-1]
    if (first === 0) return `Values reach ${last.toLocaleString()} by period end`
    return `${last > first ? '↑' : '↓'} ${Math.abs(((last-first)/first)*100).toFixed(1)}% change over the period shown`
  }
  return `${nums.length} data points — peak ${max.toLocaleString()}`
}

function computeTrendBadge(w: CanvasWidgetData): { pct: number; up: boolean } | null {
  if (['table','data_table','kpi','kpi_card'].includes(w.chart_type.toLowerCase())) return null
  const nums = ((w.chart_data?.values as (number|null)[]) ?? []).filter((v): v is number => typeof v === 'number' && !isNaN(v))
  if (nums.length < 2 || nums[0] === 0) return null
  const pct = Math.abs(((nums[nums.length-1]-nums[0])/nums[0])*100)
  return { pct: Math.round(pct*10)/10, up: nums[nums.length-1] >= nums[0] }
}

function computeKeyDriver(w: CanvasWidgetData): string | null {
  if (!['bar','bar_vertical','bar_horizontal'].includes(w.chart_type.toLowerCase())) return null
  const values = (w.chart_data?.values as (number|null)[]) ?? []
  const labels = (w.chart_data?.labels as string[]) ?? []
  const rows = (w.chart_data?.rows as Record<string,unknown>[]) ?? []
  const cols = (w.chart_data?.columns as string[]) ?? []
  const nums = values.length > 0 ? values.filter((v): v is number => typeof v === 'number') : rows.map(r => Number(r[cols[1]] ?? 0)).filter(n => !isNaN(n))
  const labArr = labels.length > 0 ? labels : rows.map(r => String(r[cols[0]] ?? ''))
  if (nums.length < 2) return null
  const total = nums.reduce((s,v) => s+v, 0)
  if (total === 0) return null
  const max = Math.max(...nums)
  const topLabel = labArr[nums.indexOf(max)]
  if (!topLabel) return null
  return `★ Key driver: ${topLabel} — ${Math.round((max/total)*100)}% of total`
}

function detectAnomalyIndices(values: (number|null)[]): number[] {
  const nums = values.filter((v): v is number => typeof v === 'number' && !isNaN(v))
  if (nums.length < 5) return []
  const mean = nums.reduce((s,v) => s+v,0)/nums.length
  const std = Math.sqrt(nums.reduce((s,v) => s+Math.pow(v-mean,2),0)/nums.length)
  if (std === 0) return []
  const out: number[] = []
  values.forEach((v,i) => { if (v !== null && Math.abs(v-mean)/std > 2.0) out.push(i) })
  return out
}

// ─── Main Component ────────────────────────────────────────────────────────────

export function VisuallReport({ canvas, widgets, projectId, onClose, onWidgetAdded, onCanvasRename }: Props) {
  const [themeIdx, setThemeIdx] = useState(() => {
    try { return parseInt(localStorage.getItem(`visually-theme-${canvas.id}`) ?? '0', 10) || 0 } catch { return 0 }
  })
  const theme = THEMES[Math.min(themeIdx, THEMES.length - 1)]

  const [activeSection, setActiveSection] = useState<SectionId>('overview')
  const [layoutMode, setLayoutMode]       = useState<LayoutMode>('grid')
  const [bgPattern, setBgPattern]         = useState<BgPattern>('dots')
  // Chart-level interactivity
  const [chartTypeOverrides, setChartTypeOverrides] = useState<Record<string,string>>({})
  const [showRawData, setShowRawData]     = useState<Set<string>>(new Set())
  const [splitViewId, setSplitViewId]     = useState<string|null>(null)
  const [fullscreenId, setFullscreenId]   = useState<string|null>(null)
  // Focus mode
  const [focusMode, setFocusMode]         = useState(false)
  // Slide / Story mode
  const [slideMode, setSlideMode]         = useState(false)
  const [slideIdx, setSlideIdx]           = useState(0)
  const [slidePlaying, setSlidePlaying]   = useState(false)
  // Bookmarks & Annotations (localStorage)
  const [bookmarked, setBookmarked] = useState<Set<string>>(() => {
    try { return new Set<string>(JSON.parse(localStorage.getItem(`vis-bm-${canvas.id}`) ?? '[]')) } catch { return new Set() }
  })
  const [annotations, setAnnotations] = useState<Record<string,string>>(() => {
    try { return JSON.parse(localStorage.getItem(`vis-ann-${canvas.id}`) ?? '{}') as Record<string,string> } catch { return {} }
  })
  const [annotationInput, setAnnotationInput] = useState<string|null>(null)
  const [annotationText, setAnnotationText]   = useState('')
  // Ticker & what-if
  const [showTicker, setShowTicker]           = useState(true)
  const [showWhatIf, setShowWhatIf]           = useState<Set<string>>(new Set())
  const [whatIfValues, setWhatIfValues]       = useState<Record<string,number>>({})
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
  const [summaryExpanded, setSummaryExpanded] = useState(false)
  // Info panel state
  const [infoWidgetId, setInfoWidgetId]           = useState<string | null>(null)
  const [widgetDescriptions, setWidgetDescriptions] = useState<Record<string, string>>({})
  const [descLoading, setDescLoading]             = useState<Record<string, boolean>>({})
  // Table accordion state — tables are collapsed by default
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  const [heroCollapsed, setHeroCollapsed] = useState(false)
  const [canvasName, setCanvasName] = useState(canvas.name)
  const [renamingCanvas, setRenamingCanvas] = useState(false)
  const [renameValue, setRenameValue] = useState(canvas.name)
  // Table chart type overrides (for visualizing table data as charts)
  const [tableChartTypes, setTableChartTypes] = useState<Record<string,string>>({})
  // AI-generated table names
  const [tableAiNames, setTableAiNames] = useState<Record<string,string>>({})
  const [tableAiNaming, setTableAiNaming] = useState<Record<string,boolean>>({})
  // Theme picker
  const [showThemePicker, setShowThemePicker] = useState(false)
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
      @keyframes vis-ticker-scroll { 0%{transform:translateX(0)} 100%{transform:translateX(-50%)} }
      .vis-card { transition: transform 0.18s ease, box-shadow 0.18s ease; cursor: default; }
      .vis-card:hover { transform: translateY(-3px); box-shadow: 0 12px 32px rgba(0,0,0,0.13) !important; }
      .vis-info-btn { opacity: 0; transition: opacity 0.15s, border-color 0.15s, color 0.15s; }
      .vis-card:hover .vis-info-btn { opacity: 1; }
      .vis-info-btn.active { opacity: 1; }
      .vis-focus-container .vis-card { transition: opacity 0.2s, transform 0.18s, box-shadow 0.18s; }
      .vis-focus-container:hover .vis-card:not(:hover) { opacity: 0.22 !important; transform: none !important; box-shadow: none !important; }
      .vis-focus-container .vis-card:hover { opacity: 1 !important; }
      .vis-ticker-inner { display:flex; gap:48px; white-space:nowrap; animation: vis-ticker-scroll 38s linear infinite; width:max-content; }
      .vis-no-print {}
      @media print { .vis-no-print{display:none!important} }
    `
    document.head.appendChild(s)
    return () => { document.getElementById(id)?.remove() }
  }, [])

  // ── Persist theme ────────────────────────────────────────────────────────
  useEffect(() => {
    try { localStorage.setItem(`visually-theme-${canvas.id}`, String(themeIdx)) } catch {}
  }, [themeIdx, canvas.id])

  // ── Slide auto-advance ───────────────────────────────────────────────────
  const slideDeck = useMemo(() => [...kpis, ...visualCharts, ...tables], [kpis, visualCharts, tables])
  useEffect(() => {
    if (!slidePlaying || !slideMode) return
    const id = setInterval(() => setSlideIdx(v => (v + 1) % Math.max(1, slideDeck.length)), 5000)
    return () => clearInterval(id)
  }, [slidePlaying, slideMode, slideDeck.length])

  // ── Close theme picker on outside click ──────────────────────────────────
  useEffect(() => {
    if (!showThemePicker) return
    const h = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('[data-theme-picker]')) setShowThemePicker(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showThemePicker])

  // ── Keyboard shortcuts ───────────────────────────────────────────────────
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Escape') {
        if (showThemePicker) { setShowThemePicker(false); return }
        if (slideMode) { setSlideMode(false); setSlidePlaying(false); return }
        if (fullscreenId) { setFullscreenId(null); return }
        if (infoWidgetId) { setInfoWidgetId(null); return }
        onClose()
      }
      if (slideMode) {
        if (e.key === 'ArrowRight') setSlideIdx(v => Math.min(v + 1, slideDeck.length - 1))
        if (e.key === 'ArrowLeft')  setSlideIdx(v => Math.max(v - 1, 0))
        if (e.key === ' ') { e.preventDefault(); setSlidePlaying(v => !v) }
        return
      }
      if (e.key === '1') setActiveSection('overview')
      if (e.key === '2' && visualCharts.length > 0) setActiveSection('charts')
      if (e.key === '3' && tables.length > 0) setActiveSection('data')
      if (e.key === 'c' || e.key === 'C') { setInfoWidgetId(null); setShowChat(v => !v) }
      if (e.key === 't' || e.key === 'T') setThemeIdx(v => (v + 1) % THEMES.length)
      if (e.key === 'f' || e.key === 'F') {
        if (fullscreenId) setFullscreenId(null)
        else if (visualCharts.length > 0) setFullscreenId(visualCharts[0].id)
      }
      if ((e.key === 's' || e.key === 'S') && !e.ctrlKey && !e.metaKey) { setSlideMode(v => !v); setSlideIdx(0) }
      if ((e.key === 'p' || e.key === 'P') && !e.ctrlKey && !e.metaKey) setBgPattern(prev => { const o: BgPattern[] = ['none','dots','mesh','graph']; return o[(o.indexOf(prev)+1)%4] })
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose, visualCharts, tables.length, infoWidgetId, slideMode, fullscreenId, slideDeck.length])

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
      await new Promise<void>((resolve, reject) => {
        c.toBlob(async b => {
          if (!b) { reject(new Error('no blob')); return }
          try {
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': b })])
            resolve()
          } catch {
            // Fallback: download as PNG instead
            const url = URL.createObjectURL(b)
            const a = document.createElement('a'); a.href = url; a.download = `chart.png`; a.click()
            URL.revokeObjectURL(url)
            resolve()
          }
        })
      })
      showToast('Chart copied / downloaded!')
    } catch { showToast('Could not export chart') }
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

  // ── New interaction callbacks ────────────────────────────────────────────
  const toggleBookmark = useCallback((id: string) => {
    setBookmarked(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      try { localStorage.setItem(`vis-bm-${canvas.id}`, JSON.stringify(Array.from(next))) } catch {}
      return next
    })
  }, [canvas.id])

  const saveAnnotation = useCallback((id: string, text: string) => {
    const trimmed = text.trim()
    setAnnotations(prev => {
      const next = trimmed ? { ...prev, [id]: trimmed } : (({ [id]: _, ...rest }) => rest)(prev)
      try { localStorage.setItem(`vis-ann-${canvas.id}`, JSON.stringify(next)) } catch {}
      return next
    })
    setAnnotationInput(null); setAnnotationText('')
  }, [canvas.id])

  const aiRenameTable = useCallback(async (w: CanvasWidgetData) => {
    if (tableAiNaming[w.id]) return
    setTableAiNaming(prev => ({ ...prev, [w.id]: true }))
    try {
      const cols = (w.chart_data?.columns as string[] | undefined)?.join(', ') ?? 'unknown columns'
      const rows = (w.chart_data?.rows as Record<string,unknown>[] | undefined) ?? []
      const sample = rows.slice(0, 3).map(r => Object.values(r).slice(0, 4).join(', ')).join(' | ')
      const resp = await chatApi.send({
        session_id: `rename-${w.id}-${canvas.id}`,
        message: `Name this data table accurately in 3–7 words. Columns: ${cols}. Sample rows: ${sample}. Current name: "${w.title}". Reply with ONLY the new name, no quotes, no explanation.`,
        project_id: projectId,
        connection_id: connectionId,
      })
      const newName = resp.data?.text?.trim().replace(/^["']|["']$/g, '') ?? w.title
      setTableAiNames(prev => ({ ...prev, [w.id]: newName }))
      showToast(`✓ Renamed to "${newName}"`)
    } catch { showToast('AI rename failed') }
    finally { setTableAiNaming(prev => ({ ...prev, [w.id]: false })) }
  }, [tableAiNaming, projectId, connectionId, canvas.id, showToast])

  const commitRename = useCallback(async () => {
    const trimmed = renameValue.trim()
    if (!trimmed || trimmed === canvasName) { setRenamingCanvas(false); return }
    setCanvasName(trimmed)
    setRenamingCanvas(false)
    try {
      await canvasApi.rename(canvas.id, trimmed)
      onCanvasRename?.(trimmed)
      showToast(`✓ Renamed to "${trimmed}"`)
    } catch { showToast('Rename failed'); setCanvasName(canvasName) }
  }, [renameValue, canvasName, canvas.id, onCanvasRename, showToast])

  const handlePrint = useCallback(() => window.print(), [])

  const handleExportJSON = useCallback(() => {
    const data = { canvas: canvas.name, exported: new Date().toISOString(), widgets: widgets.map(w => ({ title: w.title, type: w.chart_type, rows: (w.chart_data?.rows as unknown[])?.length ?? 0 })) }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `${canvas.name.replace(/\s+/g,'-')}.json`; a.click()
    URL.revokeObjectURL(url); showToast('Dashboard exported!')
  }, [canvas, widgets, showToast])

  const handleEmailCopy = useCallback(() => {
    const kpiLines = kpis.map(w => { const { num } = getKpiMeta(w); return `• ${w.title}: ${fmtNum(num)}` }).join('\n')
    navigator.clipboard.writeText(`Dashboard: ${canvas.name}\n\n${execSummary}\n\nKey Metrics:\n${kpiLines}`)
      .then(() => showToast('Email summary copied!')).catch(() => {})
  }, [kpis, canvas.name, execSummary, showToast])

  const setChartOverride = useCallback((id: string, type: string | undefined) => {
    setChartTypeOverrides(prev => {
      if (!type) { const n = { ...prev }; delete n[id]; return n }
      return { ...prev, [id]: type }
    })
  }, [])

  // ── Shared styles ────────────────────────────────────────────────────────
  const cardBase: React.CSSProperties = {
    background: theme.surface,
    borderRadius: theme.cardRadius,
    padding: '18px 20px',
    border: `1px solid ${theme.border}`,
    boxShadow: theme.glassMorphism
      ? `0 4px 24px rgba(0,0,0,0.5), 0 0 0 1px ${theme.accent}18`
      : (theme.id === 'midnight' || theme.id === 'digitalnative')
        ? '0 4px 24px rgba(0,0,0,0.5)'
        : '0 1px 4px rgba(0,0,0,0.06), 0 4px 14px rgba(0,0,0,0.04)',
    backdropFilter: theme.glassMorphism ? 'blur(12px)' : undefined,
    animation: theme.animations ? 'visually-slideUp 0.4s ease both' : undefined,
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
    const isStarred = bookmarked.has(w.id)
    const whatIf = whatIfValues[w.id] ?? 0
    return (
      <div key={w.id} id={`vr-kpi-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-kpi-${w.id}`) }}
        style={{ ...cardBase, animationDelay: `${idx * 60}ms`, borderLeft: `3px solid ${borderColor}`, position: 'relative' }}
      >
        {/* Bookmark star */}
        <button onClick={e => { e.stopPropagation(); toggleBookmark(w.id) }}
          style={{ position: 'absolute', top: 10, left: 10, background: 'none', border: 'none', cursor: 'pointer', color: isStarred ? '#F59E0B' : theme.muted, opacity: isStarred ? 1 : 0.35, padding: 2, fontSize: 13, lineHeight: 1 }}>
          {isStarred ? '★' : '☆'}
        </button>
        {/* Info button */}
        <button onClick={e => { e.stopPropagation(); openInfo(w) }} title="Explain this metric"
          className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
          style={{ position: 'absolute', top: 10, right: 10, width: 22, height: 22, borderRadius: '50%', padding: 0, background: isInfoOpen ? theme.accentBg : 'transparent', border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`, color: isInfoOpen ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Info size={11} />
        </button>

        <p style={{ fontSize: Math.max(9, theme.fontSizeBase - 4), fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 6px', padding: '0 26px 0 20px' }}>{w.title}</p>
        {hasValues ? (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
              <p style={{ fontSize: theme.kpiFontSize, fontWeight: 800, color: theme.text, margin: 0, fontVariantNumeric: 'tabular-nums', lineHeight: 1, fontFamily: theme.id === 'digitalnative' ? theme.fontFamily : '"SF Mono","JetBrains Mono",monospace' }}>
                <AnimatedCounter value={Math.round(num * (1 + whatIf/100))} />
              </p>
              {spark.length >= 2 && <Sparkline data={spark} color={borderColor} />}
            </div>
            {delta && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                <TrendBadge trend={delta} small />
                <span style={{ fontSize: 10, color: theme.muted }}>vs prior</span>
              </div>
            )}
            {/* What-if slider */}
            {showWhatIf.has(w.id) && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px dashed ${theme.border}` }}>
                <input type="range" min={-50} max={50} value={whatIf}
                  onChange={e => setWhatIfValues(prev => ({ ...prev, [w.id]: Number(e.target.value) }))}
                  style={{ width: '100%', accentColor: theme.accent, cursor: 'pointer' }} />
                <p style={{ fontSize: 10, color: theme.muted, margin: '2px 0 0', textAlign: 'center' }}>
                  {whatIf >= 0 ? '+' : ''}{whatIf}% → <strong style={{ color: theme.text }}>{fmtNum(Math.round(num*(1+whatIf/100)))}</strong>
                </p>
              </div>
            )}
            <button onClick={e => { e.stopPropagation(); setShowWhatIf(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n }) }}
              style={{ marginTop: 4, fontSize: 9, color: theme.muted, background: 'none', border: 'none', cursor: 'pointer', padding: 0, opacity: 0.6 }}>
              {showWhatIf.has(w.id) ? '▲ hide what-if' : '~ what-if'}
            </button>
          </>
        ) : (
          <ChartRenderer result={toChartResult(w)} height={72} />
        )}

        {/* Annotation */}
        {annotations[w.id] && annotationInput !== w.id && (
          <div onClick={() => { setAnnotationInput(w.id); setAnnotationText(annotations[w.id]) }}
            style={{ marginTop: 8, padding: '4px 8px', background: '#FEF9C3', border: '1px solid #FCD34D', borderRadius: 6, fontSize: 10, color: '#78350F', cursor: 'pointer' }}>
            📌 {annotations[w.id]}
          </div>
        )}
        {annotationInput === w.id && (
          <input autoFocus value={annotationText} onChange={e => setAnnotationText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') saveAnnotation(w.id, annotationText); if (e.key === 'Escape') { setAnnotationInput(null); setAnnotationText('') } }}
            placeholder="Add note… (Enter saves, Esc cancels)"
            style={{ marginTop: 8, width: '100%', fontSize: 11, padding: '4px 8px', border: `1px solid ${theme.border}`, borderRadius: 6, background: theme.bg, color: theme.text, outline: 'none', boxSizing: 'border-box' }} />
        )}
        {!annotations[w.id] && annotationInput !== w.id && (
          <button onClick={e => { e.stopPropagation(); setAnnotationInput(w.id); setAnnotationText('') }}
            className="vis-info-btn" style={{ marginTop: 4, fontSize: 9, color: theme.muted, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
            📌 add note
          </button>
        )}
      </div>
    )
  }

  // ── Chart card renderer ──────────────────────────────────────────────────
  const renderChart = (w: CanvasWidgetData, idx: number, h = 220) => {
    const isInfoOpen = infoWidgetId === w.id
    const isStarred = bookmarked.has(w.id)
    const activeType = chartTypeOverrides[w.id] ?? w.chart_type
    const trend = computeTrendBadge(w)
    const caption = computeCaption(w)
    const keyDriver = computeKeyDriver(w)
    const altType = suggestAlternativeChart(w)
    const chartResult = { ...toChartResult(w), chart_type: activeType }
    const anomalyIdxs = detectAnomalyIndices((w.chart_data?.values as (number|null)[]) ?? [])

    const rawRows = (w.chart_data?.rows as Record<string,unknown>[]) ?? []
    const rawCols = (w.chart_data?.columns as string[]) ?? (rawRows[0] ? Object.keys(rawRows[0]) : [])

    return (
      <div key={w.id} id={`vr-chart-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-chart-${w.id}`) }}
        style={{ ...cardBase, animationDelay: `${idx * 60}ms` }}
      >
        {/* Header row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
            <button onClick={e => { e.stopPropagation(); toggleBookmark(w.id) }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: isStarred ? '#F59E0B' : theme.muted, opacity: isStarred ? 1 : 0.35, padding: 0, fontSize: 13, flexShrink: 0 }}>
              {isStarred ? '★' : '☆'}
            </button>
            <p style={{ fontSize: Math.max(11, theme.fontSizeBase), fontWeight: 700, color: theme.text, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.title}</p>
            {trend && <TrendBadge trend={trend} small />}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
            {/* Info */}
            <button onClick={e => { e.stopPropagation(); openInfo(w) }} title="Explain"
              className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
              style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: isInfoOpen ? theme.accentBg : 'transparent', border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`, color: isInfoOpen ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Info size={12} />
            </button>
            {/* Fullscreen */}
            <button onClick={e => { e.stopPropagation(); setFullscreenId(fullscreenId === w.id ? null : w.id) }} title="Fullscreen (F)"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 3, color: theme.muted, opacity: 0.45, display: 'flex', alignItems: 'center' }}
              onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
              onMouseLeave={e => (e.currentTarget.style.opacity = '0.45')}>
              <Maximize2 size={12} />
            </button>
            {/* Copy */}
            <button onClick={() => copyAsPng(`vr-chart-${w.id}`)} title="Copy PNG"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 3, color: theme.muted, opacity: 0.45, display: 'flex', alignItems: 'center' }}
              onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
              onMouseLeave={e => (e.currentTarget.style.opacity = '0.45')}>
              <Copy size={12} />
            </button>
          </div>
        </div>

        {/* Chart type switcher */}
        <ChartTypeSwitcher baseType={w.chart_type.toLowerCase()} active={activeType.toLowerCase()} onSelect={t => setChartOverride(w.id, t)} theme={theme} />

        {/* Chart content — normal or split view */}
        {w.chart_data ? (
          splitViewId === w.id ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: 9, color: theme.muted, margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Current</p>
                <ChartRenderer result={chartResult} height={Math.round(h * 0.85)} showAnomalies anomalyIndices={anomalyIdxs} />
              </div>
              <div style={{ flex: 1, borderLeft: `1px solid ${theme.border}`, paddingLeft: 8 }}>
                <p style={{ fontSize: 9, color: theme.muted, margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Alternative</p>
                <ChartRenderer result={{ ...toChartResult(w), chart_type: altType ?? 'bar' }} height={Math.round(h * 0.85)} />
              </div>
            </div>
          ) : (
            <ChartRenderer result={chartResult} height={h} showAnomalies anomalyIndices={anomalyIdxs} />
          )
        ) : (
          <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>
        )}

        {/* Inline caption */}
        {caption && <p style={{ fontSize: 11, color: theme.muted, margin: '8px 0 0', fontStyle: 'italic', lineHeight: 1.45 }}>{caption}</p>}

        {/* Key driver callout */}
        {keyDriver && <KeyDriverCallout text={keyDriver} theme={theme} />}

        {/* Action row */}
        <div style={{ display: 'flex', gap: 5, marginTop: 10, flexWrap: 'wrap' }}>
          <button onClick={() => setShowRawData(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n })}
            style={{ fontSize: 10, color: theme.muted, background: 'none', border: `1px solid ${theme.border}`, borderRadius: 5, padding: '2px 8px', cursor: 'pointer' }}>
            {showRawData.has(w.id) ? '▲ Hide data' : '⊞ Raw data'}
          </button>
          {altType && (
            <button onClick={() => setSplitViewId(splitViewId === w.id ? null : w.id)}
              style={{ fontSize: 10, color: theme.muted, background: 'none', border: `1px solid ${theme.border}`, borderRadius: 5, padding: '2px 8px', cursor: 'pointer' }}>
              {splitViewId === w.id ? '✕ Split' : '⊡ Split view'}
            </button>
          )}
          <button onClick={e => { e.stopPropagation(); setAnnotationInput(w.id); setAnnotationText(annotations[w.id] ?? '') }}
            style={{ fontSize: 10, color: theme.muted, background: 'none', border: `1px solid ${theme.border}`, borderRadius: 5, padding: '2px 8px', cursor: 'pointer' }}>
            📌 Note
          </button>
        </div>

        {/* Raw data underlay */}
        {showRawData.has(w.id) && rawRows.length > 0 && (
          <div style={{ marginTop: 8, overflowX: 'auto', maxHeight: 160, overflowY: 'auto', border: `1px solid ${theme.border}`, borderRadius: 8 }}>
            <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: theme.bg }}>
                {rawCols.map(c => <th key={c} style={{ padding: '4px 8px', textAlign: 'left', fontWeight: 600, color: theme.muted, whiteSpace: 'nowrap', borderBottom: `1px solid ${theme.border}` }}>{c}</th>)}
              </tr></thead>
              <tbody>
                {rawRows.slice(0, 20).map((row, i) => (
                  <tr key={i} style={{ background: i % 2 === 0 ? 'transparent' : theme.bg }}>
                    {rawCols.map(c => <td key={c} style={{ padding: '3px 8px', color: theme.text, whiteSpace: 'nowrap', borderBottom: `1px solid ${theme.border}` }}>{String(row[c] ?? '')}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* AI alternative suggestion chip */}
        {altType && splitViewId !== w.id && !chartTypeOverrides[w.id] && (
          <p style={{ fontSize: 10, color: theme.muted, marginTop: 6, marginBottom: 0 }}>
            💡 Try as{' '}
            <button onClick={() => setChartOverride(w.id, altType)}
              style={{ color: theme.accent, background: 'none', border: 'none', cursor: 'pointer', fontSize: 10, fontWeight: 600, textDecoration: 'underline', padding: 0 }}>
              {altType}
            </button>
          </p>
        )}

        {/* Annotation */}
        {annotations[w.id] && annotationInput !== w.id && (
          <div onClick={() => { setAnnotationInput(w.id); setAnnotationText(annotations[w.id]) }}
            style={{ marginTop: 8, padding: '4px 8px', background: '#FEF9C3', border: '1px solid #FCD34D', borderRadius: 6, fontSize: 10, color: '#78350F', cursor: 'pointer' }}>
            📌 {annotations[w.id]}
          </div>
        )}
        {annotationInput === w.id && (
          <input autoFocus value={annotationText} onChange={e => setAnnotationText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') saveAnnotation(w.id, annotationText); if (e.key === 'Escape') { setAnnotationInput(null); setAnnotationText('') } }}
            placeholder="Add note… (Enter saves, Esc cancels)"
            style={{ marginTop: 8, width: '100%', fontSize: 11, padding: '4px 8px', border: `1px solid ${theme.border}`, borderRadius: 6, background: theme.bg, color: theme.text, outline: 'none', boxSizing: 'border-box' }} />
        )}
      </div>
    )
  }

  // ── Newspaper layout ─────────────────────────────────────────────────────
  const renderNewspaper = () => {
    const featured = visualCharts[0]
    const secondary = visualCharts.slice(1)
    const divider = (title: string, color: string) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '24px 0 14px' }}>
        <div style={{ width: 4, height: 20, background: color, borderRadius: 2, flexShrink: 0 }} />
        <span style={{ fontFamily: "'Georgia',serif", fontSize: 11, fontWeight: 900, textTransform: 'uppercase', letterSpacing: '0.2em', color }}>{title}</span>
        <div style={{ flex: 1, height: 1, background: `${color}40` }} />
      </div>
    )
    return (
      <div style={{ padding: '20px 28px 40px', maxWidth: 1400 }}>

        {/* Newspaper masthead */}
        <div style={{ textAlign: 'center', borderBottom: `3px double ${theme.text}`, borderTop: `3px double ${theme.text}`, padding: '10px 0', marginBottom: 22 }}>
          <p style={{ fontFamily: "'Georgia',serif", fontSize: 26, fontWeight: 900, color: theme.text, margin: 0, letterSpacing: '-0.5px', textTransform: 'uppercase' }}>{canvas.name}</p>
          <p style={{ fontSize: 10, color: theme.muted, margin: '3px 0 0', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
            ANALYTICAL REPORT · {widgets.length} METRICS · {new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }).toUpperCase()}
          </p>
        </div>

        {/* Above-the-fold: featured chart (3fr) + KPI pull quotes (1fr) */}
        <div style={{ display: 'grid', gridTemplateColumns: featured ? '3fr 1fr' : '1fr', gap: 16, marginBottom: 8 }}>
          {featured && (
            <div style={{ ...cardBase, padding: '18px 20px', borderLeft: `4px solid ${theme.accent}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                <span style={{ fontFamily: "'Georgia',serif", fontSize: 10, fontWeight: 900, textTransform: 'uppercase', letterSpacing: '0.16em', color: theme.accent }}>Featured Analysis</span>
                {(() => { const trend = computeTrendBadge(featured); return trend ? <TrendBadge trend={trend} small /> : null })()}
              </div>
              <h3 style={{ fontSize: 16, fontWeight: 800, color: theme.text, margin: '0 0 12px', letterSpacing: '-0.3px' }}>{featured.title}</h3>
              {featured.chart_data && <ChartRenderer result={{ ...toChartResult(featured), chart_type: chartTypeOverrides[featured.id] ?? featured.chart_type }} height={280} />}
              {(() => { const c = computeCaption(featured); return c ? <p style={{ fontSize: 11, color: theme.muted, margin: '10px 0 0', fontStyle: 'italic', lineHeight: 1.5 }}>{c}</p> : null })()}
              {(() => { const kd = computeKeyDriver(featured); return kd ? <KeyDriverCallout text={kd} theme={theme} /> : null })()}
            </div>
          )}
          {/* KPI pull-quotes column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {kpis.slice(0, 5).map((w, i) => {
              const { num, delta } = getKpiMeta(w)
              const col = delta?.up === false ? '#DC2626' : delta?.up ? '#16A34A' : theme.accent
              return (
                <div key={w.id} className="vis-card" style={{ ...cardBase, padding: '14px 16px', borderTop: `3px solid ${col}`, textAlign: 'center', animationDelay: `${i * 60}ms`, flex: 1 }}>
                  <p style={{ fontSize: 9, color: theme.muted, margin: '0 0 6px', textTransform: 'uppercase', letterSpacing: '0.07em', lineHeight: 1.3 }}>{w.title}</p>
                  <p style={{ fontSize: theme.kpiFontSize, fontWeight: 900, color: theme.text, margin: '0 0 5px', fontFamily: '"SF Mono",monospace', lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{fmtNum(num)}</p>
                  {delta && <TrendBadge trend={delta} />}
                </div>
              )
            })}
            {kpis.length === 0 && (
              <div style={{ ...cardBase, padding: '16px', textAlign: 'center', color: theme.muted, fontSize: 11 }}>
                No KPI cards yet.<br />Add KPI widgets to see pull quotes.
              </div>
            )}
          </div>
        </div>

        {/* Secondary charts — 3 columns */}
        {secondary.length > 0 && (
          <>
            {divider('Trend Analysis', '#7C3AED')}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(320px,1fr))', gap: 14, marginBottom: 8 }}>
              {secondary.map((w, i) => (
                <div key={w.id} className="vis-card" style={{ ...cardBase, padding: '16px 18px', animationDelay: `${i * 60}ms` }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div>
                      <h4 style={{ fontSize: 13, fontWeight: 700, color: theme.text, margin: 0 }}>{w.title}</h4>
                      {(() => { const c = computeCaption(w); return c ? <p style={{ fontSize: 10, color: theme.muted, margin: '3px 0 0', fontStyle: 'italic' }}>{c}</p> : null })()}
                    </div>
                    {(() => { const t = computeTrendBadge(w); return t ? <TrendBadge trend={t} small /> : null })()}
                  </div>
                  <ChartTypeSwitcher baseType={w.chart_type.toLowerCase()} active={(chartTypeOverrides[w.id] ?? w.chart_type).toLowerCase()} onSelect={t => setChartOverride(w.id, t)} theme={theme} />
                  {w.chart_data && <ChartRenderer result={{ ...toChartResult(w), chart_type: chartTypeOverrides[w.id] ?? w.chart_type }} height={200} />}
                  {(() => { const kd = computeKeyDriver(w); return kd ? <KeyDriverCallout text={kd} theme={theme} /> : null })()}
                </div>
              ))}
            </div>
          </>
        )}

        {/* Data tables — full detail with visualize-as */}
        {tables.length > 0 && (
          <>
            {divider('Data Tables', '#0D9488')}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(440px,1fr))', gap: 14 }}>
              {tables.map((w, i) => {
                const isExpanded = expandedTables.has(w.id)
                const rows = (w.chart_data?.rows as Record<string,unknown>[] | undefined) ?? []
                const cols = (w.chart_data?.columns as string[] | undefined) ?? []
                const activeViz = tableChartTypes[w.id] ?? 'table'
                return (
                  <div key={w.id} className="vis-card" style={{ ...cardBase, padding: 0, overflow: 'hidden', animationDelay: `${i * 60}ms` }}>
                    {/* Header */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '13px 16px', borderBottom: `1px solid ${theme.border}` }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                        <Table2 size={13} color={theme.accent} />
                        <span style={{ fontSize: 13, fontWeight: 700, color: theme.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {tableAiNames[w.id] ?? w.title}
                        </span>
                        {rows.length > 0 && <span style={{ padding: '1px 7px', background: theme.accentBg, borderRadius: 8, fontSize: 9, fontWeight: 700, color: theme.accent, flexShrink: 0 }}>{rows.length} rows</span>}
                      </div>
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        <button onClick={() => aiRenameTable(w)} title="AI rename"
                          style={{ padding: '2px 6px', fontSize: 9, borderRadius: 5, background: 'none', border: `1px solid ${theme.border}`, color: theme.muted, cursor: tableAiNaming[w.id] ? 'wait' : 'pointer' }}>
                          {tableAiNaming[w.id] ? '...' : '✨'}
                        </button>
                        <button onClick={() => setExpandedTables(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n })}
                          style={{ padding: '2px 8px', fontSize: 9, borderRadius: 5, background: isExpanded ? theme.accentBg : 'none', border: `1px solid ${theme.border}`, color: isExpanded ? theme.accent : theme.muted, cursor: 'pointer' }}>
                          {isExpanded ? '▲ Close' : '▼ Open'}
                        </button>
                      </div>
                    </div>
                    {/* Column summary pills — always visible */}
                    {cols.length > 0 && (
                      <div style={{ padding: '8px 14px', display: 'flex', gap: 5, flexWrap: 'wrap', borderBottom: `1px solid ${theme.border}` }}>
                        {cols.slice(0, 8).map(c => (
                          <span key={c} style={{ padding: '2px 8px', background: theme.bg, border: `1px solid ${theme.border}`, borderRadius: 10, fontSize: 9, color: theme.muted }}>{c}</span>
                        ))}
                        {cols.length > 8 && <span style={{ fontSize: 9, color: theme.muted, alignSelf: 'center' }}>+{cols.length - 8} more</span>}
                      </div>
                    )}
                    {/* Expanded body */}
                    {isExpanded && (
                      <div style={{ padding: '10px 14px 14px' }}>
                        {/* Visualize-as buttons */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 10, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.05em' }}>View as:</span>
                          {(['table','bar','line','pie','bar_horizontal'] as const).map(t => (
                            <button key={t} onClick={() => setTableChartTypes(prev => ({ ...prev, [w.id]: t }))}
                              style={{ padding: '2px 9px', fontSize: 10, borderRadius: 6, border: `1px solid ${activeViz === t ? theme.accent : theme.border}`, background: activeViz === t ? theme.accentBg : 'none', color: activeViz === t ? theme.accent : theme.muted, cursor: 'pointer', fontWeight: activeViz === t ? 700 : 400 }}>
                              {t === 'bar_horizontal' ? 'h-bar' : t}
                            </button>
                          ))}
                        </div>
                        {w.chart_data
                          ? <ChartRenderer result={{ ...toChartResult(w), chart_type: activeViz }} height={260} />
                          : <div style={{ height: 80, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>
                        }
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    )
  }

  // ── Section content ──────────────────────────────────────────────────────
  const renderContent = () => {
    // Newspaper layout has its own full renderer
    if (layoutMode === 'newspaper') return renderNewspaper()
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
        {/* Bookmarked / Pinned section */}
        {bookmarked.size > 0 && activeSection === 'overview' && (
          <div style={{ marginBottom: 28, borderLeft: `3px solid #F59E0B`, paddingLeft: 12 }}>
            <p style={{ ...secLabel, color: '#F59E0B' }}>★ PINNED</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))', gap: 14 }}>
              {widgets.filter(w => bookmarked.has(w.id)).map((w, i) =>
                ['kpi','kpi_card'].includes(w.chart_type) ? renderKpi(w, i) : renderChart(w, i, 180)
              )}
            </div>
          </div>
        )}

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
          <div style={{ marginBottom: 28, borderLeft: `3px solid #2563EB`, paddingLeft: 12 }}>
            <p style={secLabel}>Key Metrics</p>
            <div className={focusMode ? 'vis-focus-container' : undefined} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
              {kpis.map((w, i) => renderKpi(w, i))}
            </div>
          </div>
        )}

        {/* Visual charts */}
        {(activeSection === 'overview' || activeSection === 'charts') && visualCharts.length > 0 && (
          <div style={{ marginBottom: 28, borderLeft: `3px solid #7C3AED`, paddingLeft: 12 }}>
            {activeSection === 'overview' && <p style={secLabel}>Charts</p>}
            <div className={focusMode ? 'vis-focus-container' : undefined} style={{ display: 'grid', gridTemplateColumns: cols, gap: 14 }}>
              {visualCharts.map((w, i) => renderChart(w, i, chartH))}
            </div>
          </div>
        )}

        {/* Data tables — accordion: collapsed by default, click header to expand */}
        {(activeSection === 'overview' || activeSection === 'data') && tables.length > 0 && (
          <div style={{ borderLeft: `3px solid #0D9488`, paddingLeft: 12 }}>
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
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                      <Table2 size={14} color={theme.accent} style={{ flexShrink: 0 }} />
                      <span style={{ fontSize: 13, fontWeight: 700, color: theme.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tableAiNames[w.id] ?? w.title}
                      </span>
                      {rows.length > 0 && (
                        <span style={{ padding: '2px 7px', background: theme.accentBg, border: `1px solid ${theme.border}`, borderRadius: 10, fontSize: 10, fontWeight: 600, color: theme.accent, flexShrink: 0 }}>
                          {rows.length} rows
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                      {/* AI rename */}
                      <button onClick={e => { e.stopPropagation(); aiRenameTable(w) }} title="AI auto-name this table"
                        className="vis-info-btn"
                        style={{ padding: '2px 7px', fontSize: 9, borderRadius: 6, background: 'none', border: `1px solid ${theme.border}`, color: theme.muted, cursor: tableAiNaming[w.id] ? 'wait' : 'pointer' }}>
                        {tableAiNaming[w.id] ? <Loader2 size={9} style={{ animation: 'visually-spin 1s linear infinite' }} /> : '✨ AI Name'}
                      </button>
                      {/* Info button */}
                      <button onClick={() => openInfo(w)} title="Explain this table"
                        className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
                        style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: isInfoOpen ? theme.accentBg : 'transparent', border: `1px solid ${isInfoOpen ? theme.accent : theme.border}`, color: isInfoOpen ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <Info size={12} />
                      </button>
                      <div onClick={e => { e.stopPropagation(); setExpandedTables(prev => { const next = new Set(prev); if (next.has(w.id)) next.delete(w.id); else next.add(w.id); return next }) }}
                        style={{ color: theme.muted, display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </div>
                    </div>
                  </div>
                  {/* Accordion body */}
                  {isExpanded && (
                    <div style={{ padding: '10px 16px 14px' }}>
                      {/* Visualize-as selector */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>View as:</span>
                        {(['table','bar','line','pie','bar_horizontal'] as const).map(t => {
                          const active = (tableChartTypes[w.id] ?? 'table') === t
                          return (
                            <button key={t} onClick={() => setTableChartTypes(prev => ({ ...prev, [w.id]: t }))}
                              style={{ padding: '2px 10px', fontSize: 10, borderRadius: 6, border: `1px solid ${active ? theme.accent : theme.border}`, background: active ? theme.accentBg : 'none', color: active ? theme.accent : theme.muted, cursor: 'pointer', fontWeight: active ? 700 : 400 }}>
                              {t === 'bar_horizontal' ? 'h-bar' : t}
                            </button>
                          )
                        })}
                      </div>
                      {w.chart_data ? (
                        <ChartRenderer
                          result={{ ...toChartResult(w), chart_type: tableChartTypes[w.id] ?? 'table' }}
                          height={280}
                        />
                      ) : (
                        <div style={{ height: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>
                      )}
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
    <div style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', fontFamily: theme.fontFamily, background: theme.bg, fontSize: theme.fontSizeBase }}>

      {/* Background pattern overlay */}
      {bgPattern !== 'none' && (
        <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0, ...getPatternStyle(bgPattern, theme) }} />
      )}

      {/* ── Left Rail ──────────────────────────────────────────────────────── */}
      <nav style={{ width: 74, background: theme.rail, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '14px 0', flexShrink: 0, gap: 2, boxShadow: '2px 0 12px rgba(0,0,0,0.15)', position: 'relative', zIndex: 50, overflow: 'visible' }}>
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

        {/* Theme picker */}
        <div data-theme-picker style={{ position: 'relative' }}>
          <button onClick={() => setShowThemePicker(v => !v)} title="Choose theme (T)"
            style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: showThemePicker ? theme.railActive : 'transparent', color: showThemePicker ? '#93C5FD' : theme.railText, border: 'none', cursor: 'pointer' }}>
            <div style={{ width: 18, height: 18, borderRadius: 5, background: `linear-gradient(135deg,${theme.heroFrom},${theme.heroTo})`, border: '2px solid rgba(255,255,255,0.25)' }} />
            <span style={{ fontSize: 8, fontWeight: 500 }}>Theme</span>
          </button>
          {showThemePicker && (
            <div style={{ position: 'absolute', left: 62, bottom: 0, width: 210, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 14, padding: '8px 6px', zIndex: 200, boxShadow: '0 8px 32px rgba(0,0,0,0.28)' }}>
              <p style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 6px 6px' }}>Select Theme</p>
              {THEMES.map((t, i) => (
                <button key={t.id} onClick={() => { setThemeIdx(i); setShowThemePicker(false) }}
                  style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px', borderRadius: 9, border: 'none', background: themeIdx === i ? theme.accentBg : 'transparent', cursor: 'pointer', marginBottom: 2 }}>
                  <div style={{ width: 26, height: 26, borderRadius: 7, flexShrink: 0, background: `linear-gradient(135deg,${t.heroFrom},${t.heroTo})`, boxShadow: themeIdx === i ? `0 0 0 2px ${t.accent}` : 'none' }} />
                  <div style={{ textAlign: 'left', flex: 1 }}>
                    <p style={{ fontSize: 11, fontWeight: themeIdx === i ? 700 : 500, color: theme.text, margin: 0 }}>{t.label}</p>
                    <p style={{ fontSize: 9, color: theme.muted, margin: 0 }}>{t.fontFamily.split(',')[0].replace(/'/g,'').trim()}</p>
                  </div>
                  {themeIdx === i && <span style={{ color: theme.accent, fontSize: 13, flexShrink: 0 }}>✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

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
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative', zIndex: 1 }}>

        {/* Hero Header — collapsible */}
        <div style={{
          background: `linear-gradient(-45deg,${theme.heroFrom},${theme.heroMid},${theme.heroTo},${theme.heroMid})`,
          backgroundSize: '400% 400%', animation: 'visually-gradient 10s ease infinite',
          flexShrink: 0, position: 'relative', overflow: 'hidden',
          transition: 'padding 0.2s ease',
          padding: heroCollapsed ? '0' : '12px 24px 14px',
        }}>
          {!heroCollapsed && (
            <>
              <div style={{ position: 'absolute', top: -30, right: 80, width: 160, height: 160, borderRadius: '50%', background: 'rgba(124,58,237,0.18)', filter: 'blur(44px)', pointerEvents: 'none' }} />
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                    <span style={{ padding: '2px 8px', background: 'rgba(255,255,255,0.14)', backdropFilter: 'blur(8px)', borderRadius: 14, fontSize: 10, color: 'white', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#4ADE80', display: 'inline-block', animation: 'visually-pulse 2s ease infinite' }} />
                      LIVE
                    </span>
                    <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)' }}>{widgets.length} charts · Real-time data</span>
                  </div>
                  {renamingCanvas ? (
                    <input autoFocus value={renameValue} onChange={e => setRenameValue(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') { setRenamingCanvas(false); setRenameValue(canvasName) } }}
                      onBlur={commitRename}
                      style={{ fontSize: 20, fontWeight: 800, color: 'white', background: 'rgba(255,255,255,0.12)', border: '2px solid rgba(255,255,255,0.5)', borderRadius: 8, padding: '2px 10px', outline: 'none', width: 360, letterSpacing: '-0.4px' }} />
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => { setRenamingCanvas(true); setRenameValue(canvasName) }}>
                      <h1 style={{ fontSize: 20, fontWeight: 800, color: 'white', margin: 0, letterSpacing: '-0.4px' }}>{canvasName}</h1>
                      <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', userSelect: 'none' }} title="Click to rename">✏️</span>
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ display: 'flex', background: 'rgba(255,255,255,0.12)', borderRadius: 8, padding: 2, gap: 2 }}>
                    {(['grid', 'list', 'newspaper'] as LayoutMode[]).map(m => (
                      <button key={m} onClick={() => setLayoutMode(m)} title={`${m} layout`}
                        style={{ width: 28, height: 26, borderRadius: 6, border: 'none', cursor: 'pointer', background: layoutMode === m ? 'rgba(255,255,255,0.24)' : 'transparent', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: m === 'newspaper' ? 9 : undefined, fontWeight: m === 'newspaper' ? 700 : undefined }}>
                        {m === 'grid' ? <LayoutGrid size={13} /> : m === 'list' ? <List size={13} /> : 'NP'}
                      </button>
                    ))}
                  </div>
                  <button onClick={onClose} style={{ padding: '5px 12px', background: 'rgba(255,255,255,0.14)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 8, fontSize: 11, fontWeight: 600, color: 'white', cursor: 'pointer' }}>← Canvas</button>
                  <button onClick={() => setHeroCollapsed(true)} title="Collapse header"
                    style={{ width: 26, height: 26, borderRadius: 6, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.1)', color: 'white', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <ChevronUp size={13} />
                  </button>
                </div>
              </div>
            </>
          )}
          {/* Collapsed bar — always visible when heroCollapsed */}
          {heroCollapsed && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 16px', position: 'relative' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#4ADE80', animation: 'visually-pulse 2s ease infinite', display: 'inline-block' }} />
                <span style={{ fontSize: 12, fontWeight: 700, color: 'white', letterSpacing: '-0.2px', cursor: 'pointer' }} onClick={() => { setHeroCollapsed(false); setTimeout(() => { setRenamingCanvas(true); setRenameValue(canvasName) }, 150) }}>{canvasName}</span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)' }}>{widgets.length} widgets</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ display: 'flex', background: 'rgba(255,255,255,0.12)', borderRadius: 6, padding: 1, gap: 1 }}>
                  {(['grid', 'list', 'newspaper'] as LayoutMode[]).map(m => (
                    <button key={m} onClick={() => setLayoutMode(m)}
                      style={{ width: 22, height: 20, borderRadius: 5, border: 'none', cursor: 'pointer', background: layoutMode === m ? 'rgba(255,255,255,0.24)' : 'transparent', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 8 }}>
                      {m === 'grid' ? <LayoutGrid size={10} /> : m === 'list' ? <List size={10} /> : 'NP'}
                    </button>
                  ))}
                </div>
                <button onClick={onClose} style={{ padding: '3px 8px', background: 'rgba(255,255,255,0.14)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 6, fontSize: 10, fontWeight: 600, color: 'white', cursor: 'pointer' }}>← Canvas</button>
                <button onClick={() => setHeroCollapsed(false)} title="Expand header"
                  style={{ width: 22, height: 22, borderRadius: 5, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.1)', color: 'white', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <ChevronDown size={11} />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* AI Executive Summary — collapsed strip by default */}
        {(summaryLoading || execSummary) && (
          <div style={{ flexShrink: 0, borderBottom: `1px solid ${theme.border}`, background: theme.surface }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 20px', cursor: 'pointer' }}
              onClick={() => setSummaryExpanded(v => !v)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <div style={{ width: 18, height: 18, borderRadius: 5, background: `linear-gradient(135deg,${theme.accent},#7C3AED)`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Sparkles size={9} color="white" />
                </div>
                <span style={{ fontSize: 10, fontWeight: 700, color: theme.text, textTransform: 'uppercase', letterSpacing: '0.06em' }}>AI Executive Summary</span>
                {!summaryExpanded && execSummary && (
                  <span style={{ fontSize: 10, color: theme.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 400 }}>{execSummary.slice(0, 80)}…</span>
                )}
              </div>
              <span style={{ color: theme.muted, fontSize: 10, flexShrink: 0 }}>{summaryExpanded ? '▲' : '▼'}</span>
            </div>
            {summaryExpanded && (
              <div style={{ padding: '0 20px 12px' }}>
                {summaryLoading
                  ? <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}><Loader2 size={12} style={{ color: theme.accent }} /><span style={{ fontSize: 12, color: theme.muted }}>Generating…</span></div>
                  : <MarkdownText text={execSummary} color={theme.text} fontSize={13} />
                }
              </div>
            )}
          </div>
        )}

        {/* Section tabs */}
        <div style={{ display: 'flex', gap: 2, padding: '6px 20px 0', flexShrink: 0, borderBottom: `1px solid ${theme.border}` }}>
          {navItems.filter(s => s.show).map(s => (
            <button key={s.id} onClick={() => setActiveSection(s.id)} style={{
              padding: '5px 14px', borderRadius: '6px 6px 0 0', border: 'none', cursor: 'pointer',
              background: activeSection === s.id ? theme.bg : 'transparent',
              color: activeSection === s.id ? theme.accent : theme.muted,
              fontSize: 11, fontWeight: activeSection === s.id ? 700 : 500,
              borderBottom: `2px solid ${activeSection === s.id ? theme.accent : 'transparent'}`,
              transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 5,
              marginBottom: -1,
            }}>
              {s.icon}{s.label}
              {s.count !== null && (
                <span style={{ fontSize: 9, fontWeight: 700, background: activeSection === s.id ? theme.accent : theme.border, color: activeSection === s.id ? 'white' : theme.muted, borderRadius: 8, padding: '1px 5px' }}>{s.count}</span>
              )}
            </button>
          ))}
        </div>

        {/* Above-the-fold compact KPI bar */}
        {kpis.length > 0 && (
          <div className="vis-no-print" style={{ padding: '5px 28px', background: theme.surface, borderBottom: `1px solid ${theme.border}`, display: 'flex', gap: 20, flexShrink: 0, alignItems: 'center', overflowX: 'auto' }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', flexShrink: 0 }}>At a glance</span>
            {kpis.slice(0, 5).map(w => {
              const { num, delta } = getKpiMeta(w)
              return (
                <div key={w.id} style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                  <span style={{ fontSize: 10, color: theme.muted }}>{w.title}:</span>
                  <span style={{ fontSize: 13, fontWeight: 800, color: theme.text, fontFamily: '"SF Mono",monospace', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(num)}</span>
                  {delta && <TrendBadge trend={delta} small />}
                </div>
              )
            })}
          </div>
        )}

        {/* Auto-insight ticker */}
        {showTicker && kpis.length > 0 && (
          <div className="vis-no-print" style={{ flexShrink: 0, height: 28, background: theme.rail, overflow: 'hidden', display: 'flex', alignItems: 'center' }}>
            <div className="vis-ticker-inner" style={{ fontSize: 10, color: theme.railText }}>
              {[...kpis, ...kpis].map((w, i) => {
                const { num, delta } = getKpiMeta(w)
                const col = delta?.up === false ? '#FCA5A5' : delta?.up ? '#86EFAC' : theme.accent
                return (
                  <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: col }}>
                    <span style={{ color: theme.accent }}>●</span>
                    {w.title}: {fmtNum(num)}
                    {delta && <span>{delta.up ? '▲' : '▼'}{delta.pct.toFixed(1)}%</span>}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* Print / Export / Controls bar */}
        <div className="vis-no-print" style={{ flexShrink: 0, padding: '4px 28px', background: theme.surface, borderBottom: `1px solid ${theme.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <span style={{ fontSize: 9, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Background:</span>
            {(['none','dots','mesh','graph'] as BgPattern[]).map(p => (
              <button key={p} onClick={() => setBgPattern(p)}
                style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${bgPattern === p ? theme.accent : theme.border}`, background: bgPattern === p ? theme.accentBg : 'none', color: bgPattern === p ? theme.accent : theme.muted, cursor: 'pointer' }}>
                {p}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => setFocusMode(v => !v)}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${focusMode ? theme.accent : theme.border}`, background: focusMode ? theme.accentBg : 'none', color: focusMode ? theme.accent : theme.muted, cursor: 'pointer' }}>
              {focusMode ? '◎ Focus' : '○ Focus'}
            </button>
            <button onClick={() => setShowTicker(v => !v)}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer' }}>
              {showTicker ? '⏸ Ticker' : '▶ Ticker'}
            </button>
            <button onClick={() => { setSlideMode(true); setSlideIdx(0) }}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3 }}>
              <Play size={9} /> Present
            </button>
            <button onClick={handlePrint}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3 }}>
              <Printer size={9} /> Print
            </button>
            <button onClick={handleExportJSON}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3 }}>
              <Download size={9} /> Export
            </button>
            <button onClick={handleEmailCopy}
              style={{ padding: '1px 7px', fontSize: 9, borderRadius: 4, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer' }}>
              ✉ Email
            </button>
          </div>
        </div>

        {/* Scrollable section content */}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', background: theme.bg }}>
          {renderContent()}
        </div>
      </main>

      {/* ── Right Panel: Info or Chat ─────────────────────────────────────── */}
      {(infoWidgetId !== null || showChat) && (
        <aside style={{ width: 360, background: theme.surface, borderLeft: `1px solid ${theme.border}`, display: 'flex', flexDirection: 'column', flexShrink: 0, boxShadow: '-4px 0 20px rgba(0,0,0,0.07)', animation: 'visually-slideUp 0.22s ease both', position: 'relative', zIndex: 1 }}>

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
                    <MarkdownText text={desc} color={theme.text} fontSize={13} />
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
                    {isLatestAI
                      ? <TypewriterText text={msg.content} active speed={10} />
                      : msg.role === 'assistant'
                        ? <MarkdownText text={msg.content} color={theme.text} fontSize={13} />
                        : msg.content
                    }
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

      {/* ── Fullscreen Modal ──────────────────────────────────────────────── */}
      {fullscreenId && (() => {
        const fw = widgets.find(w => w.id === fullscreenId)
        if (!fw) return null
        const isKpi = ['kpi','kpi_card'].includes(fw.chart_type)
        const { num, delta } = isKpi ? getKpiMeta(fw) : { num: null, delta: null }
        const fwChartResult = !isKpi ? toChartResult(fw) : null
        return (
          <div onClick={() => setFullscreenId(null)}
            style={{ position: 'fixed', inset: 0, zIndex: 300, background: 'rgba(0,0,0,0.82)', backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32, animation: 'visually-fadeIn 0.18s ease both' }}>
            <div onClick={e => e.stopPropagation()}
              style={{ ...cardBase, width: '100%', maxWidth: 1100, maxHeight: '90vh', overflow: 'auto', padding: 28, position: 'relative' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <h2 style={{ fontSize: 18, fontWeight: 800, color: theme.text, margin: 0 }}>{fw.title}</h2>
                <button onClick={() => setFullscreenId(null)} title="Close (Esc)"
                  style={{ width: 32, height: 32, borderRadius: '50%', border: `1px solid ${theme.border}`, background: theme.bg, color: theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <X size={14} />
                </button>
              </div>
              {isKpi ? (
                <div style={{ textAlign: 'center', padding: '48px 0' }}>
                  <p style={{ fontSize: 72, fontWeight: 900, color: theme.text, margin: '0 0 12px', fontVariantNumeric: 'tabular-nums', fontFamily: '"SF Mono",monospace' }}>{fmtNum(num as number)}</p>
                  {delta && <TrendBadge trend={delta} />}
                </div>
              ) : (
                fwChartResult && fw.chart_data
                  ? <ChartRenderer result={fwChartResult} height={480} />
                  : <p style={{ color: theme.muted, textAlign: 'center', padding: '40px 0' }}>No data</p>
              )}
            </div>
          </div>
        )
      })()}

      {/* ── Slide / Story Mode ────────────────────────────────────────────── */}
      {slideMode && (() => {
        const slides = [...kpis, ...visualCharts]
        const total = slides.length
        if (total === 0) return null
        const si = Math.min(slideIdx, total - 1)
        const sw = slides[si]
        const isKpi = ['kpi','kpi_card'].includes(sw.chart_type)
        const { num, delta } = isKpi ? getKpiMeta(sw) : { num: null, delta: null }
        const swChartResult = !isKpi ? toChartResult(sw) : null
        const swCaption = !isKpi ? computeCaption(sw) : null
        return (
          <div style={{ position: 'fixed', inset: 0, zIndex: 350, background: `linear-gradient(-45deg,${theme.heroFrom},${theme.heroMid},${theme.heroTo})`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
            <button onClick={() => setSlideMode(false)} title="Exit (Esc)"
              style={{ position: 'absolute', top: 20, right: 24, width: 36, height: 36, borderRadius: '50%', border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(255,255,255,0.12)', color: 'white', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <X size={16} />
            </button>
            {/* Progress dots */}
            <div style={{ position: 'absolute', top: 24, left: '50%', transform: 'translateX(-50%)', display: 'flex', gap: 6, zIndex: 1 }}>
              {slides.map((_, i) => (
                <button key={i} onClick={() => setSlideIdx(i)}
                  style={{ width: i === si ? 22 : 6, height: 6, borderRadius: 3, background: i === si ? 'white' : 'rgba(255,255,255,0.35)', border: 'none', cursor: 'pointer', transition: 'all 0.25s', padding: 0 }} />
              ))}
            </div>
            {/* Slide card */}
            <div style={{ ...cardBase, width: '80%', maxWidth: 860, maxHeight: '70vh', overflow: 'auto', padding: 36, textAlign: 'center', animation: 'visually-fadeIn 0.28s ease both' }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Slide {si + 1} / {total}</p>
              <h2 style={{ fontSize: 20, fontWeight: 800, color: theme.text, marginBottom: 16 }}>{sw.title}</h2>
              {isKpi ? (
                <div>
                  <p style={{ fontSize: theme.kpiFontSize * 2, fontWeight: 900, color: theme.text, margin: '0 0 12px', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(num as number)}</p>
                  {delta && <TrendBadge trend={delta} />}
                </div>
              ) : (
                swChartResult && sw.chart_data ? <ChartRenderer result={swChartResult} height={340} /> : null
              )}
              {swCaption && <p style={{ fontSize: 12, color: theme.muted, marginTop: 12, fontStyle: 'italic' }}>{swCaption}</p>}
            </div>
            {/* Playback controls */}
            <div style={{ position: 'absolute', bottom: 28, left: '50%', transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 12 }}>
              <button onClick={() => setSlideIdx(i => Math.max(0, i - 1))} disabled={si === 0}
                style={{ width: 40, height: 40, borderRadius: '50%', border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(255,255,255,0.12)', color: 'white', cursor: si === 0 ? 'not-allowed' : 'pointer', opacity: si === 0 ? 0.4 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <ChevronLeft size={18} />
              </button>
              <button onClick={() => setSlidePlaying(v => !v)}
                style={{ width: 48, height: 48, borderRadius: '50%', border: 'none', background: 'white', color: theme.heroFrom, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 16px rgba(0,0,0,0.28)' }}>
                {slidePlaying ? <Pause size={18} /> : <Play size={18} />}
              </button>
              <button onClick={() => setSlideIdx(i => Math.min(total - 1, i + 1))} disabled={si === total - 1}
                style={{ width: 40, height: 40, borderRadius: '50%', border: '1px solid rgba(255,255,255,0.3)', background: 'rgba(255,255,255,0.12)', color: 'white', cursor: si === total - 1 ? 'not-allowed' : 'pointer', opacity: si === total - 1 ? 0.4 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        )
      })()}

      {/* ── Toast ─────────────────────────────────────────────────────────── */}
      {toast && (
        <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', background: theme.id === 'midnight' ? '#E8F4FF' : '#0a2540', color: theme.id === 'midnight' ? '#070D1A' : 'white', padding: '10px 20px', borderRadius: 30, fontSize: 13, fontWeight: 600, boxShadow: '0 4px 20px rgba(0,0,0,0.28)', animation: 'visually-slideUp 0.3s ease both', zIndex: 200, display: 'flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
          <CheckCircle2 size={14} />{toast}
        </div>
      )}
    </div>
  )
}
