'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { canvasApi, shareApi, chatApi, intelligenceApi } from '@/lib/api'
import { ExecutiveCopilot } from '@/components/report/ExecutiveCopilot'
import {
  runIntelligenceAgent, buildFallbackAnalysis,
  type ExecutiveAnalysis, type AgentKPI, type AgentChart, type AgentSection,
  type InsightCard, type PerformerRow,
} from '@/lib/intelligenceAgent'
import {
  Zap, TrendingUp, TrendingDown, Minus, BarChart2, PieChart as PieChartIcon,
  Layers, Lightbulb, Target, Activity, ChevronLeft, Loader2, AlertCircle,
  MessageSquare, Send, Sparkles, RefreshCw, Users, AlertTriangle,
  CheckCircle2, GitMerge, Trophy, ThumbsDown, ArrowRight,
  Star, Info, DollarSign, ShoppingCart, Building, Globe, Database, Cpu,
  Settings, Calendar, Clock, FileText, Filter, MapPin, Percent, Shield,
  Award, Package, Briefcase, LineChart as LineChartIcon, ChevronDown, ChevronUp,
  Code2,
} from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  LineChart, Line, ComposedChart, ScatterChart, Scatter, LabelList,
  ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
  Legend, ReferenceLine, ZAxis,
} from 'recharts'

// ── Design tokens ──────────────────────────────────────────────────────────────
const C = {
  navy: '#08213a', teal: '#00b4d8', teal2: '#0dcaf0',
  green: '#06d6a0', amber: '#ffb703', red: '#ef476f',
  violet: '#7b5ea7', bg: '#f0f4f8', card: '#ffffff',
  slate: '#64748b', ink: '#1e293b', muted: '#94a3b8',
}
const PALETTE = ['#00b4d8','#06d6a0','#7b5ea7','#ffb703','#ef476f','#0dcaf0','#f97316','#26c6da','#9c6fef','#f06292']
// Gradient accents keyed by trend
const TREND_GRAD: Record<string, string> = {
  up: `linear-gradient(135deg, ${C.green}22 0%, ${C.green}06 100%)`,
  down: `linear-gradient(135deg, ${C.red}22 0%, ${C.red}06 100%)`,
  neutral: `linear-gradient(135deg, #e2e8f015 0%, transparent 100%)`,
}

// ── Section icon — 35 named icons ─────────────────────────────────────────────
function SectionIcon({ name, size = 15 }: { name: string; size?: number }) {
  const p = { size }
  switch (name) {
    case 'trending_up':    return <TrendingUp {...p} />
    case 'trending_down':  return <TrendingDown {...p} />
    case 'users':          return <Users {...p} />
    case 'pie_chart':      return <PieChartIcon {...p} />
    case 'lightbulb':      return <Lightbulb {...p} />
    case 'target':         return <Target {...p} />
    case 'activity':       return <Activity {...p} />
    case 'overview':       return <Layers {...p} />
    case 'dollar_sign':    return <DollarSign {...p} />
    case 'shopping_cart':  return <ShoppingCart {...p} />
    case 'building':       return <Building {...p} />
    case 'globe':          return <Globe {...p} />
    case 'database':       return <Database {...p} />
    case 'cpu':            return <Cpu {...p} />
    case 'settings':       return <Settings {...p} />
    case 'calendar':       return <Calendar {...p} />
    case 'clock':          return <Clock {...p} />
    case 'file_text':      return <FileText {...p} />
    case 'filter':         return <Filter {...p} />
    case 'map_pin':        return <MapPin {...p} />
    case 'percent':        return <Percent {...p} />
    case 'shield':         return <Shield {...p} />
    case 'award':          return <Award {...p} />
    case 'check_circle':   return <CheckCircle2 {...p} />
    case 'alert_triangle': return <AlertTriangle {...p} />
    case 'arrow_up':       return <TrendingUp {...p} />
    case 'arrow_down':     return <TrendingDown {...p} />
    case 'refresh':        return <RefreshCw {...p} />
    case 'layers':         return <Layers {...p} />
    case 'package':        return <Package {...p} />
    case 'briefcase':      return <Briefcase {...p} />
    case 'heart_pulse':    return <Activity {...p} />
    case 'line_chart':     return <LineChartIcon {...p} />
    case 'zap':            return <Zap {...p} />
    default:               return <BarChart2 {...p} />
  }
}

// ── Insight icon map ───────────────────────────────────────────────────────────
const INSIGHT_ICON_MAP: Record<string, React.ReactNode> = {
  trending_up: <TrendingUp size={13} />, trending_down: <TrendingDown size={13} />,
  alert_circle: <AlertCircle size={13} />, check_circle: <CheckCircle2 size={13} />,
  lightbulb: <Lightbulb size={13} />, star: <Star size={13} />,
  alert_triangle: <AlertTriangle size={13} />, info: <Info size={13} />,
  target: <Target size={13} />, activity: <Activity size={13} />,
  thumbs_down: <ThumbsDown size={13} />, zap: <Zap size={13} />,
  dollar_sign: <DollarSign size={13} />, users: <Users size={13} />,
  shield: <Shield size={13} />, award: <Award size={13} />,
  percent: <Percent size={13} />, calendar: <Calendar size={13} />,
  building: <Building size={13} />, globe: <Globe size={13} />,
  package: <Package size={13} />, briefcase: <Briefcase size={13} />,
}
const INSIGHT_TYPE_STYLE = {
  positive: { bg: `${C.green}10`, border: `${C.green}25`, icon: C.green },
  negative: { bg: `${C.red}10`,   border: `${C.red}25`,   icon: C.red   },
  warning:  { bg: `${C.amber}10`, border: `${C.amber}25`, icon: C.amber },
  neutral:  { bg: '#f8fafc',      border: '#e8eef5',      icon: C.slate },
}

// ── Health badge ───────────────────────────────────────────────────────────────
function HealthBadge({ score, color }: { score: number; color: 'green' | 'amber' | 'red' }) {
  const ring = color === 'green' ? C.green : color === 'amber' ? C.amber : C.red
  const label = color === 'green' ? 'Healthy' : color === 'amber' ? 'Caution' : 'At Risk'
  const r = 14, circ = 2 * Math.PI * r, dash = circ * (score / 100)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <svg width={38} height={38} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={19} cy={19} r={r} fill="none" stroke="#e2e8f0" strokeWidth={3} />
        <circle cx={19} cy={19} r={r} fill="none" stroke={ring} strokeWidth={3} strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" style={{ transition: 'stroke-dasharray 0.6s ease' }} />
        <text x={19} y={19} textAnchor="middle" dominantBaseline="central" style={{ fill: ring, fontSize: 10, fontWeight: 800, transform: 'rotate(90deg)', transformOrigin: '19px 19px' }}>{score}</text>
      </svg>
      <div>
        <p style={{ fontSize: 10, fontWeight: 700, color: ring, margin: 0 }}>{label}</p>
        <p style={{ fontSize: 9, color: '#94a3b8', margin: 0 }}>Health Score</p>
      </div>
    </div>
  )
}

