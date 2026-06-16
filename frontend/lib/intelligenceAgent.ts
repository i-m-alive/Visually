/**
 * Intelligence Agent — lib/intelligenceAgent.ts
 *
 * 18 statistical skills run on raw canvas widget data BEFORE the AI call.
 * The AI (Opus) receives pre-computed facts so it writes precise executive
 * narrative rather than guessing from raw number arrays.
 *
 * Skills
 *  1.  DATA_EXTRACTION          — compact widget payload
 *  2.  TREND_ANALYSIS           — slope, CAGR, MoM, moving-avg, acceleration
 *  3.  ANOMALY_DETECTION        — Z-score, spike vs dip, reversal points
 *  4.  PERIOD_COMPARISON        — H1/H2, first/last-third, recent vs prior
 *  5.  PARETO_ANALYSIS          — 80/20, top-N, HHI concentration
 *  6.  CROSS_WIDGET_CORRELATION — Pearson r between every time-series pair
 *  7.  FORECAST                 — linear-regression with R² confidence
 *  8.  DATA_QUALITY             — null/zero streaks, unit-jump detection
 *  9.  NARRATIVE_CONTEXT        — pre-computed stats → plain-English for AI
 * 10.  SEGMENT_INTELLIGENCE     — top/mid/laggard tiers, growth leaders
 * 11.  ALERT_ENGINE             — per-widget critical / warning / healthy
 * 12.  SCHEMA_DETECTION         — column roles, target vs actual, attainment
 * 13.  EXECUTIVE_SCORING        — 0–100 health score from all widget alerts
 * 14.  COLUMN_PROFILER          — per-column stats from raw rows[]
 * 15.  DIMENSION_BREAKDOWN      — categorical × numeric aggregation
 * 16.  ROW_RANKING              — top-N / bottom-N performers
 * 17.  WITHIN_WIDGET_CORRELATION— Pearson between columns in same widget
 * 18.  LIVE_SQL_TRENDS          — fetch full rows via share token live query
 */

import { intelligenceApi, analystApi } from './api'

// ═══════════════════════════════════════════════════════════════════════════════
// Public output types
// ═══════════════════════════════════════════════════════════════════════════════

export interface AgentKPI {
  label: string
  value: string
  trend: 'up' | 'down' | 'neutral'
  trend_pct: string
  color?: string
  sparkline_data?: number[]
}

export interface AgentChartRow {
  name: string
  value: number
  base?: number
  total?: boolean
  projected?: boolean
  anomaly?: boolean
  [key: string]: unknown
}

export interface AgentChartSeries {
  key: string
  type?: 'bar' | 'line' | 'area'
  color?: string
}

export interface AgentChartReferenceLine {
  value: number
  label: string
  color?: string
}

export interface AgentChart {
  title: string
  type: 'bar' | 'line' | 'area' | 'pie' | 'table' | 'forecast' | 'combo' | 'waterfall' | 'scatter' | 'bullet'
  data: AgentChartRow[]
  projected_data?: AgentChartRow[]
  anomaly_indices?: number[]
  color?: string
  reference_lines?: AgentChartReferenceLine[]
  series?: AgentChartSeries[]
  target_value?: number
  max_value?: number
  x_key?: string
  y_key?: string
  source_sql?: string      // injected post-AI from widget sql_query
  insight?: string         // 1-2 sentence finding specific to this chart's data
}

export interface InsightCard {
  icon: string
  headline: string
  detail: string
  type: 'positive' | 'negative' | 'neutral' | 'warning'
  confidence?: number  // 1–5 stars; AI-assigned statistical confidence
}

export interface PerformerRow {
  label: string
  value: number
  formatted_value: string
  pct_of_total?: number
  rank: number
}

export interface AgentSection {
  id: string
  label: string
  icon: string
  narrative: string
  kpis: AgentKPI[]
  charts: AgentChart[]
  key_finding?: string
  recommendation?: string
  data_story?: string
  insights?: InsightCard[]
  top_performers?: PerformerRow[]
  bottom_performers?: PerformerRow[]
}

export interface ExecutiveAnalysis {
  title: string
  subtitle: string
  morning_brief: string
  health_score: number
  health_color: 'green' | 'amber' | 'red'
  kpis: AgentKPI[]
  correlations: string[]
  sections: AgentSection[]
}

export interface WidgetInput {
  id: string
  title: string
  chart_type: string
  page_id?: string
  sql_query?: string           // raw SQL that powers this widget
  position?: { x?: number; y?: number; w?: number; h?: number }
  chart_data: {
    rows: Record<string, unknown>[]
    columns: string[]
    labels: string[]
    values: number[]
  }
}

export interface ColumnProfile {
  name: string
  type: 'numeric' | 'categorical' | 'date' | 'unknown'
  nullRate: number
  uniqueCount: number
  min?: number; max?: number; mean?: number; median?: number
  q1?: number; q3?: number; stdDev?: number
  topValues?: Array<{ value: string; count: number; pct: number }>
}

export interface DimBreakdown {
  dimension: string
  metric: string
  total: number
  rows: Array<{ label: string; value: number; pct: number; rank: number }>
}

// ═══════════════════════════════════════════════════════════════════════════════
// Math utilities
// ═══════════════════════════════════════════════════════════════════════════════

function mean(arr: number[]): number {
  if (!arr.length) return 0
  return arr.reduce((s, v) => s + v, 0) / arr.length
}

function stdDev(arr: number[], m?: number): number {
  if (arr.length < 2) return 0
  const avg = m ?? mean(arr)
  return Math.sqrt(arr.reduce((s, v) => s + (v - avg) ** 2, 0) / arr.length)
}

function linReg(values: number[]): { slope: number; intercept: number; r2: number } {
  const n = values.length
  if (n < 2) return { slope: 0, intercept: values[0] ?? 0, r2: 0 }
  const xs = values.map((_, i) => i)
  const sx = xs.reduce((a, b) => a + b, 0)
  const sy = values.reduce((a, b) => a + b, 0)
  const sxy = xs.reduce((s, x, i) => s + x * values[i], 0)
  const sx2 = xs.reduce((s, x) => s + x * x, 0)
  const slope = (n * sxy - sx * sy) / (n * sx2 - sx * sx || 1)
  const intercept = (sy - slope * sx) / n
  const my = sy / n
  const ssTot = values.reduce((s, y) => s + (y - my) ** 2, 0)
  const ssRes = values.reduce((s, y, i) => s + (y - (slope * i + intercept)) ** 2, 0)
  const r2 = ssTot === 0 ? 0 : Math.max(0, 1 - ssRes / ssTot)
  return { slope, intercept, r2 }
}

function pearson(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length)
  if (n < 3) return 0
  const as = a.slice(0, n)
  const bs = b.slice(0, n)
  const ma = mean(as), mb = mean(bs)
  const num = as.reduce((s, ai, i) => s + (ai - ma) * (bs[i] - mb), 0)
  const da = Math.sqrt(as.reduce((s, ai) => s + (ai - ma) ** 2, 0))
  const db = Math.sqrt(bs.reduce((s, bi) => s + (bi - mb) ** 2, 0))
  return da === 0 || db === 0 ? 0 : num / (da * db)
}

function pct(a: number, b: number): number {
  return b === 0 ? 0 : ((a - b) / Math.abs(b)) * 100
}

function fmtPct(p: number): string {
  if (p == null || !isFinite(p)) return '—'
  const sign = p >= 0 ? '+' : ''
  return `${sign}${p.toFixed(1)}%`
}

function fmtVal(v: number): string {
  if (v == null || !isFinite(v)) return '—'
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return v % 1 === 0 ? String(v) : v.toFixed(2)
}

