'use client'
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useParams, useRouter } from 'next/navigation'
import { canvasApi, shareApi, intelligenceApi, vlyApi, chatApi, dashboardApi } from '@/lib/api'
import { ExecutiveCopilot } from '@/components/report/ExecutiveCopilot'
import { IntelligenceCopilotPanel } from '@/components/report/IntelligenceCopilotPanel'
import { VlyExportModal } from '@/components/canvas/VlyExportModal'
import { ConnectLiveDbModal } from '@/components/canvas/ConnectLiveDbModal'
import type { CanvasWidgetData } from '@/components/canvas/CanvasWidget'
import {
  runIntelligenceAgent, buildFallbackAnalysis, runSectionAgent,
  type ExecutiveAnalysis, type AgentKPI, type AgentChart, type AgentSection,
  type InsightCard, type PerformerRow, type WidgetInput,
} from '@/lib/intelligenceAgent'
import {
  Zap, TrendingUp, TrendingDown, Minus, BarChart2, PieChart as PieChartIcon,
  Layers, Lightbulb, Target, Activity, ChevronLeft, Loader2, AlertCircle,
  MessageSquare, Send, Sparkles, RefreshCw, Users, AlertTriangle,
  CheckCircle2, GitMerge, Trophy, ThumbsDown, ArrowRight,
  Star, Info, DollarSign, ShoppingCart, Building, Globe, Database, Cpu,
  Settings, Calendar, Clock, FileText, Filter, MapPin, Percent, Shield,
  Award, Package, Briefcase, LineChart as LineChartIcon, ChevronDown, ChevronUp,
  Code2, Scale,
  X, Download, Maximize2, Play, Link, Copy, Printer, Pin,
  Columns, ChevronRight, CalendarRange, Edit3, Bookmark, BookmarkCheck, Wifi,
} from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  LineChart, Line, ComposedChart, ScatterChart, Scatter, LabelList,
  ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid,
  Legend, ReferenceLine, ZAxis,
  Treemap, FunnelChart, Funnel,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
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
function SectionIcon({ name, size = 15, color }: { name: string; size?: number; color?: string }) {
  const p = { size, ...(color ? { color } : {}) }
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
  // Show the ACTUAL value (full digits, thousands separators) — never K/M/B
  // abbreviation, which rounds away the real number.
  const fmt = (n: number) =>
    Math.abs(n % 1) < 1e-9
      ? Math.round(n).toLocaleString()
      : n.toLocaleString(undefined, { maximumFractionDigits: 2 })
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
  // Separate numeric suffix from unit suffix — keep unit short so it doesn't blow up card height
  const rawSuffix = String(kpi.value).match(/[^0-9.]+$/)?.[0] ?? ''
  const suffix = rawSuffix.length > 14 ? rawSuffix.slice(0, 14) : rawSuffix
  const animatable = !isNaN(raw) && isFinite(raw)
  const exactValue = `${prefix}${animatable ? raw.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(kpi.value)}${rawSuffix}`
  // Full displayed string (actual value, no abbreviation) → drives responsive sizing
  const valueStr = animatable ? `${prefix}${raw.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}` : String(kpi.value)
  const valueFont = valueStr.length <= 7 ? 24 : valueStr.length <= 10 ? 21 : valueStr.length <= 13 ? 18 : valueStr.length <= 17 ? 15 : 13
  // Explanation shown on hovering the (i): the AI-written one if present, else derived.
  const explanation = (kpi.explanation && kpi.explanation.trim())
    ? kpi.explanation.trim()
    : `${kpi.label}: ${exactValue}${kpi.trend_pct ? ` — ${kpi.trend_pct} vs the prior period` : ''}.`
  const [tip, setTip] = useState<{ x: number; y: number } | null>(null)
  const infoRef = useRef<HTMLButtonElement>(null)
  const showTip = () => {
    const r = infoRef.current?.getBoundingClientRect()
    if (r) setTip({ x: r.right, y: r.bottom })
  }
  return (
    <div className="intel-kpi-card" style={{
      background: C.card, borderRadius: 16, padding: '14px 16px',
      border: '1px solid #e2eaf4',
      boxShadow: '0 2px 12px rgba(10,33,58,0.06)',
      display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
      position: 'relative', overflow: 'hidden',
      // Fill the grid cell (both axes) so every card is the same size
      width: '100%', height: '100%', boxSizing: 'border-box', minHeight: 104,
    }}>
      {/* Colored top accent bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, borderRadius: '16px 16px 0 0', background: accentColor }} />
      {/* Label row — reserve 2 lines so the value/trend align across cards regardless of label length */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginTop: 6, gap: 4, minHeight: 26 }}>
        <p style={{
          fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em',
          margin: 0, lineHeight: 1.3, flex: 1,
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        } as React.CSSProperties}>{kpi.label}</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
          {kpi.sparkline_data && kpi.sparkline_data.length > 1 && <Sparkline data={kpi.sparkline_data} color={accentColor} />}
          <button
            ref={infoRef}
            onMouseEnter={showTip}
            onMouseLeave={() => setTip(null)}
            onFocus={showTip}
            onBlur={() => setTip(null)}
            aria-label="About this KPI"
            style={{ width: 16, height: 16, borderRadius: '50%', border: '1px solid #d8e2ee', background: tip ? accentColor : '#fff', color: tip ? '#fff' : C.muted, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'help', padding: 0, flexShrink: 0 }}
          >
            <Info size={10} />
          </button>
        </div>
      </div>
      {/* Value — capped at 24px so long values don't overflow. Hover shows the
          exact value (the card itself abbreviates large numbers as K/M/B). */}
      <p
        title={exactValue}
        style={{ fontSize: valueFont, fontWeight: 800, color: C.ink, margin: '6px 0 0', lineHeight: 1.05, letterSpacing: '-0.02em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', cursor: 'help' }}
      >
        {animatable ? <AnimatedCounter target={raw} prefix={prefix} suffix={suffix} /> : String(kpi.value)}
      </p>
      {/* Trend pill — always occupies same space even when absent */}
      <div style={{ marginTop: 6, minHeight: 22, display: 'flex', alignItems: 'center', gap: 5 }}>
        {kpi.trend_pct ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 3, background: `${tc}15`, borderRadius: 20, padding: '3px 7px', flexShrink: 0 }}>
              <TI size={10} style={{ color: tc }} />
              <span style={{ fontSize: 10, fontWeight: 700, color: tc }}>{kpi.trend_pct}</span>
            </div>
            <span style={{ fontSize: 9, color: C.muted, whiteSpace: 'nowrap' }}>vs prior</span>
          </>
        ) : null}
      </div>

      {/* Hover explanation (portal so it isn't clipped by the card's overflow:hidden) */}
      {tip && createPortal(
        <div style={{
          position: 'fixed', top: tip.y + 6,
          left: Math.max(12, Math.min(tip.x - 240, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 268)),
          zIndex: 9999, width: 252, background: '#0a213a', color: 'rgba(255,255,255,0.92)',
          border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10,
          boxShadow: '0 12px 34px rgba(10,33,58,0.30)', padding: '10px 12px',
          fontSize: 11.5, lineHeight: 1.55, pointerEvents: 'none',
        }}>
          <p style={{ margin: '0 0 4px', fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.5)' }}>{kpi.label}</p>
          {explanation}
        </div>,
        document.body,
      )}
    </div>
  )
}

// ── Confidence stars (Feature 11) ─────────────────────────────────────────────
function ConfidenceStars({ score }: { score?: number }) {
  if (!score) return null
  return (
    <div style={{ display: 'flex', gap: 1, marginTop: 4 }} title={`Confidence: ${score}/5`}>
      {[1, 2, 3, 4, 5].map(n => (
        <Star key={n} size={9} style={{ color: n <= score ? C.amber : '#d1d5db', fill: n <= score ? C.amber : 'none' }} />
      ))}
    </div>
  )
}