// ── Animated counter ───────────────────────────────────────────────────────────
function AnimatedCounter({ target, prefix = '', suffix = '', duration = 1200 }: { target: number; prefix?: string; suffix?: string; duration?: number }) {
  const [display, setDisplay] = useState(0)
  const rafRef = useRef<number>(0)
  useEffect(() => {
    if (isNaN(target) || !isFinite(target)) { setDisplay(target); return }
    const start = Date.now()
    const tick = () => {
      const elapsed = Date.now() - start
      const t = Math.min(elapsed / duration, 1)
      const ease = 1 - (1 - t) ** 3
      setDisplay(target * ease)
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [target, duration])
  const fmt = (n: number) => {
    if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`
    if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`
    if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`
    return n % 1 === 0 ? String(Math.round(n)) : n.toFixed(1)
  }
  return <span>{prefix}{fmt(display)}{suffix}</span>
}

// ── Sparkline ──────────────────────────────────────────────────────────────────
function Sparkline({ data, color }: { data: number[]; color: string }) {
  if (!data?.length) return null
  const pts = data.slice(-12).map((v, i) => ({ i, v }))
  return (
    <ResponsiveContainer width={60} height={24}>
      <AreaChart data={pts} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <defs>
          <linearGradient id={`sg_${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={`url(#sg_${color.replace('#', '')})`} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

// ── Correlation card ───────────────────────────────────────────────────────────
function CorrelationCard({ correlations }: { correlations: string[] }) {
  if (!correlations.length) return null
  return (
    <div style={{ background: `linear-gradient(135deg, #0a1628, #0e2244)`, borderRadius: 14, padding: '16px 20px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <GitMerge size={13} style={{ color: C.violet }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: C.violet, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Key Relationships Detected</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {correlations.slice(0, 3).map((c, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <span style={{ fontSize: 10, fontWeight: 800, color: PALETTE[i], marginTop: 1 }}>r{i + 1}</span>
            <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.78)', lineHeight: 1.55, margin: 0 }}>{c}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── KPI card (animated + sparkline + gradient) ────────────────────────────────
function KpiCard({ kpi, idx }: { kpi: AgentKPI; idx: number }) {
  const TI = kpi.trend === 'up' ? TrendingUp : kpi.trend === 'down' ? TrendingDown : Minus
  const tc = kpi.trend === 'up' ? C.green : kpi.trend === 'down' ? C.red : C.muted
  const accentColor = PALETTE[idx % PALETTE.length]
  const raw = parseFloat(String(kpi.value).replace(/[^0-9.-]/g, ''))
  const prefix = String(kpi.value).match(/^[^0-9-]*/)?.[0] ?? ''
  const suffix = String(kpi.value).match(/[^0-9.]+$/)?.[0] ?? ''
  const animatable = !isNaN(raw) && isFinite(raw)
  return (
    <div style={{
      background: C.card, borderRadius: 16, padding: '16px 18px',
      border: '1px solid #e2eaf4',
      boxShadow: '0 2px 12px rgba(10,33,58,0.06)',
      display: 'flex', flexDirection: 'column', gap: 8,
      animation: 'fadeSlideIn 0.4s ease both',
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Colored top accent bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0', background: accentColor }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
        <p style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', margin: 0 }}>{kpi.label}</p>
        {kpi.sparkline_data && kpi.sparkline_data.length > 1 && <Sparkline data={kpi.sparkline_data} color={accentColor} />}
      </div>
      <p style={{ fontSize: 28, fontWeight: 800, color: C.ink, margin: 0, lineHeight: 1, letterSpacing: '-0.02em' }}>
        {animatable ? <AnimatedCounter target={raw} prefix={prefix} suffix={suffix} /> : kpi.value}
      </p>
      {kpi.trend_pct && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 3, background: `${tc}15`, borderRadius: 20, padding: '3px 8px' }}>
            <TI size={10} style={{ color: tc }} />
            <span style={{ fontSize: 11, fontWeight: 700, color: tc }}>{kpi.trend_pct}</span>
          </div>
          <span style={{ fontSize: 10, color: C.muted }}>vs prior period</span>
        </div>
      )}
    </div>
  )
}

// ── Insight cards ──────────────────────────────────────────────────────────────
function InsightCards({ insights }: { insights: InsightCard[] }) {
  if (!insights?.length) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
      {insights.map((ins, i) => {
        const style = INSIGHT_TYPE_STYLE[ins.type] ?? INSIGHT_TYPE_STYLE.neutral
        const icon = INSIGHT_ICON_MAP[ins.icon] ?? <Lightbulb size={14} />
        return (
          <div key={i} style={{
            borderRadius: 14, padding: '14px 16px',
            background: style.bg,
            border: `1px solid ${style.border}`,
            borderLeft: `3px solid ${style.icon}`,
            display: 'flex', gap: 12, alignItems: 'flex-start',
            animation: 'fadeSlideIn 0.4s ease both',
            animationDelay: `${i * 0.07}s`,
            boxShadow: '0 1px 6px rgba(10,33,58,0.04)',
          }}>
            <div style={{
              width: 34, height: 34, borderRadius: 10,
              background: `${style.icon}18`,
              border: `1px solid ${style.icon}30`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, color: style.icon,
            }}>{icon}</div>
            <div style={{ minWidth: 0 }}>
              <p style={{ fontSize: 12, fontWeight: 700, color: C.ink, margin: '0 0 4px', lineHeight: 1.35 }}>{ins.headline}</p>
              <p style={{ fontSize: 11, color: '#5a6b7c', margin: 0, lineHeight: 1.6 }}>{ins.detail}</p>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Performer panel ────────────────────────────────────────────────────────────
function PerformerPanel({ top, bottom, label }: { top: PerformerRow[]; bottom: PerformerRow[]; label: string }) {
  const [tab, setTab] = useState<'top' | 'bottom'>('top')
  const rows = tab === 'top' ? top : bottom
  const maxVal = Math.max(...rows.map(r => Math.abs(r.value)), 1)
  if (!top.length && !bottom.length) return null
  return (
    <div style={{ background: 'white', borderRadius: 13, border: '1px solid #e8eef5', overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #f1f5f9', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          {tab === 'top' ? <Trophy size={13} style={{ color: C.amber }} /> : <ThumbsDown size={13} style={{ color: C.red }} />}
          <span style={{ fontSize: 12, fontWeight: 700, color: C.navy }}>{label}</span>
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {top.length > 0 && <button onClick={() => setTab('top')} style={{ fontSize: 10, padding: '4px 10px', borderRadius: 20, border: 'none', cursor: 'pointer', background: tab === 'top' ? C.teal : '#f1f5f9', color: tab === 'top' ? 'white' : '#6b7c93', fontWeight: 600 }}>Top</button>}
          {bottom.length > 0 && <button onClick={() => setTab('bottom')} style={{ fontSize: 10, padding: '4px 10px', borderRadius: 20, border: 'none', cursor: 'pointer', background: tab === 'bottom' ? C.red : '#f1f5f9', color: tab === 'bottom' ? 'white' : '#6b7c93', fontWeight: 600 }}>Bottom</button>}
        </div>
      </div>
      <div style={{ padding: '8px 0' }}>
        {rows.map((r, i) => (
          <div key={i} style={{ padding: '7px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: i < rows.length - 1 ? '1px solid #f8fafc' : 'none' }}>
            <span style={{ width: 20, height: 20, borderRadius: '50%', background: tab === 'top' ? `${C.amber}20` : `${C.red}15`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, fontWeight: 800, color: tab === 'top' ? C.amber : C.red, flexShrink: 0 }}>{r.rank}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{ fontSize: 11, fontWeight: 600, color: C.navy, margin: '0 0 3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</p>
              <div style={{ height: 4, borderRadius: 2, background: '#f1f5f9', overflow: 'hidden' }}>
                <div style={{ height: '100%', borderRadius: 2, background: tab === 'top' ? C.teal : C.red, width: `${Math.min(100, (Math.abs(r.value) / maxVal) * 100)}%`, transition: 'width 0.6s ease' }} />
              </div>
            </div>
            <div style={{ textAlign: 'right', flexShrink: 0 }}>
              <p style={{ fontSize: 12, fontWeight: 700, color: C.navy, margin: 0 }}>{r.formatted_value}</p>
              {r.pct_of_total != null && <p style={{ fontSize: 9, color: '#94a3b8', margin: 0 }}>{r.pct_of_total.toFixed(0)}% of total</p>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── SQL drawer ─────────────────────────────────────────────────────────────────
function SqlDrawer({ sqls }: { sqls: string[] }) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  if (!sqls.length) return null
  const sql = sqls[activeIdx] ?? ''
  return (
    <div style={{ borderRadius: 12, border: '1px solid #e8eef5', overflow: 'hidden' }}>
      <button onClick={() => setOpen(o => !o)} style={{ width: '100%', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8, background: '#f8fafc', border: 'none', cursor: 'pointer', textAlign: 'left' }}>
        <Code2 size={12} style={{ color: C.slate, flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 600, color: C.slate, flex: 1 }}>View source SQL ({sqls.length} {sqls.length === 1 ? 'query' : 'queries'})</span>
        {open ? <ChevronUp size={12} style={{ color: C.slate }} /> : <ChevronDown size={12} style={{ color: C.slate }} />}
      </button>
      {open && (
        <div style={{ background: '#0d1b2a' }}>
          {sqls.length > 1 && (
            <div style={{ display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              {sqls.map((_, i) => (
                <button key={i} onClick={() => setActiveIdx(i)} style={{ fontSize: 10, padding: '3px 9px', borderRadius: 20, border: 'none', cursor: 'pointer', background: i === activeIdx ? C.teal : 'rgba(255,255,255,0.1)', color: i === activeIdx ? 'white' : 'rgba(255,255,255,0.5)', fontWeight: 600 }}>
                  Query {i + 1}
                </button>
              ))}
            </div>
          )}
          <div style={{ padding: '14px 16px', overflowX: 'auto' }}>
            <SqlHighlight sql={sql} />
          </div>
        </div>
      )}
    </div>
  )
}

function SqlHighlight({ sql }: { sql: string }) {
  const segments: Array<{ text: string; isKw: boolean }> = []
  let lastIdx = 0
  for (const match of Array.from(sql.matchAll(/\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN|FULL\s+JOIN|JOIN|ON|AS|AND|OR|NOT|IN|LIKE|BETWEEN|LIMIT|OFFSET|COUNT|SUM|AVG|MAX|MIN|DISTINCT|WITH|CASE|WHEN|THEN|ELSE|END|NULL|IS|BY|ASC|DESC|UNION|ALL|CAST|COALESCE)\b/gi))) {
    if (match.index != null && match.index > lastIdx) {
      segments.push({ text: sql.slice(lastIdx, match.index), isKw: false })
    }
    segments.push({ text: match[0], isKw: true })
    lastIdx = (match.index ?? 0) + match[0].length
  }
  if (lastIdx < sql.length) segments.push({ text: sql.slice(lastIdx), isKw: false })
  return (
    <pre style={{ margin: 0, fontFamily: "'Fira Code', 'Cascadia Code', monospace", fontSize: 11, lineHeight: 1.7, color: '#a8b8cc', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {segments.length > 0 ? segments.map((s, i) =>
        s.isKw
          ? <span key={i} style={{ color: '#63b3ed', fontWeight: 700 }}>{s.text}</span>
          : <span key={i}>{s.text}</span>
      ) : sql}
    </pre>
  )
}

// ── Trend line computation ─────────────────────────────────────────────────────
function addTrendToData(data: Array<{name: string; value: number; [key:string]: unknown}>): typeof data {
  const vals = data.map(d => d.value).filter(v => !isNaN(v))
  if (vals.length < 3) return data
  const n = vals.length
  const sx = vals.reduce((s, _, i) => s + i, 0)
  const sy = vals.reduce((s, v) => s + v, 0)
  const sxy = vals.reduce((s, v, i) => s + i * v, 0)
  const sx2 = vals.reduce((s, _, i) => s + i * i, 0)
  const slope = (n * sxy - sx * sy) / (n * sx2 - sx * sx || 1)
  const intercept = (sy - slope * sx) / n
  return data.map((d, i) => ({ ...d, _trend: Math.round(slope * i + intercept) }))
}

// ── Real HTML table ────────────────────────────────────────────────────────────
function TableView({ chart }: { chart: AgentChart }) {
  const cols = chart.data.length > 0
    ? Object.keys(chart.data[0]).filter(k => !['_trend'].includes(k))
    : ['name', 'value']
  return (
    <div style={{ overflowX: 'auto', borderRadius: 8, border: '1px solid #e8eef5' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ background: C.navy }}>
            {cols.map(c => (
              <th key={c} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 700, color: 'rgba(255,255,255,0.85)', whiteSpace: 'nowrap', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {chart.data.map((row, i) => (
            <tr key={i} style={{ background: i % 2 === 0 ? 'white' : '#fafbfd', borderBottom: '1px solid #f1f5f9' }}>
              {cols.map(c => (
                <td key={c} style={{ padding: '7px 12px', color: '#374151', whiteSpace: 'nowrap' }}>
                  {String(row[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Chart renderer ─────────────────────────────────────────────────────────────
const TTP = { contentStyle: { fontSize: 11, borderRadius: 8, border: '1px solid #e8eef5', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' } }
const XAXIS = <XAxis dataKey="name" tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
const YAXIS = <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={40} />
const GRID  = <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />

function AgentChartView({ chart, colorIdx = 0 }: { chart: AgentChart; colorIdx?: number }) {
  const color = chart.color ?? PALETTE[colorIdx % PALETTE.length]
  const gid = `g${colorIdx}`

  if (!chart.data?.length) {
    return (
      <div style={{ background: 'white', borderRadius: 13, padding: 16, border: '1px solid #e8eef5', height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 12 }}>
        No data available
      </div>
    )
  }

  // For bar/line/area: add computed trend line to data
  const dataWithTrend = ['bar', 'line', 'area'].includes(chart.type)
    ? addTrendToData(chart.data as Array<{name:string;value:number;[key:string]:unknown}>)
    : chart.data

  const refLines = chart.reference_lines?.map((rl, i) => (
    <ReferenceLine key={i} y={rl.value} stroke={rl.color ?? '#94a3b8'} strokeDasharray="4 3"
      label={{ value: rl.label, fill: rl.color ?? '#94a3b8', fontSize: 9, position: 'insideTopRight' }} />
  )) ?? []

  return (
    <div style={{ background: C.card, borderRadius: 16, padding: '16px 16px 12px', border: '1px solid #e2eaf4', animation: 'fadeSlideIn 0.5s ease both', boxShadow: '0 2px 10px rgba(10,33,58,0.05)' }}>
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 4, height: 16, borderRadius: 2, background: `linear-gradient(180deg, ${color}, ${color}70)`, flexShrink: 0 }} />
        <p style={{ fontSize: 12, fontWeight: 700, color: C.ink, margin: 0 }}>{chart.title}</p>
      </div>

      {/* ── TABLE ── */}
      {chart.type === 'table' && <TableView chart={chart} />}

      {/* ── FORECAST ── */}
      {chart.type === 'forecast' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.25} /><stop offset="100%" stopColor={color} stopOpacity={0} /></linearGradient></defs>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Area type="monotone" dataKey="value" data={chart.data} stroke={color} fill={`url(#${gid})`} strokeWidth={2} dot={false} name="Historical" />
            {chart.projected_data?.length && <Line type="monotone" dataKey="value" data={chart.projected_data} stroke={color} strokeWidth={2} strokeDasharray="6 4" dot={{ r: 4, fill: color, strokeWidth: 0 }} name="Forecast" connectNulls />}
            {chart.projected_data?.length && <ReferenceLine x={chart.data[chart.data.length - 1]?.name} stroke="#94a3b8" strokeDasharray="3 3" label={{ value: 'Now', fill: '#94a3b8', fontSize: 9 }} />}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── COMBO ── */}
      {chart.type === 'combo' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={chart.data} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} /><Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
            {(chart.series ?? [{ key: 'value', type: 'bar' }]).map((s, i) => {
              const sc = s.color ?? PALETTE[(colorIdx + i) % PALETTE.length]
              return s.type === 'line'
                ? <Line key={s.key} type="monotone" dataKey={s.key} stroke={sc} strokeWidth={2.5} dot={false} />
                : <Bar key={s.key} dataKey={s.key} fill={sc} radius={[4, 4, 0, 0]} maxBarSize={40} opacity={0.88} />
            })}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── WATERFALL ── */}
      {chart.type === 'waterfall' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={chart.data} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} formatter={(v, name) => name === 'base' ? null : v} />
            <Bar dataKey="base" stackId="wf" fill="transparent" />
            <Bar dataKey="value" stackId="wf" radius={[4, 4, 0, 0]} maxBarSize={44}>
              {chart.data.map((d, i) => (
                <Cell key={i} fill={d.total ? C.navy : (d.value ?? 0) >= 0 ? C.green : C.red} />
              ))}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── SCATTER ── */}
      {chart.type === 'scatter' && (() => {
        const xKey = chart.x_key ?? 'x'
        const yKey = chart.y_key ?? 'y'
        const scatterData = chart.data.map(d => ({ ...d, x: Number(d[xKey] ?? d.value ?? 0), y: Number(d[yKey] ?? 0) }))
        return (
          <ResponsiveContainer width="100%" height={170}>
            <ScatterChart margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
              {GRID}
              <XAxis dataKey="x" type="number" name={xKey} tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} label={{ value: xKey, fill: '#94a3b8', fontSize: 9, position: 'insideBottom', offset: -2 }} />
              <YAxis dataKey="y" type="number" name={yKey} tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={40} label={{ value: yKey, fill: '#94a3b8', fontSize: 9, angle: -90, position: 'insideLeft' }} />
              <ZAxis range={[40, 40]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={TTP.contentStyle} formatter={(v, name) => [String(v), name] as [string, string]} />
              <Scatter data={scatterData} fill={color} fillOpacity={0.75} />
            </ScatterChart>
          </ResponsiveContainer>
        )
      })()}

      {/* ── BULLET ── */}
      {chart.type === 'bullet' && (() => {
        const target = chart.target_value ?? 100
        return (
          <div style={{ padding: '8px 0', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {chart.data.slice(0, 6).map((d, i) => {
              const v = d.value, t = target, mx = Math.max(t * 1.2, v * 1.1)
              const vp = Math.min(100, (v / mx) * 100), tp = Math.min(100, (t / mx) * 100)
              const att = t > 0 ? (v / t) * 100 : 0
              const bc = att >= 90 ? C.green : att >= 70 ? C.amber : C.red
              return (
                <div key={i}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: C.navy }}>{d.name}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: bc }}>{att.toFixed(0)}% of target</span>
                  </div>
                  <div style={{ position: 'relative', height: 16, borderRadius: 4, background: '#f1f5f9', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', left: 0, top: 2, bottom: 2, width: `${vp}%`, borderRadius: 3, background: bc, transition: 'width 0.8s ease' }} />
                    <div style={{ position: 'absolute', left: `${tp}%`, top: 0, bottom: 0, width: 2, background: C.navy, borderRadius: 1 }} title={`Target: ${t}`} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                    <span style={{ fontSize: 9, color: '#94a3b8' }}>Actual: {typeof v === 'number' ? v.toLocaleString() : v}</span>
                    <span style={{ fontSize: 9, color: '#94a3b8' }}>Target: {typeof t === 'number' ? t.toLocaleString() : t}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )
      })()}

      {/* ── BAR — switches to ComposedChart for trend line ── */}
      {chart.type === 'bar' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={dataWithTrend} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} maxBarSize={44} opacity={0.88}>
              <LabelList dataKey="value" position="top" style={{ fontSize: 8, fill: '#94a3b8' }} formatter={(v: number) => Math.abs(v) >= 1e6 ? `${(v/1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v/1e3).toFixed(1)}K` : v} />
            </Bar>
            {dataWithTrend.some(d => '_trend' in d) && (
              <Line type="linear" dataKey="_trend" stroke={`${color}70`} strokeWidth={1.5} strokeDasharray="5 3" dot={false} name="Trend" legendType="none" />
            )}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── AREA — with trend line ── */}
      {chart.type === 'area' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={dataWithTrend} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.28} /><stop offset="100%" stopColor={color} stopOpacity={0} /></linearGradient></defs>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Area type="monotone" dataKey="value" stroke={color} fill={`url(#${gid})`} strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
            {dataWithTrend.some(d => '_trend' in d) && (
              <Line type="linear" dataKey="_trend" stroke={`${color}70`} strokeWidth={1.5} strokeDasharray="5 3" dot={false} name="Trend" legendType="none" />
            )}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── LINE — with trend line ── */}
      {chart.type === 'line' && (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={dataWithTrend} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} dot={false} activeDot={{ r: 4, fill: color }} />
            {dataWithTrend.some(d => '_trend' in d) && (
              <Line type="linear" dataKey="_trend" stroke={`${color}70`} strokeWidth={1.5} strokeDasharray="5 3" dot={false} name="Trend" legendType="none" />
            )}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── PIE ── */}
      {chart.type === 'pie' && (
        <ResponsiveContainer width="100%" height={190}>
          <PieChart>
            <Pie data={chart.data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={72} labelLine={false} label={({ name, percent }) => percent > 0.06 ? `${name} ${(percent * 100).toFixed(0)}%` : ''}>
              {chart.data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Pie>
            <Tooltip {...TTP} />
            <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// ── Section content ────────────────────────────────────────────────────────────
function SectionContent({ section, sectionIdx }: { section: AgentSection; sectionIdx: number }) {
  const hasPerformers = (section.top_performers?.length ?? 0) > 0 || (section.bottom_performers?.length ?? 0) > 0
  const sourceSqls = Array.from(new Set(section.charts.map(c => c.source_sql).filter(Boolean) as string[]))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

      {/* Chapter cover — data_story large headline */}
      {section.data_story && (
        <div style={{
          background: `linear-gradient(135deg, ${C.navy} 0%, #0d2d52 60%, #0a2540 100%)`,
          borderRadius: 20, padding: '30px 32px 26px',
          position: 'relative', overflow: 'hidden',
          animation: 'fadeSlideIn 0.4s ease both',
          boxShadow: '0 8px 32px rgba(8,33,58,0.18)',
        }}>
          {/* Decorative circles */}
          <div style={{ position: 'absolute', top: -30, right: -30, width: 160, height: 160, borderRadius: '50%', background: `radial-gradient(circle, ${C.teal}18 0%, transparent 70%)`, pointerEvents: 'none' }} />
          <div style={{ position: 'absolute', bottom: -40, left: '25%', width: 180, height: 180, borderRadius: '50%', background: `radial-gradient(circle, ${C.violet}12 0%, transparent 70%)`, pointerEvents: 'none' }} />
          {/* Subtle dot grid */}
          <div style={{ position: 'absolute', inset: 0, backgroundImage: `radial-gradient(circle, rgba(255,255,255,0.04) 1px, transparent 1px)`, backgroundSize: '20px 20px', pointerEvents: 'none' }} />

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, position: 'relative' }}>
            <div style={{ width: 32, height: 32, borderRadius: 10, background: `${C.teal}25`, border: `1px solid ${C.teal}40`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.teal2, flexShrink: 0 }}>
              <SectionIcon name={section.icon} size={15} />
            </div>
            <span style={{ fontSize: 11, fontWeight: 700, color: C.teal2, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{section.label}</span>
          </div>
          <p style={{ fontSize: 22, fontWeight: 800, color: 'white', margin: 0, lineHeight: 1.3, position: 'relative', maxWidth: '88%', letterSpacing: '-0.01em' }}>{section.data_story}</p>
          {/* Bottom accent line */}
          <div style={{ position: 'absolute', bottom: 0, left: 32, width: 48, height: 3, borderRadius: '3px 3px 0 0', background: `linear-gradient(90deg, ${C.teal}, ${C.teal2})` }} />
        </div>
      )}

      {/* Key finding callout */}
      {section.key_finding && (
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: 14,
          background: `${C.teal}0c`,
          borderRadius: 14, padding: '15px 18px',
          border: `1px solid ${C.teal}30`,
          borderLeft: `4px solid ${C.teal}`,
          boxShadow: '0 2px 8px rgba(10,33,58,0.04)',
        }}>
          <Zap size={15} style={{ color: C.teal, marginTop: 1, flexShrink: 0 }} />
          <p style={{ fontSize: 14, fontWeight: 700, color: C.ink, margin: 0, lineHeight: 1.55 }}>{section.key_finding}</p>
        </div>
      )}

      {/* Narrative (show only if no data_story, or always if both present) */}
      {section.narrative && !section.data_story && (
        <div style={{ background: `linear-gradient(135deg, ${C.navy} 0%, #1a3a5c 100%)`, borderRadius: 16, padding: '18px 22px' }}>
          <p style={{ fontSize: 13, lineHeight: 1.78, color: 'rgba(255,255,255,0.84)', margin: 0 }}>{section.narrative}</p>
        </div>
      )}
      {section.narrative && section.data_story && (
        <p style={{ fontSize: 13, lineHeight: 1.78, color: '#4a5568', margin: 0, padding: '0 4px' }}>{section.narrative}</p>
      )}

      {/* Insight cards */}
      {section.insights && section.insights.length > 0 && <InsightCards insights={section.insights} />}

      {/* Section KPIs */}
      {section.kpis.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(145px, 1fr))', gap: 10 }}>
          {section.kpis.map((k, i) => <KpiCard key={i} kpi={k} idx={sectionIdx * 4 + i} />)}
        </div>
      )}

      {/* Performers + Charts */}
      {(hasPerformers || section.charts.length > 0) && (
        <div style={{ display: 'grid', gridTemplateColumns: hasPerformers && section.charts.length > 0 ? '1fr 1fr' : '1fr', gap: 14, alignItems: 'start' }}>
          {hasPerformers && (
            <PerformerPanel
              top={section.top_performers ?? []}
              bottom={section.bottom_performers ?? []}
              label={section.label}
            />
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {section.charts.map((ch, i) => (
              <AgentChartView key={i} chart={ch} colorIdx={sectionIdx * 4 + i} />
            ))}
          </div>
        </div>
      )}

      {/* SQL drawer */}
      {sourceSqls.length > 0 && <SqlDrawer sqls={sourceSqls} />}

      {/* Recommendation footer */}
      {section.recommendation && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          background: `linear-gradient(135deg, ${C.teal}08, ${C.teal2}04)`,
          borderRadius: 12, padding: '13px 18px',
          border: `1px solid ${C.teal}20`,
          boxShadow: '0 1px 6px rgba(0,180,216,0.06)',
        }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: `${C.teal}18`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <ArrowRight size={13} style={{ color: C.teal }} />
          </div>
          <p style={{ fontSize: 13, color: C.ink, margin: 0, lineHeight: 1.55 }}><strong style={{ color: C.teal, fontWeight: 700 }}>Recommended Action: </strong>{section.recommendation}</p>
        </div>
      )}

      {!section.narrative && !section.key_finding && !section.data_story && !section.kpis.length && !section.charts.length && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#94a3b8', fontSize: 13 }}>No content generated for this section.</div>
      )}
    </div>
  )
}

// ── Left rail ──────────────────────────────────────────────────────────────────
function LeftRail({ sections, active, onNav }: { sections: AgentSection[]; active: string; onNav: (id: string) => void }) {
  return (
    <div style={{ background: C.navy, display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 12, gap: 3, overflowY: 'auto' }}>
      {sections.map((s, idx) => {
        const isActive = s.id === active
        return (
          <button
            key={s.id}
            onClick={() => onNav(s.id)}
            title={s.label}
            style={{
              width: 54, height: 58, borderRadius: 10,
              border: isActive ? `1.5px solid ${C.teal}80` : `1.5px solid transparent`,
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
              background: isActive ? 'rgba(0,169,212,0.18)' : 'transparent',
              color: isActive ? C.teal2 : 'rgba(255,255,255,0.45)',
              cursor: 'pointer', transition: 'all 0.15s', gap: 3, position: 'relative',
            }}
          >
            {/* Index badge */}
            <span style={{ position: 'absolute', top: 4, left: 6, fontSize: 7, fontWeight: 800, color: isActive ? C.teal2 : 'rgba(255,255,255,0.25)', lineHeight: 1 }}>{idx + 1}</span>
            <SectionIcon name={s.icon} size={14} />
            <span style={{ fontSize: 8, letterSpacing: '0.03em', fontWeight: 600, maxWidth: 46, textAlign: 'center', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.2 }}>
              {s.label.length > 8 ? s.label.slice(0, 7) + '…' : s.label}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ── Loading skeleton ───────────────────────────────────────────────────────────
function SkeletonBox({ w = '100%', h = 16, radius = 6 }: { w?: string | number; h?: number; radius?: number }) {
  return (
    <div style={{ width: w, height: h, borderRadius: radius, background: 'linear-gradient(90deg, #e8eef5 25%, #f4f7fb 50%, #e8eef5 75%)', backgroundSize: '200% 100%', animation: 'shimmer 1.5s infinite' }} />
  )
}

function AgentLoader({ step, canvasName }: { step: string; canvasName: string }) {
  const LOADER_STEPS = [
    'Extracting widget data & column schemas…',
    'Fetching live SQL data for richer profiling…',
    'Profiling columns, ranking performers…',
    'Computing dimensional breakdowns…',
    'Detecting correlations & forecasting…',
    'Scoring executive health…',
    'Sending enriched context to Opus AI analyst…',
  ]
  const [stepIdx, setStepIdx] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setStepIdx(i => Math.min(i + 1, LOADER_STEPS.length - 1)), 950)
    return () => clearInterval(t)
  }, [])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }}>
      {/* Skeleton layout behind the progress overlay */}
      <div style={{ flex: 1, padding: 22, display: 'flex', flexDirection: 'column', gap: 16, opacity: 0.5, pointerEvents: 'none', overflow: 'hidden' }}>
        {/* KPI row skeleton */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} style={{ background: 'white', borderRadius: 13, padding: '14px 16px', border: '1px solid #e8eef5', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <SkeletonBox h={9} w="60%" />
              <SkeletonBox h={28} w="75%" />
              <SkeletonBox h={9} w="45%" />
            </div>
          ))}
        </div>
        {/* Brief skeleton */}
        <div style={{ background: `linear-gradient(135deg, ${C.navy}30, #1b3a5e30)`, borderRadius: 16, padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SkeletonBox h={10} w="25%" />
          <SkeletonBox h={12} />
          <SkeletonBox h={12} w="92%" />
          <SkeletonBox h={12} w="84%" />
        </div>
        {/* Section tabs skeleton */}
        <div style={{ display: 'flex', gap: 6 }}>
          {Array.from({ length: 6 }).map((_, i) => <SkeletonBox key={i} h={32} w={90} radius={20} />)}
        </div>
        {/* Chapter cover + charts skeleton */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ background: `${C.navy}20`, borderRadius: 18, padding: '28px 28px 24px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <SkeletonBox h={12} w="30%" />
            <SkeletonBox h={24} w="80%" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <SkeletonBox h={180} radius={13} />
            <SkeletonBox h={180} radius={13} />
          </div>
        </div>
      </div>

      {/* Progress overlay */}
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(238,242,247,0.88)', backdropFilter: 'blur(4px)', gap: 28 }}>
        <div style={{ position: 'relative', width: 76, height: 76 }}>
          <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: `linear-gradient(135deg, ${C.teal}, ${C.teal2})`, opacity: 0.15, animation: 'ipulse 1.6s ease-in-out infinite' }} />
          <div style={{ position: 'absolute', inset: 10, borderRadius: '50%', background: `linear-gradient(135deg, ${C.teal}, ${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Zap size={24} style={{ color: 'white' }} />
          </div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <p style={{ fontSize: 18, fontWeight: 800, color: C.navy, margin: '0 0 4px' }}>Opus Intelligence Agent</p>
          <p style={{ fontSize: 13, color: '#6b7c93', margin: '0 0 26px' }}>Deep analysis of <strong>{canvasName}</strong></p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9, maxWidth: 420, textAlign: 'left' }}>
            {LOADER_STEPS.map((s, i) => (
              <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 10, opacity: i <= stepIdx ? 1 : 0.28, transition: 'opacity 0.45s' }}>
                <div style={{ width: 20, height: 20, borderRadius: '50%', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: i < stepIdx ? C.green : i === stepIdx ? C.teal : '#e2e8f0', transition: 'background 0.4s' }}>
                  {i < stepIdx ? <CheckCircle2 size={12} style={{ color: 'white' }} /> : i === stepIdx ? <Loader2 size={11} style={{ color: 'white', animation: 'ispin 1s linear infinite' }} /> : null}
                </div>
                <span style={{ fontSize: 12, color: i <= stepIdx ? '#374151' : '#94a3b8', fontWeight: i === stepIdx ? 700 : 400, transition: 'all 0.4s' }}>{s}</span>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 11, color: '#94a3b8', marginTop: 20 }}>{step}</p>
        </div>
      </div>
    </div>
  )
}

// ── Inline copilot ─────────────────────────────────────────────────────────────
interface ChatMsg { role: 'user' | 'assistant'; text: string }
const PAGE_CHIPS = ['What are the key risks?', 'Which metrics need attention?', 'Summarize the top performers', 'What actions do you recommend?']

function InlineCopilot({ canvasId, projectId, canvasName }: { canvasId: string; projectId: string; canvasName: string }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])

  const send = async (text: string) => {
    if (!text.trim() || sending) return
    setMsgs(p => [...p, { role: 'user', text }]); setInput(''); setSending(true)
    try {
      const resp = await chatApi.send({ message: text, project_id: projectId, dashboard_id: canvasId, model_preference: 'opus' })
      setMsgs(p => [...p, { role: 'assistant', text: resp.data?.text ?? resp.data?.response ?? 'No response.' }])
    } catch { setMsgs(p => [...p, { role: 'assistant', text: 'Failed. Please retry.' }]) }
    finally { setSending(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#fafbfd' }}>
      <div style={{ padding: '13px 16px', borderBottom: '1px solid #e8eef5', background: 'white', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <MessageSquare size={13} style={{ color: 'white' }} />
          </div>
          <div>
            <p style={{ fontSize: 13, fontWeight: 700, color: C.navy, margin: 0 }}>AI Copilot <span style={{ fontSize: 9, fontWeight: 600, color: C.teal, background: `${C.teal}15`, padding: '1px 6px', borderRadius: 10, marginLeft: 4 }}>Opus</span></p>
            <p style={{ fontSize: 10, color: '#94a3b8', margin: 0 }}>{canvasName}</p>
          </div>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 12px 0', display: 'flex', flexDirection: 'column', gap: 9 }}>
        {msgs.length === 0 && (
          <div style={{ textAlign: 'center', padding: '28px 16px' }}>
            <div style={{ width: 40, height: 40, borderRadius: 14, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px' }}><Zap size={18} style={{ color: 'white' }} /></div>
            <p style={{ fontSize: 13, fontWeight: 600, color: C.navy, margin: '0 0 4px' }}>Ask about this report</p>
            <p style={{ fontSize: 11, color: '#94a3b8', margin: 0 }}>Powered by Claude Opus + 18 skills</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{ maxWidth: '86%', padding: '9px 13px', borderRadius: m.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px', background: m.role === 'user' ? C.teal : 'white', color: m.role === 'user' ? 'white' : '#374151', fontSize: 12, lineHeight: 1.65, border: m.role === 'assistant' ? '1px solid #e8eef5' : 'none' }}>{m.text}</div>
          </div>
        ))}
        {sending && (
          <div style={{ display: 'flex', gap: 5, padding: '10px 14px', background: 'white', borderRadius: 12, border: '1px solid #e8eef5', width: 'fit-content' }}>
            {[0, 1, 2].map(i => <span key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: C.teal, display: 'inline-block', animation: `cdot 1.2s ${i * 0.18}s ease-in-out infinite` }} />)}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {msgs.length === 0 && (
        <div style={{ padding: '8px 12px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {PAGE_CHIPS.map(c => (
            <button key={c} onClick={() => send(c)} style={{ fontSize: 10, padding: '5px 10px', borderRadius: 20, background: `${C.teal}10`, color: C.teal, border: `1px solid ${C.teal}25`, cursor: 'pointer', fontWeight: 600 }}>{c}</button>
          ))}
        </div>
      )}
      <div style={{ padding: '10px 12px', borderTop: '1px solid #e8eef5', background: 'white', flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }} placeholder="Ask about this report…" rows={2} style={{ flex: 1, resize: 'none', border: '1px solid #e8eef5', borderRadius: 10, padding: '8px 12px', fontSize: 12, outline: 'none', fontFamily: 'inherit', background: '#fafbfd', color: '#374151', lineHeight: 1.5 }} />
          <button onClick={() => send(input)} disabled={!input.trim() || sending} style={{ width: 36, height: 36, borderRadius: 10, background: C.teal, color: 'white', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, opacity: !input.trim() || sending ? 0.5 : 1 }}><Send size={14} /></button>
        </div>
      </div>
      <style>{`@keyframes cdot{0%,80%,100%{transform:scale(.7);opacity:.5}40%{transform:scale(1);opacity:1}}`}</style>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Main page
// ═══════════════════════════════════════════════════════════════════════════════
type AgentStatus = 'idle' | 'loading_canvas' | 'running' | 'done' | 'error'

export default function IntelligenceCanvasPage() {
  const { canvasId } = useParams<{ canvasId: string }>()
  const router = useRouter()

  const [canvas, setCanvas] = useState<Record<string, unknown> | null>(null)
  const [rawWidgets, setRawWidgets] = useState<unknown[]>([])
  const [shareToken, setShareToken] = useState('')
  const [projectId, setProjectId] = useState('')

  const [agentStatus, setAgentStatus] = useState<AgentStatus>('idle')
  const [agentStep, setAgentStep] = useState('')
  const [analysis, setAnalysis] = useState<ExecutiveAnalysis | null>(null)
  const [agentError, setAgentError] = useState<string | null>(null)
  const [rerunning, setRerunning] = useState(false)
  const [activeSection, setActiveSection] = useState('')
  const [briefExpanded, setBriefExpanded] = useState(true)

  // Merge fresh bulk-fetched rows into the widget array from the canvas API
  const mergeWidgetData = useCallback(
    (baseWidgets: unknown[], liveData: Array<{ widget_id: string; ok: boolean; rows?: Record<string, unknown>[]; columns?: string[]; labels?: string[]; values?: unknown[] }>) => {
      const byId = new Map(liveData.filter(d => d.ok).map(d => [d.widget_id, d]))
      return (baseWidgets as Record<string, unknown>[]).map(w => {
        const live = byId.get(String(w.id))
        if (!live) return w
        const existingCd = (w.chart_data as Record<string, unknown>) ?? {}
        return {
          ...w,
          chart_data: {
            ...existingCd,
            rows: live.rows ?? existingCd.rows ?? [],
            columns: live.columns ?? existingCd.columns ?? [],
            labels: live.labels ?? existingCd.labels ?? [],
            values: live.values ?? existingCd.values ?? [],
          },
        }
      })
    },
    [],
  )

  useEffect(() => {
    const load = async () => {
      setAgentStatus('loading_canvas')
      try {
        const [canvasResp, sharesResp] = await Promise.all([
          canvasApi.get(canvasId),
          shareApi.list(canvasId).catch(() => ({ data: null })),
        ])
        const detail = canvasResp.data?.dashboard ?? canvasResp.data
        let widgets: unknown[] = detail?.widgets ?? []
        setCanvas(detail); setProjectId(detail?.project_id ?? '')
        const sharesList: unknown[] = sharesResp.data?.shares ?? (Array.isArray(sharesResp.data) ? sharesResp.data : [])
        const token = sharesList.length ? ((sharesList[0] as Record<string,string>).token ?? '') : ''
        if (token) setShareToken(token)

        // Bulk-fetch live SQL rows for every widget before running the agent
        try {
          setAgentStep('Fetching live data for all widgets…')
          const liveResp = await intelligenceApi.fetchWidgetData(canvasId)
          const liveData = liveResp.data?.widget_data ?? []
          widgets = mergeWidgetData(widgets, liveData)
          const upgraded = liveData.filter(d => d.ok && (d.rows?.length ?? 0) > 0).length
          console.log(`[intelligence:page] bulk-fetch done  upgraded=${upgraded}/${liveData.length} widgets`)
        } catch (e) {
          console.warn('[intelligence:page] bulk-fetch failed, continuing with cached data:', e)
        }

        setRawWidgets(widgets)
        setAgentStatus('running')
        const result = await runIntelligenceAgent(
          { projectId: detail?.project_id ?? '', canvasId, canvasName: detail?.name ?? 'Report', widgets: widgets as never, shareToken: token || undefined },
          s => setAgentStep(s),
        )
        setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
        setAgentStatus('done')
      } catch (err) {
        setAgentError(err instanceof Error ? err.message : 'Failed to load')
        setAgentStatus('error')
      }
    }
    load()
  }, [canvasId, mergeWidgetData])

  const rerun = useCallback(async () => {
    if (!canvas || rerunning) return
    setRerunning(true); setAgentStatus('running'); setAgentError(null)
    try {
      // Re-fetch live data on regenerate too
      let widgets = rawWidgets
      try {
        const liveResp = await intelligenceApi.fetchWidgetData(canvasId)
        widgets = mergeWidgetData(rawWidgets, liveResp.data?.widget_data ?? [])
        setRawWidgets(widgets)
      } catch { /* keep stale data */ }

      const result = await runIntelligenceAgent(
        { projectId, canvasId, canvasName: String(canvas?.name ?? 'Report'), widgets: widgets as never, shareToken: shareToken || undefined },
        s => setAgentStep(s),
      )
      setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
      setAgentStatus('done')
    } catch {
      const fb = buildFallbackAnalysis(String(canvas?.name ?? 'Report'), rawWidgets as never)
      setAnalysis(fb); setActiveSection(fb.sections[0]?.id ?? '')
      setAgentStatus('done')
    } finally { setRerunning(false) }
  }, [canvas, rerunning, projectId, canvasId, rawWidgets, shareToken, mergeWidgetData])

  const currentSection = analysis?.sections.find(s => s.id === activeSection)
  const sectionIdx = analysis?.sections.findIndex(s => s.id === activeSection) ?? 0

  if (agentStatus === 'loading_canvas' || agentStatus === 'idle') {
    return (
      <div className="flex-1 flex items-center justify-center flex-col gap-3" style={{ background: C.bg }}>
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: C.teal }} />
        <p className="text-sm text-gray-400">Loading canvas…</p>
      </div>
    )
  }
  if (agentStatus === 'running') {
    return (
      <div className="flex-1 min-h-0 flex flex-col" style={{ background: C.bg, position: 'relative' }}>
        <AgentLoader step={agentStep} canvasName={String(canvas?.name ?? '…')} />
      </div>
    )
  }
  if (agentStatus === 'error' && !analysis) {
    return (
      <div className="flex-1 flex items-center justify-center flex-col gap-4" style={{ background: C.bg }}>
        <AlertCircle className="w-8 h-8 text-red-400" />
        <p className="text-sm text-gray-600">{agentError}</p>
        <div className="flex gap-3">
          <button onClick={() => router.back()} className="text-sm text-gray-500 hover:underline">Go back</button>
          <button onClick={rerun} className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white rounded-lg" style={{ background: C.teal }}><RefreshCw size={13} /> Retry</button>
        </div>
      </div>
    )
  }
  if (!analysis) return null

  return (
    <div className="flex-1 min-h-0" style={{ display: 'grid', gridTemplateColumns: '62px 1fr 360px', overflow: 'hidden' }}>

      {/* Left rail */}
      <LeftRail sections={analysis.sections} active={activeSection} onNav={setActiveSection} />

      {/* Main content */}
      <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }}>

        {/* Header */}
        <div style={{
          background: `linear-gradient(135deg, ${C.navy} 0%, #0d3060 100%)`,
          padding: '12px 22px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
          boxShadow: '0 2px 16px rgba(8,33,58,0.2)',
        }}>
          <button onClick={() => router.back()} style={{ padding: 6, borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex' }}><ChevronLeft size={16} /></button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
            <div style={{ width: 34, height: 34, borderRadius: 10, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: `0 4px 12px ${C.teal}50` }}><Zap size={15} style={{ color: 'white' }} /></div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h1 style={{ fontSize: 15, fontWeight: 700, color: 'white', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{analysis.title}</h1>
                <span style={{ fontSize: 9, fontWeight: 700, color: C.teal2, background: `${C.teal}25`, border: `1px solid ${C.teal}50`, borderRadius: 20, padding: '2px 8px', letterSpacing: '0.06em', flexShrink: 0 }}>OPUS AI</span>
              </div>
              <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{analysis.subtitle}</p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
            <HealthBadge score={analysis.health_score} color={analysis.health_color} />
            <button onClick={rerun} disabled={rerunning} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.1)', color: 'rgba(255,255,255,0.85)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
              <RefreshCw size={10} className={rerunning ? 'animate-spin' : ''} /> Regenerate
            </button>
          </div>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: 22 }}>

          {/* Global KPIs */}
          {analysis.kpis.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 20 }}>
              {analysis.kpis.map((k, i) => <KpiCard key={i} kpi={k} idx={i} />)}
            </div>
          )}

          {/* Morning brief — collapsible */}
          {analysis.morning_brief && (
            <div style={{
              background: `linear-gradient(135deg, ${C.navy} 0%, #0d3060 60%, #0a2540 100%)`,
              borderRadius: 18, marginBottom: 20, overflow: 'hidden',
              boxShadow: '0 4px 24px rgba(8,33,58,0.14)',
              border: '1px solid rgba(255,255,255,0.06)',
              position: 'relative',
            }}>
              {/* Subtle pattern */}
              <div style={{ position: 'absolute', inset: 0, backgroundImage: `radial-gradient(circle, rgba(0,180,216,0.06) 1px, transparent 1px)`, backgroundSize: '24px 24px', pointerEvents: 'none' }} />
              <button
                onClick={() => setBriefExpanded(e => !e)}
                style={{ width: '100%', padding: '14px 24px', display: 'flex', alignItems: 'center', gap: 10, background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left', position: 'relative' }}
              >
                <div style={{ width: 28, height: 28, borderRadius: 8, background: `${C.teal}20`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Sparkles size={13} style={{ color: C.teal2 }} />
                </div>
                <span style={{ fontSize: 11, fontWeight: 700, color: C.teal2, textTransform: 'uppercase', letterSpacing: '0.09em', flex: 1 }}>AI Morning Brief</span>
                <span style={{ fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.35)', background: 'rgba(255,255,255,0.08)', borderRadius: 20, padding: '2px 8px', marginRight: 6 }}>OPUS</span>
                {briefExpanded ? <ChevronUp size={13} style={{ color: 'rgba(255,255,255,0.35)', flexShrink: 0 }} /> : <ChevronDown size={13} style={{ color: 'rgba(255,255,255,0.35)', flexShrink: 0 }} />}
              </button>
              {briefExpanded && (
                <div style={{ padding: '0 24px 22px', position: 'relative' }}>
                  <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', marginBottom: 16 }} />
                  <p style={{ fontSize: 14, lineHeight: 1.82, color: 'rgba(255,255,255,0.88)', margin: 0, fontWeight: 400 }}>{analysis.morning_brief}</p>
                </div>
              )}
            </div>
          )}

          {/* Correlations */}
          <CorrelationCard correlations={analysis.correlations} />

          {/* Section tabs */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
            {analysis.sections.map(s => {
              const isActive = s.id === activeSection
              return (
                <button key={s.id} onClick={() => setActiveSection(s.id)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 7,
                    padding: '7px 15px', borderRadius: 22, fontSize: 12, fontWeight: 600,
                    border: isActive ? `1.5px solid ${C.teal}60` : '1.5px solid #dde5ef',
                    background: isActive ? `linear-gradient(135deg, ${C.teal}18, ${C.teal2}0a)` : C.card,
                    color: isActive ? C.teal : '#5a6b7c',
                    cursor: 'pointer', transition: 'all 0.18s',
                    boxShadow: isActive ? `0 2px 10px ${C.teal}22` : '0 1px 3px rgba(10,33,58,0.04)',
                  }}>
                  <span style={{ color: isActive ? C.teal : C.muted }}><SectionIcon name={s.icon} size={12} /></span>
                  {s.label}
                </button>
              )
            })}
          </div>

          {/* Active section */}
          {currentSection && <SectionContent section={currentSection} sectionIdx={sectionIdx} />}
        </div>
      </div>

      {/* Copilot */}
      <div style={{ borderLeft: '1px solid #e8eef5', overflow: 'hidden' }}>
        {shareToken
          ? <ExecutiveCopilot token={shareToken} canvasName={String(canvas?.name ?? 'Report')} pageName={activeSection} />
          : <InlineCopilot canvasId={canvasId} projectId={projectId} canvasName={String(canvas?.name ?? 'Report')} />}
      </div>

      <style>{`
        @keyframes fadeSlideIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes ipulse { 0%,100%{transform:scale(1);opacity:.15} 50%{transform:scale(1.3);opacity:.06} }
        @keyframes ispin  { to { transform: rotate(360deg); } }
        @keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
      `}</style>
    </div>
  )
}