function toNum(v: unknown): number {
  if (v == null) return NaN
  if (typeof v === 'number') return isFinite(v) ? v : NaN
  const cleaned = String(v).replace(/[,$%\s]/g, '')
  const n = parseFloat(cleaned)
  return isFinite(n) ? n : NaN
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 1 — DATA_EXTRACTION
// ═══════════════════════════════════════════════════════════════════════════════

export type DataPattern =
  | 'kpi' | 'time_series' | 'comparison' | 'distribution'
  | 'detail' | 'financial' | 'customer' | 'general'

export function detectPattern(w: WidgetInput): DataPattern {
  const t = (w.chart_type ?? '').toLowerCase()
  const title = (w.title ?? '').toLowerCase()
  if (['kpi', 'kpi_card', 'metric', 'gauge', 'scorecard', 'number', 'kpichart'].some(s => t.includes(s))) return 'kpi'
  if (['line', 'area', 'sparkline', 'stacked_area'].some(s => t.includes(s))) return 'time_series'
  if (['pie', 'donut'].some(s => t.includes(s))) return 'distribution'
  if (['bar', 'column', 'horizontal'].some(s => t.includes(s))) return 'comparison'
  if (['table', 'pivot'].some(s => t.includes(s))) return 'detail'
  if (['trend', 'monthly', 'quarterly', 'yearly', 'weekly', 'daily'].some(k => title.includes(k))) return 'time_series'
  if (['revenue', 'sales', 'cost', 'income', 'expense', 'profit', 'arr', 'mrr'].some(k => title.includes(k))) return 'financial'
  if (['customer', 'client', 'user', 'account', 'churn', 'nps'].some(k => title.includes(k))) return 'customer'
  return 'general'
}

function isTimeSeries(w: WidgetInput): boolean {
  return ['time_series', 'financial'].includes(detectPattern(w)) || (w.chart_data?.values ?? []).length >= 6
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 2 — TREND_ANALYSIS
// ═══════════════════════════════════════════════════════════════════════════════

interface TrendResult {
  direction: 'up' | 'down' | 'flat'
  slope: number
  intercept: number
  slopePct: number
  cagr: number | null
  momChanges: number[]
  movingAvg3: number[]
  peakLabel: string; peakValue: number
  troughLabel: string; troughValue: number
  currentVsAvgPct: number
  acceleration: 'accelerating' | 'decelerating' | 'stable'
  lastMoM: number | null
}

function analyzeTrend(values: number[], labels: string[]): TrendResult | null {
  const vals = values.filter(v => v != null && !isNaN(v))
  if (vals.length < 2) return null

  const { slope, intercept } = linReg(vals)
  const avg = mean(vals)
  const direction: TrendResult['direction'] = slope > avg * 0.005 ? 'up' : slope < -avg * 0.005 ? 'down' : 'flat'
  const slopePct = avg === 0 ? 0 : (slope / avg) * 100

  const first = vals[0], last = vals[vals.length - 1]
  const cagr = first > 0 && last > 0 && vals.length > 1
    ? (last / first) ** (1 / (vals.length - 1)) - 1 : null

  const momChanges = vals.slice(1).map((v, i) => pct(v, vals[i]))
  const movingAvg3 = vals.map((_, i) => i < 2 ? vals[i] : mean(vals.slice(i - 2, i + 1)))

  const peakIdx = vals.indexOf(Math.max(...vals))
  const troughIdx = vals.indexOf(Math.min(...vals))

  const half = Math.floor(vals.length / 2)
  const firstSlope = linReg(vals.slice(0, half)).slope
  const secondSlope = linReg(vals.slice(half)).slope
  const acceleration: TrendResult['acceleration'] =
    secondSlope > firstSlope * 1.1 ? 'accelerating' :
    secondSlope < firstSlope * 0.9 ? 'decelerating' : 'stable'

  return {
    direction, slope, intercept, slopePct, cagr,
    momChanges, movingAvg3,
    peakLabel: labels[peakIdx] ?? `Period ${peakIdx + 1}`,
    peakValue: vals[peakIdx],
    troughLabel: labels[troughIdx] ?? `Period ${troughIdx + 1}`,
    troughValue: vals[troughIdx],
    currentVsAvgPct: pct(vals[vals.length - 1], avg),
    acceleration,
    lastMoM: momChanges.length ? momChanges[momChanges.length - 1] : null,
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 3 — ANOMALY_DETECTION
// ═══════════════════════════════════════════════════════════════════════════════

interface AnomalyPoint { idx: number; label: string; value: number; zScore: number; type: 'spike' | 'dip' }
interface AnomalyResult {
  mean: number; stdDev: number
  anomalies: AnomalyPoint[]
  hasAnomalies: boolean
  reversals: number[]
}

function detectAnomalies(values: number[], labels: string[]): AnomalyResult | null {
  const vals = values.filter(v => v != null && !isNaN(v))
  if (vals.length < 4) return null

  const avg = mean(vals)
  const sd = stdDev(vals, avg)
  const threshold = 2.5  // raised from 2.0 to reduce noise

  const anomalies: AnomalyPoint[] = vals
    .map((v, i) => ({ idx: i, label: labels[i] ?? `P${i + 1}`, value: v, zScore: sd === 0 ? 0 : (v - avg) / sd, type: v > avg ? 'spike' as const : 'dip' as const }))
    .filter(p => Math.abs(p.zScore) >= threshold)

  const reversals: number[] = []
  for (let i = 2; i < vals.length - 1; i++) {
    const prevDir = vals[i - 1] - vals[i - 2]
    const currDir = vals[i] - vals[i - 1]
    const nextDir = vals[i + 1] - vals[i]
    if (prevDir > 0 && currDir < 0 && nextDir < 0) reversals.push(i)
    if (prevDir < 0 && currDir > 0 && nextDir > 0) reversals.push(i)
  }

  return { mean: avg, stdDev: sd, anomalies, hasAnomalies: anomalies.length > 0, reversals }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 4 — PERIOD_COMPARISON
// ═══════════════════════════════════════════════════════════════════════════════

interface PeriodResult {
  h1Total: number; h2Total: number; h1vsh2Pct: number
  h1Mean: number; h2Mean: number
  firstThirdTotal: number; lastThirdTotal: number; firstVsLastPct: number
  latest3Mean: number; prior3Mean: number; recentVsPriorPct: number
}

function comparePeriods(values: number[]): PeriodResult | null {
  if (values.length < 4) return null
  const half = Math.floor(values.length / 2)
  const third = Math.floor(values.length / 3)
  const h1 = values.slice(0, half)
  const h2 = values.slice(half)
  const firstT = values.slice(0, third)
  const lastT = values.slice(-third)
  const latest3 = values.slice(-3)
  const prior3 = values.slice(-6, -3)
  const sum = (a: number[]) => a.reduce((s, v) => s + v, 0)
  return {
    h1Total: sum(h1), h2Total: sum(h2), h1vsh2Pct: pct(sum(h2), sum(h1)),
    h1Mean: mean(h1), h2Mean: mean(h2),
    firstThirdTotal: sum(firstT), lastThirdTotal: sum(lastT), firstVsLastPct: pct(sum(lastT), sum(firstT)),
    latest3Mean: mean(latest3), prior3Mean: mean(prior3),
    recentVsPriorPct: pct(mean(latest3), mean(prior3)),
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 5 — PARETO_ANALYSIS
// ═══════════════════════════════════════════════════════════════════════════════

interface ParetoResult {
  total: number
  top20pctItems: Array<{ label: string; value: number; share: number }>
  itemsFor80pct: Array<{ label: string; value: number; cumShare: number }>
  top3SharePct: number
  hhi: number
}

function paretoAnalysis(labels: string[], values: number[]): ParetoResult | null {
  if (labels.length < 3) return null
  const pairs = labels.map((l, i) => ({ label: l, value: values[i] ?? 0 }))
    .sort((a, b) => b.value - a.value)
  const total = pairs.reduce((s, p) => s + Math.abs(p.value), 0)
  if (total === 0) return null

  let cumShare = 0
  const itemsFor80pct: ParetoResult['itemsFor80pct'] = []
  for (const p of pairs) {
    cumShare += Math.abs(p.value) / total * 100
    itemsFor80pct.push({ label: p.label, value: p.value, cumShare })
    if (cumShare >= 80) break
  }

  const top20n = Math.max(1, Math.ceil(pairs.length * 0.2))
  const top20 = pairs.slice(0, top20n)
  const top3 = pairs.slice(0, 3)
  const hhi = pairs.reduce((s, p) => s + ((Math.abs(p.value) / total) * 100) ** 2, 0)

  return {
    total,
    top20pctItems: top20.map(p => ({ ...p, share: Math.abs(p.value) / total * 100 })),
    itemsFor80pct,
    top3SharePct: top3.reduce((s, p) => s + Math.abs(p.value) / total * 100, 0),
    hhi,
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 6 — CROSS_WIDGET_CORRELATION
// ═══════════════════════════════════════════════════════════════════════════════

export interface CorrelationResult {
  widget1: string; widget2: string
  r: number
  strength: 'strong_positive' | 'moderate_positive' | 'weak' | 'moderate_negative' | 'strong_negative'
  description: string
}

function correlateWidgets(widgets: WidgetInput[]): CorrelationResult[] {
  const tsWidgets = widgets.filter(w => isTimeSeries(w) && (w.chart_data?.values ?? []).length >= 4)
  const results: CorrelationResult[] = []

  for (let i = 0; i < tsWidgets.length; i++) {
    for (let j = i + 1; j < tsWidgets.length; j++) {
      const a = (tsWidgets[i].chart_data?.values ?? []).map(toNum)
      const b = (tsWidgets[j].chart_data?.values ?? []).map(toNum)
      const r = pearson(a, b)
      if (Math.abs(r) < 0.5) continue

      const strength: CorrelationResult['strength'] =
        r >= 0.8 ? 'strong_positive' : r >= 0.5 ? 'moderate_positive' :
        r <= -0.8 ? 'strong_negative' : r <= -0.5 ? 'moderate_negative' : 'weak'

      const t1 = tsWidgets[i].title || 'Widget A'
      const t2 = tsWidgets[j].title || 'Widget B'
      const description =
        r >= 0.8 ? `${t1} and ${t2} are strongly correlated (r=${r.toFixed(2)}) — they move almost in lockstep.` :
        r >= 0.5 ? `${t1} and ${t2} move together moderately (r=${r.toFixed(2)}).` :
        r <= -0.8 ? `${t1} and ${t2} are strongly inversely correlated (r=${r.toFixed(2)}) — when one rises, the other falls.` :
        `${t1} and ${t2} have a moderate inverse relationship (r=${r.toFixed(2)}).`

      results.push({ widget1: t1, widget2: t2, r, strength, description })
    }
  }
  return results.sort((a, b) => Math.abs(b.r) - Math.abs(a.r)).slice(0, 5)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 7 — FORECAST
// ═══════════════════════════════════════════════════════════════════════════════

export interface ForecastResult {
  points: Array<{ period: string; value: number }>
  slope: number; r2: number
  reliable: boolean
}

function forecast(values: number[], labels: string[], ahead = 3): ForecastResult | null {
  if (values.length < 4) return null
  const { slope, intercept, r2 } = linReg(values)
  const n = values.length
  const points = Array.from({ length: ahead }, (_, k) => {
    const idx = n + k
    const value = Math.round(slope * idx + intercept)
    return { period: labels[n - 1] ? `+${k + 1}p` : `Period ${idx + 1}`, value: Math.max(0, value) }
  })
  return { points, slope, r2, reliable: r2 >= 0.65 }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 8 — DATA_QUALITY
// ═══════════════════════════════════════════════════════════════════════════════

interface QualityResult {
  score: number
  issues: string[]
  sparse: boolean
  hasNulls: boolean
  hasNegatives: boolean
  unitJump: boolean
}

function assessDataQuality(w: WidgetInput): QualityResult {
  const vals = (w.chart_data?.values ?? []).map(toNum)
  const labels = (w.chart_data?.labels ?? []).map(v => String(v ?? ''))
  const issues: string[] = []
  let score = 100

  const sparse = vals.length < 4
  if (sparse) { issues.push('Sparse data (< 4 points)'); score -= 30 }

  const nullCount = vals.filter(v => v == null || isNaN(v) || v === 0).length
  const hasNulls = nullCount > 0
  if (hasNulls) { issues.push(`${nullCount} null/zero value(s)`); score -= nullCount * 5 }

  const hasNegatives = vals.some(v => v < 0)
  if (hasNegatives) { issues.push('Negative values present'); score -= 5 }

  let unitJump = false
  for (let i = 1; i < vals.length; i++) {
    if (vals[i - 1] !== 0 && Math.abs(vals[i] / vals[i - 1]) > 100) { unitJump = true; break }
  }
  if (unitJump) { issues.push('Possible unit change (large value jump)'); score -= 20 }

  const dupLabels = labels.length !== new Set(labels).size
  if (dupLabels) { issues.push('Duplicate labels'); score -= 10 }

  return { score: Math.max(0, score), issues, sparse, hasNulls, hasNegatives, unitJump }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 9 — NARRATIVE_CONTEXT
// ═══════════════════════════════════════════════════════════════════════════════

function buildNarrativeContext(
  w: WidgetInput,
  trend: TrendResult | null,
  anomaly: AnomalyResult | null,
  period: PeriodResult | null,
  fc: ForecastResult | null,
  pareto: ParetoResult | null,
): string {
  const parts: string[] = []
  const vals = (w.chart_data?.values ?? []).map(toNum)
  const n = vals.length
  if (n === 0) return 'No data available.'

  const cur = vals[vals.length - 1]
  parts.push(`${n}-point series. Current: ${fmtVal(cur)}.`)

  if (trend) {
    const dir = trend.direction === 'up' ? 'growing' : trend.direction === 'down' ? 'declining' : 'stable'
    const cagrStr = trend.cagr != null ? `, CAGR ${fmtPct(trend.cagr * 100)}` : ''
    parts.push(`Trend: ${dir}${cagrStr}. Slope ${fmtVal(trend.slope)}/period (${fmtPct(trend.slopePct)} of mean). ${trend.acceleration !== 'stable' ? `Momentum ${trend.acceleration}.` : ''}`)
    if (trend.peakValue !== cur) parts.push(`Peak: ${fmtVal(trend.peakValue)} at ${trend.peakLabel}. Trough: ${fmtVal(trend.troughValue)} at ${trend.troughLabel}.`)
    if (trend.lastMoM != null) parts.push(`Latest MoM: ${fmtPct(trend.lastMoM)}.`)
  }

  if (period) {
    parts.push(`H1 ${fmtVal(period.h1Total)} → H2 ${fmtVal(period.h2Total)} (${fmtPct(period.h1vsh2Pct)}).`)
    if (period.recentVsPriorPct !== 0) parts.push(`Recent 3p avg vs prior 3p: ${fmtPct(period.recentVsPriorPct)}.`)
  }

  if (pareto) {
    const top = pareto.itemsFor80pct
    parts.push(`80% of total (${fmtVal(pareto.total)}) driven by ${top.length} item(s): ${top.slice(0, 3).map(t => t.label).join(', ')}.`)
    parts.push(`Top-3 concentration: ${pareto.top3SharePct.toFixed(0)}%.`)
  }

  if (fc?.reliable && fc.points.length) {
    parts.push(`Forecast next period: ${fmtVal(fc.points[0].value)} (R²=${fc.r2.toFixed(2)}).`)
  }

  return parts.join(' ')
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 10 — SEGMENT_INTELLIGENCE
// ═══════════════════════════════════════════════════════════════════════════════

interface SegmentResult {
  topTier: Array<{ label: string; value: number }>
  midTier: Array<{ label: string; value: number }>
  laggards: Array<{ label: string; value: number }>
  growthLeaders: Array<{ label: string; value: number; deltaPct: number }>
  atRisk: Array<{ label: string; value: number; deltaPct: number }>
}

function segmentData(labels: string[], values: number[]): SegmentResult | null {
  if (labels.length < 4) return null
  const pairs = labels.map((l, i) => ({ label: l, value: values[i] ?? 0 })).sort((a, b) => b.value - a.value)
  const q1 = Math.floor(pairs.length * 0.25)
  const q3 = Math.floor(pairs.length * 0.75)
  const origPairs = labels.map((l, i) => ({ label: l, value: values[i] ?? 0 }))
  const withDelta = origPairs.map((p, i) => {
    const prev = origPairs[Math.max(0, i - 1)].value
    return { ...p, deltaPct: pct(p.value, prev) }
  }).slice(1)
  return {
    topTier: pairs.slice(0, q1 || 1),
    midTier: pairs.slice(q1 || 1, q3),
    laggards: pairs.slice(q3),
    growthLeaders: [...withDelta].sort((a, b) => b.deltaPct - a.deltaPct).slice(0, 3).filter(p => p.deltaPct > 0),
    atRisk: [...withDelta].sort((a, b) => a.deltaPct - b.deltaPct).slice(0, 3).filter(p => p.deltaPct < 0),
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 11 — ALERT_ENGINE
// ═══════════════════════════════════════════════════════════════════════════════


// ═══════════════════════════════════════════════════════════════════════════════
// Skill 12 — SCHEMA_DETECTION
// ═══════════════════════════════════════════════════════════════════════════════

interface SchemaResult {
  dateColumn: string | null
  metricColumns: string[]
  dimensionColumns: string[]
  targetColumn: string | null
  actualColumn: string | null
  attainmentRate: number | null
  currencyLikely: boolean
  percentageLikely: boolean
}

function detectSchema(w: WidgetInput): SchemaResult {
  const cols = w.chart_data?.columns ?? []
  const rows = w.chart_data?.rows ?? []
  const lower = (s: string) => (s ?? '').toLowerCase()
  const dateColumn = cols.find(c => ['date','time','month','quarter','year','week','period'].some(k => lower(c).includes(k))) ?? null
  const targetColumn = cols.find(c => ['target','goal','budget','plan','forecast','quota'].some(k => lower(c).includes(k))) ?? null
  const actualColumn = cols.find(c => ['actual','real','achieved','result','ytd'].some(k => lower(c).includes(k))) ?? null
  const metricColumns = cols.filter(c => ['revenue','sales','count','amount','value','total','sum','avg','rate','score'].some(k => lower(c).includes(k)) && c !== dateColumn)
  const dimensionColumns = cols.filter(c => !metricColumns.includes(c) && c !== dateColumn && c !== targetColumn && c !== actualColumn)

  let attainmentRate: number | null = null
  if (targetColumn && actualColumn && rows.length > 0) {
    const totTarget = rows.reduce((s, r) => s + Number(r[targetColumn] ?? 0), 0)
    const totActual = rows.reduce((s, r) => s + Number(r[actualColumn] ?? 0), 0)
    attainmentRate = totTarget > 0 ? (totActual / totTarget) * 100 : null
  }

  const title = lower(w.title ?? '')
  return {
    dateColumn, metricColumns, dimensionColumns, targetColumn, actualColumn, attainmentRate,
    currencyLikely: ['revenue','sales','cost','price','income','spend','$','usd','gbp','eur'].some(k => title.includes(k)),
    percentageLikely: ['rate','ratio','pct','%','percent','share','margin'].some(k => title.includes(k)),
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 13 — EXECUTIVE_SCORING
// ═══════════════════════════════════════════════════════════════════════════════

function computeHealthScore(qualityScores: number[]): { score: number; color: 'green' | 'amber' | 'red' } {
  if (!qualityScores.length) return { score: 100, color: 'green' }
  const avg = Math.round(qualityScores.reduce((s, v) => s + v, 0) / qualityScores.length)
  const score = Math.max(0, Math.min(100, avg))
  return { score, color: score >= 75 ? 'green' : score >= 50 ? 'amber' : 'red' }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 14 — COLUMN_PROFILER
// ═══════════════════════════════════════════════════════════════════════════════

export function profileColumns(rows: Record<string, unknown>[], cols: string[]): ColumnProfile[] {
  if (!rows.length || !cols.length) return []
  return cols.map(col => {
    const raw = rows.map(r => r[col])
    const nulls = raw.filter(v => v == null || v === '').length
    const nullRate = rows.length > 0 ? nulls / rows.length : 0
    const nonNull = raw.filter(v => v != null && v !== '')
    const uniqueCount = Array.from(new Set(nonNull.map(v => String(v)))).length
    if (['date','time','month','quarter','year','week','period'].some(k => col.toLowerCase().includes(k))) {
      return { name: col, type: 'date' as const, nullRate, uniqueCount }
    }
    const nums = nonNull.map(v => toNum(v)).filter(n => !isNaN(n))
    const numericRatio = nonNull.length > 0 ? nums.length / nonNull.length : 0
    if (numericRatio >= 0.7 && nums.length >= 2) {
      const sorted = [...nums].sort((a, b) => a - b)
      const mid = Math.floor(sorted.length / 2)
      const median = sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
      const avg = mean(nums)
      return {
        name: col, type: 'numeric' as const, nullRate, uniqueCount,
        min: sorted[0], max: sorted[sorted.length - 1],
        mean: avg, median, q1: sorted[Math.floor(sorted.length * 0.25)],
        q3: sorted[Math.floor(sorted.length * 0.75)], stdDev: stdDev(nums, avg),
      }
    }
    const freq: Record<string, number> = {}
    for (const v of nonNull) { const k = String(v); freq[k] = (freq[k] ?? 0) + 1 }
    const topValues = Object.entries(freq).sort((a, b) => b[1] - a[1]).slice(0, 10)
      .map(([value, count]) => ({ value, count, pct: count / (nonNull.length || 1) * 100 }))
    return { name: col, type: 'categorical' as const, nullRate, uniqueCount, topValues }
  })
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 15 — DIMENSION_BREAKDOWN
// ═══════════════════════════════════════════════════════════════════════════════

function computeDimensionBreakdowns(rows: Record<string, unknown>[], profiles: ColumnProfile[]): DimBreakdown[] {
  const catCols = profiles.filter(p => p.type === 'categorical').map(p => p.name)
  const numCols = profiles.filter(p => p.type === 'numeric').map(p => p.name)
  if (!catCols.length || !numCols.length || rows.length < 3) return []
  const results: DimBreakdown[] = []
  for (const dim of catCols.slice(0, 2)) {
    for (const metric of numCols.slice(0, 2)) {
      const grouped: Record<string, number> = {}
      for (const row of rows) {
        const k = String(row[dim] ?? '(unknown)')
        const v = toNum(row[metric])
        if (!isNaN(v)) grouped[k] = (grouped[k] ?? 0) + v
      }
      const entries = Object.entries(grouped).sort((a, b) => b[1] - a[1])
      const total = entries.reduce((s, [, v]) => s + v, 0)
      if (!total) continue
      results.push({
        dimension: dim, metric, total,
        rows: entries.slice(0, 10).map(([label, value], rank) => ({
          label, value, pct: (value / total) * 100, rank: rank + 1,
        })),
      })
    }
  }
  return results.slice(0, 4)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 16 — ROW_RANKING
// ═══════════════════════════════════════════════════════════════════════════════

function rankRows(
  rows: Record<string, unknown>[],
  metricCol: string,
  labelCol: string,
  topN = 5,
): { top: PerformerRow[]; bottom: PerformerRow[]; total: number } {
  const pairs = rows.map(r => ({ label: String(r[labelCol] ?? ''), value: toNum(r[metricCol]) }))
    .filter(p => !isNaN(p.value))
  if (!pairs.length) return { top: [], bottom: [], total: 0 }
  const total = pairs.reduce((s, p) => s + Math.abs(p.value), 0)
  const sorted = [...pairs].sort((a, b) => b.value - a.value)
  const toRow = (p: { label: string; value: number }, rank: number): PerformerRow => ({
    label: p.label, value: p.value, formatted_value: fmtVal(p.value),
    pct_of_total: total > 0 ? (Math.abs(p.value) / total) * 100 : undefined, rank,
  })
  return {
    top: sorted.slice(0, topN).map((p, i) => toRow(p, i + 1)),
    bottom: sorted.slice(-topN).reverse().map((p, i) => toRow(p, sorted.length - topN + i + 1)),
    total,
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 17 — WITHIN_WIDGET_CORRELATION
// ═══════════════════════════════════════════════════════════════════════════════

interface WithinCorr { col1: string; col2: string; r: number; description: string }

function withinWidgetCorrelations(rows: Record<string, unknown>[], profiles: ColumnProfile[]): WithinCorr[] {
  const numCols = profiles.filter(p => p.type === 'numeric').map(p => p.name)
  if (numCols.length < 2 || rows.length < 5) return []
  const results: WithinCorr[] = []
  for (let i = 0; i < numCols.length; i++) {
    for (let j = i + 1; j < numCols.length; j++) {
      const a = rows.map(r => toNum(r[numCols[i]])).filter(n => !isNaN(n))
      const b = rows.map(r => toNum(r[numCols[j]])).filter(n => !isNaN(n))
      if (a.length < 5) continue
      const r = pearson(a, b)
      if (Math.abs(r) < 0.4) continue
      const dir = r > 0 ? 'positively' : 'negatively'
      const str = Math.abs(r) >= 0.7 ? 'strongly' : 'moderately'
      results.push({ col1: numCols[i], col2: numCols[j], r,
        description: `${numCols[i]} and ${numCols[j]} are ${str} ${dir} correlated (r=${r.toFixed(2)})` })
    }
  }
  return results.sort((a, b) => Math.abs(b.r) - Math.abs(a.r)).slice(0, 3)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Skill 18 — LIVE_SQL_TRENDS
// Fetches richer raw rows from the live database via share token + widget SQL.
// Falls back gracefully to cached chart_data rows when unavailable.
// ═══════════════════════════════════════════════════════════════════════════════

async function fetchLiveSqlRows(
  shareToken: string,
  sqlQuery: string,
): Promise<Record<string, unknown>[] | null> {
  try {
    const cleaned = sqlQuery.replace(/\bLIMIT\s+\d+\b/gi, '').trim().replace(/;$/, '')
    console.log('[intelligence:sql18] fetching live rows, sql_len=%d', cleaned.length)
    const resp = await analystApi.query(shareToken, `${cleaned} LIMIT 500`)
    const rows = (resp.data?.rows ?? []) as Record<string, unknown>[]
    console.log('[intelligence:sql18] rows=%d  usable=%s', rows.length, rows.length >= 3 ? 'yes' : 'no (< 3)')
    return rows.length >= 3 ? rows : null
  } catch (err) {
    console.warn('[intelligence:sql18] fetch failed:', err)
    return null
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Prompt builder
// ═══════════════════════════════════════════════════════════════════════════════

interface WidgetContext {
  title: string
  type: string
  pattern: DataPattern
  quality_score: number
  quality_issues: string[]
  narrative: string
  schema: Pick<SchemaResult, 'targetColumn' | 'actualColumn' | 'attainmentRate' | 'currencyLikely' | 'percentageLikely'>
  sample_labels: string[]
  sample_values: number[]
  forecast_next?: string
  anomaly_count: number
  pareto_summary?: string
  segment_summary?: string
  sql_query?: string
  table_columns?: string[]
  column_profiles?: ColumnProfile[]
  top_rows?: PerformerRow[]
  bottom_rows?: PerformerRow[]
  dim_breakdowns?: DimBreakdown[]
  within_correlations?: WithinCorr[]
  live_data?: boolean   // true = rows came from live SQL query
}

function fmtColProfile(p: ColumnProfile): string {
  if (p.type === 'numeric')
    return `${p.name} [numeric]: mean=${fmtVal(p.mean ?? 0)}, min=${fmtVal(p.min ?? 0)}, max=${fmtVal(p.max ?? 0)}, stdDev=${fmtVal(p.stdDev ?? 0)}`
  if (p.type === 'categorical') {
    const top3 = (p.topValues ?? []).slice(0, 3).map(v => `${v.value} (${v.pct.toFixed(0)}%)`).join(', ')
    return `${p.name} [categorical, ${p.uniqueCount} unique]: top → ${top3}`
  }
  return `${p.name} [${p.type}]`
}

function buildWidgetBlock(ctx: WidgetContext): string {
  const lines: string[] = []
  const liveTag = ctx.live_data ? ' [LIVE DATA]' : ''
  lines.push(`WIDGET: "${ctx.title}" [${ctx.pattern}${liveTag}]`)
  lines.push(`NARRATIVE: ${ctx.narrative}`)
  if (ctx.sql_query) lines.push(`SQL_QUERY: ${ctx.sql_query.slice(0, 2000)}`)
  if (ctx.table_columns?.length) lines.push(`COLUMNS: ${ctx.table_columns.join(', ')}`)
  if (ctx.column_profiles?.length) lines.push(`PROFILES:\n  ${ctx.column_profiles.map(fmtColProfile).join('\n  ')}`)
  if (ctx.top_rows?.length) lines.push(`TOP PERFORMERS: ${ctx.top_rows.map(r => `${r.label} ${r.formatted_value}${r.pct_of_total ? ` (${r.pct_of_total.toFixed(0)}%)` : ''}`).join(', ')}`)
  if (ctx.bottom_rows?.length) lines.push(`BOTTOM PERFORMERS: ${ctx.bottom_rows.map(r => `${r.label} ${r.formatted_value}`).join(', ')}`)
  if (ctx.dim_breakdowns?.length) {
    for (const d of ctx.dim_breakdowns.slice(0, 2)) {
      const top3 = d.rows.slice(0, 3).map(r => `${r.label}=${fmtVal(r.value)} (${r.pct.toFixed(0)}%)`).join(', ')
      lines.push(`BREAKDOWN by ${d.dimension} / ${d.metric}: ${top3}`)
    }
  }
  if (ctx.within_correlations?.length) lines.push(`COLUMN CORRELATIONS: ${ctx.within_correlations.map(c => c.description).join('; ')}`)
  if (ctx.forecast_next) lines.push(`FORECAST: ${ctx.forecast_next}`)
  if (ctx.pareto_summary) lines.push(`PARETO: ${ctx.pareto_summary}`)
  if (ctx.schema.attainmentRate != null) lines.push(`ATTAINMENT: ${ctx.schema.attainmentRate.toFixed(0)}% vs target`)
  return lines.join('\n')
}

// Expanded icon names (30+) the AI can use for sections
const VALID_ICON_NAMES = [
  'overview', 'trending_up', 'trending_down', 'users', 'bar_chart', 'pie_chart',
  'lightbulb', 'target', 'activity', 'dollar_sign', 'shopping_cart', 'building',
  'globe', 'database', 'cpu', 'settings', 'calendar', 'clock', 'file_text',
  'filter', 'map_pin', 'percent', 'shield', 'award', 'check_circle', 'alert_triangle',
  'arrow_up', 'arrow_down', 'refresh', 'layers', 'package', 'briefcase',
  'heart_pulse', 'line_chart', 'zap',
].join(' | ')

function buildSchemaContextBlock(
  tables: Array<{
    name: string
    business_name?: string
    description?: string
    grain?: string
    is_fact?: boolean
    key_metrics: string[]
    key_dimensions: string[]
    key_dates: string[]
    columns: Array<{
      name: string
      business_name?: string
      description?: string
      type?: string
      is_metric?: boolean
      is_dimension?: boolean
      fk_target?: string
      examples: unknown[]
    }>
  }>,
): string {
  if (!tables.length) return ''
  const lines: string[] = ['━━━ DATABASE SCHEMA CONTEXT ━━━']
  for (const t of tables) {
    const label = t.business_name ? `${t.name} (${t.business_name})` : t.name
    const kind = t.is_fact ? 'FACT TABLE' : 'TABLE/VIEW'
    lines.push(`\n${kind}: ${label}`)
    if (t.description) lines.push(`  PURPOSE: ${t.description}`)
    if (t.grain) lines.push(`  GRAIN: ${t.grain}`)
    if (t.key_metrics.length) lines.push(`  KEY METRICS: ${t.key_metrics.join(', ')}`)
    if (t.key_dimensions.length) lines.push(`  KEY DIMENSIONS: ${t.key_dimensions.join(', ')}`)
    if (t.key_dates.length) lines.push(`  DATE COLUMNS: ${t.key_dates.join(', ')}`)
    if (t.columns.length) {
      lines.push('  COLUMNS:')
      for (const c of t.columns) {
        const cLabel = c.business_name ? `${c.name} (${c.business_name})` : c.name
        const typeTag = c.type ? ` [${c.type}]` : ''
        const fkTag = c.fk_target ? ` → FK: ${c.fk_target}` : ''
        const exTag = c.examples.length ? ` e.g. ${c.examples.slice(0, 3).join(', ')}` : ''
        const descTag = c.description ? `: ${c.description}` : ''
        lines.push(`    • ${cLabel}${typeTag}${fkTag}${descTag}${exTag}`)
      }
    }
  }
  lines.push('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
  return lines.join('\n')
}

function buildPrompt(
  canvasName: string,
  healthScore: number,
  healthColor: string,
  correlations: CorrelationResult[],
  widgetContexts: WidgetContext[],
  schemaBlock?: string,
): string {
  const corrLines = correlations.slice(0, 4).map(c => c.description).join('\n')
  const widgetBlocks = widgetContexts.map(buildWidgetBlock).join('\n\n---\n\n')

  return `You are a senior executive intelligence analyst. Rich pre-computed statistical data is provided below — including live SQL rows, column profiles, dimension breakdowns, performer rankings, correlations, the SQL query behind each visualization, and the full database schema context. Use ALL of it to produce a deeply specific, narrative-driven executive report with real numbers and concrete findings.

REPORT: "${canvasName}"

${schemaBlock || ''}

CROSS-WIDGET CORRELATIONS:
${corrLines || 'None detected'}

━━━ WIDGET DATA ━━━
${widgetBlocks}
━━━━━━━━━━━━━━━━━━

INSTRUCTIONS:
0. FOCUS: This is a BUSINESS intelligence report. Analyze the actual data to surface business insights — revenue, customers, trends, segments, performance, forecasts, risks, opportunities. Do NOT create a "Data Quality" section and do NOT discuss data-quality scores, data completeness, null rates, or data hygiene anywhere (including the morning_brief). The reader wants insights about their business, not about the data pipeline.
1. Create 4–6 thematic sections — each a chapter telling one story from the data (Revenue Performance, Customer Trends, Regional Breakdown, Forecast, etc.).
2. For every section:
   • data_story: ONE bold headline sentence with a specific number (e.g. "North region drives 61% of total revenue at $4.2M")
   • key_finding: single most important metric fact from the widget data
   • narrative: 2–3 sentences citing exact numbers from PROFILES, BREAKDOWNS, TOP/BOTTOM PERFORMERS
   • recommendation: one concrete, measurable action
   • 2–3 insights[] cards — use specific values; type = positive/negative/neutral/warning
   • top_performers / bottom_performers: 3 rows each, from TOP/BOTTOM PERFORMERS data
   • 2–3 charts built from the actual data rows (bar, line, pie, combo, waterfall, scatter, bullet) — max 20 data points per chart
   • If a widget's original chart_type is "table", OR if the widget has 3+ named columns of mixed dimensional/numeric data, ALSO include one chart with type:"table" — include up to 50 rows and preserve ALL meaningful column names as keys (e.g. {"customer":"Acme","revenue":120000,"region":"West","2023":95000,"2024":120000})
3. Extract 4–6 top-level KPIs from the most prominent metrics. For sparkline_data use the actual numeric values from the widget sample_values or column profile (max 12 values).
4. Write a 3-sentence morning_brief that opens with the single biggest finding (a specific number), covers the top 2 themes, ends with a risk or opportunity.

CHART DATA RULES — build charts from the real row data provided:
   • bar/line/area: data: [{name: dimension_value, value: metric_value}, ...]
   • combo: series: [{key:"MetricA",type:"bar"},{key:"MetricB",type:"line"}], data rows include both keys
   • pie: data rows where values sum to total
   • waterfall: data rows with base (cumulative subtotal) and value (delta)
   • bullet: target_value set, each data row is an entity vs that target
   • scatter: x_key + y_key + data rows containing those fields
   • table: data rows preserving ALL column keys from the source widget, not just name/value — e.g. {"customer":"Acme","region":"West","2022":80000,"2023":95000,"2024":120000}

ICON LIST (use ONLY these):
${VALID_ICON_NAMES}

RESPOND WITH ONLY RAW JSON — no markdown, no explanation, no trailing text:
{
  "title": "Executive Intelligence: <specific topic>",
  "subtitle": "<one crisp line about what this report measures>",
  "morning_brief": "<5-sentence story with specific numbers>",
  "kpis": [{"label":"...","value":"...","trend":"up|down|neutral","trend_pct":"+X%","sparkline_data":[n1,n2,...]}],
  "sections": [{
    "id": "sec_1", "label": "...", "icon": "<from icon list>",
    "data_story": "<bold headline with specific number>",
    "key_finding": "<1 sentence with exact metric>",
    "narrative": "<2–3 sentences with real numbers>",
    "recommendation": "<concrete action>",
    "insights": [{"icon":"<icon>","headline":"...","detail":"<specific stat>","type":"positive|negative|neutral|warning","confidence":4}],
    "top_performers": [{"label":"...","value":0,"formatted_value":"...","pct_of_total":0,"rank":1}],
    "bottom_performers": [{"label":"...","value":0,"formatted_value":"...","rank":1}],
    "kpis": [{"label":"...","value":"...","trend":"up|down|neutral","trend_pct":"","sparkline_data":[]}],
    "charts": [{"title":"...","type":"bar|line|area|pie|combo|waterfall|scatter|bullet|table","insight":"<1-2 sentence finding specific to this chart's data>","data":[{"name":"...","value":0}],"series":[{"key":"...","type":"bar|line"}],"target_value":0,"x_key":"...","y_key":"..."}]
  }]
}`
}

// ═══════════════════════════════════════════════════════════════════════════════
// Response parsing & sanitize
// ═══════════════════════════════════════════════════════════════════════════════

function _tryRepairJson(s: string): string {
  if (!s || s.indexOf('{') === -1) return ''
  let repaired = s
  // Remove inline JS comments the model sometimes emits
  repaired = repaired.replace(/\/\/[^\n]*/g, '')
  // Remove trailing commas before ] or } (JSON.parse rejects them)
  repaired = repaired.replace(/,(\s*[}\]])/g, '$1')
  // Trim trailing incomplete key/value (e.g. ends with , "title": )
  repaired = repaired.replace(/,\s*"[^"]*"?\s*:?\s*$/, '')
  // Trim trailing comma-only dangling tokens
  repaired = repaired.replace(/,\s*$/, '')
  // Close dangling string (odd number of unescaped quotes → add one)
  const quoteCount = (repaired.match(/(?<!\\)"/g) ?? []).length
  if (quoteCount % 2 !== 0) repaired += '"'
  // Count remaining unclosed brackets/braces AFTER repairs
  const opens = (repaired.match(/\[/g) ?? []).length - (repaired.match(/\]/g) ?? []).length
  const objs  = (repaired.match(/\{/g) ?? []).length - (repaired.match(/\}/g) ?? []).length
  for (let i = 0; i < Math.min(opens, 30); i++) repaired += ']'
  for (let i = 0; i < Math.min(objs, 30); i++) repaired += '}'
  return repaired
}

/** Walk the text char-by-char and extract every balanced { ... } object. Returns all candidates longest-first. */
function _extractJsonObjects(text: string): string[] {
  const results: string[] = []
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '{') continue
    let depth = 0, inStr = false, esc = false
    for (let j = i; j < text.length; j++) {
      const c = text[j]
      if (esc) { esc = false; continue }
      if (c === '\\' && inStr) { esc = true; continue }
      if (c === '"') { inStr = !inStr; continue }
      if (inStr) continue
      if (c === '{') depth++
      else if (c === '}') {
        depth--
        if (depth === 0) { results.push(text.slice(i, j + 1)); break }
      }
    }
  }
  return results.sort((a, b) => b.length - a.length)
}

function parseResponse(text: string): Omit<ExecutiveAnalysis, 'health_score' | 'health_color' | 'correlations'> | null {
  const clean = text.trim()
  // Strip markdown code fences then try balanced-brace extraction
  const stripped = clean.replace(/^```(?:json)?\n?|```$/gm, '').trim()

  // Sanitize trailing commas — JSON.parse rejects them but the model often emits them
  const sanitized = clean.replace(/,(\s*[}\]])/g, '$1')
  const sanitizedStripped = stripped.replace(/,(\s*[}\]])/g, '$1')

  const startIdx = clean.indexOf('{')
  const candidates: string[] = [
    ..._extractJsonObjects(sanitizedStripped),
    ..._extractJsonObjects(sanitized),
    ..._extractJsonObjects(stripped),
    ..._extractJsonObjects(clean),
    startIdx >= 0 ? _tryRepairJson(clean.slice(startIdx)) : '',
  ].filter(Boolean)

  for (const candidate of candidates) {
    try {
      const p = JSON.parse(candidate)
      if (p && (Array.isArray(p.sections) || Array.isArray(p.kpis))) {
        if (!p.sections) p.sections = []
        if (!p.kpis) p.kpis = []
        console.log(`[intelligence] parseResponse success  sections=${p.sections.length}  kpis=${p.kpis.length}  candidate_len=${candidate.length}`)
        return p
      }
    } catch { /* try next */ }
  }
  console.warn('[intelligence] parseResponse: all candidates failed  response_len=' + clean.length + '  first300=' + clean.slice(0, 300))
  return null
}

const VALID_CHART_TYPES = ['bar', 'line', 'area', 'pie', 'table', 'forecast', 'combo', 'waterfall', 'scatter', 'bullet'] as const
const VALID_TRENDS = ['up', 'down', 'neutral'] as const

function sanitizeKpi(k: AgentKPI): AgentKPI {
  return {
    label: String(k.label ?? 'Metric'),
    value: String(k.value ?? '—'),
    trend: VALID_TRENDS.includes(k.trend as never) ? k.trend : 'neutral',
    trend_pct: String(k.trend_pct ?? ''),
    color: k.color,
    sparkline_data: Array.isArray(k.sparkline_data)
      ? k.sparkline_data.map(Number).filter(n => !isNaN(n)).slice(0, 14)
      : undefined,
  }
}

function sanitizeInsight(ins: InsightCard): InsightCard {
  const conf = Number(ins.confidence)
  return {
    icon: String(ins.icon ?? 'lightbulb'),
    headline: String(ins.headline ?? ''),
    detail: String(ins.detail ?? ''),
    type: (['positive', 'negative', 'neutral', 'warning'] as const).includes(ins.type as never) ? ins.type : 'neutral',
    confidence: !isNaN(conf) && conf >= 1 && conf <= 5 ? Math.round(conf) : undefined,
  }
}

function sanitizePerformer(p: PerformerRow): PerformerRow {
  return {
    label: String(p.label ?? ''),
    value: Number(p.value ?? 0),
    formatted_value: String(p.formatted_value ?? fmtVal(Number(p.value ?? 0))),
    pct_of_total: p.pct_of_total != null ? Number(p.pct_of_total) : undefined,
    rank: Number(p.rank ?? 0),
  }
}

function sanitizeChart(ch: AgentChart): AgentChart {
  const type = VALID_CHART_TYPES.includes(ch.type as never) ? ch.type : 'bar'
  return {
    title: String(ch.title ?? 'Chart'),
    type,
    color: ch.color,
    anomaly_indices: ch.anomaly_indices,
    projected_data: ch.projected_data,
    reference_lines: ch.reference_lines,
    series: ch.series,
    target_value: ch.target_value != null ? Number(ch.target_value) : undefined,
    max_value: ch.max_value != null ? Number(ch.max_value) : undefined,
    x_key: ch.x_key ? String(ch.x_key) : undefined,
    y_key: ch.y_key ? String(ch.y_key) : undefined,
    insight: ch.insight ? String(ch.insight) : undefined,
    data: (ch.data ?? []).slice(0, 60).map((d: AgentChartRow) => {
      const row: AgentChartRow = { name: String(d.name ?? ''), value: Number(d.value ?? 0) }
      if (d.base != null) row.base = Number(d.base)
      if (d.total) row.total = true
      if (d.projected) row.projected = true
      for (const k of Object.keys(d)) {
        if (!['name', 'value', 'base', 'total', 'projected', 'anomaly'].includes(k)) row[k] = d[k]
      }
      return row
    }),
  }
}

function sanitizeAnalysis(
  raw: Omit<ExecutiveAnalysis, 'health_score' | 'health_color' | 'correlations'>,
  meta: Pick<ExecutiveAnalysis, 'health_score' | 'health_color' | 'correlations'>,
): ExecutiveAnalysis {
  return {
    title: String(raw.title ?? 'Executive Intelligence'),
    subtitle: String(raw.subtitle ?? ''),
    morning_brief: String(raw.morning_brief ?? ''),
    ...meta,
    kpis: (raw.kpis ?? []).slice(0, 8).map(sanitizeKpi),
    sections: (raw.sections ?? []).slice(0, 10).map(s => ({
      id: String(s.id ?? Math.random().toString(36).slice(2)),
      label: String(s.label ?? 'Section'),
      icon: String(s.icon ?? 'bar_chart'),
      narrative: String(s.narrative ?? ''),
      data_story: s.data_story ? String(s.data_story) : undefined,
      key_finding: s.key_finding ? String(s.key_finding) : undefined,
      recommendation: s.recommendation ? String(s.recommendation) : undefined,
      insights: (s.insights ?? []).slice(0, 4).map(sanitizeInsight),
      top_performers: (s.top_performers ?? []).slice(0, 5).map(sanitizePerformer),
      bottom_performers: (s.bottom_performers ?? []).slice(0, 5).map(sanitizePerformer),
      kpis: (s.kpis ?? []).slice(0, 4).map(sanitizeKpi),
      charts: (s.charts ?? []).slice(0, 4).map(sanitizeChart),
    })),
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Fallback analysis (AI unavailable)
// ═══════════════════════════════════════════════════════════════════════════════

export function buildFallbackAnalysis(
  canvasName: string,
  widgets: WidgetInput[],
  meta?: Pick<ExecutiveAnalysis, 'health_score' | 'health_color' | 'correlations'>,
): ExecutiveAnalysis {
  const kpiWidgets = widgets.filter(w => detectPattern(w) === 'kpi')
  const chartWidgets = widgets.filter(w => !['kpi', 'detail'].includes(detectPattern(w)))

  const kpis: AgentKPI[] = kpiWidgets.slice(0, 8).map(w => ({
    label: w.title || 'Metric',
    value: fmtVal(toNum(w.chart_data?.values?.[0] ?? 0)),
    trend: 'neutral' as const, trend_pct: '',
  }))

  const sections: AgentSection[] = []
  for (let i = 0; i < chartWidgets.length && sections.length < 10; i += 3) {
    const group = chartWidgets.slice(i, i + 3)
    sections.push({
      id: `sec_${sections.length}`,
      label: `Analysis ${sections.length + 1}`,
      icon: 'bar_chart',
      narrative: `${group.map(w => w.title || 'chart').join(', ')}.`,
      kpis: [],
      charts: group.map(w => ({
        title: w.title || 'Chart',
        type: (detectPattern(w) === 'time_series' ? 'area' : detectPattern(w) === 'distribution' ? 'pie' : 'bar') as AgentChart['type'],
        data: (w.chart_data?.labels ?? []).slice(0, 20).map((label, idx) => ({ name: String(label ?? ''), value: toNum(w.chart_data?.values?.[idx] ?? 0) })),
      })),
    })
  }
  if (!sections.length) sections.push({ id: 'overview', label: 'Overview', icon: 'overview', narrative: 'No chart widgets found.', kpis: [], charts: [] })

  return {
    title: `Intelligence: ${canvasName}`,
    subtitle: `${widgets.length} widget(s) — deterministic analysis`,
    morning_brief: `This report has ${widgets.length} widget(s). AI analysis was unavailable; results are based on raw data patterns.`,
    health_score: meta?.health_score ?? 50,
    health_color: meta?.health_color ?? 'amber',
    correlations: meta?.correlations ?? [],
    kpis,
    sections,
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Inject forecast projected_data into AI-returned charts
// ═══════════════════════════════════════════════════════════════════════════════

function injectForecastData(sections: AgentSection[], forecastMap: Map<string, ForecastResult>, labels: Map<string, string[]>): void {
  for (const section of sections) {
    for (const chart of section.charts) {
      if (chart.type !== 'forecast') continue
      for (const [title, fc] of Array.from(forecastMap.entries())) {
        if (chart.title.toLowerCase().includes(title.toLowerCase().slice(0, 10)) || title.toLowerCase().includes(chart.title.toLowerCase().slice(0, 10))) {
          const fcLabels = labels.get(title) ?? []
          chart.projected_data = fc.points.map((p: ForecastResult['points'][0], i: number) => ({
            name: fcLabels.length > i ? `${fcLabels[fcLabels.length - 1]} +${i + 1}` : p.period,
            value: p.value,
            projected: true,
          }))
          break
        }
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Inject average + target reference lines
// ═══════════════════════════════════════════════════════════════════════════════

function injectReferenceLines(sections: AgentSection[]): void {
  for (const section of sections) {
    for (const chart of section.charts) {
      if (!['bar', 'line', 'area', 'combo'].includes(chart.type)) continue
      if (chart.data.length < 3) continue
      const vals = chart.data.map(d => d.value).filter(v => !isNaN(v))
      if (!vals.length) continue
      const avg = mean(vals)
      const lines: AgentChartReferenceLine[] = [{ value: avg, label: 'Avg', color: '#94a3b8' }]
      if (chart.target_value != null) lines.push({ value: chart.target_value, label: 'Target', color: '#f5a623' })
      chart.reference_lines = lines
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Inject source SQL from widget map (post-AI, more reliable than AI carrying it)
// ═══════════════════════════════════════════════════════════════════════════════

function injectSourceSql(sections: AgentSection[], widgetSqlMap: Map<string, string>): void {
  for (const section of sections) {
    for (const chart of section.charts) {
      if (chart.source_sql) continue
      // Try to match chart title to a widget SQL
      for (const [widgetTitle, sql] of Array.from(widgetSqlMap.entries())) {
        const chartTitleNorm = chart.title.toLowerCase().replace(/[^a-z0-9]/g, '')
        const widgetTitleNorm = widgetTitle.toLowerCase().replace(/[^a-z0-9]/g, '')
        if (
          chartTitleNorm.includes(widgetTitleNorm.slice(0, 10)) ||
          widgetTitleNorm.includes(chartTitleNorm.slice(0, 10))
        ) {
          chart.source_sql = sql
          break
        }
      }
    }
  }
}

function injectTableCharts(analysis: ExecutiveAnalysis, widgets: WidgetInput[]): void {
  if (analysis.sections.length === 0) return

  // Collect explicit table-type widgets first
  let tableWidgets = widgets.filter(w =>
    (w.chart_type === 'table' || w.chart_type === 'data_table') &&
    (w.chart_data?.rows?.length ?? 0) > 0
  )
  // Fall back to multi-column widgets (4+ cols) if no explicit tables found
  if (tableWidgets.length === 0) {
    tableWidgets = widgets.filter(w =>
      (w.chart_data?.columns?.length ?? 0) >= 4 &&
      (w.chart_data?.rows?.length ?? 0) >= 3
    )
  }
  if (tableWidgets.length === 0) return

  // Round-robin index for widgets that don't match any section well
  let rrIdx = 0

  for (const w of tableWidgets) {
    const allRows = w.chart_data?.rows ?? []
    const cols = w.chart_data?.columns ?? []
    const rows = allRows.slice(0, 100)
    const firstCol = cols[0] ?? 'name'
    const secondNumCol = cols.find(c => {
      const sample = rows.slice(0, 5).map(r => r[c])
      return sample.some(v => v !== null && v !== undefined && !isNaN(Number(v)))
    }) ?? cols[1] ?? 'value'

    const data: AgentChartRow[] = rows.map(row => ({
      name: String(row[firstCol] ?? ''),
      value: Number(row[secondNumCol] ?? 0),
      ...row,
    }))

    const tableChart: AgentChart = {
      title: w.title || 'Data Table',
      type: 'table' as const,
      data,
      source_sql: w.sql_query,
      insight: `${allRows.length} total rows · ${cols.length} columns`,
    }

    // Score each section by keyword overlap with the widget title
    const widgetWords = (w.title || '').toLowerCase().split(/\W+/).filter(s => s.length > 3)
    let bestSection = analysis.sections[rrIdx % analysis.sections.length]
    let bestScore = 0

    for (const section of analysis.sections) {
      const haystack = [section.label, section.data_story ?? '', section.narrative ?? '']
        .join(' ').toLowerCase()
      const score = widgetWords.filter(word => haystack.includes(word)).length
      if (score > bestScore) {
        bestScore = score
        bestSection = section
      }
    }

    bestSection.charts.push(tableChart)
    rrIdx++
  }

  console.log(`[intelligence] injected ${tableWidgets.length} raw table chart(s) into existing sections`)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Public entry point
// ═══════════════════════════════════════════════════════════════════════════════

export interface AgentOptions {
  projectId: string
  canvasId: string
  canvasName: string
  widgets: WidgetInput[]
  shareToken?: string     // enables Skill 18 live SQL fetching
  dateRange?: { from: string; to: string } | null  // Feature 1: time range override
  skipSchemaFetch?: boolean  // set true to skip schema-context fetch (e.g. per-section regen)
  force?: boolean            // bypass Redis cache and force a fresh Bedrock call
}

export async function runIntelligenceAgent(
  opts: AgentOptions,
  onProgress?: (step: string) => void,
): Promise<ExecutiveAnalysis> {
  const { projectId, canvasId, canvasName, widgets, shareToken } = opts

  const sqlWidgets = widgets.filter(w => w.sql_query).length
  console.log(
    `[intelligence] start  canvas="${canvasName}"  widgets=${widgets.length}  with_sql=${sqlWidgets}  live_sql=${shareToken ? 'enabled' : 'disabled'}  model=opus`
  )

  onProgress?.(`[1/6] Extracting data from ${widgets.length} widget(s)…`)

  const forecastMap = new Map<string, ForecastResult>()
  const labelMap = new Map<string, string[]>()
  const qualityScores: number[] = []
  const widgetContexts: WidgetContext[] = []
  const widgetSqlMap = new Map<string, string>()

  // Collect SQL queries for post-AI injection
  for (const w of widgets) {
    if (w.sql_query) widgetSqlMap.set(w.title, w.sql_query)
  }
  console.log(`[intelligence] sql_map built  entries=${widgetSqlMap.size}`)

  onProgress?.('[2/6] Running statistical skills (trends, forecasts, profiles)…')

  for (const w of widgets) {
    let rows = w.chart_data?.rows ?? []
    const cols = w.chart_data?.columns ?? []
    let liveData = false

    console.log(
      `[intelligence:widget] "${w.title}"  type=${w.chart_type}  rows=${rows.length}  cols=${cols.length}  sql=${w.sql_query ? 'yes' : 'no'}`
    )

    const vals = (w.chart_data?.values ?? []).map(toNum)

    // Skill 18: fetch richer rows via live SQL if share token available.
    // Trigger when: cached data is absent/poor (rows=0 or cols=0) OR fewer than 20 rows.
    // Previously gated at < 50 which blocked Skill 18 whenever bulk-fetch "succeeded"
    // but the widget_id match had silently failed and returned 0 rows.
    const cachedDataPoor = rows.length === 0 || cols.length === 0 || vals.filter(v => !isNaN(v)).length < 3
    if (shareToken && w.sql_query && (cachedDataPoor || rows.length < 20)) {
      onProgress?.(`[2/6] Fetching live data for "${w.title}"…`)
      const liveRows = await fetchLiveSqlRows(shareToken, w.sql_query)
      if (liveRows && liveRows.length > rows.length) {
        console.log(`[intelligence:widget] "${w.title}" upgraded via live SQL: ${rows.length} → ${liveRows.length} rows`)
        rows = liveRows
        liveData = true
      }
    }
    const labs = (w.chart_data?.labels ?? []).map(v => String(v ?? ''))
    const pattern = detectPattern(w)
    const ts = isTimeSeries(w)

    const quality = assessDataQuality(w)
    const schema = detectSchema(w)
    const trend = ts ? analyzeTrend(vals, labs) : null
    const anomaly = detectAnomalies(vals, labs)
    const period = ts && vals.length >= 4 ? comparePeriods(vals) : null
    const pareto = ['comparison', 'distribution', 'general'].includes(pattern) ? paretoAnalysis(labs, vals) : null
    const segments = ['comparison', 'distribution', 'general', 'customer'].includes(pattern) ? segmentData(labs, vals) : null
    const fc = ts && vals.length >= 4 ? forecast(vals, labs, 3) : null
    if (fc) { forecastMap.set(w.title, fc); labelMap.set(w.title, labs) }

    const colProfiles = rows.length >= 2 && cols.length >= 1 ? profileColumns(rows, cols) : []
    const dimBreakdowns = colProfiles.length ? computeDimensionBreakdowns(rows, colProfiles) : []
    const withinCorrs = colProfiles.length ? withinWidgetCorrelations(rows, colProfiles) : []

    const primaryNumCol = colProfiles.find(p => p.type === 'numeric')?.name
    const primaryLabelCol = colProfiles.find(p => p.type === 'categorical' || p.type === 'date')?.name
    const ranked = primaryNumCol && primaryLabelCol && rows.length >= 3
      ? rankRows(rows, primaryNumCol, primaryLabelCol, 5)
      : null

    qualityScores.push(quality.score)

    const narrative = buildNarrativeContext(w, trend, anomaly, period, fc, pareto)
    const schemaCompact = {
      targetColumn: schema.targetColumn, actualColumn: schema.actualColumn,
      attainmentRate: schema.attainmentRate != null ? +schema.attainmentRate.toFixed(1) : null,
      currencyLikely: schema.currencyLikely, percentageLikely: schema.percentageLikely,
    }

    widgetContexts.push({
      title: w.title || '(untitled)',
      type: w.chart_type,
      pattern,
      quality_score: quality.score,
      quality_issues: quality.issues,
      narrative,
      schema: schemaCompact,
      sample_labels: labs.slice(0, 20),
      sample_values: vals.slice(0, 20),
      forecast_next: fc?.reliable ? `next period: ${fmtVal(fc.points[0].value)} (R²=${fc.r2.toFixed(2)})` : undefined,
      anomaly_count: anomaly?.anomalies.length ?? 0,
      pareto_summary: pareto ? `Top ${pareto.itemsFor80pct.length} items = 80% of ${fmtVal(pareto.total)}; concentration ${pareto.top3SharePct.toFixed(0)}%` : undefined,
      segment_summary: segments ? `${segments.atRisk.length} at-risk, ${segments.growthLeaders.length} growth leaders, ${segments.laggards.length} laggards` : undefined,
      sql_query: w.sql_query,
      table_columns: cols.length ? cols : undefined,
      column_profiles: colProfiles.length ? colProfiles : undefined,
      top_rows: ranked?.top,
      bottom_rows: ranked?.bottom,
      dim_breakdowns: dimBreakdowns.length ? dimBreakdowns : undefined,
      within_correlations: withinCorrs.length ? withinCorrs : undefined,
      live_data: liveData,
    })
  }

  onProgress?.('[3/6] Computing cross-widget correlations…')
  const correlations = correlateWidgets(widgets)

  console.log(
    `[intelligence] skills done  widgets_processed=${widgetContexts.length}  forecasts=${forecastMap.size}  correlations=${correlations.length}`
  )

  onProgress?.('[4/6] Scoring executive health…')
  const { score: healthScore, color: healthColor } = computeHealthScore(qualityScores)
  console.log(`[intelligence] health_score=${healthScore}  color=${healthColor}  widgets=${qualityScores.length}`)

  const meta: Pick<ExecutiveAnalysis, 'health_score' | 'health_color' | 'correlations'> = {
    health_score: healthScore,
    health_color: healthColor,
    correlations: correlations.map(c => c.description),
  }

  onProgress?.('[5/6] Fetching database schema context…')

  // Fix G: Fetch table/column DDL context for all tables referenced in widget SQL.
  // This gives the model grounded knowledge of column types, business names, FK relationships.
  let schemaBlock = ''
  if (!opts.skipSchemaFetch) {
    try {
      const schemaResp = await intelligenceApi.fetchSchemaContext(canvasId)
      const tables = schemaResp.data?.tables ?? []
      if (tables.length) {
        schemaBlock = buildSchemaContextBlock(tables)
        console.log(`[intelligence] schema_context  tables=${tables.length}`)
      } else {
        console.log(`[intelligence] schema_context empty (${schemaResp.data?.message ?? 'no metadata'})`)
      }
    } catch (err) {
      console.warn('[intelligence] schema-context fetch failed (non-fatal):', err)
    }
  }

  onProgress?.('[5/6] Sending enriched context to Opus AI analyst…')

  // Fix A+D: Use dedicated /intelligence/analyze endpoint (not chatApi.send).
  // This endpoint: focused system prompt, no chart-creation instructions, max_tokens=32768.
  const prompt = buildPrompt(canvasName, healthScore, healthColor, correlations, widgetContexts, schemaBlock)
  console.log(`[intelligence] prompt_len=${prompt.length}  schema_included=${schemaBlock.length > 0}  sending to /intelligence/analyze`)

  let responseText = ''
  try {
    const resp = await intelligenceApi.analyze({ prompt, canvas_name: canvasName, force: opts.force ?? false })
    responseText = resp.data?.text ?? ''
    console.log(`[intelligence] ai response received  len=${responseText.length}`)
  } catch (err) {
    console.warn('[intelligence] ai call failed, falling back to deterministic analysis:', err)
    onProgress?.('AI unavailable — using deterministic analysis…')
    return buildFallbackAnalysis(canvasName, widgets, meta)
  }

  onProgress?.('[6/6] Processing and enriching AI output…')

  const parsed = parseResponse(responseText)
  if (!parsed) {
    const truncated = responseText.length > 0 && !responseText.trim().endsWith('}')
    console.warn(
      `[intelligence] parseResponse null — ${truncated ? 'RESPONSE TRUNCATED (check max_tokens)' : 'unexpected format'}  ` +
      `response_len=${responseText.length}  first200=${responseText.slice(0, 200)}`
    )
    onProgress?.('AI returned unexpected format — using deterministic analysis…')
    return { ...buildFallbackAnalysis(canvasName, widgets, meta), _fallback: true } as ExecutiveAnalysis
  }

  if ((parsed.sections?.length ?? 0) < 3) {
    console.warn(`[intelligence] suspiciously few sections (${parsed.sections?.length}) — possible truncation`)
  }

  console.log(
    `[intelligence] parsed  sections=${parsed.sections.length}  kpis=${parsed.kpis.length}  brief_len=${parsed.morning_brief?.length ?? 0}`
  )

  const analysis = sanitizeAnalysis(parsed, meta)

  injectForecastData(analysis.sections, forecastMap, labelMap)
  injectReferenceLines(analysis.sections)
  injectSourceSql(analysis.sections, widgetSqlMap)
  injectTableCharts(analysis, widgets as WidgetInput[])

  const injectedCount = analysis.sections.flatMap(s => s.charts).filter(c => c.source_sql).length
  console.log(
    `[intelligence] done  sections=${analysis.sections.length}  charts_with_sql=${injectedCount}/${analysis.sections.flatMap(s => s.charts).length}`
  )

  return analysis
}

// ═══════════════════════════════════════════════════════════════════════════════
// Per-section regeneration (Feature 3)
// Re-runs the statistical skills and asks the AI for just one section with a
// different angle.  No live SQL fetch — uses cached widget data for speed.
// ═══════════════════════════════════════════════════════════════════════════════

export async function runSectionAgent(
  opts: AgentOptions,
  sectionLabel: string,
  existingAnalysis: ExecutiveAnalysis,
  onProgress?: (step: string) => void,
): Promise<AgentSection | null> {
  const { projectId, canvasId, canvasName, widgets } = opts

  onProgress?.(`Regenerating "${sectionLabel}"…`)

  // Quick statistical pass (no network calls)
  const widgetContexts: WidgetContext[] = []
  const qualityScores: number[] = []
  const forecastMap = new Map<string, ForecastResult>()
  const labelMap = new Map<string, string[]>()
  const widgetSqlMap = new Map<string, string>()
  for (const w of widgets) { if (w.sql_query) widgetSqlMap.set(w.title, w.sql_query) }

  for (const w of widgets) {
    const rows = w.chart_data?.rows ?? []
    const cols = w.chart_data?.columns ?? []
    const vals = (w.chart_data?.values ?? []).map(toNum)
    const labs = (w.chart_data?.labels ?? []).map(v => String(v ?? ''))
    const pattern = detectPattern(w)
    const ts = isTimeSeries(w)
    const quality = assessDataQuality(w)
    const schema = detectSchema(w)
    const trend = ts ? analyzeTrend(vals, labs) : null
    const anomaly = detectAnomalies(vals, labs)
    const period = ts && vals.length >= 4 ? comparePeriods(vals) : null
    const pareto = ['comparison', 'distribution', 'general'].includes(pattern) ? paretoAnalysis(labs, vals) : null
    const fc = ts && vals.length >= 4 ? forecast(vals, labs, 3) : null
    if (fc) { forecastMap.set(w.title, fc); labelMap.set(w.title, labs) }
    const colProfiles = rows.length >= 2 && cols.length >= 1 ? profileColumns(rows, cols) : []
    const dimBreakdowns = colProfiles.length ? computeDimensionBreakdowns(rows, colProfiles) : []
    const withinCorrs = colProfiles.length ? withinWidgetCorrelations(rows, colProfiles) : []
    const primaryNumCol = colProfiles.find(p => p.type === 'numeric')?.name
    const primaryLabelCol = colProfiles.find(p => p.type === 'categorical' || p.type === 'date')?.name
    const ranked = primaryNumCol && primaryLabelCol && rows.length >= 3
      ? rankRows(rows, primaryNumCol, primaryLabelCol, 5) : null
    qualityScores.push(quality.score)
    widgetContexts.push({
      title: w.title || '(untitled)', type: w.chart_type, pattern,
      quality_score: quality.score, quality_issues: quality.issues,
      narrative: buildNarrativeContext(w, trend, anomaly, period, fc, pareto),
      schema: { targetColumn: schema.targetColumn, actualColumn: schema.actualColumn,
        attainmentRate: schema.attainmentRate != null ? +schema.attainmentRate.toFixed(1) : null,
        currencyLikely: schema.currencyLikely, percentageLikely: schema.percentageLikely },
      sample_labels: labs.slice(0, 20), sample_values: vals.slice(0, 20),
      forecast_next: fc?.reliable ? `next period: ${fmtVal(fc.points[0].value)} (R²=${fc.r2.toFixed(2)})` : undefined,
      anomaly_count: anomaly?.anomalies.length ?? 0,
      pareto_summary: pareto ? `Top ${pareto.itemsFor80pct.length} items = 80% of ${fmtVal(pareto.total)}` : undefined,
      sql_query: w.sql_query, table_columns: cols.length ? cols : undefined,
      column_profiles: colProfiles.length ? colProfiles : undefined,
      top_rows: ranked?.top, bottom_rows: ranked?.bottom,
      dim_breakdowns: dimBreakdowns.length ? dimBreakdowns : undefined,
      within_correlations: withinCorrs.length ? withinCorrs : undefined,
    })
  }

  const correlations = correlateWidgets(widgets)
  const { score: healthScore, color: healthColor } = computeHealthScore(qualityScores)
  const widgetBlocks = widgetContexts.map(buildWidgetBlock).join('\n\n---\n\n')

  const sectionPrompt = `You are a senior executive intelligence analyst. Regenerate the "${sectionLabel}" section of the report "${canvasName}" with a FRESH PERSPECTIVE — use different language, emphasize different patterns, and surface insights not covered in the prior version.

DATA QUALITY: ${healthScore}/100

WIDGET DATA:
${widgetBlocks}

Return ONLY the JSON for a single section — no other text:
{
  "id": "sec_regen",
  "label": "${sectionLabel}",
  "icon": "<from icon list: overview|trending_up|trending_down|users|bar_chart|pie_chart|lightbulb|target|activity|dollar_sign|shopping_cart|building|globe|database|calendar|award|shield|zap>",
  "data_story": "<bold headline with specific number>",
  "key_finding": "<1 sentence with exact metric>",
  "narrative": "<2-3 sentences with real numbers>",
  "recommendation": "<concrete action>",
  "insights": [{"icon":"lightbulb","headline":"...","detail":"<specific stat>","type":"positive|negative|neutral|warning","confidence":4}],
  "top_performers": [{"label":"...","value":0,"formatted_value":"...","pct_of_total":0,"rank":1}],
  "bottom_performers": [{"label":"...","value":0,"formatted_value":"...","rank":1}],
  "kpis": [{"label":"...","value":"...","trend":"up|down|neutral","trend_pct":"","sparkline_data":[]}],
  "charts": [{"title":"...","type":"bar|line|area|pie|combo|waterfall|scatter|bullet|table","insight":"<1-2 sentence finding specific to this chart's data>","data":[{"name":"...","value":0}]}]
}`

  onProgress?.(`Sending section prompt for "${sectionLabel}"…`)
  let responseText = ''
  try {
    const resp = await intelligenceApi.analyze({ prompt: sectionPrompt, canvas_name: canvasName })
    responseText = resp.data?.text ?? ''
  } catch {
    return null
  }

  const parsed = parseResponse(responseText)
  if (!parsed) return null

  // parseResponse returns the top-level object; sections array may or may not be present
  const rawSection: AgentSection | null = (() => {
    // If it wrapped it in sections array
    if (Array.isArray((parsed as Record<string, unknown>).sections)) {
      return ((parsed as Record<string, unknown>).sections as AgentSection[])[0] ?? null
    }
    // If it returned just the section object
    if ((parsed as Record<string, unknown>).id && (parsed as Record<string, unknown>).label) {
      return parsed as unknown as AgentSection
    }
    return null
  })()

  if (!rawSection) return null

  const section: AgentSection = {
    id: String(rawSection.id ?? `sec_regen_${Date.now()}`),
    label: String(rawSection.label ?? sectionLabel),
    icon: String(rawSection.icon ?? 'bar_chart'),
    narrative: String(rawSection.narrative ?? ''),
    data_story: rawSection.data_story ? String(rawSection.data_story) : undefined,
    key_finding: rawSection.key_finding ? String(rawSection.key_finding) : undefined,
    recommendation: rawSection.recommendation ? String(rawSection.recommendation) : undefined,
    insights: (rawSection.insights ?? []).slice(0, 4).map(sanitizeInsight),
    top_performers: (rawSection.top_performers ?? []).slice(0, 5).map(sanitizePerformer),
    bottom_performers: (rawSection.bottom_performers ?? []).slice(0, 5).map(sanitizePerformer),
    kpis: (rawSection.kpis ?? []).slice(0, 4).map(sanitizeKpi),
    charts: (rawSection.charts ?? []).slice(0, 4).map(sanitizeChart),
  }

  injectSourceSql([section], widgetSqlMap)
  injectReferenceLines([section])

  return section
}
