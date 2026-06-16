'use client'
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  X, Send, Loader2, Plus, BarChart2, Table2, Sparkles,
  LayoutGrid, MessageSquare, ChevronRight, TrendingUp, TrendingDown,
  CheckCircle2, Copy, List, Info, ChevronDown, ChevronUp,
  Maximize2, Printer, ChevronLeft, Play, Pause, Download, Calendar,
  Share2, HelpCircle, FileArchive, Users, Upload,
  FunctionSquare, Clock, Shield, RefreshCw, ZoomIn, Pencil,
} from 'lucide-react'
import { useRouter } from 'next/navigation'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { chatApi, canvasApi, exportApi, vlyApi, scheduleApi, type WidgetCreate } from '@/lib/api'
import type { ChartResult } from '@/stores/pipelineStore'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'
import { ShareModal } from '@/components/canvas/ShareModal'
import { VlyImportModal } from '@/components/canvas/VlyImportModal'
import { DrillDownModal } from '@/components/canvas/DrillDownModal'
import { MeasuresPanel } from '@/components/canvas/MeasuresPanel'
import { ScheduleRefreshModal } from '@/components/canvas/ScheduleRefreshModal'
import { RLSModal } from '@/components/canvas/RLSModal'
import { DateRangeSlicer } from '@/components/canvas/DateRangeSlicer'

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