// ── Insight cards ──────────────────────────────────────────────────────────────
function InsightCards({ insights }: { insights: InsightCard[] }) {
  const [sectionOpen, setSectionOpen] = useState(false)

  if (!insights?.length) return null

  const negCount  = insights.filter(i => i.type === 'negative').length
  const warnCount = insights.filter(i => i.type === 'warning').length
  const posCount  = insights.filter(i => i.type === 'positive').length

  return (
    <div style={{ borderRadius: 14, border: '1px solid #e2eaf4', overflow: 'hidden', background: 'white' }}>

      {/* ── Collapsed header ── */}
      <button
        onClick={() => setSectionOpen(p => !p)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
          background: sectionOpen ? '#f8fafc' : 'white', border: 'none', cursor: 'pointer',
          borderBottom: sectionOpen ? '1px solid #e2eaf4' : 'none',
          transition: 'background 0.15s',
        }}
      >
        <Lightbulb size={13} style={{ color: C.amber, flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: C.navy }}>
          {insights.length} Insight{insights.length !== 1 ? 's' : ''}
        </span>
        <div style={{ display: 'flex', gap: 5, marginLeft: 2 }}>
          {negCount > 0 && (
            <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: `${C.red}15`, color: C.red, border: `1px solid ${C.red}25` }}>
              {negCount} critical
            </span>
          )}
          {warnCount > 0 && (
            <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: `${C.amber}15`, color: '#7a5900', border: `1px solid ${C.amber}30` }}>
              {warnCount} warning
            </span>
          )}
          {posCount > 0 && (
            <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: `${C.green}12`, color: '#065f46', border: `1px solid ${C.green}25` }}>
              {posCount} positive
            </span>
          )}
        </div>
        <ChevronDown size={13} style={{
          color: C.muted, marginLeft: 'auto', flexShrink: 0,
          transform: sectionOpen ? 'rotate(180deg)' : 'rotate(0deg)',
          transition: 'transform 0.25s ease',
        }} />
      </button>

      {/* ── Cards grid (animated expand) ── */}
      <div style={{
        maxHeight: sectionOpen ? 2400 : 0,
        overflow: 'hidden',
        transition: 'max-height 0.4s cubic-bezier(0.4,0,0.2,1)',
      }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(270px, 1fr))', gap: 12, padding: 14 }}>
      {insights.map((ins, i) => {
        const style = INSIGHT_TYPE_STYLE[ins.type] ?? INSIGHT_TYPE_STYLE.neutral
        const icon = INSIGHT_ICON_MAP[ins.icon] ?? <Lightbulb size={14} />
        const pulseClass = ins.type === 'negative' ? ' intel-insight-pulse' : ins.type === 'warning' ? ' intel-insight-pulse-warn' : ''

        return (
          <div key={i}
            className={`intel-insight${pulseClass}`}
            style={{
              borderRadius: 14, padding: '14px 16px',
              background: style.bg,
              border: `1px solid ${style.border}`,
              borderLeft: `3px solid ${style.icon}`,
              animation: `popIn 0.3s ${i * 0.07}s ease both`,
              display: 'flex', gap: 12, alignItems: 'flex-start',
              boxShadow: '0 1px 6px rgba(10,33,58,0.04)',
            }}
          >
            <div style={{
              width: 34, height: 34, borderRadius: 10,
              background: `${style.icon}18`, border: `1px solid ${style.icon}30`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, color: style.icon,
            }}>{icon}</div>
            <div style={{ minWidth: 0 }}>
              <p style={{ fontSize: 12, fontWeight: 700, color: C.ink, margin: '0 0 4px', lineHeight: 1.35 }} dangerouslySetInnerHTML={{ __html: mdHtml(ins.headline) }} />
              <p style={{ fontSize: 11, color: '#5a6b7c', margin: 0, lineHeight: 1.6 }} dangerouslySetInnerHTML={{ __html: mdHtml(ins.detail) }} />
              <ConfidenceStars score={ins.confidence} />
            </div>
          </div>
        )
      })}
        </div>
      </div>
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
          <div key={i} className="intel-performer-row" style={{ padding: '7px 16px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: i < rows.length - 1 ? '1px solid #f8fafc' : 'none' }}>
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

// ── SQL drawer with inline editor (Feature 4) ──────────────────────────────────
function SqlDrawer({ sqls, shareToken }: { sqls: string[]; shareToken?: string }) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const [editMode, setEditMode] = useState(false)
  const [editSql, setEditSql] = useState('')
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<{ rows: Record<string, unknown>[]; columns: string[] } | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  if (!sqls.length) return null
  const sql = sqls[activeIdx] ?? ''

  const enterEdit = () => { setEditSql(sql); setEditMode(true); setRunResult(null); setRunError(null) }
  const exitEdit  = () => { setEditMode(false); setRunResult(null); setRunError(null) }

  const runSql = async () => {
    if (!shareToken || !editSql.trim()) return
    setRunning(true); setRunError(null); setRunResult(null)
    try {
      const { analystApi } = await import('@/lib/api')
      const resp = await analystApi.query(shareToken, editSql.trim())
      const rows = (resp.data as Record<string, unknown>)?.rows as Record<string, unknown>[] ?? []
      const cols = (resp.data as Record<string, unknown>)?.columns as string[] ?? (rows[0] ? Object.keys(rows[0]) : [])
      setRunResult({ rows, columns: cols })
    } catch (e) {
      setRunError(e instanceof Error ? e.message : 'Query failed')
    } finally { setRunning(false) }
  }

  return (
    <div style={{ borderRadius: 12, border: '1px solid #e8eef5', overflow: 'hidden' }}>
      <button onClick={() => setOpen(o => !o)} style={{ width: '100%', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8, background: '#f8fafc', border: 'none', cursor: 'pointer', textAlign: 'left' }}>
        <Code2 size={12} style={{ color: C.slate, flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 600, color: C.slate, flex: 1 }}>View source SQL ({sqls.length} {sqls.length === 1 ? 'query' : 'queries'})</span>
        {open ? <ChevronUp size={12} style={{ color: C.slate }} /> : <ChevronDown size={12} style={{ color: C.slate }} />}
      </button>
      {open && (
        <div style={{ background: '#0d1b2a' }}>
          {/* query tab strip */}
          {sqls.length > 1 && (
            <div style={{ display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              {sqls.map((_, i) => (
                <button key={i} onClick={() => { setActiveIdx(i); exitEdit() }} style={{ fontSize: 10, padding: '3px 9px', borderRadius: 20, border: 'none', cursor: 'pointer', background: i === activeIdx ? C.teal : 'rgba(255,255,255,0.1)', color: i === activeIdx ? 'white' : 'rgba(255,255,255,0.5)', fontWeight: 600 }}>
                  Query {i + 1}
                </button>
              ))}
            </div>
          )}

          {/* read-only or edit view */}
          {!editMode ? (
            <div style={{ padding: '14px 16px', overflowX: 'auto', position: 'relative' }}>
              <SqlHighlight sql={sql} />
              {shareToken && (
                <button onClick={enterEdit} style={{ position: 'absolute', top: 10, right: 12, display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, padding: '4px 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', fontWeight: 600 }}>
                  <Edit3 size={10} /> Edit & Run
                </button>
              )}
            </div>
          ) : (
            <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <textarea
                value={editSql}
                onChange={e => setEditSql(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runSql() }}
                rows={6}
                style={{ width: '100%', background: '#0a1525', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, color: '#a8b8cc', fontFamily: "'Fira Code', monospace", fontSize: 11, padding: '10px 12px', resize: 'vertical', outline: 'none', boxSizing: 'border-box' }}
                spellCheck={false}
              />
              <div style={{ display: 'flex', gap: 6 }}>
                <button onClick={runSql} disabled={running || !editSql.trim()} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, padding: '5px 14px', borderRadius: 8, border: 'none', background: C.teal, color: 'white', cursor: 'pointer', fontWeight: 600, opacity: running ? 0.6 : 1 }}>
                  {running ? <Loader2 size={11} style={{ animation: 'ispin 1s linear infinite' }} /> : <Play size={11} />}
                  {running ? 'Running…' : 'Run (Ctrl+Enter)'}
                </button>
                <button onClick={exitEdit} style={{ fontSize: 11, padding: '5px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.15)', background: 'transparent', color: 'rgba(255,255,255,0.5)', cursor: 'pointer' }}>Cancel</button>
              </div>
              {runError && <p style={{ fontSize: 11, color: C.red, margin: 0 }}>{runError}</p>}
              {runResult && (
                <div style={{ overflowX: 'auto', borderRadius: 8, border: '1px solid rgba(255,255,255,0.1)', maxHeight: 260, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
                    <thead>
                      <tr style={{ background: 'rgba(255,255,255,0.06)' }}>
                        {runResult.columns.map(c => <th key={c} style={{ padding: '6px 10px', textAlign: 'left', color: 'rgba(255,255,255,0.6)', whiteSpace: 'nowrap', fontWeight: 600 }}>{c}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {runResult.rows.slice(0, 200).map((row, i) => (
                        <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                          {runResult!.columns.map(c => <td key={c} style={{ padding: '5px 10px', color: 'rgba(255,255,255,0.7)', whiteSpace: 'nowrap' }}>{String(row[c] ?? '')}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', padding: '4px 10px', margin: 0 }}>{runResult.rows.length} row(s)</p>
                </div>
              )}
            </div>
          )}
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
const YEAR_RE = /^(19|20)\d{2}$/

// Strip AI-appended placeholders like "(Sample)", "(Placeholder)", "(Demo)" from titles
function cleanChartTitle(title: string): string {
  return title.replace(/\s*\((sample|placeholder|demo|example|mock|no data|n\/a|test)\)\s*$/i, '').trim()
}

function MiniSparkline({ values }: { values: number[]; color?: string }) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values)
  const range = max - min || 1
  const W = 68, H = 28
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W
    const y = H - ((v - min) / range) * (H - 6) - 3
    return [x.toFixed(1), y.toFixed(1)] as [string, string]
  })
  const polyPts = pts.map(p => p.join(',')).join(' ')
  const isUp = values[values.length - 1] >= values[0]
  const lineColor = isUp ? '#16a34a' : '#dc2626'
  const areaPath = `M${pts[0].join(',')} ${pts.slice(1).map(p => `L${p.join(',')}`).join(' ')} L${W},${H} L0,${H} Z`
  const [lx, ly] = pts[pts.length - 1]
  return (
    <svg width={W} height={H} style={{ display: 'block', overflow: 'visible', flexShrink: 0 }}>
      <defs>
        <linearGradient id={`spk-${isUp ? 'up' : 'dn'}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity={0.18} />
          <stop offset="100%" stopColor={lineColor} stopOpacity={0.01} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#spk-${isUp ? 'up' : 'dn'})`} />
      <polyline points={polyPts} fill="none" stroke={lineColor} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={2.5} fill={lineColor} />
    </svg>
  )
}

function BulletBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(1, Math.abs(value) / max) : 0
  const color = '#0e7490'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 80 }}>
      <div style={{ flex: 1, height: 5, background: '#e2e8f0', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct * 100}%`, height: '100%', background: color, borderRadius: 3 }} />
      </div>
    </div>
  )
}

function TableView({ chart }: { chart: AgentChart }) {
  const [sortCol, setSortCol] = React.useState<string | null>(null)
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('desc')
  const [search, setSearch] = React.useState('')

  // Generic synthetic keys ('name'/'value') are pushed to the END so that when a
  // real column holds the same data, dedup keeps the REAL column name (e.g. "City",
  // "Client Count") and drops the generic "value"/"name" — not the other way round.
  const _rawCols0 = chart.data.length > 0
    ? Object.keys(chart.data[0]).filter(k => k !== '_trend')
    : ['name', 'value']
  const _GENERIC = new Set(['name', 'value'])
  const rawCols = [
    ..._rawCols0.filter(k => !_GENERIC.has(k)),
    ..._rawCols0.filter(k => _GENERIC.has(k)),
  ]

  // Drop columns where every value is identical to a prior column (e.g. NAME == CUSTOMER)
  const dedupedCols = rawCols.filter((col, idx) =>
    !rawCols.slice(0, idx).some(prev =>
      chart.data.length > 0 &&
      chart.data.every(row => String(row[col] ?? '') === String(row[prev] ?? ''))
    )
  )

  // Drop columns where every value would render as empty / junk:
  //   • null / undefined / empty string
  //   • NaN / NA / null / undefined tokens (the AI's unpopulated "value" column)
  //   • numeric 0  (fmtNum returns "—" for 0)
  // Genuine non-numeric text (e.g. names) is always kept.
  const meaningfulCols = dedupedCols.filter(col =>
    chart.data.length === 0 ||
    chart.data.some(row => {
      const v = row[col]
      if (v === null || v === undefined) return false
      const s = String(v).trim()
      if (s === '' || /^(nan|na|n\/a|null|undefined)$/i.test(s)) return false
      const n = Number(v)
      if (!isNaN(n)) return n !== 0   // numeric: meaningful only when non-zero
      return true                      // genuine non-numeric text → meaningful
    })
  )

  // Detect year-like columns (2020–2030) to build sparkline
  const yearCols = meaningfulCols.filter(c => YEAR_RE.test(c))
  const hasTrend = yearCols.length >= 2
  const displayCols = hasTrend ? meaningfulCols.filter(c => !YEAR_RE.test(c)) : meaningfulCols

  // Classify columns
  const isYoyCol = (c: string) =>
    /yoy|pct|percent|change|growth|vs\s/i.test(c) || c.includes('%')
  const isNumericCol = (c: string) =>
    chart.data.slice(0, 8).filter(r => r[c] !== null && r[c] !== undefined && r[c] !== '').some(r => !isNaN(Number(r[c])))

  // Primary value column for bullet bars (first numeric non-YOY non-name column)
  const valueCol = displayCols.find(c =>
    isNumericCol(c) && !isYoyCol(c) && !['name', 'customer', 'label'].includes(c.toLowerCase())
  )
  const maxValue = valueCol
    ? Math.max(...chart.data.map(r => Math.abs(Number(r[valueCol] ?? 0))).filter(v => !isNaN(v) && isFinite(v)))
    : 0
  const showBullet = !!valueCol && maxValue > 0 && !hasTrend

  const filteredData = React.useMemo(() => {
    if (!search.trim()) return chart.data
    const q = search.toLowerCase()
    return chart.data.filter(row =>
      Object.values(row).some(v => String(v ?? '').toLowerCase().includes(q))
    )
  }, [chart.data, search])

  const sortedData = React.useMemo(() => {
    if (!sortCol) return filteredData
    return [...filteredData].sort((a, b) => {
      const av = a[sortCol] ?? '', bv = b[sortCol] ?? ''
      const an = Number(av), bn = Number(bv)
      const cmp = !isNaN(an) && !isNaN(bn) ? an - bn : String(av).localeCompare(String(bv))
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [filteredData, sortCol, sortDir])

  const total = sortedData.length

  const toggleSort = (col: string) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('desc') }
  }

  const fmtNum = (num: number, yoy: boolean): React.ReactNode => {
    if (num === 0) return <span style={{ color: '#d1d5db' }}>—</span>
    if (yoy) {
      // Values between -2 and 2 are typically decimal rates (0.08 → 8%)
      const pct = Math.abs(num) <= 2 ? num * 100 : num
      const pos = pct >= 0
      const col = pos ? '#16a34a' : '#dc2626'
      const label = Math.abs(pct) >= 100 ? `${pct.toFixed(0)}%` : Math.abs(pct) < 0.1 ? `${pct.toFixed(2)}%` : `${pct.toFixed(1)}%`
      return (
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 3,
          color: col, fontWeight: 600, fontSize: 11,
          background: pos ? 'rgba(22,163,74,0.08)' : 'rgba(220,38,38,0.08)',
          borderRadius: 4, padding: '2px 6px', whiteSpace: 'nowrap',
        }}>
          {pos ? '▲' : '▼'}&nbsp;{pos ? '+' : ''}{label}
        </span>
      )
    }
    // Show the EXACT value, comma-separated — never an abbreviated 1.2M / 3.4B
    // form (tables should be precise; hover shows full unrounded value too).
    return (
      <span title={String(num)} style={{ color: '#0f172a', fontWeight: 600 }}>
        {num.toLocaleString(undefined, { maximumFractionDigits: 2 })}
      </span>
    )
  }

  const fmtCell = (val: unknown, col: string): React.ReactNode => {
    if (val === null || val === undefined || val === '') return <span style={{ color: '#d1d5db' }}>—</span>
    // NaN / NA / null-like tokens render as an em-dash, never the literal text.
    if (/^(nan|na|n\/a|null|undefined)$/i.test(String(val).trim())) return <span style={{ color: '#d1d5db' }}>—</span>
    const yoy = isYoyCol(col)
    if (typeof val === 'string' && val.endsWith('%')) {
      const n = parseFloat(val)
      return !isNaN(n) ? fmtNum(n / 100, true) : <span style={{ color: '#0f172a' }}>{val}</span>
    }
    const num = Number(val)
    if (!isNaN(num)) return fmtNum(num, yoy)
    return <span style={{ color: '#0f172a' }}>{String(val)}</span>
  }

  // FIXED table styling so every table on the page looks identical regardless of
  // column count. (These used to scale with totalVisualCols, which rendered wide
  // tables compact and narrow tables tall/large — i.e. two different looks.)
  const totalVisualCols =
    displayCols.length + (hasTrend ? 1 : 0) + (showBullet ? 1 : 0)
  const firstColMaxW = 260
  const cellFontSize = 11
  const cellPadV = 7   // px
  const cellPadH = 12
  const cellPad  = `${cellPadV}px ${cellPadH}px`

  // Sparkline (hasTrend) rows are taller, so show fewer of them; otherwise a fixed budget.
  const maxVisibleRows = hasTrend ? 7 : 11
  const rowPxH    = hasTrend ? 44 : 34
  const headerPxH = 42
  // Hard cap so no single table dominates the page; users scroll for the rest.
  const tableMaxHeight = Math.min(
    maxVisibleRows * rowPxH + headerPxH,
    hasTrend ? 360 : 440,
  )

  const thBase: React.CSSProperties = {
    padding: cellPad, fontWeight: 700, color: '#64748b',
    whiteSpace: 'nowrap', fontSize: Math.max(9, cellFontSize - 1), letterSpacing: '0.05em',
    textTransform: 'uppercase', cursor: 'pointer', userSelect: 'none',
    background: '#f8fafc', position: 'sticky', top: 0,
  }

  return (
    <div style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid #e2e8f0', boxShadow: '0 2px 12px rgba(0,0,0,0.06)', background: 'white', width: '100%', maxWidth: '100%', boxSizing: 'border-box' }}>

      {/* ── Header bar ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px', background: C.navy }}>
        <span style={{ flex: 1, minWidth: 0, fontSize: 11, fontWeight: 700, color: 'white', textTransform: 'uppercase', letterSpacing: '0.08em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {cleanChartTitle(chart.title)}
        </span>
        {/* Search */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, background: 'rgba(255,255,255,0.1)', borderRadius: 7, padding: '4px 9px' }}>
          <svg width={9} height={9} viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.45)" strokeWidth={2.5}>
            <circle cx={11} cy={11} r={8} /><path d="m21 21-4.35-4.35" />
          </svg>
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Filter…"
            style={{ background: 'none', border: 'none', outline: 'none', color: 'white', fontSize: 10, width: 80, '::placeholder': { color: 'rgba(255,255,255,0.4)' } } as React.CSSProperties}
          />
        </div>
        {/* Row count */}
        <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.5)', background: 'rgba(255,255,255,0.08)', borderRadius: 10, padding: '3px 8px', whiteSpace: 'nowrap' }}>
          {total} row{total !== 1 ? 's' : ''}
        </span>
      </div>

      {/* ── Scrollable table ── */}
      <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: tableMaxHeight }}>
        <table style={{ width: '100%', minWidth: Math.max(320, totalVisualCols * 90), borderCollapse: 'collapse', fontSize: cellFontSize }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e2e8f0' }}>
              {displayCols.map((c, ci) => (
                <th key={c} onClick={() => toggleSort(c)}
                  style={{
                    ...thBase,
                    textAlign: ci === 0 ? 'left' : isYoyCol(c) ? 'center' : 'right',
                    color: sortCol === c ? C.teal : '#64748b',
                    // non-first columns shrink to content; first column is capped
                    width: ci === 0 ? undefined : '1%',
                    minWidth: ci === 0 ? undefined : isNumericCol(c) || isYoyCol(c) ? 80 : 70,
                    maxWidth: ci === 0 ? firstColMaxW : undefined,
                    ...(ci === 0 ? { position: 'sticky', left: 0, zIndex: 3, boxShadow: '2px 0 4px rgba(0,0,0,0.04)' } : {}),
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    {c.replace(/_/g, ' ')}
                    {sortCol === c
                      ? <span>{sortDir === 'desc' ? ' ↓' : ' ↑'}</span>
                      : <span style={{ color: '#d1d5db', fontSize: 8 }}>⇅</span>}
                  </span>
                </th>
              ))}
              {hasTrend && (
                <th style={{ ...thBase, textAlign: 'left', zIndex: 2 }}>
                  Trend&nbsp;{yearCols[0]}–{yearCols[yearCols.length - 1]}
                </th>
              )}
              {showBullet && (
                <th style={{ ...thBase, textAlign: 'left', zIndex: 2 }}>
                  Distribution
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {sortedData.map((row, i) => {
              const trendVals = hasTrend ? yearCols.map(y => Number(row[y] ?? 0)) : []
              const trendUp = trendVals.length >= 2 && trendVals[trendVals.length - 1] >= trendVals[0]
              const rowValue = valueCol ? Number(row[valueCol] ?? 0) : 0
              const zebraBase = i % 2 === 0 ? 'white' : '#f8fafc'
              return (
                <tr key={i}
                  style={{ borderBottom: '1px solid #f1f5f9', background: zebraBase }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#f0f9ff' }}
                  onMouseLeave={e => { e.currentTarget.style.background = zebraBase }}
                >
                  {displayCols.map((c, ci) => (
                    <td key={c} style={{
                      padding: cellPad,
                      textAlign: ci === 0 ? 'left' : isYoyCol(c) ? 'center' : 'right',
                      whiteSpace: 'nowrap',
                      ...(ci === 0 ? {
                        position: 'sticky', left: 0, zIndex: 1,
                        background: 'inherit',
                        borderLeft: '3px solid transparent',
                        fontWeight: 600, color: C.ink,
                        boxShadow: '2px 0 4px rgba(0,0,0,0.04)',
                        maxWidth: firstColMaxW, overflow: 'hidden', textOverflow: 'ellipsis',
                      } : {}),
                    }}>
                      {fmtCell(row[c], c)}
                    </td>
                  ))}
                  {hasTrend && (
                    <td style={{ padding: cellPad }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                        <MiniSparkline values={trendVals} />
                        <span style={{ fontSize: 9, fontWeight: 700, color: trendUp ? '#16a34a' : '#dc2626', minWidth: 14 }}>
                          {trendUp ? '▲' : '▼'}
                        </span>
                      </div>
                    </td>
                  )}
                  {showBullet && valueCol && (
                    <td style={{ padding: cellPad, minWidth: 90 }}>
                      <BulletBar value={rowValue} max={maxValue} />
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>

        {total === 0 && (
          <div style={{ padding: '32px', textAlign: 'center', color: '#94a3b8', fontSize: 12 }}>
            No rows match &ldquo;{search}&rdquo;
          </div>
        )}
      </div>

      {/* ── Footer ── */}
      <div style={{ padding: '7px 14px', background: '#f8fafc', borderTop: '1px solid #f1f5f9', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 10, color: '#94a3b8' }}>
          {search ? `${total} of ${chart.data.length} rows` : `${total} rows`}
          {!search && total > maxVisibleRows && (
            <span style={{ marginLeft: 6, color: C.teal, fontWeight: 600 }}>
              · scroll to see {total - maxVisibleRows} more
            </span>
          )}
          {hasTrend && <span style={{ marginLeft: 8, color: '#cbd5e1' }}>· sparkline: {yearCols[0]}–{yearCols[yearCols.length - 1]}</span>}
        </span>
        {chart.insight && (
          <span style={{ fontSize: 10, color: '#64748b', fontStyle: 'italic' }}>{chart.insight}</span>
        )}
      </div>
    </div>
  )
}

// ── Chart renderer ─────────────────────────────────────────────────────────────
// Exact value for tooltips — full precision with thousands separators (axis ticks
// and inline labels stay abbreviated; hover always shows the real number).
const fmtExact = (v: unknown): React.ReactNode =>
  typeof v === 'number' && isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : (v as React.ReactNode)
const TTP = {
  contentStyle: { fontSize: 11, borderRadius: 8, border: '1px solid #e8eef5', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' },
  formatter: (v: unknown) => fmtExact(v),
}
const fmtTick = (v: number) =>
  Math.abs(v) >= 1e9 ? `${(v/1e9).toFixed(1)}B`
  : Math.abs(v) >= 1e6 ? `${(v/1e6).toFixed(1)}M`
  : Math.abs(v) >= 1e3 ? `${(v/1e3).toFixed(0)}K`
  : String(v)
const XAXIS = <XAxis dataKey="name" tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
const YAXIS = <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={46} tickFormatter={fmtTick} />
const GRID  = <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />

// Chart types that can be swapped freely (same {name,value} data shape)
const CHART_SWITCH_OPTS: Record<string, string[]> = {
  bar: ['bar', 'line', 'area', 'treemap', 'funnel', 'radar'],
  line: ['line', 'bar', 'area'],
  area: ['area', 'bar', 'line'],
  pie: ['pie', 'donut', 'bar', 'treemap'],
  donut: ['donut', 'pie', 'bar', 'treemap'],
  treemap: ['treemap', 'bar', 'pie'],
  funnel: ['funnel', 'bar'],
  radar: ['radar', 'bar'],
}
const CHART_TYPE_ICONS: Record<string, string> = {
  bar: '▌', line: '∿', area: '△', pie: '◕', donut: '◍', treemap: '▦', funnel: '▽', radar: '✶',
}

type DrillFn = (segment: string) => void

function AgentChartView({
  chart, colorIdx = 0,
  onDrill, onFullscreen,
  annotations,
  onAnnotate,
}: {
  chart: AgentChart; colorIdx?: number
  onDrill?: DrillFn
  onFullscreen?: () => void
  annotations?: Record<string, string>
  onAnnotate?: (pointName: string) => void
}) {
  const [viewType, setViewType] = useState(chart.type)
  const [insightVisible, setInsightVisible] = useState(false)
  const [showRawData, setShowRawData] = useState(false)
  const switchOpts = CHART_SWITCH_OPTS[chart.type] ?? []

  const color = chart.color ?? PALETTE[colorIdx % PALETTE.length]
  const gid = `g${colorIdx}`

  if (!chart.data?.length) {
    return (
      <div style={{ background: '#f8fafc', borderRadius: 10, padding: '10px 14px', border: '1px dashed #e2e8f0', display: 'flex', alignItems: 'center', gap: 8, color: '#94a3b8', fontSize: 11 }}>
        <span style={{ fontSize: 16, lineHeight: 1 }}>—</span>
        <span><strong style={{ color: '#64748b' }}>{chart.title}</strong> · No data available</span>
      </div>
    )
  }

  // For bar/line/area: add computed trend line to data
  const dataWithTrend = ['bar', 'line', 'area'].includes(viewType)
    ? addTrendToData(chart.data as Array<{name:string;value:number;[key:string]:unknown}>)
    : chart.data

  const refLines = chart.reference_lines?.map((rl, i) => (
    <ReferenceLine key={i} y={rl.value} stroke={rl.color ?? '#94a3b8'} strokeDasharray="4 3"
      label={{ value: rl.label, fill: rl.color ?? '#94a3b8', fontSize: 9, position: 'insideTopRight' }} />
  )) ?? []

  const chartRef = React.useRef<HTMLDivElement>(null)
  const downloadPng = () => {
    const svgEl = chartRef.current?.querySelector('svg')
    if (!svgEl) return
    const serializer = new XMLSerializer()
    const svgStr = serializer.serializeToString(svgEl)
    const rect = svgEl.getBoundingClientRect()
    const canvas = document.createElement('canvas')
    canvas.width = rect.width * 2; canvas.height = rect.height * 2
    const ctx = canvas.getContext('2d')
    const img = new Image()
    img.onload = () => {
      ctx?.drawImage(img, 0, 0, canvas.width, canvas.height)
      const a = document.createElement('a')
      a.href = canvas.toDataURL('image/png')
      a.download = `${chart.title.replace(/[^a-z0-9]/gi, '_')}.png`
      a.click()
      URL.revokeObjectURL(img.src)
    }
    const blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' })
    img.src = URL.createObjectURL(blob)
  }

  const pinnedAnnotations = annotations
    ? Object.entries(annotations).map(([k, v]) => ({ point: k, note: v }))
    : []

  return (
    <div className="intel-chart-card" style={{ background: C.card, borderRadius: 16, padding: '16px 16px 12px', border: '1px solid #e2eaf4', boxShadow: '0 2px 10px rgba(10,33,58,0.05)', width: '100%', height: '100%', minWidth: 0, boxSizing: 'border-box', display: 'flex', flexDirection: 'column' }}>
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 4, height: 16, borderRadius: 2, background: `linear-gradient(180deg, ${color}, ${color}70)`, flexShrink: 0 }} />
        <p style={{ fontSize: 12, fontWeight: 700, color: C.ink, margin: 0, flex: 1 }}>{cleanChartTitle(chart.title)}</p>
        {/* Chart type switcher + controls */}
        <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'center' }}>
          {switchOpts.length > 1 && (
            <div style={{ display: 'flex', gap: 1, background: '#f1f5f9', borderRadius: 8, padding: 2 }}>
              {switchOpts.map(t => (
                <button key={t} onClick={() => setViewType(t)} title={`View as ${t}`} style={{
                  padding: '3px 8px', borderRadius: 6, border: 'none', cursor: 'pointer',
                  background: viewType === t ? C.navy : 'transparent',
                  color: viewType === t ? 'white' : '#94a3b8',
                  fontSize: 11, fontWeight: 700, transition: 'all 0.15s', lineHeight: 1,
                }}>{CHART_TYPE_ICONS[t] ?? t.slice(0,3)}</button>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            {/* ⓘ Insight tooltip */}
            {chart.insight && (
              <div style={{ position: 'relative' }}
                onMouseEnter={() => setInsightVisible(true)}
                onMouseLeave={() => setInsightVisible(false)}
              >
                <button title="Chart insight" style={{
                  width: 20, height: 20, borderRadius: '50%', border: `1.5px solid ${C.teal}`,
                  background: insightVisible ? C.teal : 'white', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11, fontWeight: 700, lineHeight: 1,
                  color: insightVisible ? 'white' : C.teal,
                  transition: 'all 0.15s', flexShrink: 0,
                }}>i</button>
                {insightVisible && (
                  <div style={{
                    position: 'absolute', bottom: 'calc(100% + 8px)', right: 0,
                    width: 240, padding: '10px 13px',
                    background: C.navy, color: 'white',
                    borderRadius: 10, fontSize: 11, lineHeight: 1.55,
                    boxShadow: '0 8px 24px rgba(10,33,58,0.25)',
                    zIndex: 200, pointerEvents: 'none',
                    animation: 'fadeInUp 0.18s ease both',
                  }}>
                    <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', opacity: 0.6, marginBottom: 5 }}>Chart Insight</div>
                    {chart.insight}
                    {/* Caret */}
                    <div style={{
                      position: 'absolute', bottom: -6, right: 8,
                      width: 12, height: 12, background: C.navy,
                      transform: 'rotate(45deg)', borderRadius: 2,
                    }} />
                  </div>
                )}
              </div>
            )}
            {onAnnotate && <button onClick={() => onAnnotate('_chart')} title="Add note" className="intel-toolbar-btn" style={{ padding: 4, borderRadius: 6, border: '1px solid #e8eef5', background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><Pin size={10} /></button>}
            {chart.type !== 'table' && (
              <button
                onClick={() => setShowRawData(v => !v)}
                title={showRawData ? 'Hide raw data' : 'Show raw data table'}
                className="intel-toolbar-btn"
                style={{
                  padding: '3px 7px', borderRadius: 6, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3,
                  border: `1px solid ${showRawData ? C.teal : '#e8eef5'}`,
                  background: showRawData ? `${C.teal}12` : 'white',
                  color: showRawData ? C.teal : C.slate,
                  fontSize: 9, fontWeight: 700, transition: 'all 0.15s',
                }}
              >
                <Database size={9} />
                {showRawData ? 'Hide' : 'Data'}
              </button>
            )}
            <button onClick={downloadPng} title="Download PNG" className="intel-toolbar-btn" style={{ padding: 4, borderRadius: 6, border: '1px solid #e8eef5', background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><Download size={10} /></button>
            {onFullscreen && <button onClick={onFullscreen} title="Fullscreen" className="intel-toolbar-btn" style={{ padding: 4, borderRadius: 6, border: '1px solid #e8eef5', background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><Maximize2 size={10} /></button>}
          </div>
        </div>
      </div>
      {/* annotation pins display */}
      {pinnedAnnotations.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 8 }}>
          {pinnedAnnotations.map(({ point, note }) => (
            <div key={point} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, padding: '3px 8px', borderRadius: 20, background: `${C.amber}15`, border: `1px solid ${C.amber}30`, color: '#7a5900' }}>
              <Pin size={8} style={{ color: C.amber }} />{note.length > 40 ? note.slice(0, 40) + '…' : note}
            </div>
          ))}
        </div>
      )}
      <div ref={chartRef} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>

      {/* ── TABLE ── */}
      {viewType === 'table' && <TableView chart={chart} />}

      {/* ── FORECAST ── */}
      {viewType === 'forecast' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
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
      {viewType === 'combo' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={chart.data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
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
      {viewType === 'waterfall' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={chart.data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} formatter={(v, name) => name === 'base' ? null : fmtExact(v)} />
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
      {viewType === 'scatter' && (() => {
        const xKey = chart.x_key ?? 'x'
        const yKey = chart.y_key ?? 'y'
        const scatterData = chart.data.map(d => ({ ...d, x: Number(d[xKey] ?? d.value ?? 0), y: Number(d[yKey] ?? 0) }))
        return (
          <ResponsiveContainer width="100%" height={240}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
              {GRID}
              <XAxis dataKey="x" type="number" name={xKey} tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} label={{ value: xKey, fill: '#94a3b8', fontSize: 9, position: 'insideBottom', offset: -2 }} />
              <YAxis dataKey="y" type="number" name={yKey} tick={{ fontSize: 9, fill: '#94a3b8' }} axisLine={false} tickLine={false} width={40} label={{ value: yKey, fill: '#94a3b8', fontSize: 9, angle: -90, position: 'insideLeft' }} />
              <ZAxis range={[40, 40]} />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={TTP.contentStyle} formatter={(v, name) => [fmtExact(v), name] as [React.ReactNode, string]} />
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
      {viewType === 'bar' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={dataWithTrend} margin={{ top: 18, right: 16, bottom: 8, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} maxBarSize={44} opacity={0.88}
              onClick={onDrill ? (d: { name?: string }) => onDrill(String(d?.name ?? '')) : undefined}
              style={onDrill ? { cursor: 'pointer' } : undefined}>
              <LabelList dataKey="value" position="top" style={{ fontSize: 8, fill: '#94a3b8' }} formatter={(v: number) => fmtExact(v)} />
            </Bar>
            {dataWithTrend.some(d => '_trend' in d) && (
              <Line type="linear" dataKey="_trend" stroke={`${color}70`} strokeWidth={1.5} strokeDasharray="5 3" dot={false} name="Trend" legendType="none" />
            )}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── AREA — with trend line ── */}
      {viewType === 'area' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={dataWithTrend} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
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
      {viewType === 'line' && (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={dataWithTrend} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            {GRID}{XAXIS}{YAXIS}<Tooltip {...TTP} />
            <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} dot={false} activeDot={{ r: 4, fill: color }} />
            {dataWithTrend.some(d => '_trend' in d) && (
              <Line type="linear" dataKey="_trend" stroke={`${color}70`} strokeWidth={1.5} strokeDasharray="5 3" dot={false} name="Trend" legendType="none" />
            )}
            {refLines}
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {/* ── PIE / DONUT ── */}
      {(viewType === 'pie' || viewType === 'donut') && (
        <ResponsiveContainer width="100%" height={240}>
          <PieChart margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
            <Pie
              data={chart.data} dataKey="value" nameKey="name"
              cx="50%" cy="50%" outerRadius="82%" innerRadius="50%"
              labelLine={false}
              // % labels sit inside the ring so the donut can fill the card
              // without outside labels clipping at the card edges.
              label={({ cx: pcx, cy: pcy, midAngle, innerRadius: ir, outerRadius: or, percent }) => {
                if (percent < 0.06) return null
                const RADIAN = Math.PI / 180
                const r = ((ir as number) + (or as number)) / 2
                const x = (pcx as number) + r * Math.cos(-midAngle * RADIAN)
                const y = (pcy as number) + r * Math.sin(-midAngle * RADIAN)
                return (
                  <text x={x} y={y} fill="#fff" textAnchor="middle"
                    dominantBaseline="central" fontSize={10} fontWeight={700}>
                    {`${(percent * 100).toFixed(0)}%`}
                  </text>
                )
              }}
              onClick={onDrill ? (d: { name?: string }) => onDrill(String(d?.name ?? '')) : undefined}
              style={onDrill ? { cursor: 'pointer' } : undefined}>
              {chart.data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Pie>
            <Tooltip {...TTP} formatter={(v: number) => [fmtExact(v), ''] as [React.ReactNode, string]} />
            <Legend iconSize={9} wrapperStyle={{ fontSize: 10, paddingTop: 4 }} iconType="circle" />
          </PieChart>
        </ResponsiveContainer>
      )}

      {/* ── TREEMAP ── */}
      {viewType === 'treemap' && (
        <ResponsiveContainer width="100%" height={240}>
          <Treemap
            data={chart.data.map((d, i) => ({ ...d, fill: PALETTE[i % PALETTE.length] }))}
            dataKey="value" nameKey="name" aspectRatio={4 / 3} stroke="#fff" isAnimationActive={false}
          >
            <Tooltip {...TTP} formatter={(v: number) => [fmtExact(v), ''] as [React.ReactNode, string]} />
          </Treemap>
        </ResponsiveContainer>
      )}

      {/* ── FUNNEL ── */}
      {viewType === 'funnel' && (
        <ResponsiveContainer width="100%" height={240}>
          <FunnelChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
            <Tooltip {...TTP} formatter={(v: number) => [fmtExact(v), ''] as [React.ReactNode, string]} />
            <Funnel dataKey="value" nameKey="name" data={chart.data} isAnimationActive={false}>
              {chart.data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
              {/* Centred two-line label (name + value) inside each band, anchored to the
                  band centre so wide top segments never clip; white text + dark halo
                  (paint-order: stroke) stays legible on every segment colour. */}
              <LabelList content={((props: Record<string, unknown>) => {
                const x = Number(props.x), y = Number(props.y)
                const w = Number(props.width), h = Number(props.height)
                const d = (chart.data as Array<{ name?: unknown; value?: unknown }>)[Number(props.index)]
                if (!d || !isFinite(x) || !isFinite(w)) return null
                const cx = x + w / 2, cy = y + h / 2
                const halo = { stroke: 'rgba(2,18,38,0.55)', strokeWidth: 2.5, paintOrder: 'stroke' as const }
                return (
                  <g style={{ pointerEvents: 'none' }}>
                    <text x={cx} y={cy - 3} textAnchor="middle" fill="#fff" {...halo} style={{ fontSize: 10, fontWeight: 700 }}>{String(d.name ?? '')}</text>
                    <text x={cx} y={cy + 10} textAnchor="middle" fill="#fff" {...halo} style={{ fontSize: 10, fontWeight: 600 }}>{fmtExact(Number(d.value ?? 0))}</text>
                  </g>
                )
              }) as unknown as React.ComponentProps<typeof LabelList>['content']} />
            </Funnel>
          </FunnelChart>
        </ResponsiveContainer>
      )}

      {/* ── RADAR ── */}
      {viewType === 'radar' && (
        <ResponsiveContainer width="100%" height={240}>
          <RadarChart data={chart.data} margin={{ top: 12, right: 24, bottom: 12, left: 24 }}>
            <PolarGrid stroke="#e8eef5" />
            <PolarAngleAxis dataKey="name" tick={{ fontSize: 9, fill: '#94a3b8' }} />
            <PolarRadiusAxis tick={{ fontSize: 8, fill: '#cbd5e1' }} tickFormatter={fmtTick} />
            <Radar dataKey="value" stroke={color} fill={color} fillOpacity={0.4} />
            <Tooltip {...TTP} formatter={(v: number) => [fmtExact(v), ''] as [React.ReactNode, string]} />
          </RadarChart>
        </ResponsiveContainer>
      )}
      </div>{/* /ref */}

      {/* ── Raw data table (toggled by Data button) ── */}
      {showRawData && chart.type !== 'table' && (
        <div style={{ marginTop: 14, borderTop: '1px solid #f1f5f9', paddingTop: 14, animation: 'slideDown 0.2s ease both' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <Database size={10} style={{ color: C.teal }} />
            <span style={{ fontSize: 10, fontWeight: 700, color: C.teal, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Raw Data</span>
            <span style={{ fontSize: 9, color: '#94a3b8', marginLeft: 2 }}>{chart.data.length} rows</span>
          </div>
          <TableView chart={chart} />
        </div>
      )}
    </div>
  )
}

// ── Section content helpers ────────────────────────────────────────────────────

// Normalise a KPI label so "Total Revenue ($)" and "Total Revenue" compare equal
function normLabel(l: string): string {
  return l.toLowerCase()
    .replace(/\s*\(.*?\)\s*/g, ' ')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const _kfKeyWords = (t: string) =>
  t.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter(w => w.length > 3)

// Lightweight inline markdown → HTML for the report body text (the AI emits
// **bold**, *italic*, `code`). HTML is escaped FIRST so AI text can't inject markup.
// Matches the copilot's renderer so markdown is consistent across the page.
function mdHtml(s: string | undefined): string {
  if (!s) return ''
  let h = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  h = h.replace(/__(.+?)__/g, '<strong>$1</strong>')
  h = h.replace(/\*(.+?)\*/g, '<em>$1</em>')
  h = h.replace(/`([^`]+?)`/g, '<code>$1</code>')
  return h
}

// Strip ALL leading sentences of the narrative that merely restate the section
// headline / key finding, so the body adds new detail instead of repeating it.
// (Fix 2 — stronger client-side dedup vs both data_story and key_finding.)
function dedupeNarrative(narrative: string | undefined, ...headlines: (string | undefined)[]): string {
  if (!narrative) return ''
  const refs = headlines.filter(Boolean).join(' ')
  if (!refs) return narrative.trim()
  const headW = new Set(_kfKeyWords(refs))
  if (!headW.size) return narrative.trim()
  const sentences = narrative.match(/[^.!?]+[.!?]+(\s|$)/g) ?? [narrative]
  let i = 0
  while (i < sentences.length) {
    const sw = _kfKeyWords(sentences[i])
    if (sw.length) {
      const overlap = sw.filter(w => headW.has(w)).length / Math.min(sw.length, headW.size)
      if (overlap < 0.45) break          // first genuinely-new sentence — stop here
    }
    i++
  }
  // Never strip everything: if the whole body echoed the headline, drop just the lead.
  if (i >= sentences.length) i = Math.min(1, sentences.length - 1)
  return sentences.slice(i).join('').trim() || narrative.trim()
}

// Pull the figure-bearing clauses out of a narrative as scannable bullets.
// (Fix 4 — present supporting numbers as a structured list, not repeated prose.)
function extractKeyFigures(text: string, max = 6): string[] {
  if (!text) return []
  const figurePattern = /\d[\d,]*(?:\.\d+)?\s*%|\(\s*\d{1,3}\s*%\s*\)|\$[\d,]+(?:\.\d+)?|\b\d[\d,]{2,}\b/
  const out: string[] = []
  const seen = new Set<string>()
  text.replace(/\s+/g, ' ')
    .split(/[,;:]\s+|\.\s+/)
    .map(c => c.trim().replace(/^(and|but|while|with|the|a|an|also)\s+/i, '').replace(/[.;:,]+$/, '').trim())
    .forEach(c => {
      if (out.length >= max) return
      if (c.length < 6 || c.length > 90) return
      if (!figurePattern.test(c)) return
      const key = c.toLowerCase()
      if (seen.has(key)) return
      seen.add(key)
      out.push(c)
    })
  return out
}

// Render text with numeric / percent tokens emphasised.
function boldFigures(text: string): React.ReactNode[] {
  return text.split(/(\$?\d[\d,]*(?:\.\d+)?\s*%?(?:\s*\(\s*\d{1,3}\s*%\s*\))?)/g)
    .map((p, i) => /\d/.test(p)
      ? <strong key={i} style={{ color: C.ink, fontWeight: 700 }}>{p}</strong>
      : <React.Fragment key={i}>{p}</React.Fragment>)
}

// ── Section content ────────────────────────────────────────────────────────────
function SectionContent({
  section, sectionIdx,
  globalKpis,
  onRegenSection, regenning,
  onDrill, onFullscreen,
  annotations, onAnnotate,
}: {
  section: AgentSection; sectionIdx: number
  globalKpis?: AgentKPI[]
  onRegenSection?: () => void
  regenning?: boolean
  onDrill?: (chart: AgentChart, segment: string) => void
  onFullscreen?: (chart: AgentChart) => void
  annotations?: Record<string, string>
  onAnnotate?: (chartTitle: string, pointName: string) => void
}) {
  const hasPerformers = (section.top_performers?.length ?? 0) > 0 || (section.bottom_performers?.length ?? 0) > 0
  const [showFullNarrative, setShowFullNarrative] = useState(false)

  // Fix 2 — strip leading sentences of the narrative that restate the headline.
  const displayNarrative = dedupeNarrative(section.narrative, section.data_story, section.key_finding)
  // Fix 4 — surface the supporting numbers as a scannable list.
  const keyFigures = extractKeyFigures(displayNarrative)
  const hasKeyFigures = keyFigures.length >= 2

  // Fix B — build performer label set for duplicate-table detection
  const performerLabels = new Set([
    ...(section.top_performers ?? []).map(p => p.label.trim().toLowerCase()),
    ...(section.bottom_performers ?? []).map(p => p.label.trim().toLowerCase()),
  ])
  const accentColor = PALETTE[sectionIdx % PALETTE.length]

  const chartProps = (ch: AgentChart, i: number) => ({
    chart: ch, colorIdx: sectionIdx * 4 + i,
    onDrill: onDrill ? (seg: string) => onDrill(ch, seg) : undefined,
    onFullscreen: onFullscreen ? () => onFullscreen(ch) : undefined,
    annotations: annotations ? Object.fromEntries(
      Object.entries(annotations)
        .filter(([k]) => k.startsWith(`${section.id}|${ch.title}|`))
        .map(([k, v]) => [k.split('|')[2] ?? k, v])
    ) : undefined,
    onAnnotate: onAnnotate ? (pt: string) => onAnnotate(ch.title, pt) : undefined,
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Section header bar (replaces huge dark chapter cover) ── */}
      <div className="intel-section-hdr" style={{
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        paddingBottom: 14, borderBottom: `2px solid ${accentColor}20`,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flex: 1 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10, flexShrink: 0, marginTop: 2,
            background: `linear-gradient(135deg, ${accentColor}20, ${accentColor}10)`,
            border: `1px solid ${accentColor}30`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <SectionIcon name={section.icon} size={16} color={accentColor} />
          </div>
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 10, fontWeight: 700, color: accentColor, textTransform: 'uppercase', letterSpacing: '0.1em', margin: '0 0 4px' }}>{section.label}</p>
            {section.data_story
              ? <p style={{ fontSize: 18, fontWeight: 800, color: C.ink, margin: 0, lineHeight: 1.3, letterSpacing: '-0.01em' }} dangerouslySetInnerHTML={{ __html: mdHtml(section.data_story) }} />
              : section.key_finding
                ? <p style={{ fontSize: 16, fontWeight: 700, color: C.ink, margin: 0, lineHeight: 1.35 }} dangerouslySetInnerHTML={{ __html: mdHtml(section.key_finding) }} />
                : <p style={{ fontSize: 14, fontWeight: 600, color: C.slate, margin: 0 }} dangerouslySetInnerHTML={{ __html: mdHtml(section.narrative?.slice(0, 90) ?? '') }} />
            }
            {section.recommendation && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 8 }}>
                <ArrowRight size={11} style={{ color: '#f97316', marginTop: 2, flexShrink: 0 }} />
                <p style={{ fontSize: 12, color: '#7c2d12', margin: 0, lineHeight: 1.55 }}>
                  <span style={{ fontWeight: 700, color: '#f97316' }}>Next Step: </span>
                  <span dangerouslySetInnerHTML={{ __html: mdHtml(section.recommendation) }} />
                </p>
              </div>
            )}
          </div>
        </div>
        {onRegenSection && (
          <button onClick={onRegenSection} disabled={regenning} style={{
            display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, padding: '5px 12px',
            borderRadius: 8, border: '1px solid #e2eaf4', background: 'white',
            color: regenning ? C.teal : C.slate, cursor: 'pointer', flexShrink: 0, marginLeft: 12,
            transition: 'all 0.15s',
          }}>
            <RefreshCw size={9} style={regenning ? { animation: 'ispin 1s linear infinite' } : {}} />
            {regenning ? 'Refreshing…' : 'Refresh'}
          </button>
        )}
      </div>

      {/* ── Supporting detail ──────────────────────────────────────────────── */}
      {/* Fix 4: scannable key-figure bullets instead of a paragraph that echoes the headline. */}
      {hasKeyFigures && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(248px, 1fr))', gap: '7px 22px' }}>
          {keyFigures.map((f, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 9, fontSize: 12.5, lineHeight: 1.5, color: '#4a5568' }}>
              <span style={{ width: 5, height: 5, borderRadius: '50%', background: accentColor, marginTop: 7, flexShrink: 0 }} />
              <span>{boldFigures(f)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Fix 1: full prose narrative is demoted behind a disclosure when figures are shown;
          shown directly only when there's nothing scannable to stand in for it. */}
      {displayNarrative && (
        hasKeyFigures ? (
          <div>
            <button
              onClick={() => setShowFullNarrative(v => !v)}
              style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: C.slate, background: 'none', border: 'none', padding: '2px 0', cursor: 'pointer' }}
            >
              {showFullNarrative ? 'Hide full analysis' : 'Show full analysis'}
              {showFullNarrative ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>
            {showFullNarrative && (
              <p style={{ fontSize: 13, lineHeight: 1.78, color: '#64748b', margin: '8px 0 0' }} dangerouslySetInnerHTML={{ __html: mdHtml(displayNarrative) }} />
            )}
          </div>
        ) : (
          <p style={{ fontSize: 13, lineHeight: 1.78, color: '#4a5568', margin: 0 }} dangerouslySetInnerHTML={{ __html: mdHtml(displayNarrative) }} />
        )
      )}

      {/* ── Insight cards ── */}
      {section.insights && section.insights.length > 0 && (
        <InsightCards insights={section.insights} />
      )}

      {/* ── Performers + Charts ── */}
      {(hasPerformers || section.charts.length > 0) && (() => {
        const charts = section.charts.filter(ch => {
          if ((ch.data?.length ?? 0) === 0) return false
          // Skip charts where every single cell is null/empty (AI-generated ghosts)
          const hasAnyRealValue = ch.data.some(row =>
            Object.values(row).some(v => v !== null && v !== undefined && v !== '' && String(v).trim() !== '')
          )
          if (!hasAnyRealValue) return false

          // Hide charts that would render blank. Key on the SAME field the renderer
          // reads, not just "any numeric column": almost every chart type draws from
          // `value` (bar/line/area/pie/donut/funnel/treemap/radar/waterfall/forecast),
          // scatter draws from x/y. A chart with numbers only under some other key
          // (e.g. `jobs`) but no real `value` renders empty — so drop it. `combo` may
          // use multiple series keys, so it keeps the broad any-numeric check.
          if (ch.type !== 'table') {
            const toN = (v: unknown) => (typeof v === 'number' ? v : Number(v))
            const nonZero = (v: unknown) => { const n = toN(v); return isFinite(n) && n !== 0 }
            let renderable: boolean
            if (ch.type === 'scatter') {
              const xk = (ch as { x_key?: string }).x_key || 'x'
              const yk = (ch as { y_key?: string }).y_key || 'y'
              renderable = ch.data.some(r => isFinite(toN(r[xk] ?? r.x ?? r.value)) && isFinite(toN(r[yk] ?? r.y)))
            } else if (ch.type === 'combo') {
              const catKeys = new Set(['name', 'x', String((ch as { x_key?: string }).x_key ?? '')].filter(Boolean).map(s => s.toLowerCase()))
              renderable = ch.data.some(r => Object.entries(r).some(([k, v]) => !catKeys.has(k.toLowerCase()) && typeof v !== 'boolean' && nonZero(v)))
            } else {
              renderable = ch.data.some(r => nonZero((r as { value?: unknown }).value))
            }
            if (!renderable) return false
          }

          // Fix B — suppress table charts whose first-column entities are already
          // shown by the PerformerPanel (≥50% overlap → duplicate visual)
          if (ch.type === 'table' && performerLabels.size > 0) {
            const firstKey = Object.keys(ch.data[0]).find(
              k => !['value','base','total','projected','anomaly'].includes(k.toLowerCase())
            ) ?? Object.keys(ch.data[0])[0]
            const tableEntities = ch.data
              .map(r => String(r[firstKey] ?? '').trim().toLowerCase())
              .filter(Boolean)
            if (tableEntities.length > 0) {
              const matched = tableEntities.filter(e => performerLabels.has(e)).length
              if (matched / Math.min(tableEntities.length, performerLabels.size) >= 0.5) return false
            }
          }

          return true
        })

        // A chart "needs the full row" if it's a table type OR its data rows carry
        // more than 3 column keys beyond the standard name/value pair (e.g. multi-year
        // revenue matrices, detail tables with 4+ dimensions). These cannot share a
        // half-width column without truncating content.
        const RESERVED = new Set(['name','value','base','total','projected','anomaly'])
        const isWide = (ch: AgentChart) =>
          ch.type === 'table' ||
          (ch.data.length > 0 &&
            Object.keys(ch.data[0]).filter(k => !RESERVED.has(k)).length > 2)

        const wideCharts    = charts.filter(isWide)
        const compactCharts = charts.filter(ch => !isWide(ch))
        const firstCompact  = compactCharts[0]
        const restCompact   = compactCharts.slice(1)

        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Row 1 — performers sidebar + first compact chart */}
            {(hasPerformers || firstCompact) && (
              <div style={{
                display: 'grid',
                gridTemplateColumns: hasPerformers && firstCompact ? '260px minmax(0, 1fr)' : '1fr',
                gap: 16, alignItems: 'start',
              }}>
                {hasPerformers && (
                  <div style={{ animation: 'fadeInLeft 0.3s 0.05s ease both', minWidth: 0 }}>
                    <PerformerPanel top={section.top_performers ?? []} bottom={section.bottom_performers ?? []} label={section.label} />
                  </div>
                )}
                {firstCompact && (
                  <div style={{ animation: 'fadeInUp 0.32s 0.08s ease both', minWidth: 0 }}>
                    <AgentChartView {...chartProps(firstCompact, 0)} />
                  </div>
                )}
              </div>
            )}

            {/* Remaining compact charts — max 2 per row, responsive */}
            {restCompact.length > 0 && (
              <div style={{
                display: 'grid',
                gridTemplateColumns: restCompact.length === 1
                  ? '1fr'
                  : 'repeat(auto-fit, minmax(min(100%, 380px), 1fr))',
                gap: 16, alignItems: 'stretch',
              }}>
                {restCompact.map((ch, i) => {
                  // A lone trailing chart (odd count) would otherwise leave an
                  // empty second column — span it across the full row instead.
                  const isLoneTrailing = i === restCompact.length - 1 && restCompact.length % 2 === 1
                  return (
                    <div key={`compact-${i}`} style={{ animation: `fadeInUp 0.32s ${0.1 + i * 0.06}s ease both`, minWidth: 0, gridColumn: isLoneTrailing ? '1 / -1' : undefined }}>
                      <AgentChartView {...chartProps(ch, i + 1)} />
                    </div>
                  )
                })}
              </div>
            )}

            {/* Wide charts (tables + multi-column) — always occupy the full row */}
            {wideCharts.map((ch, i) => (
              <div key={`wide-${i}`} style={{ animation: `fadeInUp 0.3s ${0.08 + i * 0.05}s ease both`, width: '100%', minWidth: 0 }}>
                <AgentChartView {...chartProps(ch, compactCharts.length + i)} />
              </div>
            ))}

          </div>
        )
      })()}

      {!section.narrative && !section.key_finding && !section.data_story && !section.kpis.length && !section.charts.length && (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#94a3b8', fontSize: 13 }}>No content generated for this section.</div>
      )}
    </div>
  )
}

// ── Left rail ──────────────────────────────────────────────────────────────────
const RAIL_COLLAPSED = 64
const RAIL_EXPANDED  = 256

interface RailAction { key: string; icon: React.ReactNode; label: string; onClick: () => void; tint?: string }

function LeftRail({ sections, active, onNav, actions = [], onBack }: { sections: AgentSection[]; active: string; onNav: (id: string) => void; actions?: RailAction[]; onBack?: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const labelStyle = (vis: boolean): React.CSSProperties => ({
    fontSize: 12.5, fontWeight: 600, letterSpacing: '0.01em', color: 'rgba(255,255,255,0.72)',
    whiteSpace: 'nowrap', textAlign: 'left', flex: 1, minWidth: 0, paddingRight: 12,
    overflow: 'hidden', textOverflow: 'ellipsis', opacity: vis ? 1 : 0,
    transition: 'opacity 0.16s ease', pointerEvents: 'none',
  })
  return (
    // Outer keeps a fixed 64px footprint in the grid; the nav itself is absolutely
    // positioned so it can slide open over the content on hover without reflowing it.
    <div style={{ position: 'relative', width: RAIL_COLLAPSED, flexShrink: 0 }}>
      <nav
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
        style={{
          position: 'absolute', top: 0, left: 0, bottom: 0,
          width: expanded ? RAIL_EXPANDED : RAIL_COLLAPSED,
          background: '#071a2e',
          borderRight: '1px solid rgba(255,255,255,0.06)',
          boxShadow: expanded ? '8px 0 28px rgba(0,0,0,0.4)' : 'none',
          display: 'flex', flexDirection: 'column',
          paddingTop: 14, paddingBottom: 14, gap: 4,
          overflowY: 'auto', overflowX: 'hidden',
          transition: 'width 0.22s cubic-bezier(.4,0,.2,1), box-shadow 0.22s ease',
          zIndex: 60, scrollbarWidth: 'none',
        } as React.CSSProperties}
      >
        {/* Back button (moved out of the header) */}
        {onBack && (
          <>
            <button
              onClick={onBack}
              title="Back"
              className="intel-nav-item"
              style={{ width: '100%', flexShrink: 0, background: 'transparent', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 12, padding: '2px 0 4px 10px', outline: 'none' }}
            >
              <div style={{ width: 44, height: 44, borderRadius: 13, flexShrink: 0, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.10)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,0.82)' }}>
                <ChevronLeft size={18} />
              </div>
              <span style={labelStyle(expanded)}>Back</span>
            </button>
            <div style={{ height: 1, background: 'rgba(255,255,255,0.08)', margin: '6px 12px 8px' }} />
          </>
        )}
        {sections.map((s, idx) => {
          const isActive = s.id === active
          const color = PALETTE[idx % PALETTE.length]
          const color2 = PALETTE[(idx + 2) % PALETTE.length]
          return (
            <button
              key={s.id}
              onClick={() => onNav(s.id)}
              title={s.label}
              className="intel-nav-item"
              style={{
                position: 'relative', width: '100%', flexShrink: 0,
                background: isActive ? 'rgba(255,255,255,0.06)' : 'transparent',
                border: 'none', cursor: 'pointer',
                display: 'flex', flexDirection: 'row', alignItems: 'center',
                gap: 12, padding: '5px 0 5px 10px', outline: 'none',
              }}
            >
              {/* Active accent bar on the rail's left edge */}
              {isActive && (
                <span style={{ position: 'absolute', left: 0, top: 9, bottom: 9, width: 3, borderRadius: '0 2px 2px 0', background: C.teal }} />
              )}
              <div style={{
                width: 44, height: 44, borderRadius: 13, flexShrink: 0,
                background: `linear-gradient(135deg, ${color} 0%, ${color2} 100%)`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'white',
                boxShadow: isActive ? `0 0 0 2.5px ${C.teal}, 0 4px 16px ${color}70` : `0 2px 8px ${color}44`,
                transform: isActive ? 'scale(1.06)' : 'scale(1)',
                transition: 'all 0.2s ease',
              }}>
                <SectionIcon name={s.icon} size={18} />
              </div>
              <span style={{
                fontSize: 12.5, fontWeight: 600, letterSpacing: '0.01em',
                color: isActive ? '#fff' : 'rgba(255,255,255,0.72)',
                whiteSpace: 'nowrap', textAlign: 'left', flex: 1, minWidth: 0, paddingRight: 12,
                overflow: 'hidden', textOverflow: 'ellipsis',
                opacity: expanded ? 1 : 0,
                transition: 'opacity 0.16s ease',
                pointerEvents: 'none',
              }}>{s.label}</span>
            </button>
          )
        })}

        {/* Action buttons (moved out of the header to declutter it) */}
        {actions.length > 0 && (
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 2, paddingTop: 8 }}>
            <div style={{ height: 1, background: 'rgba(255,255,255,0.08)', margin: '6px 12px 8px' }} />
            {actions.map(a => (
              <button
                key={a.key}
                onClick={a.onClick}
                title={a.label}
                className="intel-nav-item"
                style={{
                  width: '100%', flexShrink: 0, background: 'transparent', border: 'none',
                  cursor: 'pointer', display: 'flex', flexDirection: 'row', alignItems: 'center',
                  gap: 12, padding: '4px 0 4px 10px', outline: 'none',
                }}
              >
                <div style={{
                  width: 44, height: 44, borderRadius: 13, flexShrink: 0,
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.10)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: a.tint ?? 'rgba(255,255,255,0.82)',
                }}>
                  {a.icon}
                </div>
                <span style={{
                  fontSize: 12.5, fontWeight: 600, letterSpacing: '0.01em',
                  color: 'rgba(255,255,255,0.72)', whiteSpace: 'nowrap', textAlign: 'left',
                  flex: 1, minWidth: 0, paddingRight: 12, overflow: 'hidden', textOverflow: 'ellipsis',
                  opacity: expanded ? 1 : 0, transition: 'opacity 0.16s ease', pointerEvents: 'none',
                }}>{a.label}</span>
              </button>
            ))}
          </div>
        )}
      </nav>
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

// ── Inline copilot replaced by IntelligenceCopilotPanel ────────────────────────
// (InlineCopilot removed — IntelligenceCopilotPanel is used directly in the FAB)

function buildReportContext_UNUSED(analysis: ExecutiveAnalysis, canvasName: string): string {
  const kpiLines = analysis.kpis.slice(0, 10).map(k =>
    `  • ${k.label}: ${k.value}${k.trend !== 'neutral' ? ` (${k.trend}${k.trend_pct ? ' ' + k.trend_pct : ''})` : ''}`
  ).join('\n')

  const sectionDetails = analysis.sections.map(s => {
    const chartLines = s.charts.slice(0, 4).map(ch => {
      const pts = ch.data.slice(0, 15).map(d => `${d.name}=${d.value}`).join(', ')
      return `    [${ch.type.toUpperCase()}] "${ch.title}": ${pts}${ch.insight ? `\n      → ${ch.insight}` : ''}`
    }).join('\n')

    const insightLines = (s.insights ?? []).slice(0, 4).map(ins =>
      `    ${ins.type.toUpperCase()}: ${ins.headline} — ${ins.detail}`
    ).join('\n')

    const performers = [
      ...(s.top_performers ?? []).slice(0, 3).map(p => `▲ ${p.label} ${p.formatted_value}`),
      ...(s.bottom_performers ?? []).slice(0, 3).map(p => `▼ ${p.label} ${p.formatted_value}`),
    ].join('  ')

    return [
      `──── ${s.label.toUpperCase()} ────`,
      `  Finding: ${s.key_finding ?? ''}`,
      `  Narrative: ${s.narrative?.slice(0, 250) ?? ''}`,
      s.recommendation ? `  Action: ${s.recommendation}` : '',
      chartLines ? `  Charts:\n${chartLines}` : '',
      insightLines ? `  Signals:\n${insightLines}` : '',
      performers ? `  Performers: ${performers}` : '',
    ].filter(Boolean).join('\n')
  }).join('\n\n')

  return `╔══════════════════════════════════════════════════════╗
║  INTELLIGENCE REPORT: ${canvasName.toUpperCase().slice(0, 30).padEnd(30)} ║
╚══════════════════════════════════════════════════════╝
Health Score: ${analysis.health_score}/100   Brief: ${analysis.morning_brief?.slice(0, 200) ?? ''}

TOP KPIs:
${kpiLines}

DETAILED SECTION DATA (use these exact numbers to answer):
${sectionDetails}
${analysis.correlations.length ? `\nKEY CORRELATIONS: ${analysis.correlations.slice(0, 4).join(' | ')}` : ''}

══════════════════════════════════════════════════════
INSTRUCTION: You have the complete intelligence report data above with actual numbers.
Answer ALL user questions with SPECIFIC VALUES from the data above.
NEVER say "I'll analyze", "I would show", or "I'll look into" — give the direct answer NOW using the numbers provided.
If the user asks for something NOT in the context, generate SQL to query it live.
══════════════════════════════════════════════════════

User question: `
}

function InlineCopilot_UNUSED({ canvasId, projectId, canvasName, analysis }: { canvasId: string; projectId: string; canvasName: string; analysis?: ExecutiveAnalysis }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const contextInjectedRef = useRef(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])

  const send = async (text: string) => {
    if (!text.trim() || sending) return
    setMsgs(p => [...p, { role: 'user', text }]); setInput(''); setSending(true)
    try {
      // Prepend report context to the first message only
      let fullMessage = text
      if (!contextInjectedRef.current && analysis) {
        fullMessage = buildReportContext(analysis, canvasName) + text
        contextInjectedRef.current = true
      }
      const resp = await chatApi.send({
        message: fullMessage,
        project_id: projectId,
        dashboard_id: canvasId,
        model_preference: 'sonnet',  // explicit — prevents backend auto-upgrade to Opus on large messages
      })
      setMsgs(p => [...p, { role: 'assistant', text: resp.data?.text ?? resp.data?.response ?? 'No response.' }])
    } catch { setMsgs(p => [...p, { role: 'assistant', text: 'Failed. Please retry.' }]) }
    finally { setSending(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#fafbfd' }}>
      <div style={{ padding: '13px 16px', borderBottom: '1px solid #e8eef5', background: `linear-gradient(135deg, ${C.navy}, #0d3060)`, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 28, height: 28, borderRadius: 8, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Zap size={13} style={{ color: 'white' }} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <p style={{ fontSize: 13, fontWeight: 700, color: 'white', margin: 0 }}>Report Copilot</p>
              <span style={{ fontSize: 8, fontWeight: 700, color: C.green, background: `${C.green}20`, border: `1px solid ${C.green}40`, borderRadius: 10, padding: '1px 6px' }}>SONNET</span>
            </div>
            <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.45)', margin: 0 }}>Full report context loaded</p>
          </div>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 12px 0', display: 'flex', flexDirection: 'column', gap: 9 }}>
        {msgs.length === 0 && (
          <div style={{ textAlign: 'center', padding: '28px 16px' }}>
            <div style={{ width: 40, height: 40, borderRadius: 14, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 12px' }}><Zap size={18} style={{ color: 'white' }} /></div>
            <p style={{ fontSize: 13, fontWeight: 600, color: C.navy, margin: '0 0 4px' }}>Ask about this report</p>
            <p style={{ fontSize: 11, color: '#94a3b8', margin: 0 }}>Powered by Claude Sonnet — full report context</p>
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

// ── Feature 2: Drill-Through Modal ────────────────────────────────────────────
function DrillThroughModal({
  title, segment, rows, columns, onClose,
}: { title: string; segment: string; rows: Record<string, unknown>[]; columns: string[]; onClose: () => void }) {
  const cols = columns.length ? columns : rows[0] ? Object.keys(rows[0]) : []
  return (
    <div className="intel-modal-enter" style={{ position: 'fixed', inset: 0, background: 'rgba(8,33,58,0.55)', backdropFilter: 'blur(6px)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div className="intel-modal-inner-enter" style={{ background: 'white', borderRadius: 20, width: '90%', maxWidth: 760, maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(8,33,58,0.25)' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #e8eef5', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 13, fontWeight: 700, color: C.navy, margin: 0 }}>Drill-Through: <span style={{ color: C.teal }}>{segment}</span></p>
            <p style={{ fontSize: 11, color: C.muted, margin: 0 }}>{title} · {rows.length} row{rows.length !== 1 ? 's' : ''}</p>
          </div>
          <button onClick={onClose} style={{ padding: 6, border: '1px solid #e8eef5', borderRadius: 8, background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><X size={14} /></button>
        </div>
        <div style={{ overflow: 'auto', flex: 1 }}>
          {rows.length === 0 ? (
            <p style={{ textAlign: 'center', color: C.muted, padding: '40px 0', fontSize: 13 }}>No rows available for this segment.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: C.navy, position: 'sticky', top: 0 }}>
                  {cols.map(c => <th key={c} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 700, color: 'rgba(255,255,255,0.85)', whiteSpace: 'nowrap', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 500).map((row, i) => (
                  <tr key={i} style={{ background: i % 2 === 0 ? 'white' : '#fafbfd', borderBottom: '1px solid #f1f5f9' }}>
                    {cols.map(c => <td key={c} style={{ padding: '6px 12px', color: '#374151', whiteSpace: 'nowrap' }}>{String(row[c] ?? '')}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Feature 5: Full-Screen Chart Modal ────────────────────────────────────────
function FullscreenChartModal({ chart, colorIdx, onClose }: { chart: AgentChart; colorIdx: number; onClose: () => void }) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const accentColor = chart.color ?? PALETTE[colorIdx % PALETTE.length]

  return (
    <div className="intel-modal-enter" style={{ position: 'fixed', inset: 0, background: 'rgba(8,33,58,0.7)', backdropFilter: 'blur(8px)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
      <div className="intel-modal-inner-enter" style={{ background: 'white', borderRadius: 20, width: '94%', maxWidth: 900, display: 'flex', flexDirection: 'column', boxShadow: '0 24px 80px rgba(8,33,58,0.3)' }}>
        <div style={{ padding: '14px 20px', borderBottom: '1px solid #e8eef5', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 4, height: 18, borderRadius: 2, background: accentColor }} />
          <p style={{ fontSize: 15, fontWeight: 700, color: C.ink, margin: 0, flex: 1 }}>{chart.title}</p>
          <button onClick={onClose} style={{ padding: 6, border: '1px solid #e8eef5', borderRadius: 8, background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><X size={15} /></button>
        </div>
        <div style={{ padding: 24 }}>
          <AgentChartView chart={{ ...chart }} colorIdx={colorIdx} />
        </div>
        <p style={{ fontSize: 10, color: C.muted, textAlign: 'center', padding: '0 0 12px' }}>Press ESC to close</p>
      </div>
    </div>
  )
}

// ── Feature 6: Presentation Slide Deck Mode ───────────────────────────────────
function PresentationOverlay({
  sections, startIdx, onClose,
}: { sections: AgentSection[]; startIdx: number; onClose: () => void }) {
  const [idx, setIdx] = useState(startIdx)
  const section = sections[idx]

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') setIdx(i => Math.min(i + 1, sections.length - 1))
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   setIdx(i => Math.max(i - 1, 0))
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [sections.length, onClose])

  if (!section) return null
  return (
    <div style={{ position: 'fixed', inset: 0, background: C.navy, zIndex: 2000, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* slide header */}
      <div style={{ padding: '14px 28px', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ width: 36, height: 36, borderRadius: 12, background: `${C.teal}25`, border: `1px solid ${C.teal}40`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.teal2, flexShrink: 0 }}>
          <SectionIcon name={section.icon} size={17} />
        </div>
        <div style={{ flex: 1 }}>
          <p style={{ fontSize: 11, fontWeight: 700, color: C.teal2, textTransform: 'uppercase', letterSpacing: '0.09em', margin: 0 }}>{section.label}</p>
          <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', margin: 0 }}>{idx + 1} / {sections.length}</p>
        </div>
        <button onClick={onClose} style={{ padding: 8, border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex' }}><X size={15} /></button>
      </div>

      {/* slide body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '32px 60px' }}>
        {section.data_story && (
          <p style={{ fontSize: 32, fontWeight: 800, color: 'white', margin: '0 0 20px', lineHeight: 1.25, letterSpacing: '-0.02em', maxWidth: 820 }} dangerouslySetInnerHTML={{ __html: mdHtml(section.data_story) }} />
        )}
        {section.key_finding && (
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', background: `${C.teal}15`, borderLeft: `4px solid ${C.teal}`, borderRadius: '0 12px 12px 0', padding: '14px 18px', marginBottom: 20, maxWidth: 780 }}>
            <Zap size={16} style={{ color: C.teal, flexShrink: 0 }} />
            <p style={{ fontSize: 18, fontWeight: 600, color: 'white', margin: 0, lineHeight: 1.5 }} dangerouslySetInnerHTML={{ __html: mdHtml(section.key_finding) }} />
          </div>
        )}
        {section.narrative && (
          <p style={{ fontSize: 15, lineHeight: 1.8, color: 'rgba(255,255,255,0.75)', margin: '0 0 24px', maxWidth: 780 }} dangerouslySetInnerHTML={{ __html: mdHtml(section.narrative) }} />
        )}
        {section.charts.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
            {section.charts.slice(0, 4).map((ch, i) => <AgentChartView key={i} chart={ch} colorIdx={i} />)}
          </div>
        )}
      </div>

      {/* nav arrows */}
      <div style={{ padding: '14px 28px', borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <button onClick={() => setIdx(i => Math.max(i - 1, 0))} disabled={idx === 0} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '8px 18px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.7)', cursor: idx === 0 ? 'not-allowed' : 'pointer', opacity: idx === 0 ? 0.3 : 1 }}>
          <ChevronLeft size={14} /> Prev
        </button>
        <div style={{ display: 'flex', gap: 6 }}>
          {sections.map((_, i) => <div key={i} style={{ width: i === idx ? 18 : 6, height: 6, borderRadius: 3, background: i === idx ? C.teal : 'rgba(255,255,255,0.25)', transition: 'all 0.2s' }} />)}
        </div>
        <button onClick={() => setIdx(i => Math.min(i + 1, sections.length - 1))} disabled={idx === sections.length - 1} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '8px 18px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.7)', cursor: idx === sections.length - 1 ? 'not-allowed' : 'pointer', opacity: idx === sections.length - 1 ? 0.3 : 1 }}>
          Next <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
}

// ── Feature 8: Share modal ────────────────────────────────────────────────────
function ShareModal({
  url, onClose,
}: { url: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
  }
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(8,33,58,0.45)', backdropFilter: 'blur(4px)', zIndex: 900, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ background: 'white', borderRadius: 18, width: '90%', maxWidth: 480, padding: '24px 24px', boxShadow: '0 16px 48px rgba(8,33,58,0.2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          <div style={{ width: 36, height: 36, borderRadius: 10, background: `${C.teal}18`, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.teal }}><Link size={16} /></div>
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: C.navy, margin: 0 }}>Share Report</p>
            <p style={{ fontSize: 11, color: C.muted, margin: 0 }}>Anyone with access can view this report</p>
          </div>
          <button onClick={onClose} style={{ padding: 6, border: '1px solid #e8eef5', borderRadius: 8, background: 'white', cursor: 'pointer', display: 'flex', color: C.slate }}><X size={14} /></button>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input readOnly value={url} style={{ flex: 1, padding: '9px 12px', borderRadius: 9, border: '1px solid #e2eaf4', fontSize: 12, color: '#374151', background: '#fafbfd', outline: 'none', fontFamily: 'monospace' }} onClick={e => (e.target as HTMLInputElement).select()} />
          <button onClick={copy} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '9px 16px', borderRadius: 9, border: 'none', background: copied ? C.green : C.teal, color: 'white', cursor: 'pointer', fontWeight: 600, fontSize: 12, flexShrink: 0 }}>
            {copied ? <><CheckCircle2 size={13} /> Copied!</> : <><Copy size={13} /> Copy</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Feature 10: Annotation popover ───────────────────────────────────────────
function AnnotationPopover({
  annotKey, existing, onSave, onDelete, onClose,
}: { annotKey: string; existing?: string; onSave: (text: string) => void; onDelete: () => void; onClose: () => void }) {
  const [text, setText] = useState(existing ?? '')
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(8,33,58,0.3)', backdropFilter: 'blur(3px)', zIndex: 950, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ background: 'white', borderRadius: 16, width: '90%', maxWidth: 380, padding: '20px', boxShadow: '0 12px 36px rgba(8,33,58,0.18)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Pin size={14} style={{ color: C.amber }} />
          <p style={{ fontSize: 13, fontWeight: 700, color: C.navy, margin: 0, flex: 1 }}>
            {existing ? 'Edit Note' : 'Add Note'}
          </p>
          <button onClick={onClose} style={{ padding: 4, border: 'none', background: 'none', cursor: 'pointer', color: C.slate }}><X size={13} /></button>
        </div>
        <p style={{ fontSize: 10, color: C.muted, margin: '0 0 8px', fontFamily: 'monospace' }}>{annotKey}</p>
        <textarea value={text} onChange={e => setText(e.target.value)} rows={3} placeholder="Add your annotation…" style={{ width: '100%', border: '1px solid #e2eaf4', borderRadius: 8, padding: '8px 10px', fontSize: 12, resize: 'none', outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }} autoFocus />
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <button onClick={() => text.trim() && onSave(text.trim())} disabled={!text.trim()} style={{ flex: 1, padding: '8px', borderRadius: 8, border: 'none', background: C.teal, color: 'white', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>Save</button>
          {existing && <button onClick={onDelete} style={{ padding: '8px 12px', borderRadius: 8, border: `1px solid ${C.red}30`, background: `${C.red}10`, color: C.red, fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>Delete</button>}
          <button onClick={onClose} style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #e2eaf4', background: 'white', color: C.slate, fontSize: 12, cursor: 'pointer' }}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

// ── Feature 1: Date range picker bar ─────────────────────────────────────────
function DateRangeBar({
  value, onChange, onApply, onClear, loading,
}: {
  value: { from: string; to: string }
  onChange: (v: { from: string; to: string }) => void
  onApply: () => void
  onClear: () => void
  loading?: boolean
}) {
  return (
    <div style={{ padding: '8px 22px', background: `${C.teal}08`, borderBottom: '1px solid #dde8f0', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', flexShrink: 0 }}>
      <CalendarRange size={13} style={{ color: C.teal, flexShrink: 0 }} />
      <span style={{ fontSize: 11, fontWeight: 600, color: C.teal, marginRight: 4 }}>Date Range Override</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input type="date" value={value.from} onChange={e => onChange({ ...value, from: e.target.value })}
          style={{ fontSize: 11, padding: '4px 8px', border: '1px solid #dde8f0', borderRadius: 7, outline: 'none', color: C.ink, background: 'white' }} />
        <span style={{ fontSize: 11, color: C.muted }}>→</span>
        <input type="date" value={value.to} onChange={e => onChange({ ...value, to: e.target.value })}
          style={{ fontSize: 11, padding: '4px 8px', border: '1px solid #dde8f0', borderRadius: 7, outline: 'none', color: C.ink, background: 'white' }} />
      </div>
      <button onClick={onApply} disabled={!value.from || !value.to || loading} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, padding: '5px 13px', borderRadius: 7, border: 'none', background: C.teal, color: 'white', cursor: 'pointer', fontWeight: 600, opacity: !value.from || !value.to ? 0.5 : 1 }}>
        {loading ? <Loader2 size={10} style={{ animation: 'ispin 1s linear infinite' }} /> : <RefreshCw size={10} />}
        Apply & Regenerate
      </button>
      <button onClick={onClear} style={{ fontSize: 11, padding: '5px 10px', borderRadius: 7, border: '1px solid #dde8f0', background: 'white', color: C.slate, cursor: 'pointer' }}>Clear</button>
    </div>
  )
}

// ── Feature 9: Freshness badge ────────────────────────────────────────────────
function FreshnessBadge({ fetchedAt }: { fetchedAt: Date }) {
  const [label, setLabel] = useState('')
  useEffect(() => {
    const update = () => {
      const diffMs = Date.now() - fetchedAt.getTime()
      const mins = Math.floor(diffMs / 60000)
      setLabel(mins < 1 ? 'just now' : mins === 1 ? '1 min ago' : `${mins} min ago`)
    }
    update()
    const t = setInterval(update, 30000)
    return () => clearInterval(t)
  }, [fetchedAt])
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9, padding: '3px 9px', borderRadius: 20, background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)', color: 'rgba(255,255,255,0.55)', flexShrink: 0 }}>
      <div style={{ width: 5, height: 5, borderRadius: '50%', background: C.green, flexShrink: 0 }} />
      Data {label}
    </div>
  )
}

// Shows when the report was last data-synced or AI-regenerated, from the backend
// timestamps. Picks the most recent and labels it accordingly.
function ActivityBadge({ activity }: { activity: { last_synced_at: string | null; last_regenerated_at: string | null } }) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(n => n + 1), 30000); return () => clearInterval(t) }, [])
  const parse = (s: string | null) => {
    if (!s) return null
    const d = new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z')
    return isNaN(d.getTime()) ? null : d
  }
  const synced = parse(activity.last_synced_at)
  const regen = parse(activity.last_regenerated_at)
  // Regenerate stamps both to the same instant → prefer the "Regenerated" label then.
  const isRegen = !!regen && (!synced || regen.getTime() >= synced.getTime())
  const when = isRegen ? regen : synced
  if (!when) return null
  const mins = Math.floor((Date.now() - when.getTime()) / 60000)
  const rel = mins < 1 ? 'just now' : mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.floor(mins / 60)}h ago` : `${Math.floor(mins / 1440)}d ago`
  return (
    <span
      title={`${isRegen ? 'Report rebuilt' : 'Data synced'} ${when.toLocaleString()}`}
      style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 9, padding: '3px 9px', borderRadius: 20, background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)', color: 'rgba(255,255,255,0.55)', flexShrink: 0 }}
    >
      <Clock size={9} /> {isRegen ? 'Regenerated' : 'Synced'} {rel}
    </span>
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
  const [showExport, setShowExport] = useState(false)
  const [showConnectDb, setShowConnectDb] = useState(false)
  const [agentError, setAgentError] = useState<string | null>(null)
  const [aiFallbackWarning, setAiFallbackWarning] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  // Discovery stage — mine the underlying tables for extra insights. Default ON.
  const [discoverEnabled, setDiscoverEnabled] = useState(() => {
    try { return localStorage.getItem(`intel_discover_${canvasId}`) !== '0' } catch { return true }
  })
  const discoverEnabledRef = useRef(discoverEnabled)
  useEffect(() => {
    discoverEnabledRef.current = discoverEnabled
    try { localStorage.setItem(`intel_discover_${canvasId}`, discoverEnabled ? '1' : '0') } catch {}
  }, [discoverEnabled, canvasId])
  const [syncMenuOpen, setSyncMenuOpen] = useState(false)
  const [autoSyncMins, setAutoSyncMins] = useState<number | null>(() => {
    try { return parseInt(localStorage.getItem(`intel_sync_${canvasId}`) ?? '') || null } catch { return null }
  })
  const syncMenuRef = useRef<HTMLDivElement>(null)
  const rerunRef = useRef<() => void>(() => {})
  const [activeSection, setActiveSection] = useState('')
  const [sectionLeaving, setSectionLeaving] = useState(false)
  const sectionNavPending = useRef<string>('')
  const handleNavSection = useCallback((id: string) => {
    if (id === activeSection) return
    setSectionLeaving(true)
    sectionNavPending.current = id
    setTimeout(() => {
      setActiveSection(sectionNavPending.current)
      setSectionLeaving(false)
    }, 240)
  }, [activeSection])
  const [briefExpanded, setBriefExpanded] = useState(true)

  // Feature 1: Time range override
  const [showDatePicker, setShowDatePicker] = useState(false)
  const [pendingDate, setPendingDate] = useState({ from: '', to: '' })
  const [appliedDateRange, setAppliedDateRange] = useState<{ from: string; to: string } | null>(null)
  const [dateLoading, setDateLoading] = useState(false)

  // Feature 2: Drill-through
  const [drillData, setDrillData] = useState<{ chart: AgentChart; segment: string; rows: Record<string, unknown>[]; columns: string[] } | null>(null)

  // Feature 3: Per-section regeneration
  const [regenSection, setRegenSection] = useState<string | null>(null)

  // Feature 5: Fullscreen chart
  const [fsChart, setFsChart] = useState<{ chart: AgentChart; colorIdx: number } | null>(null)

  // Feature 6: Presentation mode
  const [presenting, setPresenting] = useState(false)
  const [presentStartIdx, setPresentStartIdx] = useState(0)

  // Feature 7: Print mode
  const [printMode, setPrintMode] = useState(false)

  // Feature 8: Share modal
  const [shareModalUrl, setShareModalUrl] = useState<string | null>(null)

  // Feature 9: Data freshness
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null)
  // Persisted last data-sync / AI-regenerate timestamps (from the backend).
  const [lastActivity, setLastActivity] = useState<{ last_synced_at: string | null; last_regenerated_at: string | null } | null>(null)

  // Feature 10: Annotations
  const [annotations, setAnnotations] = useState<Record<string, string>>(() => {
    if (typeof window === 'undefined') return {}
    try { return JSON.parse(localStorage.getItem(`intel_annot_${canvasId}`) ?? '{}') } catch { return {} }
  })
  const [annotatingKey, setAnnotatingKey] = useState<string | null>(null)

  // Feature 12: Side-by-side comparison
  const [compareMode, setCompareMode] = useState(false)
  const [compareSectionId, setCompareSectionId] = useState('')

  // Save analysis + Copilot FAB
  const [saved, setSaved] = useState(false)
  const [hasSavedData, setHasSavedData] = useState(false)
  const [copilotOpen, setCopilotOpen] = useState(false)

  // Feature 2: map SQL → rows for drill-through
  const sqlToRows = React.useMemo(() => {
    const m = new Map<string, { rows: Record<string, unknown>[]; columns: string[] }>()
    for (const w of rawWidgets as WidgetInput[]) {
      if (w.sql_query && w.chart_data?.rows?.length) {
        m.set(w.sql_query.trim(), { rows: w.chart_data.rows, columns: w.chart_data.columns ?? [] })
      }
    }
    return m
  }, [rawWidgets])

  // Feature 10: save annotation to localStorage
  const saveAnnotation = useCallback((key: string, text: string) => {
    setAnnotations(prev => {
      const next = { ...prev, [key]: text }
      try { localStorage.setItem(`intel_annot_${canvasId}`, JSON.stringify(next)) } catch {}
      return next
    })
    setAnnotatingKey(null)
  }, [canvasId])

  const deleteAnnotation = useCallback((key: string) => {
    setAnnotations(prev => {
      const next = { ...prev }
      delete next[key]
      try { localStorage.setItem(`intel_annot_${canvasId}`, JSON.stringify(next)) } catch {}
      return next
    })
    setAnnotatingKey(null)
  }, [canvasId])

  // Feature 3: regenerate a single section — re-queries the DB first (force cache
  // bypass) so a section refresh reflects current values, then regenerates just
  // that section from the fresh data. (mergeWidgetData has stable [] deps, so it's
  // safe to call here without listing it in the dependency array.)
  const handleRegenSection = useCallback(async (sectionId: string) => {
    if (!canvas || !analysis) return
    const sec = analysis.sections.find(s => s.id === sectionId)
    if (!sec) return
    setRegenSection(sectionId)
    try {
      // 1) Pull fresh live rows for every widget (same path as Sync Now).
      let widgets = rawWidgets
      try {
        setAgentStep('Fetching live data…')
        const liveResp = await intelligenceApi.fetchWidgetData(canvasId, appliedDateRange ?? undefined, true)
        widgets = mergeWidgetData(rawWidgets, liveResp.data?.widget_data ?? [])
        setRawWidgets(widgets)
        setLastFetchedAt(new Date())
      } catch { /* fall back to currently-loaded data */ }

      // 2) Regenerate the section from the (now fresh) data.
      const updated = await runSectionAgent(
        { projectId, canvasId, canvasName: String(canvas?.name ?? 'Report'), widgets: widgets as never, shareToken: shareToken || undefined, dateRange: appliedDateRange ?? undefined },
        sec.label,
        analysis,
        s => setAgentStep(s),
      )
      if (updated) {
        setAnalysis(prev => prev ? {
          ...prev,
          sections: prev.sections.map(s => s.id === sectionId ? { ...updated, id: sectionId } : s),
        } : prev)
      }
    } catch { /* keep old section */ }
    finally { setRegenSection(null) }
  }, [canvas, analysis, projectId, canvasId, rawWidgets, shareToken, appliedDateRange])

  // Merge fresh bulk-fetched rows into the widget array from the canvas API.
  // Fix C: normalize both sides of the widget_id comparison to lowercase without dashes
  // so UUID format differences (e.g. "abc-123" vs "abc123") never cause silent mismatches.
  const mergeWidgetData = useCallback(
    (baseWidgets: unknown[], liveData: Array<{ widget_id: string; ok: boolean; rows?: Record<string, unknown>[]; columns?: string[]; labels?: string[]; values?: unknown[] }>) => {
      const norm = (id: unknown) => String(id ?? '').toLowerCase().replace(/-/g, '')
      const byId = new Map(liveData.filter(d => d.ok).map(d => [norm(d.widget_id), d]))
      const merged = (baseWidgets as Record<string, unknown>[]).map(w => {
        const live = byId.get(norm(w.id))
        if (!live) return w
        const existingCd = (w.chart_data as Record<string, unknown>) ?? {}
        const existingRows = (existingCd.rows as unknown[]) ?? []
        const existingCols = (existingCd.columns as string[]) ?? []
        const existingLabels = (existingCd.labels as unknown[]) ?? []
        const existingValues = (existingCd.values as unknown[]) ?? []
        const liveRows = live.rows ?? []
        const liveCols = live.columns ?? []
        const liveLabels = live.labels ?? []
        const liveValues = live.values ?? []
        return {
          ...w,
          chart_data: {
            ...existingCd,
            // always keep whichever source has more rows — never downgrade
            rows: liveRows.length > existingRows.length ? liveRows : existingRows,
            columns: liveCols.length > 0 ? liveCols : existingCols,
            labels: liveLabels.length > existingLabels.length ? liveLabels : existingLabels,
            values: liveValues.length > existingValues.length ? liveValues : existingValues,
          },
        }
      })
      const enriched = merged.filter(w => ((w as Record<string,unknown>).chart_data as Record<string,unknown>)?.rows && ((((w as Record<string,unknown>).chart_data as Record<string,unknown>).rows) as unknown[]).length > 0).length
      if (enriched === 0 && liveData.filter(d => d.ok).length > 0) {
        console.warn('[intelligence:merge] ZERO widgets enriched — widget_id mismatch? sample ids:', liveData.slice(0,2).map(d=>d.widget_id), 'widget ids:', (baseWidgets as Record<string,unknown>[]).slice(0,2).map(w=>w.id))
      } else {
        console.log(`[intelligence:merge] enriched=${enriched}/${baseWidgets.length} widgets with live rows`)
      }
      return merged
    },
    [],
  )

  // Build suggested questions from the analysis for the copilot
  const analysisSuggestions = React.useMemo<string[]>(() => {
    if (!analysis) return []
    const qs: string[] = []
    const sec = analysis.sections.find(s => s.id === activeSection) ?? analysis.sections[0]
    if (sec?.key_finding) qs.push(`What is driving: "${sec.key_finding.slice(0, 60)}"?`)
    if (sec?.recommendation) qs.push(`How do I action this: "${sec.recommendation.slice(0, 60)}"?`)
    analysis.sections.slice(0, 3).forEach(s => {
      if (s.charts[0]) qs.push(`Show the trend for "${s.charts[0].title}"`)
    })
    const kpi = analysis.kpis[0]
    if (kpi) qs.push(`What is driving "${kpi.label}" being ${kpi.value}?`)
    qs.push('Which areas need the most attention?', 'What are the top risks in this report?', 'Summarize the key takeaways')
    return Array.from(new Set(qs)).slice(0, 6)
  }, [analysis, activeSection])

  // Add copilot-generated charts to the active intelligence section
  const handleAddToPage = useCallback((charts: Array<{ title: string; chart_type: string; chart_data?: { labels?: unknown[]; values?: unknown[]; rows?: Record<string, unknown>[]; columns?: string[] } } | undefined>) => {
    if (!analysis) return
    const targetId = activeSection || analysis.sections[0]?.id

    const isNum = (v: unknown) => v !== null && v !== undefined && v !== '' && !isNaN(Number(v))

    const newCharts: AgentChart[] = charts.filter(Boolean).map(c => {
      const ct = c!
      const baseType = (['bar_vertical', 'bar'].includes(ct.chart_type) ? 'bar'
        : ct.chart_type === 'line' ? 'line'
        : ct.chart_type === 'area' ? 'area'
        : ['pie', 'donut'].includes(ct.chart_type) ? 'pie'
        : 'bar') as AgentChart['type']

      // Rebuild from rows+columns when available — the backend's labels/values
      // mis-handle 3-column results (it puts the 2nd column, often a text
      // dimension, into `values`, which renders as NaN = a blank chart).
      const rows = (ct.chart_data?.rows ?? []) as Record<string, unknown>[]
      const columns = (ct.chart_data?.columns ?? []) as string[]
      if (rows.length && columns.length >= 2) {
        const numericCols = columns.filter(col => {
          const sample = rows.slice(0, 12).filter(r => r[col] !== null && r[col] !== undefined && r[col] !== '')
          return sample.length > 0 && sample.every(r => isNum(r[col]))
        })
        const dimCols = columns.filter(col => !numericCols.includes(col))
        const nameCol = dimCols[0] ?? columns[0]
        const measureCol = numericCols[0]

        // 2 dimensions + 1 measure (e.g. month × status × count) → pivot the
        // second dimension into series so every category renders.
        if (measureCol && dimCols.length >= 2 && baseType !== 'pie') {
          const seriesCol = dimCols[1]
          const seriesKeys = Array.from(new Set(rows.map(r => String(r[seriesCol] ?? '')))).slice(0, 12)
          const byName = new Map<string, Record<string, unknown>>()
          for (const r of rows) {
            const nm = String(r[nameCol] ?? '')
            if (!byName.has(nm)) byName.set(nm, { name: nm })
            byName.get(nm)![String(r[seriesCol] ?? '')] = Number(r[measureCol] ?? 0)
          }
          const data = Array.from(byName.values()).map(d => {
            for (const k of seriesKeys) if (!(k in d)) d[k] = 0
            return d
          }) as unknown as AgentChart['data']
          return {
            title: ct.title,
            type: 'combo' as AgentChart['type'],
            data,
            series: seriesKeys.map(k => ({ key: k, type: (baseType === 'line' ? 'line' : 'bar') as 'bar' | 'line' })),
          }
        }

        // 1 dimension + 1 measure → simple name/value.
        if (measureCol) {
          return {
            title: ct.title,
            type: baseType,
            data: rows.map(r => ({ name: String(r[nameCol] ?? ''), value: Number(r[measureCol] ?? 0) })),
          }
        }
      }

      // Fallback: labels/values (2-column results work fine here).
      const labels = ct.chart_data?.labels ?? []
      const values = ct.chart_data?.values ?? []
      return {
        title: ct.title,
        type: baseType,
        data: (labels as unknown[]).map((l, i) => ({ name: String(l ?? ''), value: Number((values as unknown[])[i] ?? 0) })),
      }
    })
    if (!newCharts.length) return
    setAnalysis(prev => prev ? {
      ...prev,
      sections: prev.sections.map(s =>
        s.id === targetId ? { ...s, charts: [...s.charts, ...newCharts] } : s
      ),
    } : prev)
  }, [analysis, activeSection])

  // Persist a generated report durably: server-side (survives device/browser
  // changes and quota limits) + localStorage as a fast local cache.
  const persistReport = useCallback(async (result: ExecutiveAnalysis) => {
    try { localStorage.setItem(`intel_analysis_${canvasId}`, JSON.stringify(result)) } catch {}
    try { await intelligenceApi.saveReport(canvasId, result) } catch (e) { console.warn('[intelligence] server save failed (kept local):', e) }
    setHasSavedData(true)
  }, [canvasId])

  const saveAnalysis = useCallback(() => {
    if (!analysis) return
    void persistReport(analysis)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }, [analysis, persistReport])

  useEffect(() => {
    let cancelled = false
    const LOCK_KEY    = `intel_lock_${canvasId}`
    const RESULT_KEY  = `intel_analysis_${canvasId}`
    const LOCK_STALE  = 10 * 60 * 1000  // 10 min — stale lock threshold

    const load = async () => {
      // ── 1. Check for saved analysis in localStorage ──────────────────────
      let savedObj: ExecutiveAnalysis | null = null
      try {
        const savedRaw = localStorage.getItem(RESULT_KEY)
        if (savedRaw) {
          const parsed = JSON.parse(savedRaw) as ExecutiveAnalysis
          if (parsed.sections?.length) savedObj = parsed
        }
      } catch {}

      // ── 2. Always fetch canvas metadata (projectId, shareToken) ──────────
      setAgentStatus('loading_canvas')
      try {
        const [canvasResp, sharesResp] = await Promise.all([
          canvasApi.get(canvasId),
          shareApi.list(canvasId).catch(() => ({ data: null })),
        ])
        if (cancelled) return

        const detail = canvasResp.data?.dashboard ?? canvasResp.data
        let widgets: unknown[] = detail?.widgets ?? []
        setCanvas(detail); setProjectId(detail?.project_id ?? '')
        setLastActivity({
          last_synced_at: (detail as { last_synced_at?: string | null } | null)?.last_synced_at ?? null,
          last_regenerated_at: (detail as { last_regenerated_at?: string | null } | null)?.last_regenerated_at ?? null,
        })
        const sharesList: unknown[] = sharesResp.data?.shares ?? (Array.isArray(sharesResp.data) ? sharesResp.data : [])
        const token = sharesList.length ? ((sharesList[0] as Record<string,string>).token ?? '') : ''
        if (token) setShareToken(token)

        // ── Server-saved report takes priority — durable across devices/browsers.
        //    Once a report exists we render it as-is and NEVER auto-regenerate;
        //    only an explicit Sync / Regenerate rebuilds it.
        let serverAnalysis: ExecutiveAnalysis | null = null
        try {
          const rep = await intelligenceApi.getReport(canvasId)
          const a = rep.data?.analysis as ExecutiveAnalysis | null
          if (a && a.sections?.length) serverAnalysis = a
        } catch { /* fall through to local cache / generate */ }
        if (cancelled) return

        if (serverAnalysis) {
          setRawWidgets(widgets)   // must set before return so copilot has connection_id
          setAnalysis(serverAnalysis); setActiveSection(serverAnalysis.sections[0]?.id ?? '')
          setAgentStatus('done'); setHasSavedData(true)
          try { localStorage.setItem(RESULT_KEY, JSON.stringify(serverAnalysis)) } catch {}
          return
        }

        // Legacy local copy — use it AND migrate it to the server so it's durable.
        if (savedObj) {
          setRawWidgets(widgets)
          setAnalysis(savedObj); setActiveSection(savedObj.sections[0]?.id ?? '')
          setAgentStatus('done'); setHasSavedData(true)
          try { await intelligenceApi.saveReport(canvasId, savedObj) } catch {}
          return
        }

        // ── 3. Cross-tab lock: only one tab runs the analysis ─────────────
        const existingLock = localStorage.getItem(LOCK_KEY)
        const lockAge = existingLock ? Date.now() - Number(existingLock) : Infinity
        const lockHeld = existingLock && lockAge < LOCK_STALE

        if (lockHeld) {
          // Another tab is already running — wait for it to write the result
          console.log('[intelligence:page] another tab is running analysis, waiting…')
          setAgentStatus('running')
          setAgentStep('Another tab is generating this report — waiting…')
          const POLL_INTERVAL = 3000, MAX_WAIT = 5 * 60 * 1000
          const started = Date.now()
          await new Promise<void>(resolve => {
            const poll = setInterval(() => {
              if (cancelled) { clearInterval(poll); resolve(); return }
              try {
                const raw = localStorage.getItem(RESULT_KEY)
                if (raw) {
                  const parsed = JSON.parse(raw) as ExecutiveAnalysis
                  if (parsed.sections?.length) {
                    clearInterval(poll)
                    if (!cancelled) {
                      setAnalysis(parsed); setActiveSection(parsed.sections[0]?.id ?? '')
                      setAgentStatus('done'); setHasSavedData(true)
                    }
                    resolve(); return
                  }
                }
              } catch {}
              // Lock gone or stale — take over
              const currentLock = localStorage.getItem(LOCK_KEY)
              const currentAge = currentLock ? Date.now() - Number(currentLock) : Infinity
              if (!currentLock || currentAge >= LOCK_STALE || Date.now() - started > MAX_WAIT) {
                clearInterval(poll); resolve()
              }
            }, POLL_INTERVAL)
          })
          if (cancelled) return
          // If we got here due to timeout/lock-gone but still no result, fall through to compute
          const fallbackRaw = localStorage.getItem(RESULT_KEY)
          if (fallbackRaw) return  // resolved above already set state
          // else fall through and compute
        }

        // ── 4. Acquire lock and run analysis ─────────────────────────────
        localStorage.setItem(LOCK_KEY, String(Date.now()))

        // Bulk-fetch live SQL rows for every widget
        try {
          setAgentStep('Fetching live data for all widgets…')
          const liveResp = await intelligenceApi.fetchWidgetData(canvasId)
          if (cancelled) { localStorage.removeItem(LOCK_KEY); return }
          const liveData = liveResp.data?.widget_data ?? []
          widgets = mergeWidgetData(widgets, liveData)
          const upgraded = liveData.filter(d => d.ok && (d.rows?.length ?? 0) > 0).length
          console.log(`[intelligence:page] bulk-fetch done  upgraded=${upgraded}/${liveData.length} widgets`)
          setLastFetchedAt(new Date())
        } catch (e) {
          if (cancelled) { localStorage.removeItem(LOCK_KEY); return }
          console.warn('[intelligence:page] bulk-fetch failed, continuing with cached data:', e)
        }

        if (cancelled) { localStorage.removeItem(LOCK_KEY); return }
        setRawWidgets(widgets)
        setAgentStatus('running')
        setAiFallbackWarning(false)
        const result = await runIntelligenceAgent(
          { projectId: detail?.project_id ?? '', canvasId, canvasName: detail?.name ?? 'Report', widgets: widgets as never, shareToken: token || undefined, discover: discoverEnabledRef.current },
          s => { if (!cancelled) setAgentStep(s) },
        )
        if (cancelled) { localStorage.removeItem(LOCK_KEY); return }
        if ((result as Record<string,unknown>)._fallback) setAiFallbackWarning(true)
        setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
        setAgentStatus('done')
        // Auto-save (server + local) so future visits load it as-is and never
        // regenerate — and so other tabs pick it up. Skip persisting a fallback.
        if (!(result as unknown as Record<string, unknown>)._fallback) {
          void persistReport(result)
        } else {
          try { localStorage.setItem(RESULT_KEY, JSON.stringify(result)) } catch {}
        }
      } catch (err) {
        if (cancelled) return
        setAgentError(err instanceof Error ? err.message : 'Failed to load')
        setAgentStatus('error')
      } finally {
        localStorage.removeItem(LOCK_KEY)
      }
    }
    load()
    return () => { cancelled = true }
  }, [canvasId, mergeWidgetData])

  // Feature 1: apply date range and re-generate
  const applyDateRange = useCallback(async () => {
    if (!canvas || !pendingDate.from || !pendingDate.to) return
    const dr = { from: pendingDate.from, to: pendingDate.to }
    setAppliedDateRange(dr)
    // A date-range change must produce a genuinely fresh report — drop the saved
    // snapshot and force both the data re-query and the AI rebuild (no cache).
    try { localStorage.removeItem(`intel_analysis_${canvasId}`) } catch {}
    setHasSavedData(false)
    setDateLoading(true); setAgentStatus('running'); setAgentError(null); setAiFallbackWarning(false)
    try {
      let widgets = rawWidgets
      try {
        const liveResp = await intelligenceApi.fetchWidgetData(canvasId, dr, true)
        widgets = mergeWidgetData(rawWidgets, liveResp.data?.widget_data ?? [])
        setRawWidgets(widgets)
        setLastFetchedAt(new Date())
      } catch { /* keep stale */ }
      const result = await runIntelligenceAgent(
        { projectId, canvasId, canvasName: String(canvas?.name ?? 'Report'), widgets: widgets as never, shareToken: shareToken || undefined, dateRange: dr, force: true, discover: discoverEnabled },
        s => setAgentStep(s),
      )
      if ((result as Record<string,unknown>)._fallback) setAiFallbackWarning(true)
      setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
      setAgentStatus('done')
      void persistReport(result)   // durable save (server + local)
      // Date-range apply re-fetches data AND rebuilds the report → record a regenerate.
      try { const r = await dashboardApi.recordActivity(canvasId, 'regenerate'); setLastActivity(r.data) } catch {}
    } catch {
      setAgentStatus('done')
    } finally { setDateLoading(false) }
  }, [canvas, pendingDate, rawWidgets, projectId, canvasId, shareToken, mergeWidgetData, discoverEnabled, persistReport])

  const rerun = useCallback(async () => {
    if (!canvas || rerunning) return
    // Clear any saved analysis so fresh data is used
    try { localStorage.removeItem(`intel_analysis_${canvasId}`) } catch {}
    setHasSavedData(false)
    setRerunning(true); setAgentStatus('running'); setAgentError(null); setAiFallbackWarning(false)
    try {
      // Re-fetch live data on regenerate too — force a cache bypass so the user
      // gets genuinely fresh rows, not the cached intelligence-data payload.
      let widgets = rawWidgets
      try {
        const liveResp = await intelligenceApi.fetchWidgetData(canvasId, appliedDateRange ?? undefined, true)
        widgets = mergeWidgetData(rawWidgets, liveResp.data?.widget_data ?? [])
        setRawWidgets(widgets)
        setLastFetchedAt(new Date())
      } catch { /* keep stale data */ }

      const result = await runIntelligenceAgent(
        { projectId, canvasId, canvasName: String(canvas?.name ?? 'Report'), widgets: widgets as never, shareToken: shareToken || undefined, force: true, discover: discoverEnabled },
        s => setAgentStep(s),
      )
      if ((result as Record<string,unknown>)._fallback) setAiFallbackWarning(true)
      setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
      setAgentStatus('done')
      void persistReport(result)   // durable save (server + local)
      // Sync Now re-fetches live data → record a data sync.
      try { const r = await dashboardApi.recordActivity(canvasId, 'sync'); setLastActivity(r.data) } catch {}
    } catch {
      const fb = buildFallbackAnalysis(String(canvas?.name ?? 'Report'), rawWidgets as never)
      setAnalysis(fb); setActiveSection(fb.sections[0]?.id ?? '')
      setAiFallbackWarning(true)
      setAgentStatus('done')
    } finally { setRerunning(false) }
  }, [canvas, rerunning, projectId, canvasId, rawWidgets, shareToken, mergeWidgetData, appliedDateRange, discoverEnabled, persistReport])

  // Regenerate from scratch — rebuild the AI report on the CURRENT data without
  // re-querying the database. Distinct from Sync Now (which also re-fetches data).
  // Clears the saved snapshot and forces the orchestrator cache bypass.
  const regenerate = useCallback(async () => {
    if (!canvas || rerunning || regenerating) return
    try { localStorage.removeItem(`intel_analysis_${canvasId}`) } catch {}
    setHasSavedData(false)
    setRegenerating(true); setAgentStatus('running'); setAgentError(null); setAiFallbackWarning(false)
    try {
      const result = await runIntelligenceAgent(
        { projectId, canvasId, canvasName: String(canvas?.name ?? 'Report'), widgets: rawWidgets as never, shareToken: shareToken || undefined, dateRange: appliedDateRange ?? undefined, force: true, discover: discoverEnabled },
        s => setAgentStep(s),
      )
      if ((result as unknown as Record<string, unknown>)._fallback) setAiFallbackWarning(true)
      setAnalysis(result); setActiveSection(result.sections[0]?.id ?? '')
      setAgentStatus('done')
      void persistReport(result)   // durable save (server + local)
      // Full AI rebuild → record a regenerate.
      try { const r = await dashboardApi.recordActivity(canvasId, 'regenerate'); setLastActivity(r.data) } catch {}
    } catch {
      setAgentStatus('done')
    } finally { setRegenerating(false) }
  }, [canvas, rerunning, regenerating, projectId, canvasId, rawWidgets, shareToken, appliedDateRange, discoverEnabled, persistReport])

  // Keep rerunRef current so the auto-sync interval never closes over a stale rerun
  useEffect(() => { rerunRef.current = rerun }, [rerun])

  // Auto-sync interval — DISABLED by request: a saved report must never regenerate
  // on its own; it only rebuilds when the user explicitly clicks Sync or Regenerate.
  // (Schedule UI is retained but does not auto-fire.)
  useEffect(() => {
    void autoSyncMins
    void rerunRef
  }, [autoSyncMins])

  // Close sync dropdown on outside click
  useEffect(() => {
    if (!syncMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (syncMenuRef.current && !syncMenuRef.current.contains(e.target as Node)) setSyncMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [syncMenuOpen])

  const SYNC_OPTS = [
    { label: 'Every 30 min', mins: 30 },
    { label: 'Every hour',   mins: 60 },
    { label: 'Every 6 hours', mins: 360 },
    { label: 'Daily',        mins: 1440 },
  ]

  function applySchedule(mins: number | null) {
    setAutoSyncMins(mins)
    try {
      if (mins) localStorage.setItem(`intel_sync_${canvasId}`, String(mins))
      else localStorage.removeItem(`intel_sync_${canvasId}`)
    } catch {}
    setSyncMenuOpen(false)
  }

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
    <div className="flex-1 min-h-0" style={{ display: 'grid', gridTemplateColumns: `${RAIL_COLLAPSED}px 1fr`, overflow: 'hidden' }}>

      {/* Left rail */}
      <LeftRail
        sections={analysis.sections}
        active={activeSection}
        onNav={handleNavSection}
        onBack={() => router.back()}
        actions={[
          { key: 'pdf', icon: <Printer size={18} />, label: 'Export PDF', onClick: () => { setPrintMode(true); setTimeout(() => { window.print(); setPrintMode(false) }, 150) } },
          { key: 'export', icon: <Download size={18} />, label: 'Export .vly', onClick: () => setShowExport(true), tint: C.teal2 },
          { key: 'share', icon: <Link size={18} />, label: 'Share report', onClick: () => setShareModalUrl(window.location.href) },
        ]}
      />

      {/* Main content */}
      <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', background: C.bg }}>

        {/* Header */}
        <div style={{
          background: `linear-gradient(135deg, ${C.navy} 0%, #0d3060 100%)`,
          padding: '12px 22px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
          boxShadow: '0 2px 16px rgba(8,33,58,0.2)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
            <div style={{ width: 34, height: 34, borderRadius: 10, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: `0 4px 12px ${C.teal}50` }}><Zap size={15} style={{ color: 'white' }} /></div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                {/* Real canvas/report name — never the AI-renamed title */}
                <h1 style={{ fontSize: 15, fontWeight: 700, color: 'white', margin: 0, lineHeight: 1.25, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{String(canvas?.name ?? analysis.title)}</h1>
                <span style={{ fontSize: 9, fontWeight: 700, color: C.teal2, background: `${C.teal}25`, border: `1px solid ${C.teal}50`, borderRadius: 20, padding: '2px 8px', letterSpacing: '0.06em', flexShrink: 0 }}>OPUS AI</span>
              {hasSavedData && <span style={{ fontSize: 9, fontWeight: 700, color: C.green, background: `${C.green}20`, border: `1px solid ${C.green}50`, borderRadius: 20, padding: '2px 8px', letterSpacing: '0.06em', flexShrink: 0 }}>SAVED</span>}
              </div>
              {/* AI executive framing kept as the descriptor line */}
              <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{analysis.title || analysis.subtitle}</p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {lastActivity && (lastActivity.last_synced_at || lastActivity.last_regenerated_at)
              ? <ActivityBadge activity={lastActivity} />
              : lastFetchedAt && <FreshnessBadge fetchedAt={lastFetchedAt} />}
            {/* Feature 1: date range toggle */}
            <button onClick={() => setShowDatePicker(p => !p)} title="Filter by date range" className="intel-hdr-btn" style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${showDatePicker ? C.teal : 'rgba(255,255,255,0.2)'}`, background: showDatePicker ? `${C.teal}30` : 'rgba(255,255,255,0.1)', color: showDatePicker ? C.teal2 : 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
              <CalendarRange size={11} />{appliedDateRange ? `${appliedDateRange.from.slice(5)} → ${appliedDateRange.to.slice(5)}` : 'Date'}
            </button>
            {/* Feature 6: present — temporarily disabled (re-enable later) */}
            {/* <button onClick={() => { setPresentStartIdx(analysis.sections.findIndex(s => s.id === activeSection)); setPresenting(true) }} title="Presentation mode" className="intel-hdr-btn" style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.2)', background: 'rgba(255,255,255,0.1)', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
              <Play size={11} /> Present
            </button> */}
            {/* Data-source indicator: Offline pill + connect button when offline;
                a green "Live" pill when connected to a database. */}
            {(((canvas as { is_offline?: boolean } | null)?.is_offline) || (canvas?.layout_config as { data_mode?: string } | undefined)?.data_mode === 'offline') ? (
              <>
                <span
                  title="This canvas was imported without a live database. The report and copilot run on the bundled table data."
                  style={{ padding: '5px 10px', borderRadius: 8, border: `1px solid ${C.teal}60`, background: `${C.teal}20`, color: C.teal2, display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 600 }}
                >
                  <Database size={11} /> Offline data
                </span>
                <button
                  onClick={() => setShowConnectDb(true)}
                  title="Connect a live database — switch this canvas from bundled offline data to real-time data"
                  style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${C.violet}70`, background: `${C.violet}25`, color: '#c4b5fd', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 700 }}
                >
                  <Database size={11} /> Connect live DB
                </button>
              </>
            ) : (
              <span
                title={(() => {
                  const hint = (canvas?.layout_config as { connection_hint?: { host?: string; db_type?: string; database_name?: string } } | undefined)?.connection_hint
                  return hint?.host ? `Connected to ${hint.db_type ?? 'database'}${hint.database_name ? ` · ${hint.database_name}` : ''}` : 'Connected to a live database'
                })()}
                style={{ padding: '5px 10px', borderRadius: 8, border: `1px solid ${C.green}60`, background: `${C.green}20`, color: C.green, display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 600 }}
              >
                <Wifi size={11} /> Live
                {(() => {
                  const hint = (canvas?.layout_config as { connection_hint?: { db_type?: string } } | undefined)?.connection_hint
                  return hint?.db_type ? <span style={{ opacity: 0.8 }}>· {hint.db_type}</span> : null
                })()}
              </span>
            )}
            {/* PDF / Export .vly / Share moved into the LeftRail to declutter the header */}
            {/* Save button (kept in the top bar) */}
            <button onClick={saveAnalysis} title={hasSavedData ? 'Analysis saved locally' : 'Save analysis to browser'} style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${saved ? C.green + '80' : 'rgba(255,255,255,0.2)'}`, background: saved ? `${C.green}25` : 'rgba(255,255,255,0.1)', color: saved ? C.green : 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, transition: 'all 0.2s' }}>
              {saved ? <BookmarkCheck size={11} /> : <Bookmark size={11} />}
              {saved ? 'Saved!' : hasSavedData ? 'Re-save' : 'Save'}
            </button>
            {/* Compare (moved from section tabs) — temporarily disabled (re-enable later) */}
            {/* <button onClick={() => { setCompareMode(p => !p); if (!compareMode && analysis.sections[1]) setCompareSectionId(analysis.sections[1].id) }} title="Side-by-side compare" style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${compareMode ? C.violet + '80' : 'rgba(255,255,255,0.2)'}`, background: compareMode ? `${C.violet}30` : 'rgba(255,255,255,0.1)', color: compareMode ? '#c4b5fd' : 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, transition: 'all 0.2s' }}>
              <Columns size={11} /> Compare
            </button> */}
            {/* Regenerate from scratch — rebuilds the AI report on current data, no DB re-query */}
            <button
              onClick={regenerate}
              disabled={regenerating || rerunning}
              title="Rebuild the AI report from scratch using the current data (no database re-query)"
              style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '6px 12px', fontSize: 11, fontWeight: 700,
                cursor: (regenerating || rerunning) ? 'not-allowed' : 'pointer',
                background: regenerating ? `${C.violet}40` : `${C.violet}25`,
                border: `1px solid ${C.violet}70`, borderRadius: 8, color: '#c4b5fd',
                transition: 'all 0.15s',
              }}
            >
              <Sparkles size={11} style={regenerating ? { animation: 'ispin 1s linear infinite' } : {}} />
              {regenerating ? 'Regenerating…' : 'Regenerate'}
            </button>
            {/* ── Sync split-button ── */}
            <div ref={syncMenuRef} style={{ position: 'relative', display: 'flex' }}>
              {/* Left: Sync Now */}
              <button
                onClick={rerun}
                disabled={rerunning}
                title="Fetch fresh data from the database, then regenerate the report"
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '6px 12px', fontSize: 11, fontWeight: 700, cursor: rerunning ? 'not-allowed' : 'pointer',
                  background: rerunning ? `${C.teal}40` : `${C.teal}30`,
                  border: `1px solid ${C.teal}70`, borderRight: 'none',
                  borderRadius: '8px 0 0 8px', color: C.teal2,
                  transition: 'all 0.15s',
                }}
              >
                <RefreshCw size={11} style={rerunning ? { animation: 'ispin 1s linear infinite' } : {}} />
                {rerunning ? 'Syncing…' : 'Sync Now'}
                {autoSyncMins && !rerunning && (
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: C.green, flexShrink: 0 }} />
                )}
              </button>
              {/* Right: Schedule dropdown trigger */}
              <button
                onClick={() => setSyncMenuOpen(p => !p)}
                title="Schedule auto-sync"
                style={{
                  display: 'flex', alignItems: 'center', padding: '6px 8px',
                  background: syncMenuOpen ? `${C.teal}40` : `${C.teal}20`,
                  border: `1px solid ${C.teal}70`,
                  borderRadius: '0 8px 8px 0', color: C.teal2, cursor: 'pointer',
                  transition: 'all 0.15s',
                }}
              >
                <ChevronDown size={12} style={{ transition: 'transform 0.2s', transform: syncMenuOpen ? 'rotate(180deg)' : 'rotate(0deg)' }} />
              </button>

              {/* Dropdown */}
              {syncMenuOpen && (
                <div style={{
                  position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 9999,
                  background: 'white', borderRadius: 12, border: '1px solid #e2eaf4',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.14)', minWidth: 200, overflow: 'hidden',
                  animation: 'slideDown 0.18s ease both',
                }}>
                  {/* Sync Now row */}
                  <button
                    onClick={() => { rerun(); setSyncMenuOpen(false) }}
                    disabled={rerunning}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center', gap: 9,
                      padding: '11px 14px', background: 'none', border: 'none',
                      borderBottom: '1px solid #f1f5f9', cursor: 'pointer',
                      fontSize: 12, fontWeight: 600, color: C.navy,
                    }}
                  >
                    <RefreshCw size={13} style={{ color: C.teal }} />
                    Sync Now
                    <span style={{ marginLeft: 'auto', fontSize: 10, color: C.muted }}>fetch + regenerate</span>
                  </button>

                  {/* Discovery toggle */}
                  <button
                    onClick={() => setDiscoverEnabled(v => !v)}
                    title="When on, the agent mines the underlying tables for extra insights beyond the report's widgets"
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center', gap: 9,
                      padding: '11px 14px', background: 'none', border: 'none',
                      borderBottom: '1px solid #f1f5f9', cursor: 'pointer',
                      fontSize: 12, fontWeight: 600, color: C.navy,
                    }}
                  >
                    <Sparkles size={13} style={{ color: discoverEnabled ? C.violet : C.muted }} />
                    Discover extra insights
                    <span style={{
                      marginLeft: 'auto', fontSize: 10, fontWeight: 700,
                      color: discoverEnabled ? C.green : C.muted,
                      background: discoverEnabled ? `${C.green}18` : '#f1f5f9',
                      borderRadius: 20, padding: '2px 8px',
                    }}>{discoverEnabled ? 'ON' : 'OFF'}</span>
                  </button>

                  {/* Schedule section */}
                  <div style={{ padding: '8px 14px 4px' }}>
                    <p style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 6px' }}>Auto-sync</p>
                  </div>
                  {SYNC_OPTS.map(opt => (
                    <button
                      key={opt.mins}
                      onClick={() => applySchedule(autoSyncMins === opt.mins ? null : opt.mins)}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center', gap: 9,
                        padding: '9px 14px', background: autoSyncMins === opt.mins ? `${C.teal}08` : 'none',
                        border: 'none', cursor: 'pointer', fontSize: 12,
                        fontWeight: autoSyncMins === opt.mins ? 700 : 400,
                        color: autoSyncMins === opt.mins ? C.teal : C.navy,
                      }}
                    >
                      <Clock size={12} style={{ color: autoSyncMins === opt.mins ? C.teal : C.muted, flexShrink: 0 }} />
                      {opt.label}
                      {autoSyncMins === opt.mins && (
                        <span style={{ marginLeft: 'auto', fontSize: 10, fontWeight: 700, color: C.green }}>● Active</span>
                      )}
                    </button>
                  ))}
                  {autoSyncMins && (
                    <button
                      onClick={() => applySchedule(null)}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center', gap: 9,
                        padding: '9px 14px', background: 'none', border: 'none',
                        borderTop: '1px solid #f1f5f9', cursor: 'pointer',
                        fontSize: 12, color: C.red, fontWeight: 600,
                      }}
                    >
                      Cancel schedule
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Feature 1: date range picker bar */}
        {showDatePicker && (
          <DateRangeBar
            value={pendingDate}
            onChange={setPendingDate}
            onApply={applyDateRange}
            onClear={() => { setAppliedDateRange(null); setPendingDate({ from: '', to: '' }) }}
            loading={dateLoading}
          />
        )}

        {/* Fix F: fallback warning — AI response failed, showing deterministic output */}
        {aiFallbackWarning && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            background: '#fef3c7', border: '1px solid #f59e0b',
            borderRadius: 8, padding: '8px 14px', margin: '0 22px',
            fontSize: 13, color: '#92400e',
          }}>
            <span style={{ fontSize: 16 }}>⚠️</span>
            <span>
              <strong>AI analysis unavailable</strong> — showing deterministic report from raw data.
              The /intelligence/analyze endpoint may be unreachable or the response was malformed.
              Check backend logs for <code>[intelligence/analyze]</code> errors.
            </span>
            <button
              onClick={() => setAiFallbackWarning(false)}
              style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: '#92400e', fontWeight: 700 }}
            >✕</button>
          </div>
        )}

        {/* Scrollable body */}
        <div className="print-zone" style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '22px 28px' }}>
          <div style={{ maxWidth: 1280, marginLeft: 'auto', marginRight: 'auto', width: '100%', boxSizing: 'border-box' }}>

          {/* KPI bar — tab-scoped, max 6, fully remounts on tab switch */}
          {(() => {
            const isFirstSection = analysis.sections[0]?.id === activeSection
            const sectionKpis   = currentSection?.kpis ?? []
            const source =
              sectionKpis.length > 0 ? sectionKpis
              : isFirstSection         ? analysis.kpis
              : []
            const seen = new Set<string>()
            const displayKpis: AgentKPI[] = []
            for (const k of source) {
              const key = normLabel(k.label)
              if (!seen.has(key)) { seen.add(key); displayKpis.push(k) }
            }
            const visible  = displayKpis.slice(0, 6)
            const overflow = displayKpis.length - visible.length
            if (visible.length === 0) return null
            return (
              <div key={`kpi-bar-${activeSection}`} style={{ marginBottom: 20 }}>
                {/* auto-fill + a capped max track (190–240px) so cards stay a uniform,
                    comfortable size — they never balloon when there are only 2–3 of them. */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 240px))', gap: 12, alignItems: 'stretch' }}>
                  {visible.map((k, i) => (
                    <div key={i} className={`intel-card intel-kpi-${i}`} style={{ display: 'flex' }}>
                      <KpiCard kpi={k} idx={i} />
                    </div>
                  ))}
                </div>
                {overflow > 0 && (
                  <p style={{ fontSize: 11, color: C.muted, margin: '8px 0 0', textAlign: 'right' }}>
                    +{overflow} more KPIs in the section detail below
                  </p>
                )}
              </div>
            )
          })()}


          {/* Active section — or side-by-side (Feature 12) */}
          {!compareMode && currentSection && (
            <div key={currentSection.id} className={`intel-section-enter${sectionLeaving ? ' intel-section-leave' : ''}`}>
            <SectionContent
              section={currentSection} sectionIdx={sectionIdx}
              globalKpis={analysis.kpis}
              onRegenSection={() => handleRegenSection(currentSection.id)}
              regenning={regenSection === currentSection.id}
              onDrill={(chart, seg) => {
                const entry = chart.source_sql ? sqlToRows.get(chart.source_sql.trim()) : undefined
                const filtered = entry ? entry.rows.filter(r => Object.values(r).some(v => String(v) === seg)) : []
                setDrillData({ chart, segment: seg, rows: filtered.length ? filtered : entry?.rows ?? [], columns: entry?.columns ?? [] })
              }}
              onFullscreen={(chart) => setFsChart({ chart, colorIdx: sectionIdx * 4 })}
              annotations={annotations}
              onAnnotate={(chartTitle, pt) => setAnnotatingKey(`${currentSection.id}|${chartTitle}|${pt}`)}
            />
            </div>
          )}
          {compareMode && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, alignItems: 'start' }}>
              {currentSection && (
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, color: C.teal, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, padding: '4px 10px', background: `${C.teal}10`, borderRadius: 20, display: 'inline-block' }}>Primary</div>
                  <SectionContent
                    section={currentSection} sectionIdx={sectionIdx}
                    globalKpis={analysis.kpis}
                    onRegenSection={() => handleRegenSection(currentSection.id)}
                    regenning={regenSection === currentSection.id}
                    onDrill={(chart, seg) => {
                      const entry = chart.source_sql ? sqlToRows.get(chart.source_sql.trim()) : undefined
                      const filtered = entry ? entry.rows.filter(r => Object.values(r).some(v => String(v) === seg)) : []
                      setDrillData({ chart, segment: seg, rows: filtered.length ? filtered : entry?.rows ?? [], columns: entry?.columns ?? [] })
                    }}
                    onFullscreen={(chart) => setFsChart({ chart, colorIdx: sectionIdx * 4 })}
                    annotations={annotations}
                    onAnnotate={(chartTitle, pt) => setAnnotatingKey(`${currentSection.id}|${chartTitle}|${pt}`)}
                  />
                </div>
              )}
              {(() => {
                const compSec = analysis.sections.find(s => s.id === compareSectionId)
                const compIdx = analysis.sections.findIndex(s => s.id === compareSectionId)
                if (!compSec) return <div style={{ textAlign: 'center', color: C.muted, fontSize: 12, padding: '40px 0' }}>Click a section tab to compare</div>
                return (
                  <div>
                    <div style={{ fontSize: 10, fontWeight: 700, color: C.violet, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, padding: '4px 10px', background: `${C.violet}10`, borderRadius: 20, display: 'inline-block' }}>Compare</div>
                    <SectionContent
                      section={compSec} sectionIdx={compIdx}
                      globalKpis={analysis.kpis}
                      onFullscreen={(chart) => setFsChart({ chart, colorIdx: compIdx * 4 })}
                      annotations={annotations}
                      onAnnotate={(chartTitle, pt) => setAnnotatingKey(`${compSec.id}|${chartTitle}|${pt}`)}
                    />
                  </div>
                )
              })()}
            </div>
          )}
          </div>{/* /maxWidth wrapper */}
        </div>
      </div>

      {/* Morning Overview — sticky floating card, top-right, never affects page layout */}
      {analysis.morning_brief && (
        <div style={{
          position: 'fixed',
          top: 70,
          right: copilotOpen ? 490 : 24,
          zIndex: 700,
          width: 290,
          transition: 'right 0.3s ease',
          borderRadius: 14,
          border: `1px solid ${C.teal}35`,
          background: `linear-gradient(160deg, ${C.navy}fc 0%, #0b2a54fc 100%)`,
          boxShadow: `0 8px 32px rgba(8,33,58,0.28), 0 2px 8px ${C.teal}20`,
          overflow: 'hidden',
          backdropFilter: 'blur(8px)',
        }}>
          <button
            onClick={() => setBriefExpanded(e => !e)}
            style={{ width: '100%', padding: '9px 13px', display: 'flex', alignItems: 'center', gap: 8, background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
          >
            <div style={{ width: 22, height: 22, borderRadius: 6, background: `${C.teal}30`, border: `1px solid ${C.teal}50`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Sparkles size={10} style={{ color: C.teal }} />
            </div>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.92)', flex: 1, letterSpacing: '0.01em' }}>Morning Overview</span>
            <span style={{ fontSize: 9, fontWeight: 700, color: C.teal, background: `${C.teal}22`, border: `1px solid ${C.teal}45`, borderRadius: 20, padding: '2px 7px', flexShrink: 0 }}>AI</span>
            {briefExpanded
              ? <ChevronUp   size={11} style={{ color: 'rgba(255,255,255,0.4)', flexShrink: 0 }} />
              : <ChevronDown size={11} style={{ color: 'rgba(255,255,255,0.4)', flexShrink: 0 }} />}
          </button>
          <div style={{
            overflow: 'hidden',
            maxHeight: briefExpanded ? 420 : 0,
            transition: 'max-height 0.3s cubic-bezier(0.4,0,0.2,1)',
          }}>
            <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', margin: '0 13px' }} />
            <p style={{ fontSize: 12, lineHeight: 1.78, color: 'rgba(255,255,255,0.70)', margin: 0, padding: '11px 13px 14px', overflowY: 'auto', maxHeight: 380 }}
              dangerouslySetInnerHTML={{ __html: mdHtml(analysis.morning_brief) }} />
          </div>
        </div>
      )}

      {/* AI Copilot — chat panel, independently positioned so it never overflows viewport */}
      {copilotOpen && (
        <div style={{
          position: 'fixed',
          top: 12,           /* safe gap from top of viewport */
          bottom: 88,        /* clears FAB (56px) + gap (20px) + bottom anchor (12px) */
          right: 24,
          zIndex: 1000,
          borderRadius: 16,
          boxShadow: '0 24px 64px rgba(0,0,0,0.18), 0 8px 32px rgba(0,180,216,0.1)',
          overflow: 'visible',
          animation: 'slideUpPanel 0.3s cubic-bezier(0.34,1.56,0.64,1)',
          display: 'flex',
        }}>
          {shareToken
            ? <ExecutiveCopilot token={shareToken} canvasName={String(canvas?.name ?? 'Report')} pageName={activeSection} />
            : (() => {
              const _offline = ((canvas as { is_offline?: boolean } | null)?.is_offline) || (canvas?.layout_config as { data_mode?: string } | undefined)?.data_mode === 'offline'
              return <IntelligenceCopilotPanel
                projectId={projectId}
                canvasId={canvasId}
                widgets={rawWidgets as CanvasWidgetData[]}
                pages={(canvas?.layout_config as { pages?: { id: string; name: string; order: number }[] })?.pages ?? []}
                onClose={() => setCopilotOpen(false)}
                onWidgetAdded={() => {}}
                title="Report Copilot"
                subtitle={_offline ? 'Full report context · Offline data' : 'Full report context · Live DB access'}
                initialWidth={440}
                suggestedQuestions={analysisSuggestions}
                onAddToPage={handleAddToPage}
                isOffline={_offline}
              />
            })()}
        </div>
      )}

      {/* AI Copilot — FAB button */}
      <div style={{ position: 'fixed', bottom: 20, right: 24, zIndex: 1001 }}>
        <button
          onClick={() => setCopilotOpen(p => !p)}
          title={copilotOpen ? 'Close AI Copilot' : 'Open AI Copilot'}
          className="intel-fab-btn"
          style={{
            width: 56, height: 56, borderRadius: '50%', border: 'none', cursor: 'pointer',
            background: copilotOpen
              ? `linear-gradient(135deg, ${C.red}, #f97316)`
              : `linear-gradient(135deg, ${C.teal} 0%, ${C.teal2} 100%)`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: `0 6px 24px ${copilotOpen ? C.red : C.teal}60, 0 2px 8px rgba(0,0,0,0.15)`,
            transition: 'all 0.25s cubic-bezier(0.34,1.56,0.64,1)',
            animation: copilotOpen ? 'none' : 'fabFloat 3s ease-in-out infinite',
          }}
        >
          <span style={{ transition: 'transform 0.25s', transform: copilotOpen ? 'rotate(90deg)' : 'rotate(0deg)', display: 'flex' }}>
            {copilotOpen ? <X size={22} color="white" /> : <MessageSquare size={22} color="white" />}
          </span>
        </button>
      </div>

      <style>{`
        /* ── Appear keyframes ──────────────────────────────────── */
        @keyframes fadeSlideIn   { from { opacity:0; transform:translateY(8px);        } to { opacity:1; transform:translateY(0);    } }
        @keyframes fadeInUp      { from { opacity:0; transform:translateY(22px);       } to { opacity:1; transform:translateY(0);    } }
        @keyframes fadeInLeft    { from { opacity:0; transform:translateX(-18px);      } to { opacity:1; transform:translateX(0);    } }
        @keyframes fadeInRight   { from { opacity:0; transform:translateX(18px);       } to { opacity:1; transform:translateX(0);    } }
        @keyframes scaleIn       { from { opacity:0; transform:scale(0.88) translateY(6px); } to { opacity:1; transform:scale(1) translateY(0); } }
        @keyframes popIn         { from { opacity:0; transform:scale(0.82);             } to { opacity:1; transform:scale(1);         } }
        @keyframes blurIn        { from { opacity:0; filter:blur(6px); transform:scale(0.97); } to { opacity:1; filter:blur(0); transform:scale(1); } }
        @keyframes slideDown     { from { opacity:0; transform:translateY(-14px);      } to { opacity:1; transform:translateY(0);    } }
        @keyframes slideUpPanel  { from { opacity:0; transform:translateY(28px) scale(0.95); } to { opacity:1; transform:translateY(0) scale(1); } }

        /* ── Disappear keyframes ───────────────────────────────── */
        @keyframes fadeOutDown   { from { opacity:1; transform:translateY(0);          } to { opacity:0; transform:translateY(16px); } }
        @keyframes fadeOutUp     { from { opacity:1; transform:translateY(0);          } to { opacity:0; transform:translateY(-14px);} }
        @keyframes fadeOutLeft   { from { opacity:1; transform:translateX(0);          } to { opacity:0; transform:translateX(-20px);} }
        @keyframes scaleOutFade  { from { opacity:1; transform:scale(1);               } to { opacity:0; transform:scale(0.94);      } }
        @keyframes blurOut       { from { opacity:1; filter:blur(0); transform:scale(1); } to { opacity:0; filter:blur(4px); transform:scale(1.02); } }

        /* ── Misc ──────────────────────────────────────────────── */
        @keyframes fabFloat  { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-6px)} }
        @keyframes ipulse    { 0%,100%{transform:scale(1);opacity:.15} 50%{transform:scale(1.3);opacity:.06} }
        @keyframes ispin     { to { transform:rotate(360deg); } }
        @keyframes shimmer   { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
        @keyframes cdot      { 0%,80%,100%{transform:scale(.7);opacity:.5} 40%{transform:scale(1);opacity:1} }
        @keyframes borderPulse { 0%,100%{box-shadow:0 0 0 0 rgba(0,180,216,0.18)} 50%{box-shadow:0 0 0 5px rgba(0,180,216,0)} }

        /* ── Section transitions ───────────────────────────────── */
        .intel-section-enter { animation: fadeInUp 0.38s cubic-bezier(0.22,1,0.36,1) both; }
        .intel-section-leave { animation: fadeOutDown 0.24s ease both; pointer-events:none; }

        /* ── Global KPI stagger ────────────────────────────────── */
        .intel-kpi-0 { animation: scaleIn 0.32s 0.04s both; }
        .intel-kpi-1 { animation: scaleIn 0.32s 0.09s both; }
        .intel-kpi-2 { animation: scaleIn 0.32s 0.14s both; }
        .intel-kpi-3 { animation: scaleIn 0.32s 0.19s both; }
        .intel-kpi-4 { animation: scaleIn 0.32s 0.24s both; }
        .intel-kpi-5 { animation: scaleIn 0.32s 0.29s both; }

        /* ── KPI cards ─────────────────────────────────────────── */
        .intel-kpi-card {
          transition: transform 0.22s cubic-bezier(.34,1.56,.64,1), box-shadow 0.22s ease, border-color 0.2s !important;
        }
        .intel-kpi-card:hover {
          transform: translateY(-5px) scale(1.02) !important;
          box-shadow: 0 16px 40px rgba(0,0,0,0.12) !important;
          border-color: rgba(0,180,216,0.25) !important;
        }

        /* ── Chart cards ───────────────────────────────────────── */
        .intel-chart-card {
          transition: transform 0.24s cubic-bezier(.34,1.56,.64,1), box-shadow 0.24s ease, border-color 0.2s !important;
        }
        .intel-chart-card:hover {
          transform: translateY(-4px) !important;
          box-shadow: 0 18px 44px rgba(0,0,0,0.1) !important;
          border-color: rgba(0,180,216,0.2) !important;
        }

        /* ── Section header bar ────────────────────────────────── */
        .intel-section-hdr { animation: fadeInLeft 0.32s 0.05s ease both; }

        /* ── Finding / Recommendation cards ────────────────────── */
        .intel-finding-card {
          animation: fadeInLeft 0.3s 0.1s ease both;
          transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        .intel-finding-card:hover {
          transform: translateX(4px) !important;
          box-shadow: 0 6px 20px rgba(0,180,216,0.12) !important;
        }
        .intel-rec-card {
          animation: fadeInRight 0.3s 0.15s ease both;
          transition: transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        .intel-rec-card:hover {
          transform: translateX(-4px) !important;
          box-shadow: 0 6px 20px rgba(249,115,22,0.12) !important;
        }

        /* ── Insight cards ─────────────────────────────────────── */
        @keyframes insightPulse {
          0%,100% { box-shadow: 0 1px 6px rgba(239,71,111,0.04); }
          50% { box-shadow: 0 0 0 7px rgba(239,71,111,0.07), 0 4px 16px rgba(239,71,111,0.15); }
        }
        @keyframes insightPulseWarn {
          0%,100% { box-shadow: 0 1px 6px rgba(255,183,3,0.04); }
          50% { box-shadow: 0 0 0 7px rgba(255,183,3,0.08), 0 4px 16px rgba(255,183,3,0.14); }
        }
        @keyframes insightExpandIn {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .intel-insight {
          animation: popIn 0.28s ease both;
          transition: transform 0.2s cubic-bezier(.34,1.56,.64,1), box-shadow 0.2s ease !important;
        }
        .intel-insight:hover {
          transform: translateY(-3px) scale(1.015) !important;
          box-shadow: 0 10px 28px rgba(0,0,0,0.09) !important;
        }
        .intel-insight-pulse {
          animation: popIn 0.28s ease both, insightPulse 3s 1s ease-in-out infinite !important;
        }
        .intel-insight-pulse:hover {
          transform: translateY(-3px) scale(1.015) !important;
          box-shadow: 0 10px 28px rgba(239,71,111,0.18) !important;
          animation: none !important;
        }
        .intel-insight-pulse-warn {
          animation: popIn 0.28s ease both, insightPulseWarn 3s 1.5s ease-in-out infinite !important;
        }
        .intel-insight-pulse-warn:hover {
          transform: translateY(-3px) scale(1.015) !important;
          box-shadow: 0 10px 28px rgba(255,183,3,0.18) !important;
          animation: none !important;
        }

        /* ── LeftRail nav buttons ──────────────────────────────── */
        .intel-nav-item {
          border-radius: 12px;
          transition: background 0.16s ease !important;
        }
        .intel-nav-item:hover { background: rgba(255,255,255,0.08) !important; }

        /* ── Header action buttons ─────────────────────────────── */
        .intel-hdr-btn {
          transition: background 0.14s, border-color 0.14s, color 0.14s, transform 0.14s !important;
        }
        .intel-hdr-btn:hover { transform: translateY(-1px) scale(1.04) !important; }
        .intel-hdr-btn:active { transform: scale(0.97) !important; }

        /* ── Performer panel items ──────────────────────────────── */
        .intel-performer-row {
          transition: background 0.15s, transform 0.18s ease, padding-left 0.15s !important;
          border-radius: 8px;
        }
        .intel-performer-row:hover {
          background: rgba(0,180,216,0.06) !important;
          transform: translateX(4px) !important;
          padding-left: 18px !important;
        }

        /* ── Table view ─────────────────────────────────────────── */
        .intel-table-card { animation: fadeInUp 0.3s 0.12s ease both; }
        .intel-table-row {
          transition: background 0.12s !important;
        }
        .intel-table-row:hover { background: #f0f9ff !important; }

        /* ── Morning brief ──────────────────────────────────────── */
        .intel-morning { animation: slideDown 0.28s 0.06s ease both; transition: box-shadow 0.2s, border-color 0.2s !important; }
        .intel-morning:hover { box-shadow: 0 4px 18px rgba(0,0,0,0.07) !important; border-color: rgba(0,180,216,0.2) !important; }

        /* ── Section tabs (old-style cards — unused but keep) ───── */
        .intel-card { transition: transform 0.22s ease, box-shadow 0.22s ease !important; }
        .intel-card:hover { transform: translateY(-3px) !important; box-shadow: 0 12px 32px rgba(0,0,0,0.1) !important; }

        /* ── Modal overlays ─────────────────────────────────────── */
        .intel-modal-enter { animation: blurIn 0.22s ease both; }
        .intel-modal-leave { animation: blurOut 0.18s ease both; pointer-events:none; }
        .intel-modal-inner-enter { animation: scaleIn 0.25s cubic-bezier(.34,1.56,.64,1) both; }
        .intel-modal-inner-leave { animation: scaleOutFade 0.18s ease both; pointer-events:none; }

        /* ── FAB ────────────────────────────────────────────────── */
        .intel-fab-btn {
          transition: transform 0.2s cubic-bezier(.34,1.56,.64,1), box-shadow 0.2s !important;
        }
        .intel-fab-btn:hover { transform: scale(1.08) !important; box-shadow: 0 12px 40px rgba(0,180,216,0.4) !important; }
        .intel-fab-btn:active { transform: scale(0.95) !important; }

        /* ── Chart type switcher buttons ───────────────────────── */
        .intel-chart-type-btn { transition: all 0.15s !important; }
        .intel-chart-type-btn:hover { transform: scale(1.12) !important; }

        /* ── Toolbar icon buttons (download, fullscreen, annotate) */
        .intel-toolbar-btn {
          transition: background 0.14s, color 0.14s, transform 0.15s cubic-bezier(.34,1.56,.64,1) !important;
        }
        .intel-toolbar-btn:hover { transform: scale(1.18) !important; }
        .intel-toolbar-btn:active { transform: scale(0.9) !important; }

        /* ── Feature 7: PDF / print ─────────────────────────────── */
        @media print {
          body * { visibility: hidden !important; }
          .print-zone, .print-zone * { visibility: visible !important; }
          .print-zone { position: absolute; left: 0; top: 0; width: 100%; }
          .no-print { display: none !important; }
          @page { margin: 16mm; }
        }
      `}</style>

      {/* Feature 2: Drill-through modal */}
      {drillData && (
        <DrillThroughModal
          title={drillData.chart.title}
          segment={drillData.segment}
          rows={drillData.rows}
          columns={drillData.columns}
          onClose={() => setDrillData(null)}
        />
      )}

      {/* Feature 5: Fullscreen chart */}
      {fsChart && (
        <FullscreenChartModal
          chart={fsChart.chart}
          colorIdx={fsChart.colorIdx}
          onClose={() => setFsChart(null)}
        />
      )}

      {/* Feature 6: Presentation mode */}
      {presenting && (
        <PresentationOverlay
          sections={analysis.sections}
          startIdx={presentStartIdx}
          onClose={() => setPresenting(false)}
        />
      )}

      {/* Feature 8: Share modal */}
      {shareModalUrl && (
        <ShareModal url={shareModalUrl} onClose={() => setShareModalUrl(null)} />
      )}

      {showExport && (
        <VlyExportModal
          canvasId={canvasId}
          intelligence={analysis ?? undefined}
          onClose={() => setShowExport(false)}
        />
      )}

      {showConnectDb && projectId && (
        <ConnectLiveDbModal
          projectId={projectId}
          dashboardId={canvasId}
          hint={(canvas as { connection_hint?: import('@/components/canvas/ConnectLiveDbModal').ConnHint } | null)?.connection_hint}
          onClose={() => setShowConnectDb(false)}
          onConnected={async () => {
            // Now live — drop the saved offline report (server + local) so the page
            // rebuilds against live data on reload and the offline pill disappears.
            try { localStorage.removeItem(`intel_analysis_${canvasId}`) } catch {}
            try { await intelligenceApi.deleteReport(canvasId) } catch {}
            window.location.reload()
          }}
        />
      )}

      {/* Feature 10: Annotation popover */}
      {annotatingKey && (
        <AnnotationPopover
          annotKey={annotatingKey}
          existing={annotations[annotatingKey]}
          onSave={text => saveAnnotation(annotatingKey, text)}
          onDelete={() => deleteAnnotation(annotatingKey)}
          onClose={() => setAnnotatingKey(null)}
        />
      )}
    </div>
  )
}