function Sparkline({ data, color, width = 64, height = 28, filled = false, fullWidth = false }: { data: number[]; color: string; width?: number; height?: number; filled?: boolean; fullWidth?: boolean }) {
  if (data.length < 2) return null
  const W = 220, H = height
  const mn = Math.min(...data), mx = Math.max(...data), rng = mx - mn || 1
  const coords = data.map((v, i) => ({
    x: (i / (data.length - 1)) * W,
    y: H - ((v - mn) / rng) * (H * 0.82) - H * 0.09,
  }))
  const pts = coords.map(p => `${p.x},${p.y}`).join(' ')
  const uid = `spark-${color.replace(/[^a-z0-9]/gi,'')}-${W}-${H}`

  if (filled || fullWidth) {
    const areaPath = `M${coords[0].x},${H} ` + coords.map(p => `L${p.x},${p.y}`).join(' ') + ` L${coords[coords.length-1].x},${H} Z`
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width={fullWidth ? '100%' : width} height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
        <defs>
          <linearGradient id={uid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill={`url(#${uid})`} />
        <polyline points={pts} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
    )
  }
  return (
    <svg width={width} height={height} style={{ overflow: 'visible', display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TypewriterText({ text, active, speed = 10, onComplete }: { text: string; active: boolean; speed?: number; onComplete?: () => void }) {
  const [shown, setShown] = useState(active ? '' : text)
  useEffect(() => {
    if (!active) { setShown(text); return }
    setShown('')
    let i = 0
    const id = setInterval(() => {
      i++
      setShown(text.slice(0, i))
      if (i >= text.length) { clearInterval(id); onComplete?.() }
    }, speed)
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

// ─── Filter Types ─────────────────────────────────────────────────────────────

interface FilterConfig {
  id: string
  column: string
  display_name: string
  filter_type: string
  available_values: string[]
  table: string
}

interface CanvasPage {
  id: string
  name: string
  order: number
}

interface Props {
  canvas: { id: string; name: string; project_id: string; filter_config?: FilterConfig[] }
  widgets: CanvasWidgetData[]
  pages?: CanvasPage[]
  initialPageId?: string
  projectId: string
  onClose: () => void
  onWidgetAdded?: () => void
  canEdit?: boolean
  onCanvasRename?: (newName: string) => void
  onPageRename?: (id: string, name: string) => void
  onPageDelete?: (id: string) => void
  onPageDuplicate?: (id: string) => void
  onPageReorder?: (newPages: CanvasPage[]) => void
}

function computeLinearForecast(values: number[], steps: number): number[] {
  const n = values.length
  if (n < 3 || steps < 1) return []
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0
  values.forEach((v, i) => { sumX += i; sumY += v; sumXY += i * v; sumX2 += i * i })
  const denom = n * sumX2 - sumX * sumX
  if (denom === 0) return Array(steps).fill(values[n - 1])
  const slope = (n * sumXY - sumX * sumY) / denom
  const intercept = (sumY - slope * sumX) / n
  return Array.from({ length: steps }, (_, i) => Math.max(0, Math.round(intercept + slope * (n + i))))
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

function detectDateCol(cols: string[], rows: Record<string, unknown>[]): string | null {
  if (!rows.length) return null
  return cols.find(c => {
    const v = String(rows[0]?.[c] ?? '')
    return /^\d{4}-\d{2}/.test(v) || /^\d{2}[\/\-]\d{2}[\/\-]\d{4}/.test(v)
  }) ?? null
}

function applyDateFilter(
  rows: Record<string, unknown>[],
  col: string,
  from: string,
  to: string,
): { rows: Record<string, unknown>[]; indices: number[] } {
  const fromMs = from ? new Date(from).getTime() : -Infinity
  const toMs   = to   ? new Date(to + 'T23:59:59').getTime() : Infinity
  const indices: number[] = []
  const filtered = rows.filter((r, i) => {
    const ms = new Date(String(r[col] ?? '')).getTime()
    const keep = isNaN(ms) || (ms >= fromMs && ms <= toMs)
    if (keep) indices.push(i)
    return keep
  })
  return { rows: filtered, indices }
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
  const rawValues = (w.chart_data?.values as (number | null)[]) ?? []
  const rows = (w.chart_data?.rows as Record<string, unknown>[]) ?? []
  const cols = (w.chart_data?.columns as string[]) ?? []

  // When values array is empty (e.g. single-column KPI SQL), extract from rows.
  // Single column → that column is the value; multi-column → use second column.
  let effective = rawValues.filter((v): v is number => v !== null)
  if (effective.length === 0 && rows.length > 0 && cols.length > 0) {
    const valueCol = cols.length === 1 ? cols[0] : cols[1]
    effective = rows.map(r => {
      const v = r[valueCol]
      if (typeof v === 'number') return v
      const n = parseFloat(String(v ?? ''))
      return isNaN(n) ? null : n
    }).filter((v): v is number => v !== null)
  }

  const num  = effective[effective.length - 1] ?? 0
  const prev = effective.length >= 2 ? effective[effective.length - 2] : null
  const delta = prev !== null && prev !== 0
    ? { pct: Math.abs(((num - prev) / prev) * 100), up: num >= prev }
    : null
  return { num, spark: effective.slice(-7), delta }
}

function fmtNum(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function getChartHeight(chartType: string, layoutMode: LayoutMode): number {
  if (layoutMode === 'list') return 300
  const ct = chartType.toLowerCase()
  if (['treemap'].includes(ct)) return 320
  if (['pie', 'donut'].includes(ct)) return 270
  if (['scatter'].includes(ct)) return 260
  return 260
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

// Maps canvas THEMES to the --dash-* CSS variables ChartRenderer uses for tables.
// Without this, ChartRenderer falls back to light-theme defaults even inside a dark canvas.
function themeTableVars(t: Theme): React.CSSProperties {
  const dark = ['midnight', 'digitalnative'].includes(t.id)
  return {
    '--dash-card-bg':     t.surface,
    '--dash-row-alt':     dark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.03)',
    '--dash-row-text':    t.text,
    '--dash-th-bg':       dark ? 'rgba(255,255,255,0.10)' : 'rgba(0,0,0,0.05)',
    '--dash-table-border': t.border,
    '--dash-text-muted':  t.muted,
  } as React.CSSProperties
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

export function VisuallReport({ canvas, widgets, pages = [], initialPageId = '', projectId, onClose, onWidgetAdded, canEdit = false, onCanvasRename, onPageRename, onPageDelete, onPageDuplicate, onPageReorder }: Props) {
  const router = useRouter()
  const [themeIdx, setThemeIdx] = useState(() => {
    try { return parseInt(localStorage.getItem(`visually-theme-${canvas.id}`) ?? '0', 10) || 0 } catch { return 0 }
  })
  const theme = THEMES[Math.min(themeIdx, THEMES.length - 1)]

  const [navCollapsed, setNavCollapsed] = useState(false)
  const [comparisonMode, setComparisonMode] = useState(false)
  const [comparedCharts, setComparedCharts] = useState<Set<string>>(new Set())
  const [forecastCharts, setForecastCharts] = useState<Set<string>>(new Set())
  const [kpiDensity, setKpiDensity]         = useState<'compact' | 'normal' | 'spacious'>('normal')
  const [newspaperInsights, setNewspaperInsights] = useState<Record<string, string>>({})
  const [newspaperInsightLoading, setNewspaperInsightLoading] = useState<Record<string, boolean>>({})
  const [tableVizSuggestions, setTableVizSuggestions] = useState<Record<string, string>>({})
  const [tableVizSugLoading, setTableVizSugLoading] = useState<Record<string, boolean>>({})
  const [activeSection, setActiveSection] = useState<SectionId>('overview')
  const [layoutMode, setLayoutMode]       = useState<LayoutMode>('grid')
  const [bgPattern, setBgPattern]         = useState<BgPattern>('dots')
  // Chart-level interactivity
  const [chartTypeOverrides, setChartTypeOverrides] = useState<Record<string,string>>({})
  const [showRawData, setShowRawData]     = useState<Set<string>>(new Set())
  const [splitViewId, setSplitViewId]     = useState<string|null>(null)
  const [chartDateDraft,    setChartDateDraft]    = useState<Record<string, { from: string; to: string }>>({})
  const [chartDateFilters,  setChartDateFilters]  = useState<Record<string, { from: string; to: string }>>({})
  const [chartDateOpen,     setChartDateOpen]     = useState<Set<string>>(new Set())
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
  // What-if
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
  // Reset textarea height when input is cleared (e.g. after sending)
  useEffect(() => {
    if (!input && chatInputRef.current) chatInputRef.current.style.height = 'auto'
  }, [input])
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
  // Background pattern picker (nav rail)
  const [showBgPicker, setShowBgPicker] = useState(false)
  // Page management
  const [pageCtxMenu, setPageCtxMenu] = useState<{ id: string; x: number; y: number } | null>(null)
  const [pageEditId, setPageEditId] = useState<string | null>(null)
  const [pageEditDraft, setPageEditDraft] = useState('')
  const pageEditRef = useRef<HTMLInputElement>(null)
  const pageCtxRef  = useRef<HTMLDivElement>(null)
  // Filter panel
  const [filterPanelOpen, setFilterPanelOpen] = useState(false)
  const [activeFilters, setActiveFilters] = useState<Record<string, string[]>>({})
  const [filteredWidgets, setFilteredWidgets] = useState<CanvasWidgetData[] | null>(null)
  const [reportPageId, setReportPageId] = useState<string>(() => initialPageId || pages[0]?.id || '')
  const [filterLoading, setFilterLoading] = useState(false)
  const filterConfigs: FilterConfig[] = canvas.filter_config ?? []
  // Global date range (date_range filter_config entries)
  const dateFCs = filterConfigs.filter(f => f.filter_type === 'date_range')
  const catFCs  = filterConfigs.filter(f => f.filter_type !== 'date_range')
  const [globalDateDraft, setGlobalDateDraft] = useState<Record<string, { start: string; end: string }>>({})
  const [appliedDateRange, setAppliedDateRange] = useState<Record<string, { start: string; end: string }>>({})
  const [dateRangeApplying, setDateRangeApplying] = useState(false)
  const [crossFilter, setCrossFilter] = useState<{ column: string; value: string } | null>(null)
  const [scrollProgress, setScrollProgress] = useState(0)
  const [showShortcutSheet, setShowShortcutSheet] = useState(false)
  const [shareModal, setShareModal] = useState<{ open: boolean; loading: boolean; url: string | null; error: string | null }>({ open: false, loading: false, url: null, error: null })
  const [showNewShareModal, setShowNewShareModal] = useState(false)
  const [showVlyImport, setShowVlyImport] = useState(false)
  // Tier 5 modals
  const [showMeasures, setShowMeasures] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)
  const [showRLS, setShowRLS] = useState(false)
  const [drilldown, setDrilldown] = useState<{ widgetId: string; widgetTitle: string; column: string; value: string } | null>(null)
  const [crossFilteredWidgets, setCrossFilteredWidgets] = useState<CanvasWidgetData[] | null>(null)
  const [crossFilterLoading, setCrossFilterLoading] = useState(false)
  const endRef         = useRef<HTMLDivElement>(null)
  const toastRef       = useRef<ReturnType<typeof setTimeout> | null>(null)
  const chatInputRef   = useRef<HTMLTextAreaElement>(null)
  const scrollAreaRef  = useRef<HTMLDivElement>(null)

  const connectionId   = widgets.find(w => w.connection_id)?.connection_id

  // Filter widgets to the active page (with legacy fallback for widgets without page_id)
  const defaultPageId = pages[0]?.id ?? ''
  const filterByPage = (list: CanvasWidgetData[]) => {
    if (pages.length === 0) return list
    return list.filter(w => {
      const wPageId = (w.config?.page_id as string) || ''
      return wPageId ? wPageId === reportPageId : reportPageId === defaultPageId
    })
  }
  const pageWidgets    = filterByPage(widgets)
  // Priority: explicit filter > cross-filter server results > plain page widgets
  const displayWidgets = filteredWidgets
    ? filterByPage(filteredWidgets)
    : crossFilteredWidgets
      ? filterByPage(crossFilteredWidgets)
      : pageWidgets

  const KPI_TYPES   = new Set(['kpi', 'kpi_card'])
  const TABLE_TYPES = new Set(['table', 'data_table', 'pivot_table'])

  const kpis         = displayWidgets.filter(w => KPI_TYPES.has(w.chart_type))
  const tables       = displayWidgets.filter(w => TABLE_TYPES.has(w.chart_type))
  // Any widget that is not a KPI and not a table is a visual chart — catches all current
  // and future chart types without needing an exhaustive allow-list.
  const visualCharts = displayWidgets.filter(w => !KPI_TYPES.has(w.chart_type) && !TABLE_TYPES.has(w.chart_type))
  const recommended    = useMemo(() => getRecommendedQuestions(widgets), [widgets])
  const hasActiveFilters = Object.values(activeFilters).some(v => v.length > 0) || Object.keys(appliedDateRange).length > 0

  // Anomaly callout data — across all displayed widgets
  const globalAnomalies = useMemo(() => {
    const results: Array<{ widgetId: string; title: string; label: string; value: number; sigma: number }> = []
    displayWidgets.forEach(w => {
      const rawVals = (w.chart_data?.values as (number|null)[]) ?? []
      const rawRows = (w.chart_data?.rows as Record<string, unknown>[]) ?? []
      const rawCols = (w.chart_data?.columns as string[]) ?? []
      const numVals: (number|null)[] = rawVals.length > 0 ? rawVals
        : rawRows.map(r => { const v = r[rawCols[1] ?? '']; return typeof v === 'number' ? v : parseFloat(String(v ?? 'nan')) || null })
      const labArr: string[] = (w.chart_data?.labels as string[]) ?? rawRows.map(r => String(r[rawCols[0] ?? ''] ?? ''))
      const idxs = detectAnomalyIndices(numVals)
      if (idxs.length > 0) {
        const nums = numVals.filter((v): v is number => v !== null)
        const mean = nums.reduce((s, v) => s + v, 0) / nums.length
        const std  = Math.sqrt(nums.reduce((s, v) => s + Math.pow(v - mean, 2), 0) / nums.length)
        idxs.slice(0, 2).forEach(idx => {
          const val = numVals[idx]
          if (val !== null && std > 0) {
            results.push({ widgetId: w.id, title: w.title, label: labArr[idx] ?? `Point ${idx + 1}`, value: val, sigma: Math.abs(val - mean) / std })
          }
        })
      }
    })
    return results.sort((a, b) => b.sigma - a.sigma).slice(0, 5)
  }, [displayWidgets])

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
      @keyframes vis-page-enter { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
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
      .vis-hide-scroll { scrollbar-width:none; -ms-overflow-style:none; }
      .vis-hide-scroll::-webkit-scrollbar { display:none; }
      .vis-nav { transition: width 0.22s cubic-bezier(.4,0,.2,1), opacity 0.2s ease; }
      .vis-scroll-progress { transition: width 0.12s linear; }
      @media print {
        .vis-no-print { display:none!important; }
        nav { display:none!important; }
        aside { display:none!important; }
        body { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
        .vis-card { break-inside:avoid; box-shadow:none!important; border:1px solid #ccc!important; }
        svg { overflow:visible!important; }
        * { animation:none!important; transition:none!important; }
      }
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

  // ── Close bg picker on outside click ─────────────────────────────────────
  useEffect(() => {
    if (!showBgPicker) return
    const h = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('[data-bg-picker]')) setShowBgPicker(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showBgPicker])

  // ── Close page context menu on outside click ──────────────────────────────
  useEffect(() => {
    if (!pageCtxMenu) return
    const h = (e: MouseEvent) => {
      if (pageCtxRef.current && !pageCtxRef.current.contains(e.target as Node)) setPageCtxMenu(null)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [pageCtxMenu])

  // ── Focus page rename input ───────────────────────────────────────────────
  useEffect(() => {
    if (pageEditId) pageEditRef.current?.focus()
  }, [pageEditId])

  // ── Keyboard shortcuts ───────────────────────────────────────────────────
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Escape') {
        if (showShortcutSheet) { setShowShortcutSheet(false); return }
        if (showNewShareModal) { setShowNewShareModal(false); return }
        if (showVlyImport) { setShowVlyImport(false); return }
        if (showMeasures) { setShowMeasures(false); return }
        if (showSchedule) { setShowSchedule(false); return }
        if (showRLS) { setShowRLS(false); return }
        if (drilldown) { setDrilldown(null); return }
        if (shareModal.open) { setShareModal(s => ({ ...s, open: false })); return }
        if (showThemePicker) { setShowThemePicker(false); return }
        if (showBgPicker) { setShowBgPicker(false); return }
        if (pageCtxMenu) { setPageCtxMenu(null); return }
        if (pageEditId) { setPageEditId(null); return }
        if (slideMode) { setSlideMode(false); setSlidePlaying(false); return }
        if (fullscreenId) { setFullscreenId(null); return }
        if (crossFilter) { setCrossFilter(null); return }
        if (infoWidgetId) { setInfoWidgetId(null); return }
        onClose()
      }
      if (e.key === '?') { setShowShortcutSheet(v => !v); return }
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
  }, [onClose, visualCharts, tables.length, infoWidgetId, slideMode, fullscreenId, slideDeck.length, showShortcutSheet, shareModal.open, crossFilter])

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

  // ── Scroll progress ───────────────────────────────────────────────────────
  useEffect(() => {
    const el = scrollAreaRef.current
    if (!el) return
    const onScroll = () => {
      const max = el.scrollHeight - el.clientHeight
      setScrollProgress(max > 0 ? el.scrollTop / max : 0)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // ── Cross-filter toggle + server-side requery ─────────────────────────────
  const handleCrossFilter = useCallback((column: string, value: unknown) => {
    const v = String(value)
    setCrossFilter(prev => prev?.column === column && prev?.value === v ? null : { column, value: v })
  }, [])

  // Requery all other widgets server-side when crossFilter changes
  useEffect(() => {
    if (!crossFilter) {
      setCrossFilteredWidgets(null)
      return
    }
    let cancelled = false
    setCrossFilterLoading(true)
    canvasApi.requery(canvas.id, { [crossFilter.column]: [crossFilter.value] })
      .then(r => {
        if (cancelled) return
        const updatedMap: Record<string, { rows: Record<string, unknown>[]; columns: string[] }> = {}
        r.data.widgets?.forEach((w: { widget_id: string; chart_data: { rows: Record<string, unknown>[]; columns: string[] } }) => {
          updatedMap[w.widget_id] = w.chart_data
        })
        setCrossFilteredWidgets(
          widgets.map(w => updatedMap[w.id]
            ? { ...w, chart_data: { ...w.chart_data, rows: updatedMap[w.id].rows, columns: updatedMap[w.id].columns } }
            : w
          )
        )
      })
      .catch(() => { if (!cancelled) showToast('Cross-filter query failed') })
      .finally(() => { if (!cancelled) setCrossFilterLoading(false) })
    return () => { cancelled = true }
  }, [crossFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Share handler — generates an HTML export as the shareable link ────────
  const handleShare = useCallback(async () => {
    setShareModal({ open: true, loading: true, url: null, error: null })
    try {
      const triggerResp = await exportApi.trigger({
        dashboard_id: canvas.id,
        project_id: projectId,
        export_type: 'html',
        theme: theme.id,
        include_chat: false,
        token_expiry_days: 7,
      })
      const jobId = triggerResp.data?.job_id
      if (!jobId) throw new Error('No job ID returned')
      for (let attempt = 0; attempt < 20; attempt++) {
        await new Promise(r => setTimeout(r, 1500))
        const statusResp = await exportApi.getJob(jobId)
        if (statusResp.data?.status === 'done') {
          setShareModal({ open: true, loading: false, url: exportApi.downloadUrl(jobId), error: null })
          return
        }
        if (statusResp.data?.status === 'failed') throw new Error('Export job failed')
      }
      throw new Error('Export timed out')
    } catch (err: unknown) {
      setShareModal({ open: true, loading: false, url: null, error: err instanceof Error ? err.message : 'Share failed' })
    }
  }, [canvas.id, projectId, theme.id])

  // ── Filter requery — re-execute all widget SQL when active filters change ────
  useEffect(() => {
    const catActive = Object.values(activeFilters).some(v => v.length > 0)
    const dateActive = Object.keys(appliedDateRange).length > 0
    if (!catActive && !dateActive) {
      setFilteredWidgets(null)
      return
    }
    const combined: Record<string, string[] | { start: string; end: string }> = {
      ...activeFilters,
      ...appliedDateRange,
    }
    let cancelled = false
    setFilterLoading(true)
    canvasApi.requery(canvas.id, combined)
      .then(r => {
        if (cancelled) return
        const updatedMap: Record<string, { rows: Record<string, unknown>[]; columns: string[] }> = {}
        r.data.widgets?.forEach((w: { widget_id: string; chart_data: { rows: Record<string, unknown>[]; columns: string[] } }) => {
          updatedMap[w.widget_id] = w.chart_data
        })
        setFilteredWidgets(
          widgets.map(w => updatedMap[w.id]
            ? { ...w, chart_data: { ...w.chart_data, rows: updatedMap[w.id].rows, columns: updatedMap[w.id].columns } }
            : w
          )
        )
      })
      .catch(() => { if (!cancelled) showToast('Filter query failed') })
      .finally(() => { if (!cancelled) setFilterLoading(false) })
    return () => { cancelled = true }
  }, [activeFilters, appliedDateRange]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleFilter = useCallback((column: string, value: string) => {
    setActiveFilters(prev => {
      const current = prev[column] ?? []
      const has = current.includes(value)
      return { ...prev, [column]: has ? current.filter(v => v !== value) : [...current, value] }
    })
  }, [])

  const clearAllFilters = useCallback(() => {
    setActiveFilters({})
    setAppliedDateRange({})
    setGlobalDateDraft({})
    setFilteredWidgets(null)
  }, [])

  // ── Load AI insights for all charts & tables whenever widgets arrive ─────
  useEffect(() => {
    const toLoad = [...visualCharts, ...tables].filter(w => !newspaperInsights[w.id] && !newspaperInsightLoading[w.id])
    if (toLoad.length === 0) return
    toLoad.forEach(w => {
      setNewspaperInsightLoading(prev => ({ ...prev, [w.id]: true }))
      const rows = (w.chart_data?.rows as Record<string,unknown>[] | undefined) ?? []
      const cols = (w.chart_data?.columns as string[] | undefined) ?? []
      const sample = rows.slice(0, 3).map(r => cols.map(c => `${c}:${r[c]}`).join(', ')).join(' | ')
      chatApi.send({
        session_id: `insight-${w.id}`,
        message: `Chart title: "${w.title}" (type: ${w.chart_type}). Columns: ${cols.join(', ')}. Sample: ${sample}. Write exactly 2 short sentences: (1) what this data shows specifically, (2) the single most important business insight. Plain prose, no headers, bullets, or markdown.`,
        project_id: projectId,
        connection_id: connectionId,
      }).then(r => setNewspaperInsights(prev => ({ ...prev, [w.id]: r.data?.text?.trim() ?? '' })))
        .catch(() => {})
        .finally(() => setNewspaperInsightLoading(prev => ({ ...prev, [w.id]: false })))
    })
  }, [visualCharts.length, tables.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load AI viz suggestions for tables ───────────────────────────────────
  useEffect(() => {
    const toLoad = tables.filter(w => !tableVizSuggestions[w.id] && !tableVizSugLoading[w.id])
    if (toLoad.length === 0) return
    toLoad.forEach(w => {
      setTableVizSugLoading(prev => ({ ...prev, [w.id]: true }))
      const cols = (w.chart_data?.columns as string[] | undefined) ?? []
      const rows = (w.chart_data?.rows as Record<string,unknown>[] | undefined) ?? []
      const sample = rows.slice(0, 3).map(r => cols.map(c => `${c}:${r[c]}`).join(', ')).join(' | ')
      chatApi.send({
        session_id: `viz-suggest-${w.id}`,
        message: `Table: "${w.title}". Columns: ${cols.join(', ')}. Sample rows: ${sample}. Reply with ONLY one of: bar, line, pie, bar_horizontal, area — whichever best visualizes this data. No other text.`,
        project_id: projectId,
        connection_id: connectionId,
      }).then(r => {
        const raw = (r.data?.text?.trim() ?? '').toLowerCase()
        const match = (['bar_horizontal','bar','line','pie','area'] as const).find(v => raw.includes(v))
        if (match) setTableVizSuggestions(prev => ({ ...prev, [w.id]: match }))
      }).catch(() => {})
        .finally(() => setTableVizSugLoading(prev => ({ ...prev, [w.id]: false })))
    })
  }, [tables.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Build a comparison chart result (current vs prior half-period) ───────
  const makeComparisonResult = useCallback((w: CanvasWidgetData): ChartResult => {
    const wRows  = (w.chart_data?.rows  as Record<string, unknown>[]) ?? []
    const wCols  = (w.chart_data?.columns as string[]) ?? []
    if (wRows.length < 4 || wCols.length < 2) return toChartResult(w)
    const half = Math.floor(wRows.length / 2)
    const prior   = wRows.slice(0, half)
    const current = wRows.slice(half)
    const xK = wCols[0], yK = wCols[1]
    const maxLen = Math.max(prior.length, current.length)
    const compRows = Array.from({ length: maxLen }, (_, i) => ({
      [xK]: current[i]?.[xK] ?? prior[i]?.[xK] ?? `P${i + 1}`,
      'Current': typeof current[i]?.[yK] === 'number' ? current[i][yK] : parseFloat(String(current[i]?.[yK] ?? 'nan')) || null,
      'Prior':   typeof prior[i]?.[yK]   === 'number' ? prior[i][yK]   : parseFloat(String(prior[i]?.[yK]   ?? 'nan')) || null,
    }))
    const baseType = w.chart_type.toLowerCase()
    const compType = ['line', 'area'].includes(baseType) ? 'grouped_line' : 'grouped_bar'
    return {
      ...toChartResult(w),
      chart_type: compType,
      chart_data: { rows: compRows, columns: [xK, 'Current', 'Prior'], labels: [], values: [] },
    }
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
      onWidgetAdded?.()
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

  // Chart cards use tighter padding so the chart area gets more vertical space
  const chartCardBase: React.CSSProperties = { ...cardBase, padding: '12px 14px' }

  const ribbonBtn = (active: boolean): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '5px 10px', fontSize: 10, fontWeight: active ? 700 : 500,
    border: `1px solid ${active ? theme.accent : 'transparent'}`,
    borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap' as const,
    background: active ? theme.accentBg : 'transparent',
    color: active ? theme.accent : theme.muted,
    transition: 'all 0.12s', flexShrink: 0,
  })

  // Compact button style for the tools section inside the sidebar
  const sideBtn = (active: boolean): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 3,
    padding: '3px 7px', fontSize: 10, fontWeight: active ? 600 : 400,
    border: `1px solid ${active ? theme.accent : theme.border}`,
    borderRadius: 5, cursor: 'pointer', whiteSpace: 'nowrap' as const,
    background: active ? theme.accentBg : 'transparent',
    color: active ? theme.accent : theme.muted,
    transition: 'all 0.1s',
  })

  // ── KPI card renderer ────────────────────────────────────────────────────
  const renderKpi = (w: CanvasWidgetData, idx: number) => {
    const { num, spark, delta } = getKpiMeta(w)
    const hasValues = spark.length > 0
    const trendColor = delta ? (delta.up ? '#16A34A' : '#DC2626') : theme.accent
    const isInfoOpen = infoWidgetId === w.id
    const isStarred  = bookmarked.has(w.id)
    const whatIf     = whatIfValues[w.id] ?? 0
    const displayNum = Math.round(num * (1 + whatIf / 100))

    // Smart compact formatter: 1,104 → 1.1K | 68,800,000 → 68.8M
    const fmtCompact = (n: number): string => {
      const sign = n < 0 ? '-' : ''
      const abs  = Math.abs(n)
      if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`
      if (abs >= 10_000)    return `${sign}${(abs / 1_000).toFixed(0)}K`
      if (abs >= 1_000)     return `${sign}${(abs / 1_000).toFixed(1)}K`
      return n.toLocaleString()
    }

    const accentBg = `${trendColor}09`

    return (
      <div key={w.id} id={`vr-kpi-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-kpi-${w.id}`) }}
        style={{
          ...cardBase,
          animationDelay: `${idx * 60}ms`,
          position: 'relative',
          overflow: 'hidden',
          padding: spark.length >= 2 ? '11px 13px 0' : '11px 13px 11px',
          display: 'flex',
          flexDirection: 'column',
          borderLeft: `3.5px solid ${trendColor}`,
          background: `linear-gradient(140deg, ${accentBg} 0%, ${theme.surface} 60%)`,
          boxShadow: `0 1px 3px rgba(0,0,0,0.06), inset 1px 0 0 ${trendColor}22`,
        }}
      >
        {/* ── Header row ── */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 4, marginBottom: 7 }}>
          <p style={{
            fontSize: 9, fontWeight: 600, color: theme.muted,
            textTransform: 'uppercase', letterSpacing: '0.09em',
            margin: 0, lineHeight: 1.35, flex: 1,
            overflow: 'hidden', display: '-webkit-box',
            WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          }}>{w.title}</p>
          <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
            <button onClick={e => { e.stopPropagation(); toggleBookmark(w.id) }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: isStarred ? '#F59E0B' : theme.muted, opacity: isStarred ? 1 : 0.28, padding: 0, fontSize: 11, lineHeight: 1 }}>
              {isStarred ? '★' : '☆'}
            </button>
            <button onClick={e => { e.stopPropagation(); openInfo(w) }} title="Explain this metric"
              className={`vis-info-btn${isInfoOpen ? ' active' : ''}`}
              style={{ width: 16, height: 16, borderRadius: '50%', padding: 0, background: isInfoOpen ? theme.accentBg : 'transparent', border: `1px solid ${isInfoOpen ? theme.accent : 'transparent'}`, color: isInfoOpen ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: isInfoOpen ? 1 : 0.35 }}>
              <Info size={8} />
            </button>
          </div>
        </div>

        {/* ── Main content ── */}
        {hasValues ? (
          <>
            {/* Big number + inline delta */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: delta ? 5 : 0 }}>
              <span style={{
                fontSize: Math.min(theme.kpiFontSize, 26), fontWeight: 800,
                color: theme.text, lineHeight: 1,
                fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.03em',
                fontFamily: theme.id === 'digitalnative' ? theme.fontFamily : '"SF Mono","JetBrains Mono",monospace',
              }}>
                {fmtCompact(displayNum)}
              </span>
              {delta && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 2,
                  padding: '2px 6px', borderRadius: 5,
                  background: delta.up ? 'rgba(22,163,74,0.12)' : 'rgba(220,38,38,0.12)',
                  fontSize: 9, fontWeight: 700, color: trendColor, flexShrink: 0,
                }}>
                  {delta.up
                    ? <TrendingUp size={9} style={{ flexShrink: 0 }} />
                    : <TrendingDown size={9} style={{ flexShrink: 0 }} />}
                  {delta.pct.toFixed(1)}%
                </span>
              )}
            </div>

            {/* "vs prior" label */}
            {delta && (
              <p style={{ fontSize: 8, color: theme.muted, margin: '0 0 6px', opacity: 0.75 }}>
                vs prior period
              </p>
            )}

            {/* Full-width filled sparkline — bleeds to card edges */}
            {spark.length >= 2 && (
              <div style={{ marginTop: 'auto', marginLeft: -13, marginRight: -13, lineHeight: 0 }}>
                <Sparkline data={spark} color={trendColor} height={34} fullWidth filled />
              </div>
            )}
          </>
        ) : (
          <ChartRenderer result={toChartResult(w)} height={64} />
        )}
      </div>
    )
  }

  // ── Chart card renderer ──────────────────────────────────────────────────
  const renderChart = (w: CanvasWidgetData, idx: number, h = 220) => {
    // Slicer widget — renders as a DateRangeSlicer that broadcasts to all charts
    if (w.chart_type === 'slicer' || (w.config?.widget_type as string) === 'slicer') {
      const slicerCol = (w.config?.slicer_column as string) || w.title
      const currentRange = appliedDateRange[slicerCol] ?? null
      return (
        <div key={w.id} style={{ gridColumn: 'span 2' }}>
          <DateRangeSlicer
            title={w.title}
            columnName={slicerCol}
            value={currentRange}
            onChange={(col, range) => {
              if (range) {
                setAppliedDateRange(prev => ({ ...prev, [col]: range }))
              } else {
                setAppliedDateRange(prev => { const r = { ...prev }; delete r[col]; return r })
              }
            }}
            theme={{
              surface: theme.surface, border: theme.border, text: theme.text,
              muted: theme.muted, accent: theme.accent, accentBg: theme.accentBg,
              bg: theme.bg, cardRadius: theme.cardRadius,
            }}
          />
        </div>
      )
    }

    const isInfoOpen = infoWidgetId === w.id
    const isStarred = bookmarked.has(w.id)
    const activeType = chartTypeOverrides[w.id] ?? w.chart_type
    const trend = computeTrendBadge(w)
    const caption = computeCaption(w)
    const keyDriver = computeKeyDriver(w)
    const altType = suggestAlternativeChart(w)
    const wRows = (w.chart_data?.rows as Record<string, unknown>[]) ?? []
    const wCols = (w.chart_data?.columns as string[]) ?? (wRows[0] ? Object.keys(wRows[0]) : [])
    const dateCol       = detectDateCol(wCols, wRows)
    const dateDraft     = chartDateDraft[w.id]
    const dateFilter    = chartDateFilters[w.id]
    const isDateOpen    = chartDateOpen.has(w.id)
    const hasDateFilter = !!(dateFilter?.from || dateFilter?.to)
    const hasDraft      = !!(dateDraft?.from || dateDraft?.to)
    const draftChanged  = dateDraft?.from !== (dateFilter?.from ?? '') || dateDraft?.to !== (dateFilter?.to ?? '')
    const showApply     = hasDraft && draftChanged

    const wLabels = (w.chart_data?.labels  as string[]         | undefined) ?? []
    const wValues = (w.chart_data?.values  as (number|null)[]  | undefined) ?? []

    const filterResult  = dateCol && hasDateFilter
      ? applyDateFilter(wRows, dateCol, dateFilter?.from ?? '', dateFilter?.to ?? '')
      : null
    const filteredRows   = filterResult?.rows   ?? wRows
    const keepSet        = filterResult ? new Set(filterResult.indices) : null
    const filteredLabels = keepSet ? wLabels.filter((_, i) => keepSet.has(i)) : wLabels
    const filteredValues = keepSet ? wValues.filter((_, i) => keepSet.has(i)) : wValues

    const baseChartData = toChartResult(w).chart_data
    const filteredChartData = {
      ...baseChartData,
      rows:   filteredRows,
      labels: filteredLabels,
      values: filteredValues,
    }

    // Apply active cross-filter to this chart's data (must be computed before baseResult)
    const xfRows = crossFilter
      ? (() => {
          const matched = filteredRows.filter(r => String(r[crossFilter.column] ?? '') === crossFilter.value)
          return matched.length > 0 ? matched : filteredRows
        })()
      : filteredRows
    const xfLabels = crossFilter && xfRows !== filteredRows
      ? filteredLabels.filter((_, i) => filteredRows.indexOf(xfRows[i]) !== -1)
      : filteredLabels
    const xfValues = crossFilter && xfRows !== filteredRows
      ? filteredValues.filter((_, i) => filteredRows.indexOf(xfRows[i]) !== -1)
      : filteredValues
    const activeChartData = crossFilter
      ? { ...filteredChartData, rows: xfRows, labels: xfLabels, values: xfValues }
      : filteredChartData

    const isCompared = comparedCharts.has(w.id)
    const isForecasting = forecastCharts.has(w.id)
    const isLineLike = ['line', 'area', 'bar', 'bar_vertical', 'stacked_area'].includes(activeType)

    // Forecast: extend data by ~30% using linear regression
    const forecastRows = (() => {
      if (!isForecasting || !isLineLike || filteredRows.length < 4 || wCols.length < 2) return filteredRows
      const yCol = wCols[1]
      const nums = filteredRows.map(r => typeof r[yCol] === 'number' ? r[yCol] as number : parseFloat(String(r[yCol] ?? 'nan')) || 0)
      const steps = Math.max(2, Math.ceil(filteredRows.length * 0.3))
      const preds = computeLinearForecast(nums, steps)
      const lastX = filteredRows[filteredRows.length - 1]?.[wCols[0]]
      const forecasted = preds.map((v, i) => ({ [wCols[0]]: `→ ${String(lastX ?? '')}+${i + 1}`, [yCol]: v }))
      return [...filteredRows, ...forecasted]
    })()
    const forecastChartData = isForecasting ? { ...filteredChartData, rows: forecastRows } : filteredChartData

    const baseResult = isCompared && filteredRows.length >= 4
      ? makeComparisonResult({ ...w, chart_data: { ...w.chart_data, rows: filteredRows } as typeof w.chart_data })
      : { ...toChartResult(w), chart_type: activeType, chart_data: isForecasting ? forecastChartData : activeChartData }
    const chartResult = baseResult
    const forecastStepCount = isForecasting && isLineLike && filteredRows.length >= 4 ? Math.max(2, Math.ceil(filteredRows.length * 0.3)) : 0
    const anomalyIdxs = detectAnomalyIndices(filteredValues.length ? filteredValues : (w.chart_data?.values as (number|null)[]) ?? [])

    const rawRows = filteredRows
    const rawCols = wCols

    return (
      <div key={w.id} id={`vr-chart-${w.id}`} className="vis-card"
        onContextMenu={e => { e.preventDefault(); copyAsPng(`vr-chart-${w.id}`) }}
        style={{ ...chartCardBase, animationDelay: `${idx * 60}ms` }}
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
            {/* Forecast toggle — line-like charts only */}
            {isLineLike && (
              <button
                onClick={e => { e.stopPropagation(); setForecastCharts(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n }) }}
                title="Toggle forecast"
                style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: isForecasting ? '#7C3AED18' : 'transparent', border: `1px solid ${isForecasting ? '#7C3AED' : theme.border}`, color: isForecasting ? '#7C3AED' : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11 }}>
                ∿
              </button>
            )}
            {/* Comparison toggle */}
            <button
              onClick={e => { e.stopPropagation(); setComparedCharts(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n }) }}
              title="Compare current vs prior"
              style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: isCompared ? theme.accentBg : 'transparent', border: `1px solid ${isCompared ? theme.accent : theme.border}`, color: isCompared ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10 }}>
              ⇆
            </button>
            {/* Date filter toggle — only shown when a date column exists */}
            {dateCol && (
              <button onClick={e => { e.stopPropagation(); setChartDateOpen(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n }) }}
                title="Date filter"
                style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: (isDateOpen || hasDateFilter) ? theme.accentBg : 'transparent', border: `1px solid ${(isDateOpen || hasDateFilter) ? theme.accent : theme.border}`, color: (isDateOpen || hasDateFilter) ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
                <Calendar size={11} />
                {hasDateFilter && <span style={{ position: 'absolute', top: -2, right: -2, width: 6, height: 6, borderRadius: '50%', background: theme.accent }} />}
              </button>
            )}
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

        {/* Date range filter strip */}
        {isDateOpen && dateCol && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', marginBottom: 8, background: `${theme.accent}08`, border: `1px solid ${theme.accent}22`, borderRadius: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 3 }}>
              <Calendar size={9} />{dateCol}
            </span>
            <input type="date" value={dateDraft?.from ?? ''}
              onChange={e => setChartDateDraft(prev => ({ ...prev, [w.id]: { from: e.target.value, to: prev[w.id]?.to ?? '' } }))}
              style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
            <span style={{ fontSize: 10, color: theme.muted }}>→</span>
            <input type="date" value={dateDraft?.to ?? ''}
              onChange={e => setChartDateDraft(prev => ({ ...prev, [w.id]: { from: prev[w.id]?.from ?? '', to: e.target.value } }))}
              style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
            {showApply && (
              <button
                onClick={() => setChartDateFilters(prev => ({ ...prev, [w.id]: { from: dateDraft?.from ?? '', to: dateDraft?.to ?? '' } }))}
                style={{ fontSize: 10, padding: '3px 12px', borderRadius: 6, border: 'none', background: theme.accent, color: 'white', cursor: 'pointer', fontWeight: 600 }}>
                Apply
              </button>
            )}
            {(hasDateFilter || hasDraft) && (
              <button onClick={() => {
                setChartDateDraft(prev => { const n = { ...prev }; delete n[w.id]; return n })
                setChartDateFilters(prev => { const n = { ...prev }; delete n[w.id]; return n })
              }}
                style={{ fontSize: 9, padding: '2px 8px', borderRadius: 6, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer' }}>
                Clear
              </button>
            )}
            {hasDateFilter && (
              <span style={{ fontSize: 9, color: theme.muted, marginLeft: 'auto' }}>
                {filteredRows.length} / {wRows.length} rows
              </span>
            )}
          </div>
        )}

        {/* Status badges (comparison / forecast) */}
        {(isCompared || (isForecasting && forecastStepCount > 0)) && (
          <div style={{ display: 'flex', gap: 5, marginBottom: 6, flexWrap: 'wrap' }}>
            {isCompared && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 10, background: theme.accentBg, border: `1px solid ${theme.accent}40`, fontSize: 9, fontWeight: 600, color: theme.accent }}>
                ⇆ Current vs Prior
              </span>
            )}
            {isForecasting && forecastStepCount > 0 && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 10, background: '#7C3AED12', border: '1px solid #7C3AED40', fontSize: 9, fontWeight: 600, color: '#7C3AED' }}>
                ∿ Forecast +{forecastStepCount} pts
              </span>
            )}
          </div>
        )}

        {crossFilter && (
          <div style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: theme.accent }}>
              {crossFilterLoading ? '⏳' : '⬡'} Cross-filter:
            </span>
            <span style={{ padding: '1px 7px', background: theme.accentBg, border: `1px solid ${theme.accent}44`, borderRadius: 8, fontSize: 9, fontWeight: 600, color: theme.accent }}>
              {crossFilter.column}: {crossFilter.value}
            </span>
            <button
              onClick={() => setDrilldown({ widgetId: w.id, widgetTitle: w.title, column: crossFilter.column, value: crossFilter.value })}
              style={{ fontSize: 9, color: theme.accent, background: 'none', border: `1px solid ${theme.accent}44`, borderRadius: 4, cursor: 'pointer', padding: '1px 6px', display: 'flex', alignItems: 'center', gap: 2 }}
            >
              <ZoomIn size={8} /> Drill down
            </button>
            <button onClick={() => { setCrossFilter(null); setCrossFilteredWidgets(null) }} style={{ fontSize: 9, color: theme.muted, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>✕ clear</button>
          </div>
        )}

        {/* Chart content — normal or split view */}
        {w.chart_data ? (
          splitViewId === w.id ? (
            <div style={{ display: 'flex', gap: 8, overflow: 'hidden' }}>
              <div style={{ flex: 1, overflow: 'hidden' }}>
                <p style={{ fontSize: 9, color: theme.muted, margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Current</p>
                <ChartRenderer result={chartResult} height={Math.round(h * 0.85)} showAnomalies anomalyIndices={anomalyIdxs} />
              </div>
              <div style={{ flex: 1, borderLeft: `1px solid ${theme.border}`, paddingLeft: 8, overflow: 'hidden' }}>
                <p style={{ fontSize: 9, color: theme.muted, margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Alternative</p>
                <ChartRenderer result={{ ...toChartResult(w), chart_type: altType ?? 'bar' }} height={Math.round(h * 0.85)} />
              </div>
            </div>
          ) : (
            <div style={{ ...themeTableVars(theme), overflow: 'hidden', cursor: wRows.length > 0 ? 'crosshair' : 'default' }}>
              <ChartRenderer result={chartResult} height={h} showAnomalies anomalyIndices={anomalyIdxs} onDataPointClick={handleCrossFilter} />
              {/* Forecast region overlay — faint dashed separator */}
              {isForecasting && forecastStepCount > 0 && (
                <div style={{ position: 'relative', marginTop: -4 }}>
                  <div style={{ position: 'absolute', top: -h, right: 0, width: `${Math.round((forecastStepCount / (filteredRows.length + forecastStepCount)) * 100)}%`, height: h, background: 'linear-gradient(to right, transparent, rgba(124,58,237,0.04))', pointerEvents: 'none', borderLeft: '1.5px dashed rgba(124,58,237,0.35)' }} />
                </div>
              )}
            </div>
          )
        ) : (
          <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>
        )}

        {/* Inline caption */}
        {caption && <p style={{ fontSize: 11, color: theme.muted, margin: '8px 0 0', fontStyle: 'italic', lineHeight: 1.45 }}>{caption}</p>}

        {/* Key driver callout */}
        {keyDriver && <KeyDriverCallout text={keyDriver} theme={theme} />}

        {/* AI Insight panel */}
        {(newspaperInsights[w.id] || newspaperInsightLoading[w.id]) && (
          <div style={{ marginTop: 10, padding: '9px 12px', background: `${theme.accent}0A`, borderLeft: `3px solid ${theme.accent}`, borderRadius: '0 8px 8px 0', border: `1px solid ${theme.accent}1A` }}>
            <p style={{ fontSize: 9, fontWeight: 800, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.1em', margin: '0 0 5px' }}>✦ AI Insight</p>
            {newspaperInsightLoading[w.id]
              ? <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {[100, 78].map(pct => <div key={pct} style={{ height: 9, borderRadius: 4, background: `${theme.muted}25`, width: `${pct}%` }} />)}
                </div>
              : <p style={{ fontSize: 11, color: theme.text, lineHeight: 1.65, margin: 0 }}>{newspaperInsights[w.id]}</p>
            }
          </div>
        )}

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
              {featured.chart_data && <div style={{ ...themeTableVars(theme), overflow: 'hidden' }}><ChartRenderer result={{ ...toChartResult(featured), chart_type: chartTypeOverrides[featured.id] ?? featured.chart_type }} height={280} /></div>}
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

        {/* Pull-quote callout — most impressive KPI */}
        {kpis.length > 0 && (() => {
          const top = kpis.reduce((best, w) => {
            const { num: bn } = getKpiMeta(best)
            const { num: wn } = getKpiMeta(w)
            return Math.abs(parseFloat(String(wn ?? 0))) > Math.abs(parseFloat(String(bn ?? 0))) ? w : best
          }, kpis[0])
          const { num, delta } = getKpiMeta(top)
          const col = delta?.up === false ? '#DC2626' : delta?.up ? '#16A34A' : theme.accent
          return (
            <div style={{ margin: '22px 0 8px', padding: '18px 28px', borderLeft: `5px solid ${col}`, background: `${col}0D`, borderRadius: '0 10px 10px 0', position: 'relative' }}>
              <p style={{ fontFamily: "'Georgia',serif", fontSize: 22, fontWeight: 900, color: theme.text, margin: '0 0 6px', lineHeight: 1.2, letterSpacing: '-0.4px' }}>
                "{fmtNum(num)}"
              </p>
              <p style={{ fontSize: 11, color: theme.muted, margin: 0, letterSpacing: '0.06em', textTransform: 'uppercase', fontWeight: 600 }}>{top.title}</p>
              {delta && <div style={{ marginTop: 6 }}><TrendBadge trend={delta} /></div>}
              <span style={{ position: 'absolute', top: 10, right: 16, fontSize: 40, opacity: 0.07, color: theme.text, fontFamily: "'Georgia',serif", lineHeight: 1 }}>"</span>
            </div>
          )
        })()}

        {/* Secondary charts — 2-column newspaper layout with AI insight */}
        {secondary.length > 0 && (
          <>
            {divider('Trend Analysis', '#7C3AED')}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 8 }}>
              {secondary.map((w, i) => {
                const insight = newspaperInsights[w.id]
                const loading = newspaperInsightLoading[w.id]
                return (
                  <div key={w.id} className="vis-card" style={{ ...cardBase, padding: '16px 18px', animationDelay: `${i * 60}ms` }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                      <div>
                        <h4 style={{ fontSize: 13, fontWeight: 700, color: theme.text, margin: 0 }}>{w.title}</h4>
                        {(() => { const c = computeCaption(w); return c ? <p style={{ fontSize: 10, color: theme.muted, margin: '3px 0 0', fontStyle: 'italic' }}>{c}</p> : null })()}
                      </div>
                      {(() => { const t = computeTrendBadge(w); return t ? <TrendBadge trend={t} small /> : null })()}
                    </div>
                    <ChartTypeSwitcher baseType={w.chart_type.toLowerCase()} active={(chartTypeOverrides[w.id] ?? w.chart_type).toLowerCase()} onSelect={t => setChartOverride(w.id, t)} theme={theme} />
                    {/* 60/40 split: chart | AI insight */}
                    <div style={{ display: 'grid', gridTemplateColumns: insight || loading ? '3fr 2fr' : '1fr', gap: 14, alignItems: 'start' }}>
                      <div>
                        {w.chart_data
                          ? <div style={themeTableVars(theme)}><ChartRenderer result={{ ...toChartResult(w), chart_type: chartTypeOverrides[w.id] ?? w.chart_type }} height={200} /></div>
                          : <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>}
                        {(() => { const kd = computeKeyDriver(w); return kd ? <KeyDriverCallout text={kd} theme={theme} /> : null })()}
                      </div>
                      {(insight || loading) && (
                        <div style={{ borderLeft: `2px solid ${theme.accent}40`, paddingLeft: 14, paddingTop: 4 }}>
                          <p style={{ fontSize: 9, fontWeight: 800, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.1em', margin: '0 0 8px' }}>AI Insight</p>
                          {loading
                            ? <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                {[100, 85, 70].map(w => <div key={w} style={{ height: 10, borderRadius: 4, background: `${theme.muted}30`, width: `${w}%`, animation: 'pulse 1.5s ease-in-out infinite' }} />)}
                              </div>
                            : <p style={{ fontFamily: "'Georgia',serif", fontSize: 12, color: theme.text, lineHeight: 1.65, margin: 0 }}>{insight}</p>
                          }
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
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
                          ? <div style={themeTableVars(theme)}><ChartRenderer result={{ ...toChartResult(w), chart_type: activeViz }} height={260} /></div>
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
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 380, gap: 14 }}>
        {/* Dashboard wireframe illustration */}
        <svg width="100" height="80" viewBox="0 0 100 80" fill="none">
          <rect x="4" y="4" width="92" height="14" rx="4" fill={theme.accentBg} stroke={theme.accent} strokeWidth="1.5"/>
          <rect x="8" y="8" width="24" height="6" rx="2" fill={theme.accent} opacity="0.4"/>
          <rect x="4" y="24" width="28" height="52" rx="4" fill={theme.accentBg} stroke={theme.accent} strokeWidth="1.5"/>
          <rect x="9" y="54" width="7" height="16" rx="2" fill={theme.accent} opacity="0.5"/>
          <rect x="19" y="44" width="7" height="26" rx="2" fill={theme.accent} opacity="0.7"/>
          <rect x="36" y="24" width="60" height="24" rx="4" fill={theme.accentBg} stroke={theme.accent} strokeWidth="1.5"/>
          <circle cx="54" cy="36" r="8" stroke={theme.accent} strokeWidth="1.5" fill="none"/>
          <path d="M54 28 A8 8 0 0 1 62 36" stroke={theme.accent} strokeWidth="3" strokeLinecap="round"/>
          <rect x="36" y="52" width="28" height="24" rx="4" fill={theme.accentBg} stroke={theme.accent} strokeWidth="1.5"/>
          <rect x="68" y="52" width="28" height="24" rx="4" fill={theme.accentBg} stroke={theme.accent} strokeWidth="1.5"/>
          <rect x="40" y="57" width="20" height="3" rx="1.5" fill={theme.accent} opacity="0.4"/>
          <rect x="40" y="63" width="14" height="3" rx="1.5" fill={theme.accent} opacity="0.3"/>
          <rect x="72" y="57" width="20" height="3" rx="1.5" fill={theme.accent} opacity="0.4"/>
          <rect x="72" y="63" width="14" height="3" rx="1.5" fill={theme.accent} opacity="0.3"/>
        </svg>
        <p style={{ fontSize: 15, fontWeight: 700, color: theme.text, margin: 0 }}>No charts yet</p>
        <p style={{ fontSize: 13, color: theme.muted, margin: 0, textAlign: 'center', maxWidth: 260, lineHeight: 1.5 }}>Ask the AI Copilot to generate visualizations, or add widgets from the canvas editor</p>
        <button onClick={() => setShowChat(true)} style={{
          padding: '10px 22px', background: theme.accent, border: 'none', borderRadius: 10,
          fontSize: 13, fontWeight: 600, color: 'white', cursor: 'pointer',
        }}>Open AI Copilot</button>
      </div>
    )

    const cols = layoutMode === 'list' ? '1fr' : 'repeat(auto-fill, minmax(340px, 1fr))'

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


        {/* KPIs — overview only */}
        {activeSection === 'overview' && kpis.length > 0 && (
          <div style={{ marginBottom: 28, borderLeft: `3px solid #2563EB`, paddingLeft: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <p style={{ ...secLabel, margin: 0 }}>Key Metrics</p>
              {/* Density toggle */}
              <div style={{ display: 'flex', gap: 2, background: theme.bg, borderRadius: 8, padding: 2, border: `1px solid ${theme.border}` }}>
                {(['compact', 'normal', 'spacious'] as const).map(d => (
                  <button key={d} onClick={() => setKpiDensity(d)}
                    style={{ padding: '2px 8px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 9, fontWeight: kpiDensity === d ? 700 : 400, background: kpiDensity === d ? theme.accent : 'transparent', color: kpiDensity === d ? 'white' : theme.muted, transition: 'all 0.15s' }}>
                    {d === 'compact' ? '⊟' : d === 'normal' ? '⊞' : '⊠'} {d}
                  </button>
                ))}
              </div>
            </div>
            <div className={focusMode ? 'vis-focus-container' : undefined} style={{
              display: 'grid',
              gridTemplateColumns: kpiDensity === 'compact'
                ? 'repeat(auto-fill, minmax(120px, 1fr))'
                : kpiDensity === 'spacious'
                  ? 'repeat(auto-fill, minmax(190px, 1fr))'
                  : 'repeat(auto-fill, minmax(148px, 1fr))',
              gap: kpiDensity === 'compact' ? 8 : kpiDensity === 'spacious' ? 14 : 10,
            }}>
              {kpis.map((w, i) => renderKpi(w, i))}
            </div>
          </div>
        )}

        {/* Visual charts */}
        {(activeSection === 'overview' || activeSection === 'charts') && visualCharts.length > 0 && (
          <div style={{ marginBottom: 28, borderLeft: `3px solid #7C3AED`, paddingLeft: 12 }}>
            {activeSection === 'overview' && <p style={secLabel}>Charts</p>}
            <div className={focusMode ? 'vis-focus-container' : undefined} style={{ display: 'grid', gridTemplateColumns: cols, gap: 14, alignItems: 'start' }}>
              {visualCharts.map((w, i) => renderChart(w, i, getChartHeight(w.chart_type, layoutMode)))}
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
              const tRows = (w.chart_data?.rows as Record<string, unknown>[] | undefined) ?? []
              const tCols = (w.chart_data?.columns as string[] | undefined) ?? (tRows[0] ? Object.keys(tRows[0]) : [])
              const tDateCol      = detectDateCol(tCols, tRows)
              const tDateDraft    = chartDateDraft[w.id]
              const tDateFilter   = chartDateFilters[w.id]
              const tDateOpen     = chartDateOpen.has(w.id)
              const tHasFilter    = !!(tDateFilter?.from || tDateFilter?.to)
              const tHasDraft     = !!(tDateDraft?.from || tDateDraft?.to)
              const tDraftChanged = tDateDraft?.from !== (tDateFilter?.from ?? '') || tDateDraft?.to !== (tDateFilter?.to ?? '')
              const tShowApply    = tHasDraft && tDraftChanged
              const tFilterResult = tDateCol && tHasFilter ? applyDateFilter(tRows, tDateCol, tDateFilter?.from ?? '', tDateFilter?.to ?? '') : null
              const rows          = tFilterResult?.rows ?? tRows
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
                      {tRows.length > 0 && (
                        <span style={{ padding: '2px 7px', background: tHasFilter ? `${theme.accent}18` : theme.accentBg, border: `1px solid ${tHasFilter ? theme.accent : theme.border}`, borderRadius: 10, fontSize: 10, fontWeight: 600, color: theme.accent, flexShrink: 0 }}>
                          {tHasFilter ? `${rows.length} / ${tRows.length}` : rows.length} rows
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                      {/* Date filter button */}
                      {tDateCol && (
                        <button onClick={e => { e.stopPropagation(); setChartDateOpen(prev => { const n = new Set(prev); if (n.has(w.id)) n.delete(w.id); else n.add(w.id); return n }) }}
                          title="Date filter"
                          style={{ width: 24, height: 24, borderRadius: '50%', padding: 0, background: (tDateOpen || tHasFilter) ? theme.accentBg : 'transparent', border: `1px solid ${(tDateOpen || tHasFilter) ? theme.accent : theme.border}`, color: (tDateOpen || tHasFilter) ? theme.accent : theme.muted, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', flexShrink: 0 }}>
                          <Calendar size={11} />
                          {tHasFilter && <span style={{ position: 'absolute', top: -2, right: -2, width: 6, height: 6, borderRadius: '50%', background: theme.accent }} />}
                        </button>
                      )}
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
                      {/* Date range filter strip */}
                      {tDateOpen && tDateCol && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', marginBottom: 10, background: `${theme.accent}08`, border: `1px solid ${theme.accent}22`, borderRadius: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 9, fontWeight: 700, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 3 }}>
                            <Calendar size={9} />{tDateCol}
                          </span>
                          <input type="date" value={tDateDraft?.from ?? ''}
                            onChange={e => setChartDateDraft(prev => ({ ...prev, [w.id]: { from: e.target.value, to: prev[w.id]?.to ?? '' } }))}
                            style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
                          <span style={{ fontSize: 10, color: theme.muted }}>→</span>
                          <input type="date" value={tDateDraft?.to ?? ''}
                            onChange={e => setChartDateDraft(prev => ({ ...prev, [w.id]: { from: prev[w.id]?.from ?? '', to: e.target.value } }))}
                            style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
                          {tShowApply && (
                            <button onClick={() => setChartDateFilters(prev => ({ ...prev, [w.id]: { from: tDateDraft?.from ?? '', to: tDateDraft?.to ?? '' } }))}
                              style={{ fontSize: 10, padding: '3px 12px', borderRadius: 6, border: 'none', background: theme.accent, color: 'white', cursor: 'pointer', fontWeight: 600 }}>
                              Apply
                            </button>
                          )}
                          {(tHasFilter || tHasDraft) && (
                            <button onClick={() => {
                              setChartDateDraft(prev => { const n = { ...prev }; delete n[w.id]; return n })
                              setChartDateFilters(prev => { const n = { ...prev }; delete n[w.id]; return n })
                            }} style={{ fontSize: 9, padding: '2px 8px', borderRadius: 6, border: `1px solid ${theme.border}`, background: 'none', color: theme.muted, cursor: 'pointer' }}>
                              Clear
                            </button>
                          )}
                        </div>
                      )}
                      {/* AI viz suggestion */}
                      {(tableVizSuggestions[w.id] || tableVizSugLoading[w.id]) && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10, padding: '5px 10px', background: `${theme.accent}0A`, border: `1px solid ${theme.accent}22`, borderRadius: 8 }}>
                          <span style={{ fontSize: 9, fontWeight: 700, color: theme.accent, flexShrink: 0 }}>✦ AI recommends:</span>
                          {tableVizSugLoading[w.id]
                            ? <span style={{ fontSize: 10, color: theme.muted }}>analyzing…</span>
                            : <>
                                <span style={{ fontSize: 10, fontWeight: 700, color: theme.accent }}>{tableVizSuggestions[w.id]}</span>
                                <button onClick={() => setTableChartTypes(prev => ({ ...prev, [w.id]: tableVizSuggestions[w.id]! }))}
                                  style={{ padding: '2px 9px', fontSize: 10, borderRadius: 6, background: theme.accent, border: 'none', color: 'white', cursor: 'pointer', fontWeight: 600 }}>
                                  Apply
                                </button>
                              </>
                          }
                        </div>
                      )}
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
                      {w.chart_data ? (() => {
                        const tKeepSet = tFilterResult ? new Set(tFilterResult.indices) : null
                        const tLabels  = (w.chart_data?.labels as string[] | undefined) ?? []
                        const tValues  = (w.chart_data?.values as (number|null)[] | undefined) ?? []
                        const filteredTableData = {
                          ...toChartResult(w).chart_data,
                          rows:   rows,
                          labels: tKeepSet ? tLabels.filter((_, idx) => tKeepSet.has(idx)) : tLabels,
                          values: tKeepSet ? tValues.filter((_, idx) => tKeepSet.has(idx)) : tValues,
                        }
                        return (
                          <div style={themeTableVars(theme)}>
                            <ChartRenderer
                              result={{ ...toChartResult(w), chart_type: tableChartTypes[w.id] ?? 'table', chart_data: filteredTableData }}
                              height={280}
                            />
                          </div>
                        )
                      })() : (
                        <div style={{ height: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', color: theme.muted, fontSize: 12 }}>No data</div>
                      )}
                      {/* AI insight for table */}
                      {(newspaperInsights[w.id] || newspaperInsightLoading[w.id]) && (
                        <div style={{ marginTop: 10, padding: '9px 12px', background: `${theme.accent}0A`, borderLeft: `3px solid ${theme.accent}`, borderRadius: '0 8px 8px 0', border: `1px solid ${theme.accent}1A` }}>
                          <p style={{ fontSize: 9, fontWeight: 800, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.1em', margin: '0 0 5px' }}>✦ AI Insight</p>
                          {newspaperInsightLoading[w.id]
                            ? <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {[100, 75].map(pct => <div key={pct} style={{ height: 9, borderRadius: 4, background: `${theme.muted}25`, width: `${pct}%` }} />)}
                              </div>
                            : <p style={{ fontSize: 11, color: theme.text, lineHeight: 1.65, margin: 0 }}>{newspaperInsights[w.id]}</p>
                          }
                        </div>
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

      {/* Background pattern is applied directly on the scrollable charts area so it shows through */}

      {/* ── Left Rail ──────────────────────────────────────────────────────── */}
      <nav className="vis-nav" style={{ width: navCollapsed ? 0 : 74, opacity: navCollapsed ? 0 : 1, overflow: navCollapsed ? 'hidden' : 'visible', background: theme.rail, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: navCollapsed ? 0 : '14px 0', flexShrink: 0, gap: 2, boxShadow: navCollapsed ? 'none' : '2px 0 12px rgba(0,0,0,0.15)', position: 'relative', zIndex: 50 }}>
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

        {/* Filters toggle */}
        <button onClick={() => setFilterPanelOpen(v => !v)} title="Filters"
          style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: filterPanelOpen ? theme.railActive : 'transparent', color: filterPanelOpen ? '#93C5FD' : theme.railText, border: 'none', cursor: 'pointer', position: 'relative' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
          <span style={{ fontSize: 9, fontWeight: 500 }}>Filters</span>
          {hasActiveFilters && (
            <span style={{ position: 'absolute', top: 6, right: 8, width: 7, height: 7, borderRadius: '50%', background: theme.accent, border: `1.5px solid ${theme.rail}` }} />
          )}
        </button>

        {/* Theme picker */}
        <div data-theme-picker style={{ position: 'relative' }}>
          <button onClick={() => setShowThemePicker(v => !v)} title="Choose theme (T)"
            style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: showThemePicker ? theme.railActive : 'transparent', color: showThemePicker ? '#93C5FD' : theme.railText, border: 'none', cursor: 'pointer' }}>
            <div style={{ width: 18, height: 18, borderRadius: 5, background: `linear-gradient(135deg,${theme.heroFrom},${theme.heroTo})`, border: '2px solid rgba(255,255,255,0.25)' }} />
            <span style={{ fontSize: 8, fontWeight: 500 }}>Theme</span>
          </button>
          {showThemePicker && (() => {
            const isDarkTheme = ['midnight', 'digitalnative'].includes(theme.id)
            const pickerBg    = isDarkTheme ? '#16172A' : (theme.surface.startsWith('rgba') ? '#FFFFFF' : theme.surface)
            const pickerText  = isDarkTheme ? '#E2E8F0' : theme.text
            const pickerMuted = isDarkTheme ? 'rgba(255,255,255,0.45)' : theme.muted
            const activeBg    = isDarkTheme ? 'rgba(255,255,255,0.12)' : theme.accentBg
            return (
              <div style={{ position: 'absolute', left: 62, bottom: 0, width: 216, background: pickerBg, border: `1px solid ${isDarkTheme ? 'rgba(255,255,255,0.12)' : theme.border}`, borderRadius: 14, padding: '8px 6px', zIndex: 200, boxShadow: '0 8px 40px rgba(0,0,0,0.45)' }}>
                <p style={{ fontSize: 9, fontWeight: 700, color: pickerMuted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 6px 6px' }}>Select Theme</p>
                {THEMES.map((t, i) => (
                  <button key={t.id} onClick={() => { setThemeIdx(i); setShowThemePicker(false) }}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px', borderRadius: 9, border: 'none', background: themeIdx === i ? activeBg : 'transparent', cursor: 'pointer', marginBottom: 2 }}
                    onMouseEnter={e => { if (themeIdx !== i) e.currentTarget.style.background = isDarkTheme ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.04)' }}
                    onMouseLeave={e => { if (themeIdx !== i) e.currentTarget.style.background = 'transparent' }}>
                    <div style={{ width: 26, height: 26, borderRadius: 7, flexShrink: 0, background: `linear-gradient(135deg,${t.heroFrom},${t.heroTo})`, boxShadow: themeIdx === i ? `0 0 0 2px ${t.accent}` : 'none' }} />
                    <div style={{ textAlign: 'left', flex: 1 }}>
                      <p style={{ fontSize: 11, fontWeight: themeIdx === i ? 700 : 500, color: pickerText, margin: 0 }}>{t.label}</p>
                      <p style={{ fontSize: 9, color: pickerMuted, margin: 0 }}>{t.fontFamily.split(',')[0].replace(/'/g,'').trim()}</p>
                    </div>
                    {themeIdx === i && <span style={{ color: t.accent, fontSize: 13, flexShrink: 0 }}>✓</span>}
                  </button>
                ))}
              </div>
            )
          })()}
        </div>

        {/* Background pattern picker */}
        <div data-bg-picker style={{ position: 'relative' }}>
          <button onClick={() => setShowBgPicker(v => !v)} title="Background pattern"
            style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: showBgPicker ? theme.railActive : 'transparent', color: showBgPicker ? '#93C5FD' : theme.railText, border: 'none', cursor: 'pointer' }}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="1" y="1" width="5" height="5" rx="1.5"/>
              <rect x="10" y="1" width="5" height="5" rx="1.5"/>
              <rect x="1" y="10" width="5" height="5" rx="1.5"/>
              <rect x="10" y="10" width="5" height="5" rx="1.5"/>
            </svg>
            <span style={{ fontSize: 8, fontWeight: 500 }}>BG</span>
          </button>
          {showBgPicker && (() => {
            const isDarkTheme = ['midnight', 'digitalnative'].includes(theme.id)
            const pickerBg   = isDarkTheme ? '#16172A' : (theme.surface.startsWith('rgba') ? '#FFFFFF' : theme.surface)
            const pickerText = isDarkTheme ? '#E2E8F0' : theme.text
            const pickerMuted = isDarkTheme ? 'rgba(255,255,255,0.45)' : theme.muted
            return (
              <div style={{ position: 'absolute', left: 62, bottom: 0, width: 170, background: pickerBg, border: `1px solid ${isDarkTheme ? 'rgba(255,255,255,0.12)' : theme.border}`, borderRadius: 14, padding: '8px 6px', zIndex: 200, boxShadow: '0 8px 40px rgba(0,0,0,0.45)' }}>
                <p style={{ fontSize: 9, fontWeight: 700, color: pickerMuted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 6px 6px' }}>Background</p>
                {(['none', 'dots', 'mesh', 'graph'] as BgPattern[]).map(p => (
                  <button key={p} onClick={() => { setBgPattern(p); setShowBgPicker(false) }}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px', borderRadius: 9, border: 'none', background: bgPattern === p ? (isDarkTheme ? 'rgba(255,255,255,0.12)' : theme.accentBg) : 'transparent', cursor: 'pointer', marginBottom: 2 }}
                    onMouseEnter={e => { if (bgPattern !== p) e.currentTarget.style.background = isDarkTheme ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.04)' }}
                    onMouseLeave={e => { if (bgPattern !== p) e.currentTarget.style.background = 'transparent' }}>
                    <span style={{ fontSize: 12, fontWeight: bgPattern === p ? 700 : 500, color: bgPattern === p ? theme.accent : pickerText, textTransform: 'capitalize', flex: 1, textAlign: 'left' }}>{p === 'none' ? 'None' : p.charAt(0).toUpperCase() + p.slice(1)}</span>
                    {bgPattern === p && <span style={{ color: theme.accent, fontSize: 13 }}>✓</span>}
                  </button>
                ))}
              </div>
            )
          })()}
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

        {/* Edit in Canvas (builder only) */}
        {canEdit && (
          <button
            onClick={() => router.push(`/projects/${projectId}/canvas/${canvas.id}`)}
            title="Edit in Canvas Builder"
            style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: 'rgba(37,99,235,0.18)', color: '#93C5FD', border: 'none', cursor: 'pointer' }}
          >
            <Pencil size={16} />
            <span style={{ fontSize: 9, fontWeight: 500 }}>Edit</span>
          </button>
        )}

        {/* Back */}
        <button onClick={onClose} title="Back" style={{ width: 52, minHeight: 48, borderRadius: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: 'transparent', color: theme.railText, border: 'none', cursor: 'pointer' }}>
          <X size={16} />
          <span style={{ fontSize: 9, fontWeight: 500 }}>Back</span>
        </button>
      </nav>

      {/* ── Nav collapse toggle — floats at the left edge of the content area ── */}
      <button
        onClick={() => setNavCollapsed(v => !v)}
        title={navCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        style={{
          position: 'absolute', left: navCollapsed ? 6 : 68, top: '50%', transform: 'translateY(-50%)',
          zIndex: 60, width: 20, height: 40, borderRadius: 8,
          background: theme.rail, border: `1px solid ${theme.border}`,
          color: theme.railText, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '2px 0 8px rgba(0,0,0,0.2)',
          transition: 'left 0.22s cubic-bezier(.4,0,.2,1)',
        }}
      >
        {navCollapsed ? <ChevronRight size={11} /> : <ChevronLeft size={11} />}
      </button>

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

              {/* Page switcher — shown in hero when canvas has multiple pages */}
              {pages.length > 1 && (
                <div className="vis-hide-scroll" style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 10, overflowX: 'auto', paddingBottom: 1 }}>
                  {[...pages].sort((a, b) => a.order - b.order).map(page => {
                    const isActive = page.id === reportPageId
                    const count = widgets.filter(w => {
                      const pid = (w.config?.page_id as string) || ''
                      return pid ? pid === page.id : page.id === defaultPageId
                    }).length
                    return pageEditId === page.id ? (
                      <input key={page.id}
                        ref={pageEditRef}
                        value={pageEditDraft}
                        onChange={e => setPageEditDraft(e.target.value)}
                        onBlur={() => { const t = pageEditDraft.trim(); if (t && t !== page.name) onPageRename?.(page.id, t); setPageEditId(null) }}
                        onKeyDown={e => { if (e.key === 'Enter') { const t = pageEditDraft.trim(); if (t && t !== page.name) onPageRename?.(page.id, t); setPageEditId(null) } if (e.key === 'Escape') setPageEditId(null) }}
                        onClick={e => e.stopPropagation()}
                        style={{ width: 100, fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 14, border: '2px solid rgba(255,255,255,0.7)', background: 'rgba(255,255,255,0.18)', color: 'white', outline: 'none', flexShrink: 0 }}
                      />
                    ) : (
                      <button key={page.id}
                        onClick={() => setReportPageId(page.id)}
                        onDoubleClick={() => { setPageEditId(page.id); setPageEditDraft(page.name) }}
                        onContextMenu={e => { e.preventDefault(); setPageCtxMenu({ id: page.id, x: e.clientX, y: e.clientY }) }}
                        style={{
                          padding: '3px 13px', borderRadius: 14, cursor: 'pointer', flexShrink: 0,
                          border: `1.5px solid ${isActive ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.25)'}`,
                          background: isActive ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.07)',
                          backdropFilter: 'blur(8px)',
                          color: isActive ? 'white' : 'rgba(255,255,255,0.6)',
                          fontSize: 11, fontWeight: isActive ? 700 : 500,
                          transition: 'all 0.18s ease',
                          display: 'flex', alignItems: 'center', gap: 5,
                        }}
                      >
                        {page.name}
                        {count > 0 && <span style={{ fontSize: 8, opacity: 0.6, background: 'rgba(255,255,255,0.15)', borderRadius: 8, padding: '0 4px' }}>{count}</span>}
                      </button>
                    )
                  })}
                </div>
              )}
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

        {/* Section tabs + action buttons — same row */}
        <div style={{ display: 'flex', alignItems: 'stretch', padding: '0 20px', flexShrink: 0, borderBottom: `1px solid ${theme.border}`, background: theme.surface }}>
          {/* Left: section tabs */}
          <div style={{ display: 'flex', gap: 1, alignItems: 'stretch', flex: 1 }}>
            {navItems.filter(s => s.show).map(s => (
              <button key={s.id} onClick={() => setActiveSection(s.id)} style={{
                padding: '7px 14px', borderRadius: 0, border: 'none', cursor: 'pointer',
                background: 'transparent',
                color: activeSection === s.id ? theme.accent : theme.muted,
                fontSize: 11, fontWeight: activeSection === s.id ? 700 : 500,
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
        </div>


        {/* Page switcher strip — MOVED TO HERO — this placeholder kept for context menu portal */}
        {pages.length > 1 && (
          <div className="vis-no-print vis-hide-scroll" style={{ display: 'none' }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0, marginRight: 6 }}>Pages</span>
            {[...pages].sort((a, b) => a.order - b.order).map(page => {
              const isActive = page.id === reportPageId
              const isEditing = pageEditId === page.id
              const count = widgets.filter(w => {
                const pid = (w.config?.page_id as string) || ''
                return pid ? pid === page.id : page.id === defaultPageId
              }).length
              return (
                <div key={page.id} style={{ position: 'relative', flexShrink: 0 }}>
                  {isEditing ? (
                    <input
                      ref={pageEditRef}
                      value={pageEditDraft}
                      onChange={e => setPageEditDraft(e.target.value)}
                      onBlur={() => {
                        const t = pageEditDraft.trim()
                        if (t && t !== page.name) onPageRename?.(page.id, t)
                        setPageEditId(null)
                      }}
                      onKeyDown={e => {
                        if (e.key === 'Enter') {
                          const t = pageEditDraft.trim()
                          if (t && t !== page.name) onPageRename?.(page.id, t)
                          setPageEditId(null)
                        }
                        if (e.key === 'Escape') setPageEditId(null)
                      }}
                      onClick={e => e.stopPropagation()}
                      style={{ width: 90, fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 10, border: `2px solid ${theme.accent}`, background: theme.accentBg, color: theme.accent, outline: 'none' }}
                    />
                  ) : (
                    <button
                      onClick={() => setReportPageId(page.id)}
                      onDoubleClick={() => { setPageEditId(page.id); setPageEditDraft(page.name) }}
                      onContextMenu={e => { e.preventDefault(); setPageCtxMenu({ id: page.id, x: e.clientX, y: e.clientY }) }}
                      style={{
                        padding: '3px 11px', borderRadius: 12, cursor: 'pointer',
                        border: `1px solid ${isActive ? theme.accent : theme.border}`,
                        background: isActive ? theme.accentBg : 'transparent',
                        color: isActive ? theme.accent : theme.muted,
                        fontSize: 10, fontWeight: isActive ? 700 : 500,
                        transition: 'all 0.15s', whiteSpace: 'nowrap',
                        display: 'flex', alignItems: 'center', gap: 4,
                      }}
                    >
                      {page.name}
                      {count > 0 && <span style={{ fontSize: 8, opacity: 0.55 }}>{count}</span>}
                    </button>
                  )}
                </div>
              )
            })}
            {/* Page context menu */}
            {pageCtxMenu && (
              <div ref={pageCtxRef} style={{ position: 'fixed', left: pageCtxMenu.x, top: pageCtxMenu.y, zIndex: 300, background: theme.surface, border: `1px solid ${theme.border}`, borderRadius: 10, padding: '4px', boxShadow: '0 8px 32px rgba(0,0,0,0.22)', minWidth: 160 }}>
                <button onClick={() => { const p = pages.find(pg => pg.id === pageCtxMenu.id); if (p) { setPageEditId(p.id); setPageEditDraft(p.name) } setPageCtxMenu(null) }}
                  style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: theme.text, fontSize: 12, cursor: 'pointer', textAlign: 'left' }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.accentBg)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  ✏️ Rename
                </button>
                {onPageDuplicate && (
                  <button onClick={() => { onPageDuplicate(pageCtxMenu.id); setPageCtxMenu(null) }}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: theme.text, fontSize: 12, cursor: 'pointer', textAlign: 'left' }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.accentBg)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                    ⧉ Duplicate
                  </button>
                )}
                {onPageReorder && pages.length > 1 && (() => {
                  const sorted = [...pages].sort((a, b) => a.order - b.order)
                  const idx = sorted.findIndex(p => p.id === pageCtxMenu.id)
                  return (
                    <>
                      {idx > 0 && (
                        <button onClick={() => {
                          const arr = [...sorted]
                          const tmp = arr[idx].order; arr[idx] = { ...arr[idx], order: arr[idx-1].order }; arr[idx-1] = { ...arr[idx-1], order: tmp }
                          onPageReorder(arr); setPageCtxMenu(null)
                        }}
                          style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: theme.text, fontSize: 12, cursor: 'pointer', textAlign: 'left' }}
                          onMouseEnter={e => (e.currentTarget.style.background = theme.accentBg)}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                          ← Move left
                        </button>
                      )}
                      {idx < sorted.length - 1 && (
                        <button onClick={() => {
                          const arr = [...sorted]
                          const tmp = arr[idx].order; arr[idx] = { ...arr[idx], order: arr[idx+1].order }; arr[idx+1] = { ...arr[idx+1], order: tmp }
                          onPageReorder(arr); setPageCtxMenu(null)
                        }}
                          style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: theme.text, fontSize: 12, cursor: 'pointer', textAlign: 'left' }}
                          onMouseEnter={e => (e.currentTarget.style.background = theme.accentBg)}
                          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                          → Move right
                        </button>
                      )}
                    </>
                  )
                })()}
                {onPageDelete && pages.length > 1 && (
                  <>
                    <div style={{ margin: '3px 0', borderTop: `1px solid ${theme.border}` }} />
                    <button onClick={() => { onPageDelete(pageCtxMenu.id); if (pageCtxMenu.id === reportPageId) setReportPageId(pages.find(p => p.id !== pageCtxMenu.id)?.id ?? ''); setPageCtxMenu(null) }}
                      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 7, border: 'none', background: 'transparent', color: '#DC2626', fontSize: 12, cursor: 'pointer', textAlign: 'left' }}
                      onMouseEnter={e => (e.currentTarget.style.background = '#FEF2F2')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                      🗑 Delete page
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        )}



        {/* Scrollable section content + optional filter sidebar */}
        <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
          {/* Filter panel sidebar */}
          {filterPanelOpen && (
            <aside style={{ width: 248, flexShrink: 0, background: theme.surface, borderRight: `1px solid ${theme.border}`, display: 'flex', flexDirection: 'column', overflowY: 'auto', animation: 'visually-slideUp 0.18s ease both' }}>

              {/* ── Tools section ─────────────────────────────────────────────── */}
              <div style={{ padding: '10px 12px 8px', borderBottom: `1px solid ${theme.border}`, flexShrink: 0 }}>

                {/* View */}
                <p style={{ fontSize: 8, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 4px' }}>View</p>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  <button onClick={() => setFocusMode(v => !v)} style={sideBtn(focusMode)} title="Dim unfocused cards on hover"><ZoomIn size={9} /> Focus</button>
                  <button onClick={() => { const next = !comparisonMode; setComparisonMode(next); if (next) setComparedCharts(new Set(visualCharts.map(w => w.id))); else setComparedCharts(new Set()) }} style={sideBtn(comparisonMode)} title="Compare current vs prior period">⇆ Compare</button>
                  <button onClick={() => { setSlideMode(true); setSlideIdx(0) }} style={sideBtn(false)} title="Presentation / slide mode"><Play size={9} /> Present</button>
                </div>

                {/* Analytics */}
                <p style={{ fontSize: 8, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 4px' }}>Analytics</p>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  <button onClick={() => setShowMeasures(true)} style={sideBtn(showMeasures)} title="Calculated Measures"><FunctionSquare size={9} /> Measures</button>
                  <button
                    onClick={() => { if (crossFilter && visualCharts[0]) setDrilldown({ widgetId: visualCharts[0].id, widgetTitle: visualCharts[0].title, column: crossFilter.column, value: crossFilter.value }) }}
                    style={{ ...sideBtn(!!drilldown), opacity: crossFilter ? 1 : 0.38, cursor: crossFilter ? 'pointer' : 'default' }}
                    title={crossFilter ? `Drill into "${crossFilter.value}"` : 'Click a chart bar first'}
                  ><ZoomIn size={9} /> Drilldown</button>
                </div>

                {/* Security */}
                <p style={{ fontSize: 8, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 4px' }}>Security</p>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  <button onClick={() => setShowRLS(true)} style={sideBtn(showRLS)} title="Row-Level Security"><Shield size={9} /> Row-Level Security</button>
                </div>

                {/* Automation */}
                <p style={{ fontSize: 8, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 4px' }}>Automation</p>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  <button onClick={() => setShowSchedule(true)} style={sideBtn(showSchedule)} title="Scheduled Refresh"><Clock size={9} /> Schedule Refresh</button>
                  <button
                    onClick={async () => { try { await scheduleApi.refreshNow(canvas.id); onWidgetAdded?.(); showToast('✓ Dashboard refreshed') } catch { showToast('Refresh failed') } }}
                    style={sideBtn(false)} title="Re-run all widget queries"
                  ><RefreshCw size={9} /> Refresh Now</button>
                </div>

                {/* Export */}
                <p style={{ fontSize: 8, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: '0 0 4px' }}>Export</p>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  <button onClick={handlePrint} style={sideBtn(false)}><Printer size={9} /> Print</button>
                  <button onClick={handleExportJSON} style={sideBtn(false)}><Download size={9} /> Export</button>
                  <button onClick={handleEmailCopy} style={sideBtn(false)}>✉ Email</button>
                  <button onClick={() => setShowNewShareModal(true)} style={sideBtn(false)}><Users size={9} /> Share</button>
                  <button onClick={() => vlyApi.exportVly(canvas.id)} title="Download .vly bundle" style={sideBtn(false)}><FileArchive size={9} /> .vly</button>
                  <button onClick={() => setShowVlyImport(true)} title="Import .vly file" style={sideBtn(false)}><Upload size={9} /> Import</button>
                  <button onClick={() => setShowShortcutSheet(true)} style={sideBtn(false)} title="Keyboard shortcuts"><HelpCircle size={9} /> Shortcuts</button>
                </div>
              </div>

              {/* Filter header */}
              <div style={{ padding: '12px 14px 10px', borderBottom: `1px solid ${theme.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={theme.accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                  <span style={{ fontSize: 11, fontWeight: 700, color: theme.text, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Filters</span>
                  {hasActiveFilters && (
                    <span style={{ padding: '1px 6px', background: theme.accent, borderRadius: 8, fontSize: 9, fontWeight: 700, color: 'white' }}>
                      {Object.values(activeFilters).flat().length + Object.keys(appliedDateRange).length}
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {hasActiveFilters && (
                    <button onClick={clearAllFilters} style={{ fontSize: 10, color: theme.accent, background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontWeight: 600 }}>Clear all</button>
                  )}
                  <button onClick={() => setFilterPanelOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, padding: 2, display: 'flex' }}>
                    <X size={13} />
                  </button>
                </div>
              </div>

              {/* Loading indicator */}
              {filterLoading && (
                <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 6, borderBottom: `1px solid ${theme.border}`, background: `${theme.accent}08`, flexShrink: 0 }}>
                  <Loader2 size={11} style={{ color: theme.accent, animation: 'visually-spin 1s linear infinite' }} />
                  <span style={{ fontSize: 10, color: theme.muted }}>Re-querying data…</span>
                </div>
              )}

              {/* No filters state */}
              {catFCs.length === 0 && dateFCs.length === 0 && (
                <div style={{ padding: '24px 16px', textAlign: 'center' }}>
                  <div style={{ width: 40, height: 40, borderRadius: 12, background: theme.accentBg, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px' }}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={theme.accent} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                  </div>
                  <p style={{ fontSize: 12, fontWeight: 600, color: theme.text, margin: '0 0 6px' }}>No filters detected</p>
                  <p style={{ fontSize: 11, color: theme.muted, margin: 0, lineHeight: 1.5 }}>
                    Filters are automatically extracted from Power BI screenshots. Upload a screenshot with visible filter panels to enable this feature.
                  </p>
                </div>
              )}

              {/* Date range filter groups */}
              {dateFCs.map(f => {
                const draft   = globalDateDraft[f.column]   ?? { start: '', end: '' }
                const applied = appliedDateRange[f.column]
                const hasApplied = !!(applied?.start || applied?.end)
                const draftChanged = draft.start !== (applied?.start ?? '') || draft.end !== (applied?.end ?? '')
                return (
                  <div key={f.id} style={{ padding: '10px 14px', borderBottom: `1px solid ${theme.border}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                        <Calendar size={10} color={theme.accent} />
                        <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{f.display_name}</p>
                        {hasApplied && <span style={{ padding: '1px 5px', background: theme.accent, borderRadius: 8, fontSize: 8, fontWeight: 700, color: 'white' }}>active</span>}
                      </div>
                      {hasApplied && (
                        <button onClick={() => {
                          setAppliedDateRange(prev => { const n = { ...prev }; delete n[f.column]; return n })
                          setGlobalDateDraft(prev => { const n = { ...prev }; delete n[f.column]; return n })
                        }}
                          style={{ fontSize: 9, color: theme.accent, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>✕ clear</button>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <input type="date" value={draft.start}
                          onChange={e => setGlobalDateDraft(prev => ({ ...prev, [f.column]: { ...draft, start: e.target.value } }))}
                          style={{ flex: 1, fontSize: 10, padding: '3px 6px', borderRadius: 5, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
                        <span style={{ fontSize: 9, color: theme.muted, flexShrink: 0 }}>→</span>
                        <input type="date" value={draft.end}
                          onChange={e => setGlobalDateDraft(prev => ({ ...prev, [f.column]: { ...draft, end: e.target.value } }))}
                          style={{ flex: 1, fontSize: 10, padding: '3px 6px', borderRadius: 5, border: `1px solid ${theme.border}`, background: theme.bg, color: theme.text, outline: 'none' }} />
                      </div>
                      {draftChanged && draft.start && draft.end && (
                        <button
                          disabled={dateRangeApplying}
                          onClick={async () => {
                            setDateRangeApplying(true)
                            setAppliedDateRange(prev => ({ ...prev, [f.column]: { start: draft.start, end: draft.end } }))
                            setDateRangeApplying(false)
                          }}
                          style={{ fontSize: 10, padding: '4px 10px', borderRadius: 6, border: 'none', background: theme.accent, color: 'white', cursor: 'pointer', fontWeight: 600, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                          {dateRangeApplying && <Loader2 size={10} style={{ animation: 'visually-spin 1s linear infinite' }} />}
                          Apply to all charts
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}

              {/* Categorical filter groups */}
              {catFCs.map(f => {
                const selected = activeFilters[f.column] ?? []
                return (
                  <div key={f.id} style={{ padding: '10px 14px', borderBottom: `1px solid ${theme.border}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 }}>
                      <p style={{ fontSize: 10, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>{f.display_name}</p>
                      {selected.length > 0 && (
                        <button onClick={() => setActiveFilters(prev => { const n = { ...prev }; delete n[f.column]; return n })}
                          style={{ fontSize: 9, color: theme.accent, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>✕ clear</button>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {f.available_values.slice(0, 15).map(val => {
                        const isChecked = selected.includes(val)
                        return (
                          <label key={val} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '2px 4px', borderRadius: 5, background: isChecked ? `${theme.accent}12` : 'transparent', transition: 'background 0.12s' }}>
                            <input type="checkbox" checked={isChecked} onChange={() => toggleFilter(f.column, val)}
                              style={{ accentColor: theme.accent, cursor: 'pointer', width: 12, height: 12, flexShrink: 0 }} />
                            <span style={{ fontSize: 11, color: isChecked ? theme.accent : theme.text, fontWeight: isChecked ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{val}</span>
                          </label>
                        )
                      })}
                      {f.available_values.length > 15 && (
                        <span style={{ fontSize: 10, color: theme.muted, fontStyle: 'italic', padding: '2px 4px' }}>+{f.available_values.length - 15} more values</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </aside>
          )}

          {/* Charts area — Power BI-style: patterned workspace with centered white sheet */}
          <div ref={scrollAreaRef} style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'auto', backgroundColor: theme.bg, ...getPatternStyle(bgPattern, theme), position: 'relative' }}>
            {/* Scroll progress bar */}
            {scrollProgress > 0.01 && (
              <div className="vis-scroll-progress vis-no-print" style={{ position: 'sticky', top: 0, left: 0, height: 3, background: theme.accent, width: `${scrollProgress * 100}%`, zIndex: 10, borderRadius: '0 2px 2px 0', opacity: 0.8 }} />
            )}
            {/* Cross-filter active banner */}
            {crossFilter && (
              <div className="vis-no-print" style={{ padding: '5px 20px', background: `${theme.accent}12`, borderBottom: `1px solid ${theme.accent}30`, display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                <span style={{ fontSize: 9, fontWeight: 700, color: theme.accent, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Cross-filter</span>
                <span style={{ padding: '2px 9px', background: theme.accentBg, border: `1px solid ${theme.accent}44`, borderRadius: 10, fontSize: 11, fontWeight: 600, color: theme.accent, display: 'flex', alignItems: 'center', gap: 5 }}>
                  {crossFilter.column}: {crossFilter.value}
                  <button onClick={() => setCrossFilter(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.accent, padding: 0, lineHeight: 1, fontSize: 12 }}>×</button>
                </span>
                <span style={{ fontSize: 10, color: theme.muted }}>All charts filtered — click again or press Esc to clear</span>
              </div>
            )}
            {/* Active filter chips */}
            {hasActiveFilters && (
              <div style={{ padding: '6px 20px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', borderBottom: `1px solid ${theme.border}`, background: `${theme.accent}08`, flexShrink: 0 }}>
                <span style={{ fontSize: 9, fontWeight: 700, color: theme.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Filtered by:</span>
                {Object.entries(activeFilters).flatMap(([col, vals]) =>
                  vals.map(val => (
                    <span key={`${col}:${val}`} style={{ padding: '2px 8px', background: theme.accentBg, border: `1px solid ${theme.accent}44`, borderRadius: 10, fontSize: 10, fontWeight: 600, color: theme.accent, display: 'flex', alignItems: 'center', gap: 4 }}>
                      {val}
                      <button onClick={() => toggleFilter(col, val)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.accent, padding: 0, lineHeight: 1, fontSize: 11 }}>×</button>
                    </span>
                  ))
                )}
              </div>
            )}
            {/* ── Centered page sheet (Power BI canvas style) ──────────── */}
            <div style={{ padding: '24px 40px 56px', display: 'flex', justifyContent: 'center', minHeight: '100%' }}>
              <div style={{
                width: '100%', maxWidth: 1440,
                background: theme.surface,
                borderRadius: 6,
                boxShadow: ['midnight','digitalnative'].includes(theme.id)
                  ? '0 6px 40px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.08)'
                  : '0 2px 8px rgba(0,0,0,0.06), 0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)',
                minHeight: '70vh',
                overflow: 'hidden',
              }}>
                <div key={`${activeSection}::${reportPageId}`} style={{ animation: 'vis-page-enter 0.22s cubic-bezier(0.4,0,0.2,1) both' }}>
                  {renderContent()}
                </div>
              </div>
            </div>
          </div>{/* end scrollable charts area */}
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
              const isDarkTheme = ['midnight', 'digitalnative'].includes(theme.id)
              const bubbleBg = msg.role === 'user' ? '#2563EB' : (isDarkTheme ? 'rgba(255,255,255,0.10)' : '#F3F4F6')
              const bubbleText = msg.role === 'user' ? 'white' : theme.text
              return (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start', gap: 8 }}>
                  <div style={{ maxWidth: '90%', padding: '9px 13px', borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '4px 16px 16px 16px', background: bubbleBg, color: bubbleText, fontSize: 13, lineHeight: 1.55 }}>
                    {isLatestAI
                      ? <TypewriterText
                          text={msg.content}
                          active
                          speed={10}
                          onComplete={() => setMessages(prev => prev.map((m, mi) => mi === i ? { ...m, typing: false } : m))}
                        />
                      : msg.role === 'assistant'
                        ? <MarkdownText text={msg.content} color={bubbleText} fontSize={13} />
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
                <div style={{ padding: '10px 14px', background: ['midnight', 'digitalnative'].includes(theme.id) ? 'rgba(255,255,255,0.10)' : '#F3F4F6', borderRadius: '4px 16px 16px 16px', display: 'flex', gap: 4, alignItems: 'center' }}>
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
                ref={chatInputRef}
                value={input}
                onChange={e => {
                  setInput(e.target.value)
                  e.target.style.height = 'auto'
                  e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Ask about your data or request a chart…"
                rows={1}
                style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 13, color: theme.text, resize: 'none', minHeight: '24px', maxHeight: '120px', overflowY: 'auto', lineHeight: 1.5, fontFamily: 'inherit' }}
              />
              <button onClick={() => send()} disabled={!input.trim() || sending} style={{ width: 34, height: 34, borderRadius: 9, flexShrink: 0, background: 'linear-gradient(135deg,#2563EB,#7C3AED)', border: 'none', cursor: input.trim() && !sending ? 'pointer' : 'not-allowed', opacity: !input.trim() || sending ? 0.45 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Send size={13} color="white" />
              </button>
            </div>
            <p style={{ fontSize: 10, color: theme.muted, marginTop: 6, textAlign: 'center' }}>
              1/2/3 sections · C copilot · T theme · ? shortcuts
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

      {/* ── Keyboard Shortcut Cheat-Sheet ────────────────────────────────── */}
      {showShortcutSheet && (
        <div onClick={() => setShowShortcutSheet(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 500, background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32, animation: 'visually-fadeIn 0.15s ease both' }}>
          <div onClick={e => e.stopPropagation()} style={{ ...cardBase, width: 380, padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <HelpCircle size={16} color={theme.accent} />
                <p style={{ fontSize: 14, fontWeight: 800, color: theme.text, margin: 0 }}>Keyboard Shortcuts</p>
              </div>
              <button onClick={() => setShowShortcutSheet(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, padding: 2 }}><X size={14} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {([
                ['1', 'Overview section'],
                ['2', 'Charts section'],
                ['3', 'Data tables section'],
                ['C', 'Toggle AI Copilot panel'],
                ['T', 'Cycle through themes'],
                ['F', 'Fullscreen first chart'],
                ['S', 'Toggle presentation / slide mode'],
                ['P', 'Cycle background pattern'],
                ['?', 'Show this cheat-sheet'],
                ['Esc', 'Close panel / go back to canvas'],
              ] as [string, string][]).map(([key, desc]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 0', borderBottom: `1px solid ${theme.border}` }}>
                  <span style={{ fontSize: 12, color: theme.text }}>{desc}</span>
                  <kbd style={{ padding: '2px 8px', background: theme.bg, border: `1px solid ${theme.border}`, borderRadius: 5, fontSize: 11, fontFamily: '"SF Mono","JetBrains Mono",monospace', fontWeight: 700, color: theme.accent, flexShrink: 0 }}>{key}</kbd>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── New Share Modal (links + collaborators) ───────────────────────── */}
      {showNewShareModal && (
        <ShareModal
          canvasId={canvas.id}
          canvasName={canvas.name}
          onClose={() => setShowNewShareModal(false)}
        />
      )}

      {/* ── .vly Import Modal ─────────────────────────────────────────────── */}
      {showVlyImport && (
        <VlyImportModal
          projectId={projectId}
          onClose={() => setShowVlyImport(false)}
        />
      )}

      {/* ── Tier 5: Measures Panel ────────────────────────────────────────── */}
      {showMeasures && (
        <MeasuresPanel
          canvasId={canvas.id}
          onClose={() => setShowMeasures(false)}
        />
      )}

      {/* ── Tier 5: Schedule Refresh Modal ───────────────────────────────── */}
      {showSchedule && (
        <ScheduleRefreshModal
          canvasId={canvas.id}
          onClose={() => setShowSchedule(false)}
          onRefreshedNow={onWidgetAdded ?? (() => {})}
        />
      )}

      {/* ── Tier 5: RLS Modal ─────────────────────────────────────────────── */}
      {showRLS && (
        <RLSModal
          canvasId={canvas.id}
          onClose={() => setShowRLS(false)}
        />
      )}

      {/* ── Tier 5: Drill-Down Modal ──────────────────────────────────────── */}
      {drilldown && (
        <DrillDownModal
          canvasId={canvas.id}
          widgetId={drilldown.widgetId}
          widgetTitle={drilldown.widgetTitle}
          drillColumn={drilldown.column}
          drillValue={drilldown.value}
          connectionId={connectionId}
          onClose={() => setDrilldown(null)}
        />
      )}

      {/* ── Legacy HTML Share Modal ───────────────────────────────────────── */}
      {shareModal.open && (
        <div onClick={() => setShareModal(s => ({ ...s, open: false }))}
          style={{ position: 'fixed', inset: 0, zIndex: 500, background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32, animation: 'visually-fadeIn 0.15s ease both' }}>
          <div onClick={e => e.stopPropagation()} style={{ ...cardBase, width: 460, padding: 28 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ width: 38, height: 38, borderRadius: 11, background: theme.accentBg, display: 'flex', alignItems: 'center', justifyContent: 'center', border: `1px solid ${theme.accent}44` }}>
                  <Share2 size={17} color={theme.accent} />
                </div>
                <div>
                  <p style={{ fontSize: 14, fontWeight: 700, color: theme.text, margin: 0 }}>Share Report</p>
                  <p style={{ fontSize: 10, color: theme.muted, margin: 0 }}>Read-only HTML export · expires in 7 days</p>
                </div>
              </div>
              <button onClick={() => setShareModal(s => ({ ...s, open: false }))} style={{ background: 'none', border: 'none', cursor: 'pointer', color: theme.muted, padding: 2 }}><X size={14} /></button>
            </div>
            {shareModal.loading && (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <Loader2 size={26} style={{ color: theme.accent, animation: 'visually-spin 1s linear infinite', display: 'block', margin: '0 auto 14px' }} />
                <p style={{ fontSize: 13, color: theme.muted, margin: 0 }}>Generating your shareable report…</p>
                <p style={{ fontSize: 11, color: theme.muted, margin: '5px 0 0' }}>This may take up to 30 seconds</p>
              </div>
            )}
            {!shareModal.loading && shareModal.url && (
              <div>
                <p style={{ fontSize: 12, color: theme.muted, margin: '0 0 10px' }}>Shareable download link (anyone can open this):</p>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input readOnly value={shareModal.url}
                    style={{ flex: 1, fontSize: 11, padding: '8px 10px', border: `1px solid ${theme.border}`, borderRadius: 8, background: theme.bg, color: theme.text, outline: 'none' }} />
                  <button onClick={() => { navigator.clipboard.writeText(shareModal.url!); showToast('Link copied!') }}
                    style={{ padding: '8px 16px', background: theme.accent, border: 'none', borderRadius: 8, fontSize: 12, fontWeight: 600, color: 'white', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                    <Copy size={12} /> Copy
                  </button>
                </div>
                <p style={{ fontSize: 11, color: theme.muted, marginTop: 10, lineHeight: 1.5 }}>
                  Recipients can view the report without logging in. The link downloads a self-contained HTML file.
                </p>
              </div>
            )}
            {!shareModal.loading && shareModal.error && (
              <div style={{ padding: '14px 16px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8 }}>
                <p style={{ fontSize: 12, color: '#DC2626', margin: 0 }}>⚠ {shareModal.error}</p>
                <button onClick={handleShare} style={{ marginTop: 10, fontSize: 11, color: '#DC2626', background: 'none', border: '1px solid #FECACA', borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}>
                  Try again
                </button>
              </div>
            )}
          </div>
        </div>
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
