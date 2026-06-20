'use client'
import React from 'react'
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
  AreaChart, Area,
  ComposedChart, ReferenceLine,
  Treemap,
  RadialBarChart, RadialBar, PolarAngleAxis,
  FunnelChart, Funnel, LabelList,
  RadarChart, Radar, PolarGrid, PolarRadiusAxis,
} from 'recharts'
import type { ChartResult } from '@/stores/pipelineStore'

const DEFAULT_COLORS = [
  '#2563EB', '#0EA5E9', '#16A34A', '#D97706', '#DC2626',
  '#7C3AED', '#0D9488', '#E8520A', '#DB2777', '#65A30D',
  '#6366F1', '#F59E0B', '#10B981', '#8B5CF6', '#EF4444',
]

// Compact swatch legend rendered beneath a chart. Used in chat where recharts'
// built-in legend either can't represent per-category colours (single-series
// bars) or is hidden at small heights. Fixed height + overflow:hidden so it
// never pushes the chart past its allotted box.
function SwatchLegend({ items, colors, height: h = 24 }: { items: string[]; colors: string[]; height?: number }) {
  return (
    <div style={{
      height: h, overflow: 'hidden',
      display: 'flex', flexWrap: 'wrap', alignContent: 'flex-start',
      gap: '3px 12px', justifyContent: 'center', padding: '4px 8px 0',
      fontSize: 10, lineHeight: 1.2,
    }}>
      {items.map((label, i) => (
        <span key={label + i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--dash-text-muted, #6B7280)', maxWidth: 130 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: colors[i % colors.length], flexShrink: 0 }} />
          <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
        </span>
      ))}
    </div>
  )
}

interface Props {
  result: ChartResult | null | undefined
  compact?: boolean
  colors?: string[]
  height?: number
  showAnomalies?: boolean
  anomalyIndices?: number[]
  /** Cross-filter callback — fired when user clicks a bar/slice/row */
  onDataPointClick?: (column: string, value: unknown) => void
  /** When true, render per-category colours + a swatch legend on single-series
   *  charts and force pie/donut legends to show. Opt-in for chat surfaces so
   *  dashboard widgets keep their existing single-colour look. */
  legend?: boolean
  /** Display-only column renames { originalColumnName: "Display Name" }. Applied to
   *  table headers; data/cell lookups still use the original column name. */
  columnLabels?: Record<string, string>
}

export function ChartRenderer({ result, compact = false, colors, height: heightProp, showAnomalies = false, anomalyIndices = [], onDataPointClick, legend = false, columnLabels }: Props) {
  const COLORS = colors?.length ? colors : DEFAULT_COLORS

  // Table sort + search state — must be declared before any early return (React rules of hooks)
  const [sortCol, setSortCol] = React.useState<string | null>(null)
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('asc')
  const [tableSearch, setTableSearch] = React.useState('')

  // Measure container width so the table can adapt font + padding to available space
  const tableContainerRef = React.useRef<HTMLDivElement>(null)
  const [tableWidth, setTableWidth] = React.useState(0)
  React.useEffect(() => {
    const el = tableContainerRef.current
    if (!el) return
    const obs = new ResizeObserver(entries => {
      setTableWidth(entries[0]?.contentRect.width ?? 0)
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // sortedRows must live at the top level — Rules of Hooks.
  const _rawRows = result?.chart_data?.rows as Record<string, unknown>[] | undefined
  const sortedRows = React.useMemo(() => {
    let rows = _rawRows ?? []
    if (tableSearch.trim()) {
      const q = tableSearch.trim().toLowerCase()
      rows = rows.filter(r => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(q)))
    }
    if (!sortCol) return rows
    return [...rows].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol]
      const n = (x: unknown) => typeof x === 'number' ? x : (parseFloat(String(x)) || 0)
      if (typeof av === 'number' || typeof bv === 'number') {
        return sortDir === 'asc' ? n(av) - n(bv) : n(bv) - n(av)
      }
      return sortDir === 'asc'
        ? String(av ?? '').localeCompare(String(bv ?? ''))
        : String(bv ?? '').localeCompare(String(av ?? ''))
    })
  }, [_rawRows, sortCol, sortDir, tableSearch])

  if (!result) return null
  const { chart_type, title, x_axis_label, y_axis_label, chart_data } = result
  if (!chart_data) return null
  const { rows, columns, labels, values } = chart_data
  // Display-only column renames: explicit prop wins, else carried on the result.
  const effColumnLabels = columnLabels ?? result.column_labels

  const height = heightProp ?? (compact ? 180 : 300)
  const ct = (chart_type || 'bar_vertical').toLowerCase()

  // Extended fields stored by orchestrator for complex chart types
  const series   = (chart_data as Record<string, unknown>).series   as { name: string; values: (number | null)[] }[] | undefined
  const matrix   = (chart_data as Record<string, unknown>).matrix   as { row_labels: string[]; col_labels: string[]; values: (number | null)[][] } | undefined
  const barVals  = (chart_data as Record<string, unknown>).bar_values  as (number | null)[] | undefined
  const lineVals = (chart_data as Record<string, unknown>).line_values as (number | null)[] | undefined
  const barLabel = (chart_data as Record<string, unknown>).bar_label  as string | undefined
  const lineLbl  = (chart_data as Record<string, unknown>).line_label as string | undefined
  const xVals    = (chart_data as Record<string, unknown>).x_values   as (number | null)[] | undefined
  const yVals    = (chart_data as Record<string, unknown>).y_values   as (number | null)[] | undefined
  const zVals    = (chart_data as Record<string, unknown>).z_values   as (number | null)[] | undefined
  // New chart type fields
  const targetVals   = (chart_data as Record<string, unknown>).target_values as (number | null)[] | undefined
  const boxStats     = (chart_data as Record<string, unknown>).box_stats     as Array<{ min: number; q1: number; median: number; q3: number; max: number }> | undefined
  const sankeyNodes  = (chart_data as Record<string, unknown>).nodes         as string[] | undefined
  const sankeyLinks  = (chart_data as Record<string, unknown>).links         as Array<{ source: number; target: number; value: number }> | undefined
  const chordMatrix  = (chart_data as Record<string, unknown>).chord_matrix  as { entities: string[]; matrix: number[][] } | undefined
  const netNodes     = (chart_data as Record<string, unknown>).network_nodes as string[] | undefined
  const netEdges     = (chart_data as Record<string, unknown>).network_edges as Array<{ source: string; target: string; weight: number }> | undefined
  const ganttTasks   = (chart_data as Record<string, unknown>).gantt_tasks   as Array<{ task: string; start: string; end: string; category: string }> | undefined
  const orgNodes     = (chart_data as Record<string, unknown>).org_nodes     as Array<{ id: string; name: string; parent: string }> | undefined

  // Smart column detection: when rows contain typed data, prefer string→X, number→Y
  let xKey = columns[0] || x_axis_label || 'x'
  let yKey = columns[1] || y_axis_label || 'value'
  if (rows.length > 0 && columns.length >= 2) {
    const sample = rows[0]
    const strCols = columns.filter(c => typeof sample[c] === 'string')
    const numCols = columns.filter(c => typeof sample[c] === 'number')
    if (strCols.length > 0 && numCols.length > 0) {
      xKey = strCols[0]
      yKey = numCols[0]
    } else if (numCols.length >= 2 && typeof sample[xKey] === 'number' && typeof sample[yKey] === 'number') {
      // Both numeric: keep default order (x=first, y=second)
    } else if (strCols.length >= 2) {
      // Both strings: use first as category, count rows as value (fallback)
      xKey = strCols[0]
    }
  }

  // recharts data array from rows (or from labels/values if rows is empty)
  const rawRechartData = rows.length > 0
    ? rows
    : labels.map((l, i) => ({ [xKey]: l, [yKey]: values[i] }))

  // Strip trailing all-zero rows (SQL often returns future months with 0 values)
  const lastNonZeroIdx = (() => {
    for (let i = rawRechartData.length - 1; i >= 0; i--) {
      const v = Number(rawRechartData[i][yKey] ?? 0)
      if (!isNaN(v) && v !== 0) return i
    }
    return rawRechartData.length - 1
  })()
  const trimmedData = rawRechartData.slice(0, lastNonZeroIdx + 1)

  // Format ISO datetime labels to a readable short form (e.g. "2026-03-01T00:00:00" → "Mar 2026")
  const formatXLabel = (val: unknown): string => {
    const s = String(val ?? '')
    // Matches ISO datetime: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD 00:00:00
    const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ]/)
    if (isoMatch) {
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
      const mon = months[parseInt(isoMatch[2], 10) - 1] ?? isoMatch[2]
      return `${mon} ${isoMatch[1]}`
    }
    return s
  }

  const rechartData = trimmedData.map(row => ({
    ...row,
    [xKey]: formatXLabel(row[xKey]),
  }))

  // ── Shared computed values (used by ref-line, labels, comparison) ──────────
  const numYVals = rechartData.map(r => Number(r[yKey] ?? 0)).filter(n => !isNaN(n) && isFinite(n))
  const avgY = numYVals.length > 1 ? numYVals.reduce((s, v) => s + v, 0) / numYVals.length : null
  const fmtAvg = avgY !== null
    ? (Math.abs(avgY) >= 1e6 ? `Avg ${(avgY / 1e6).toFixed(1)}M` : Math.abs(avgY) >= 1e3 ? `Avg ${(avgY / 1e3).toFixed(1)}K` : `Avg ${Math.round(avgY)}`)
    : ''

  // Time-series axes (dates / months) should stay single-colour — a rainbow of
  // months reads as noise. Categorical axes get one colour per category.
  const xIsTime =
    trimmedData.some(r => /^\d{4}-\d{2}-\d{2}[T ]/.test(String(r[xKey] ?? ''))) ||
    /\b(date|month|year|day|time|week|quarter|qtr|period|fy)\b/i.test(String(xKey))
  const metricName = y_axis_label || yKey

  // ── KPI ──────────────────────────────────────────────────────────────────────
  if (ct === 'kpi' || ct === 'kpi_card') {
    // Prefer chart_data.values; fall back to the first column of row 0; finally
    // fall back to the first NUMERIC field of row 0 (covers refreshed snapshots
    // where the metric isn't columns[0] / values wasn't populated).
    const r0 = rows[0] as Record<string, unknown> | undefined
    const firstNumeric = r0 ? Object.values(r0).find((v) => typeof v === 'number') : undefined
    const val = values.find((v) => v !== null && v !== undefined)
      ?? (r0 ? (r0[columns[0] ?? ''] ?? firstNumeric) : undefined)
    const displayVal = typeof val === 'number'
      ? val.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : String(val ?? 'N/A')
    const fontSize = height < 120 ? 'text-2xl' : height < 200 ? 'text-4xl' : 'text-5xl'
    return (
      <div className="flex flex-col items-center justify-center w-full" style={{ height }}>
        {title && <p className="text-sm text-gray-500 mb-1 text-center truncate max-w-full px-2">{title}</p>}
        <p className={`${fontSize} font-bold text-brand font-display`}>{displayVal}</p>
      </div>
    )
  }

  // ── Multi-row card ────────────────────────────────────────────────────────────
  if (ct === 'multi_row_card') {
    const kpis = rows.length > 0
      ? rows.map(r => ({ name: String(r[columns[0]] ?? ''), val: r[columns[1]] }))
      : labels.map((l, i) => ({ name: l, val: values[i] }))
    const fmtVal = (v: unknown) =>
      typeof v === 'number'
        ? (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toLocaleString(undefined, { maximumFractionDigits: 2 }))
        : String(v ?? '—')
    return (
      <div className="w-full overflow-auto" style={{ height }}>
        <div className="flex flex-col divide-y divide-gray-100">
          {kpis.map((k, i) => (
            <div key={i} className="flex items-center justify-between px-3 py-2 hover:bg-gray-50 transition-colors">
              <span className="text-xs font-medium text-gray-500 truncate max-w-[55%]">{k.name}</span>
              <span className="text-sm font-bold text-blue-700 tabular-nums">{fmtVal(k.val)}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── Table ─────────────────────────────────────────────────────────────────────
  if (ct === 'table' || ct === 'data_table') {
    const baseCols = columns.length ? columns : (rows[0] ? Object.keys(rows[0]) : [])
    // Drop redundant/empty columns the way the report table does:
    //  1) a column whose every value duplicates an earlier column (e.g. "ends" == "placement count")
    //  2) a column whose every value is blank / NaN / NA / null / 0  (phantom "value" columns)
    const _dataRows = sortedRows.length ? sortedRows : rows
    const _isJunk = (v: unknown) => {
      if (v === null || v === undefined) return true
      const s = String(v).trim()
      if (s === '' || /^(nan|na|n\/a|null|undefined)$/i.test(s)) return true
      const n = Number(v)
      return !isNaN(n) && n === 0
    }
    const cols = baseCols.filter((col, idx) => {
      if (_dataRows.length === 0) return true
      // (1) exact-duplicate of an earlier kept column → drop
      const dup = baseCols.slice(0, idx).some(prev =>
        _dataRows.every(r => String(r[col] ?? '') === String(r[prev] ?? '')))
      if (dup) return false
      // (2) every value is junk → drop
      return _dataRows.some(r => !_isJunk(r[col]))
    })

    // Adaptive sizing — tier based on measured container width
    const tw = tableWidth || 600
    const tSz: 'xs' | 'sm' | 'md' | 'lg' = tw < 280 ? 'xs' : tw < 420 ? 'sm' : tw < 650 ? 'md' : 'lg'
    const T = {
      font:   { xs: 9,  sm: 10, md: 11, lg: 12 }[tSz],
      padX:   { xs: 4,  sm: 6,  md: 8,  lg: 12 }[tSz],
      padY:   { xs: 2,  sm: 3,  md: 4,  lg: 6  }[tSz],
      colMin: { xs: 70, sm: 90, md: 110, lg: 140 }[tSz],
      numMin: { xs: 50, sm: 60, md: 72,  lg: 90  }[tSz],
      barW:   { xs: 36, sm: 44, md: 52,  lg: 64  }[tSz],
    }

    // Heatmap: per-column min/max for numeric columns
    const colStats: Record<string, { min: number; max: number; isNum: boolean }> = {}
    cols.forEach(c => {
      const nums = sortedRows.map(r => {
        const v = r[c]; return typeof v === 'number' ? v : parseFloat(String(v ?? ''))
      }).filter(n => !isNaN(n) && isFinite(n))
      colStats[c] = nums.length > 1
        ? { min: Math.min(...nums), max: Math.max(...nums), isNum: true }
        : { min: 0, max: 0, isNum: false }
    })
    const primaryNumCol = cols.find(c => colStats[c]?.isNum) ?? null
    const primaryMax = primaryNumCol && colStats[primaryNumCol] ? colStats[primaryNumCol].max : 1

    function heatmapBg(col: string, rawVal: unknown): string | undefined {
      const stats = colStats[col]
      if (!stats?.isNum) return undefined
      const v = typeof rawVal === 'number' ? rawVal : parseFloat(String(rawVal ?? ''))
      if (isNaN(v) || stats.max === stats.min) return undefined
      const intensity = (v - stats.min) / (stats.max - stats.min)
      return v >= 0
        ? `rgba(37,99,235,${(intensity * 0.22).toFixed(3)})`
        : `rgba(220,38,38,${(Math.abs(intensity) * 0.22).toFixed(3)})`
    }

    const thStyle: React.CSSProperties = {
      background: 'var(--dash-th-bg, #F3F4F6)',
      color: 'var(--dash-text-muted, #6B7280)',
      borderColor: 'var(--dash-table-border, #E5E7EB)',
    }

    return (
      <div ref={tableContainerRef} className="flex flex-col w-full min-h-0" style={{ height: height ?? '100%', fontSize: T.font }}>

        {/* Search bar */}
        <div
          className="flex items-center gap-1 border-b border-gray-100 bg-white flex-shrink-0"
          style={{ padding: `${T.padY}px ${T.padX}px` }}
        >
          <svg className="flex-shrink-0 text-gray-400" style={{ width: T.font, height: T.font }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" />
          </svg>
          <input
            value={tableSearch}
            onChange={e => setTableSearch(e.target.value)}
            placeholder="Search…"
            className="flex-1 outline-none placeholder-gray-400 bg-transparent"
            style={{ fontSize: T.font }}
          />
          {tableSearch && (
            <button onClick={() => setTableSearch('')} className="text-gray-300 hover:text-gray-500 leading-none" style={{ fontSize: T.font }}>✕</button>
          )}
        </div>

        {/* Scrollable table — auto layout, min-width per col */}
        <div className="overflow-auto flex-1 min-h-0">
          <table className="border-collapse" style={{ tableLayout: 'auto', minWidth: '100%', fontSize: T.font }}>
            <colgroup>
              {cols.map(c => <col key={c} style={{ minWidth: colStats[c]?.isNum ? T.numMin : T.colMin }} />)}
              {primaryNumCol && <col style={{ width: T.barW, minWidth: T.barW }} />}
            </colgroup>
            <thead className="sticky top-0 z-10">
              <tr>
                {cols.map(c => (
                  <th
                    key={c}
                    className="text-left font-semibold border-b select-none cursor-pointer hover:opacity-80"
                    style={{ ...thStyle, padding: `${T.padY}px ${T.padX}px`, whiteSpace: 'nowrap' }}
                    title={effColumnLabels?.[c] ? `${effColumnLabels[c]} · ${c}` : c}
                    onClick={() => {
                      if (sortCol === c) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
                      else { setSortCol(c); setSortDir('asc') }
                    }}
                  >
                    {(effColumnLabels?.[c] ?? c)}{sortCol === c ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                  </th>
                ))}
                {primaryNumCol && (
                  <th className="border-b" style={{ ...thStyle, width: T.barW, padding: `${T.padY}px ${T.padX}px` }} />
                )}
              </tr>
            </thead>
            <tbody>
              {sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={cols.length + (primaryNumCol ? 1 : 0)} className="text-center text-gray-400" style={{ padding: `${T.padY * 3}px ${T.padX}px` }}>
                    No results
                  </td>
                </tr>
              ) : sortedRows.map((row, i) => {
                const rowBg = i % 2 === 1 ? 'var(--dash-row-alt, #F9FAFB)' : 'var(--dash-card-bg, #FFFFFF)'
                const barVal = primaryNumCol ? (typeof row[primaryNumCol] === 'number' ? row[primaryNumCol] as number : parseFloat(String(row[primaryNumCol] ?? 0))) : 0
                const barValSafe = isNaN(barVal as number) ? 0 : barVal as number
                const barPct = primaryMax > 0 ? Math.max(0, Math.min(1, barValSafe / primaryMax)) : 0
                return (
                  <tr
                    key={i}
                    className={onDataPointClick ? 'cursor-pointer hover:opacity-80' : ''}
                    style={{ background: rowBg }}
                    onClick={() => onDataPointClick && cols[0] && onDataPointClick(cols[0], row[cols[0]])}
                  >
                    {cols.map(c => {
                      const heat = heatmapBg(c, row[c])
                      return (
                        <td
                          key={c}
                          className="border-b"
                          title={String(row[c] ?? '')}
                          style={{
                            padding: `${T.padY}px ${T.padX}px`,
                            color: 'var(--dash-row-text, #374151)',
                            borderColor: 'var(--dash-table-border, #E5E7EB)',
                            background: heat ?? undefined,
                            fontWeight: colStats[c]?.isNum ? 500 : undefined,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {String(row[c] ?? '')}
                        </td>
                      )
                    })}
                    {primaryNumCol && (
                      <td
                        className="border-b"
                        style={{ padding: `${T.padY}px ${T.padX}px`, borderColor: 'var(--dash-table-border, #E5E7EB)', background: rowBg, width: T.barW }}
                      >
                        {(T.barW ?? 0) > 8 && (
                          <svg width={T.barW - 8} height={8}>
                            <rect x={0} y={0} width={T.barW - 8} height={8} rx={2} fill="var(--dash-row-alt, #F3F4F6)" opacity={0.5} />
                            <rect x={0} y={0} width={Math.max(2, barPct * (T.barW - 8))} height={8} rx={2} fill={COLORS[0]} opacity={0.75} />
                          </svg>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Footer */}
        {sortedRows.length > 0 && (
          <div
            className="text-gray-400 border-t border-gray-100 flex-shrink-0 flex items-center justify-between"
            style={{ padding: `${T.padY}px ${T.padX}px`, fontSize: Math.max(9, T.font - 1) }}
          >
            <span>
              {tableSearch
                ? `${sortedRows.length} of ${(_rawRows ?? []).length} rows`
                : `${sortedRows.length} row${sortedRows.length !== 1 ? 's' : ''}`}
            </span>
            <span>{cols.length} col{cols.length !== 1 ? 's' : ''}</span>
          </div>
        )}
      </div>
    )
  }

  // ── Pivot table ────────────────────────────────────────────────────────────────
  if (ct === 'pivot_table') {
    const rowDimKey = columns[0] || xKey
    const colDimKey = columns[1] || 'col'
    const valueKey  = columns[2] || yKey
    const rowValues = Array.from(new Set(rechartData.map(r => String(r[rowDimKey] ?? ''))))
    const colValues = Array.from(new Set(rechartData.map(r => String(r[colDimKey] ?? ''))))
    const lookup: Record<string, Record<string, unknown>> = {}
    rechartData.forEach(r => {
      const rv = String(r[rowDimKey] ?? ''), cv = String(r[colDimKey] ?? '')
      if (!lookup[rv]) lookup[rv] = {}
      lookup[rv][cv] = r[valueKey]
    })
    const thStyle: React.CSSProperties = {
      background: 'var(--dash-th-bg, #F3F4F6)',
      color: 'var(--dash-text-muted, #6B7280)',
      borderColor: 'var(--dash-table-border, #E5E7EB)',
    }
    return (() => {
        const ptw = tableWidth || 600
        const pSz: 'xs'|'sm'|'md'|'lg' = ptw < 280 ? 'xs' : ptw < 420 ? 'sm' : ptw < 650 ? 'md' : 'lg'
        const pF  = { xs: 9, sm: 10, md: 11, lg: 12 }[pSz]
        const pPX = { xs: 4, sm: 6,  md: 8,  lg: 12 }[pSz]
        const pPY = { xs: 2, sm: 3,  md: 4,  lg: 6  }[pSz]
        const pLabelMin = { xs: 70, sm: 90, md: 110, lg: 140 }[pSz]
        const pValMin   = { xs: 48, sm: 60, md: 72,  lg: 90  }[pSz]
        const cellStyle = (align: 'left'|'right', isHead = false): React.CSSProperties => ({
          padding: `${pPY}px ${pPX}px`,
          whiteSpace: 'nowrap',
          textAlign: align,
          background: isHead ? 'var(--dash-th-bg, #F3F4F6)' : undefined,
          color: isHead ? 'var(--dash-text-muted, #6B7280)' : 'var(--dash-row-text, #374151)',
          borderColor: 'var(--dash-table-border, #E5E7EB)',
          fontWeight: isHead ? 600 : undefined,
        })
        return (
          <div ref={tableContainerRef} className="overflow-auto w-full" style={{ height, fontSize: pF }}>
            <table className="border-collapse" style={{ minWidth: '100%', tableLayout: 'auto', fontSize: pF }}>
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="border" style={{ ...cellStyle('left', true), minWidth: pLabelMin }}>{rowDimKey}</th>
                  {colValues.map(cv => (
                    <th key={cv} className="border" style={{ ...cellStyle('right', true), minWidth: pValMin }}>{cv}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rowValues.map((rv, i) => (
                  <tr key={rv} style={{ background: i % 2 === 1 ? 'var(--dash-row-alt, #F9FAFB)' : 'var(--dash-card-bg, #FFFFFF)' }}>
                    <td className="border font-medium" style={cellStyle('left')}>{rv}</td>
                    {colValues.map(cv => (
                      <td key={cv} className="border" style={cellStyle('right')}>
                        {lookup[rv]?.[cv] != null ? String(lookup[rv][cv]) : '—'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })()
  }

  // ── Pie / Donut ────────────────────────────────────────────────────────────────
  if (ct === 'pie' || ct === 'donut') {
    const MAX_SLICES = 12
    // Coerce values to numbers (they can arrive as strings from JSON) and drop
    // null / NaN / non-positive slices — recharts can't size those and they make
    // the pie render blank or with wrong angles.
    let rawPie = (labels.length > 0
      ? labels.map((l, i) => ({ name: String(l ?? ''), value: Number(values[i] ?? 0) }))
      : rechartData.map(r => ({ name: String(r[xKey] ?? ''), value: Number(r[yKey] ?? 0) }))
    ).filter(d => isFinite(d.value) && d.value > 0)

    if (rawPie.length === 0) {
      return (
        <div className="h-full flex items-center justify-center text-gray-300 text-xs" style={{ height }}>
          No positive values to chart
        </div>
      )
    }
    // Sort descending by value so "Other" bucket is the tail
    rawPie = [...rawPie].sort((a, b) => b.value - a.value)
    let pieData = rawPie
    if (rawPie.length > MAX_SLICES) {
      const top = rawPie.slice(0, MAX_SLICES - 1)
      const otherSum = rawPie.slice(MAX_SLICES - 1).reduce((s, d) => s + d.value, 0)
      pieData = [...top, { name: `Other (${rawPie.length - MAX_SLICES + 1})`, value: otherSum }]
    }
    const manySlices = pieData.length > 7
    const showLegend = legend ? height > 110 : height > 160
    // Only show inline labels when the card is tall enough to give the labels room.
    // Below 300px rely on the legend + tooltip — labels overflow card edges at smaller heights.
    const showInlineLabels = !manySlices && height >= 300
    // Truncate long names so labels never overflow the SVG viewport horizontally
    const truncName = (s: string) => s.length > 9 ? s.slice(0, 8) + '…' : s
    // Shrink the pie when inline labels are shown to keep them inside the SVG bounds.
    const outerRad = showInlineLabels ? '40%' : (manySlices ? '60%' : '55%')
    return (
      <div style={{ overflow: 'hidden', height }}>
        <ResponsiveContainer width="100%" height={height}>
          <PieChart style={onDataPointClick ? { cursor: 'pointer' } : undefined}>
            <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%"
              innerRadius={ct === 'donut' ? '45%' : 0}
              outerRadius={outerRad}
              label={showInlineLabels ? ({ name, percent }) => `${truncName(String(name ?? ''))} (${((percent ?? 0) * 100).toFixed(0)}%)` : undefined}
              labelLine={showInlineLabels}
              onClick={onDataPointClick ? (data) => onDataPointClick(xKey, data.name) : undefined}
            >
              {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
            <Tooltip formatter={(v: number) => v.toLocaleString()} />
            {showLegend && <Legend wrapperStyle={{ fontSize: 11, maxHeight: 60, overflowY: 'auto' }} />}
          </PieChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // ── Sunburst (two-ring pie) ────────────────────────────────────────────────────
  if (ct === 'sunburst') {
    if (columns.length >= 3 && rows.length > 0) {
      const parentKey = columns[0], childKey = columns[1], valKey = columns[2]
      const parentTotals: Record<string, number> = {}
      const childData = rows.map(r => ({
        name: String(r[childKey] ?? ''),
        value: Number(r[valKey] ?? 0),
        parent: String(r[parentKey] ?? ''),
      }))
      childData.forEach(c => { parentTotals[c.parent] = (parentTotals[c.parent] ?? 0) + c.value })
      const parentData = Object.entries(parentTotals).map(([name, value], i) => ({ name, value, fill: COLORS[i % COLORS.length] }))
      return (
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie data={parentData} dataKey="value" cx="50%" cy="50%" outerRadius="35%" stroke="white" strokeWidth={2}>
              {parentData.map((d, i) => <Cell key={i} fill={d.fill} />)}
            </Pie>
            <Pie data={childData} dataKey="value" cx="50%" cy="50%" innerRadius="40%" outerRadius="65%"
              label={height > 250 ? ({ name }) => name : undefined} labelLine={height > 250}>
              {childData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
            <Tooltip />
            {height > 200 && <Legend />}
          </PieChart>
        </ResponsiveContainer>
      )
    }
    // Fallback: render as donut
    const pieData = labels.map((l, i) => ({ name: l, value: values[i] ?? 0 }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius="45%" outerRadius="70%">
            {pieData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Pie>
          <Tooltip />
        </PieChart>
      </ResponsiveContainer>
    )
  }

  // ── Scatter ───────────────────────────────────────────────────────────────────
  if (ct === 'scatter') {
    const scatterData = rows.map(r => ({
      x: parseFloat(String(r[columns[0]] ?? 0)) || 0,
      y: parseFloat(String(r[columns[1]] ?? 0)) || 0,
    }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="x" name={x_axis_label} tick={{ fontSize: 11 }} />
          <YAxis dataKey="y" name={y_axis_label} tick={{ fontSize: 11 }} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} />
          <Scatter data={scatterData} fill={COLORS[0]} />
        </ScatterChart>
      </ResponsiveContainer>
    )
  }

  // ── Bubble ────────────────────────────────────────────────────────────────────
  if (ct === 'bubble') {
    // Support both orchestrator-stored x/y/z_values and raw rows
    const bubbleData = xVals && yVals && zVals
      ? xVals.map((x, i) => ({ x: x ?? 0, y: yVals[i] ?? 0, z: Math.max(1, zVals[i] ?? 1) }))
      : rows.map(r => ({
          x: parseFloat(String(r[columns[0]] ?? 0)) || 0,
          y: parseFloat(String(r[columns[1]] ?? 0)) || 0,
          z: Math.max(1, parseFloat(String(r[columns[2]] ?? 10)) || 10),
        }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="x" name={x_axis_label || columns[0]} tick={{ fontSize: 11 }} />
          <YAxis dataKey="y" name={y_axis_label || columns[1]} tick={{ fontSize: 11 }} />
          <Tooltip cursor={{ strokeDasharray: '3 3' }} content={({ payload }) => {
            if (!payload?.length) return null
            const d = payload[0]?.payload as { x: number; y: number; z: number }
            return (
              <div className="bg-white border border-gray-200 rounded-lg px-3 py-2 text-xs shadow">
                <p>{columns[0]}: {d?.x}</p>
                <p>{columns[1]}: {d?.y}</p>
                <p>{columns[2]}: {d?.z}</p>
              </div>
            )
          }} />
          <Scatter data={bubbleData} fill={COLORS[0]}>
            {bubbleData.map((d, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]}
                r={Math.min(30, Math.max(4, Math.sqrt(Number(d.z)) * 4))} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    )
  }

  // ── Line ──────────────────────────────────────────────────────────────────────
  if (ct === 'line') {
    const anomalySet = new Set(anomalyIndices)
    const legH = legend ? 22 : 0
    return (
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height={height - legH}>
          <LineChart data={rechartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={xKey} label={{ value: x_axis_label, position: 'insideBottom', offset: -5 }} tick={{ fontSize: 11 }} />
            <YAxis label={{ value: y_axis_label, angle: -90, position: 'insideLeft' }} tick={{ fontSize: 11 }} />
            <Tooltip />
            {avgY !== null && (
              <ReferenceLine y={avgY} stroke="#F59E0B" strokeDasharray="4 2"
                label={{ value: fmtAvg, position: 'insideTopRight', fill: '#F59E0B', fontSize: 9, fontWeight: 600 }} />
            )}
            <Line type="monotone" dataKey={yKey} name={metricName} stroke={COLORS[0]} strokeWidth={2}
              dot={showAnomalies ? (props: Record<string, unknown>) => {
                const idx = props.index as number
                if (!anomalySet.has(idx)) return <circle key={idx} cx={props.cx as number} cy={props.cy as number} r={0} fill="none" />
                return <circle key={idx} cx={props.cx as number} cy={props.cy as number} r={5} fill="#EF4444" stroke="white" strokeWidth={1.5} />
              } : false}
            />
          </LineChart>
        </ResponsiveContainer>
        {legend && <SwatchLegend items={[metricName]} colors={[COLORS[0]]} height={legH} />}
      </div>
    )
  }

  // ── Area ──────────────────────────────────────────────────────────────────────
  if (ct === 'area') {
    const legH = legend ? 22 : 0
    return (
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height={height - legH}>
          <AreaChart data={rechartData}>
            <defs>
              <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS[0]} stopOpacity={0.3} />
                <stop offset="95%" stopColor={COLORS[0]} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Area type="monotone" dataKey={yKey} name={metricName} stroke={COLORS[0]} strokeWidth={2}
              fill="url(#areaGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
        {legend && <SwatchLegend items={[metricName]} colors={[COLORS[0]]} height={legH} />}
      </div>
    )
  }

  // ── Stacked area ──────────────────────────────────────────────────────────────
  if (ct === 'stacked_area') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const areaData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={areaData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {seriesKeys.map((key, i) => (
            <Area key={key} type="monotone" dataKey={key} stackId="1"
              stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]}
              fillOpacity={0.6} strokeWidth={1.5} dot={false} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  // ── Horizontal bar ────────────────────────────────────────────────────────────
  if (ct === 'bar_horizontal') {
    // Dynamically size the Y-axis label column so long names like
    // "Commercial Lines Account Manager" don't wrap into adjacent bars.
    const longestLabel = rechartData.reduce((max, row) => {
      const label = String(row[xKey] ?? '')
      return label.length > max ? label.length : max
    }, 0)
    // ~6.5px per character, capped at 220px, minimum 120px
    const yAxisWidth = Math.min(220, Math.max(120, longestLabel * 6.5))
    const maxChars = Math.floor(yAxisWidth / 6.5)

    // Custom tick: truncates long labels with ellipsis; SVG <title> shows full text on hover
    const HorizontalYTick = ({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) => {
      const full = String(payload?.value ?? '')
      const display = full.length > maxChars ? full.slice(0, maxChars - 1) + '…' : full
      return (
        <g transform={`translate(${x},${y})`}>
          <title>{full}</title>
          <text x={0} y={0} dy={4} textAnchor="end" fontSize={11} fill="#6B7280">{display}</text>
        </g>
      )
    }

    // Cap display at top 50 rows to avoid unusable charts; show a note if truncated
    const MAX_BARS = 50
    const truncated = rechartData.length > MAX_BARS
    const displayData = truncated ? rechartData.slice(0, MAX_BARS) : rechartData
    // Each bar needs at least 30px height; cap total at 700px with internal scroll
    const barH = Math.min(700, Math.max(height, displayData.length * 30))
    return (
      <div>
        {truncated && (
          <p style={{ fontSize: 10, color: '#9CA3AF', marginBottom: 4 }}>
            Showing top {MAX_BARS} of {rechartData.length} rows (sorted by value)
          </p>
        )}
        <ResponsiveContainer width="100%" height={barH}>
          <BarChart data={displayData} layout="vertical" margin={{ left: 8, right: 56, top: 4, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis dataKey={xKey} type="category" width={yAxisWidth} tick={<HorizontalYTick />} axisLine={false} tickLine={false} />
            <Tooltip formatter={(v: number) => v.toLocaleString()} />
            {avgY !== null && (
              <ReferenceLine x={avgY} stroke="#F59E0B" strokeDasharray="4 2"
                label={{ value: fmtAvg, position: 'top', fill: '#F59E0B', fontSize: 9, fontWeight: 600 }} />
            )}
            <Bar
              dataKey={yKey}
              radius={[0, 3, 3, 0]}
              maxBarSize={22}
              onClick={onDataPointClick ? (data) => onDataPointClick(xKey, data[xKey]) : undefined}
            >
              {displayData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
              <LabelList dataKey={yKey} position="right" style={{ fontSize: 9, fill: 'var(--dash-text-muted, #6B7280)' }}
                formatter={(v: number) => isNaN(v) ? '' : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v)} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // ── Stacked bar (vertical) ────────────────────────────────────────────────────
  if (ct === 'stacked_bar' || ct === 'stacked_bar_100') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const stackData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={stackData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {seriesKeys.map((key, i) => (
            <Bar key={key} dataKey={key} stackId="stack"
              fill={COLORS[i % COLORS.length]}
              radius={i === seriesKeys.length - 1 ? [3, 3, 0, 0] : undefined} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // ── Stacked bar horizontal ────────────────────────────────────────────────────
  if (ct === 'stacked_bar_horizontal') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const stackData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData
    const barH = Math.max(height, stackData.length * 36)
    return (
      <ResponsiveContainer width="100%" height={barH}>
        <BarChart data={stackData} layout="vertical">
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis dataKey={xKey} type="category" width={110} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {seriesKeys.map((key, i) => (
            <Bar key={key} dataKey={key} stackId="hstack" fill={COLORS[i % COLORS.length]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // ── Grouped bar ───────────────────────────────────────────────────────────────
  if (ct === 'grouped_bar') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const groupData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={groupData} barGap={2} barCategoryGap="20%">
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {seriesKeys.map((key, i) => (
            <Bar key={key} dataKey={key} fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // ── Combo (bar + line on same axes) ──────────────────────────────────────────
  if (ct === 'combo') {
    const bKey = barLabel || (columns[1] ?? yKey)
    const lKey = lineLbl  || (columns[2] ?? '')
    const comboData = barVals && lineVals
      ? labels.map((l, i) => ({ [xKey]: l, [bKey]: barVals[i] ?? 0, [lKey]: lineVals[i] ?? 0 }))
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={comboData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="bar" tick={{ fontSize: 11 }} />
          {lKey && <YAxis yAxisId="line" orientation="right" tick={{ fontSize: 11 }} />}
          <Tooltip />
          <Legend />
          <Bar yAxisId="bar" dataKey={bKey} fill={COLORS[0]} radius={[3, 3, 0, 0]} opacity={0.85} />
          {lKey && (
            <Line yAxisId="line" type="monotone" dataKey={lKey}
              stroke={COLORS[4]} strokeWidth={2.5} dot={{ r: 3 }} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    )
  }

  // ── Histogram ─────────────────────────────────────────────────────────────────
  if (ct === 'histogram') {
    const raw = values.filter((v): v is number => typeof v === 'number' && !isNaN(v))
    if (raw.length === 0) return <div className="flex items-center justify-center h-full text-gray-300 text-xs" style={{ height }}>No data</div>
    const binCount = Math.min(20, Math.ceil(Math.sqrt(raw.length)))
    const minV = Math.min(...raw), maxV = Math.max(...raw)
    const binW = (maxV - minV) / binCount || 1
    const bins = Array.from({ length: binCount }, (_, i) => ({
      label: `${(minV + i * binW).toFixed(1)}`,
      count: 0,
    }))
    raw.forEach(v => {
      const idx = Math.min(Math.floor((v - minV) / binW), binCount - 1)
      bins[idx].count++
    })
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={bins} barCategoryGap="2%">
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={Math.floor(binCount / 6)} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: number) => [v, 'Count']} />
          <Bar dataKey="count" fill={COLORS[0]} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // ── Waterfall ─────────────────────────────────────────────────────────────────
  if (ct === 'waterfall') {
    let cumulative = 0
    const wfData = labels.map((l, i) => {
      const v = values[i] ?? 0
      const isLast = i === labels.length - 1
      const isTotal = isLast || /total|net|end/i.test(l)
      let offset: number, barVal: number, fill: string
      if (isTotal) {
        offset = 0; barVal = cumulative; fill = COLORS[1]
      } else if (Number(v) >= 0) {
        offset = cumulative; barVal = Number(v); fill = '#16A34A'
        cumulative += Number(v)
      } else {
        offset = cumulative + Number(v); barVal = Math.abs(Number(v)); fill = '#DC2626'
        cumulative += Number(v)
      }
      return { name: l, offset, value: barVal, fill }
    })
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={wfData} barCategoryGap="25%">
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip formatter={(_v, _n, props) => [props.payload.value, props.payload.name]} />
          <Bar dataKey="offset" stackId="wf" fill="transparent" />
          <Bar dataKey="value" stackId="wf" radius={[3, 3, 0, 0]}>
            {wfData.map((d, i) => <Cell key={i} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // ── Funnel ────────────────────────────────────────────────────────────────────
  if (ct === 'funnel') {
    const funnelData = labels.map((l, i) => ({
      name: l, value: values[i] ?? 0, fill: COLORS[i % COLORS.length],
    })).sort((a, b) => Number(b.value) - Number(a.value))
    const fmtFunnel = (v: number) =>
      Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
      : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K`
      : v.toLocaleString(undefined, { maximumFractionDigits: 1 })
    // Centred, two-line label (name + value) drawn inside each band. Anchored to the
    // band centre rather than an edge so wide top segments never clip, with a dark halo
    // (paint-order: stroke) so white text stays legible on every segment colour.
    const FunnelLabel = (props: Record<string, unknown>) => {
      const x = Number(props.x), y = Number(props.y)
      const w = Number(props.width), h = Number(props.height)
      const idx = Number(props.index)
      const d = funnelData[idx]
      if (!d || !isFinite(x) || !isFinite(w)) return null
      const cx = x + w / 2
      const cy = y + h / 2
      const halo = { stroke: 'rgba(2,18,38,0.45)', strokeWidth: 3, paintOrder: 'stroke' as const }
      return (
        <g style={{ pointerEvents: 'none' }}>
          <text x={cx} y={cy - 4} textAnchor="middle" fill="#fff" {...halo}
            style={{ fontSize: 11, fontWeight: 700 }}>{d.name}</text>
          <text x={cx} y={cy + 11} textAnchor="middle" fill="#fff" {...halo}
            style={{ fontSize: 11, fontWeight: 600 }}>{fmtFunnel(Number(d.value))}</text>
        </g>
      )
    }
    return (
      <ResponsiveContainer width="100%" height={height}>
        <FunnelChart margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
          <Tooltip formatter={(v: number) => Number(v).toLocaleString()} />
          <Funnel dataKey="value" data={funnelData} isAnimationActive>
            <LabelList content={FunnelLabel as unknown as React.ComponentProps<typeof LabelList>['content']} />
          </Funnel>
        </FunnelChart>
      </ResponsiveContainer>
    )
  }

  // ── Treemap ───────────────────────────────────────────────────────────────────
  if (ct === 'treemap') {
    const tmData = labels.map((l, i) => ({ name: l, size: Math.max(1, Number(values[i] ?? 1)) }))
    const CustomTile = ({ x, y, width, height: h, name, value }: Record<string, unknown>) => {
      const nx = Number(x), ny = Number(y), nw = Number(width), nh = Number(h)
      const idx = tmData.findIndex(d => d.name === name)
      const fill = COLORS[Math.max(0, idx) % COLORS.length]
      // Unique clip-path id per cell so text never overflows into neighbouring cells
      const clipId = `tm-clip-${Math.round(nx * 10)}-${Math.round(ny * 10)}`
      if (nw < 20 || nh < 20) return <g><rect x={nx} y={ny} width={nw} height={nh} fill={fill} stroke="white" strokeWidth={2} /></g>
      return (
        <g>
          <defs>
            <clipPath id={clipId}>
              <rect x={nx + 2} y={ny + 2} width={Math.max(0, nw - 4)} height={Math.max(0, nh - 4)} />
            </clipPath>
          </defs>
          <rect x={nx} y={ny} width={nw} height={nh} fill={fill} stroke="white" strokeWidth={2} rx={4} />
          {nw > 50 && nh > 30 && (
            <text x={nx + nw / 2} y={ny + nh / 2 - (nh > 50 ? 8 : 0)} textAnchor="middle" fill="white"
              fontSize={Math.min(13, nw / 6)} fontWeight={600} clipPath={`url(#${clipId})`}>
              {String(name)}
            </text>
          )}
          {nw > 50 && nh > 50 && (
            <text x={nx + nw / 2} y={ny + nh / 2 + 12} textAnchor="middle" fill="rgba(255,255,255,0.8)"
              fontSize={11} clipPath={`url(#${clipId})`}>
              {typeof value === 'number' ? value.toLocaleString() : String(value)}
            </text>
          )}
        </g>
      )
    }
    return (
      <div style={{ overflow: 'hidden', height }}>
        <ResponsiveContainer width="100%" height={height}>
          <Treemap data={tmData} dataKey="size" aspectRatio={4 / 3} stroke="white"
            content={<CustomTile />} />
        </ResponsiveContainer>
      </div>
    )
  }

  // ── Heatmap ───────────────────────────────────────────────────────────────────
  if (ct === 'heatmap') {
    const rowLabels = matrix?.row_labels ?? Array.from(new Set(rows.map(r => String(r[columns[0]] ?? ''))))
    const colLabels = matrix?.col_labels ?? Array.from(new Set(rows.map(r => String(r[columns[1]] ?? ''))))
    const cellMap: Record<string, Record<string, number>> = {}
    if (matrix) {
      matrix.row_labels.forEach((rl, ri) => {
        cellMap[rl] = {}
        matrix.col_labels.forEach((cl, ci) => { cellMap[rl][cl] = Number(matrix.values[ri]?.[ci] ?? 0) })
      })
    } else {
      rows.forEach(r => {
        const rl = String(r[columns[0]] ?? ''), cl = String(r[columns[1]] ?? '')
        if (!cellMap[rl]) cellMap[rl] = {}
        cellMap[rl][cl] = Number(r[columns[2]] ?? 0)
      })
    }
    const allVals = rowLabels.flatMap(rl => colLabels.map(cl => cellMap[rl]?.[cl] ?? 0))
    const minV = Math.min(...allVals), maxV = Math.max(...allVals)
    const norm = (v: number) => maxV === minV ? 0.5 : (v - minV) / (maxV - minV)
    const cellPx = Math.max(20, Math.min(48, Math.floor((height - 40) / Math.max(1, rowLabels.length))))
    return (
      <div className="overflow-auto w-full" style={{ height }}>
        <table className="border-collapse text-xs" style={{ tableLayout: 'fixed' }}>
          <thead>
            <tr>
              <th className="w-20 border" />
              {colLabels.map(cl => (
                <th key={cl} className="border px-1 py-0.5 font-medium text-gray-600 whitespace-nowrap text-center"
                  style={{ width: cellPx, fontSize: 9 }}>{cl}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowLabels.map(rl => (
              <tr key={rl}>
                <td className="border px-1 py-0.5 font-medium text-gray-600 whitespace-nowrap text-right pr-2">{rl}</td>
                {colLabels.map(cl => {
                  const v = cellMap[rl]?.[cl] ?? 0
                  const t = norm(v)
                  const bg = `rgba(37,99,235,${0.08 + t * 0.82})`
                  const fg = t > 0.55 ? 'white' : '#1F2937'
                  return (
                    <td key={cl} title={`${rl} / ${cl}: ${v}`}
                      style={{ background: bg, color: fg, height: cellPx, textAlign: 'center', border: '1px solid rgba(0,0,0,0.06)', fontSize: 10 }}>
                      {v !== 0 ? (Number.isInteger(v) ? v : v.toFixed(1)) : ''}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  // ── Gauge ──────────────────────────────────────────────────────────────────────
  if (ct === 'gauge') {
    const val = Number(values[0] ?? rows[0]?.[columns[0]] ?? 0)
    const maxVal = Number(values[1] ?? rows[0]?.[columns[1]] ?? Math.max(val * 1.25, 100))
    const pct = Math.min(100, Math.max(0, (val / maxVal) * 100))
    const gaugeColor = pct >= 80 ? '#16A34A' : pct >= 50 ? '#D97706' : '#DC2626'
    const gaugeData = [{ name: 'value', value: pct, fill: gaugeColor }]
    return (
      <div style={{ position: 'relative', height }}>
        <ResponsiveContainer width="100%" height={height}>
          <RadialBarChart cx="50%" cy="68%" innerRadius="55%" outerRadius="88%"
            barSize={18} data={gaugeData} startAngle={180} endAngle={0}>
            <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
            <RadialBar background={{ fill: '#F3F4F6' }} dataKey="value" cornerRadius={8} />
          </RadialBarChart>
        </ResponsiveContainer>
        <div style={{ position: 'absolute', bottom: '16%', left: '50%', transform: 'translateX(-50%)', textAlign: 'center' }}>
          <p style={{ fontSize: Math.max(16, Math.min(26, height / 7)), fontWeight: 800, margin: 0, color: gaugeColor }}>
            {val.toLocaleString(undefined, { maximumFractionDigits: 1 })}
          </p>
          <p style={{ fontSize: 10, color: '#9CA3AF', margin: '2px 0 0', whiteSpace: 'nowrap' }}>
            {x_axis_label || 'of'} {maxVal.toLocaleString()}
          </p>
        </div>
      </div>
    )
  }

  // ── Radar (Spider) ────────────────────────────────────────────────────────────
  if (ct === 'radar') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const radarData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { subject: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData.map(r => ({ subject: String(r[xKey] ?? ''), [yKey]: Number(r[yKey] ?? 0) }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={radarData} margin={{ top: 10, right: 30, bottom: 10, left: 30 }}>
          <PolarGrid />
          <PolarAngleAxis dataKey="subject" tick={{ fontSize: 11 }} />
          <PolarRadiusAxis tick={{ fontSize: 9 }} />
          {seriesKeys.map((key, i) => (
            <Radar key={key} name={key} dataKey={key}
              stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.25} strokeWidth={2} />
          ))}
          <Tooltip />
          {seriesKeys.length > 1 && <Legend />}
        </RadarChart>
      </ResponsiveContainer>
    )
  }

  // ── Ribbon (Bump / Rank-over-time) ────────────────────────────────────────────
  if (ct === 'ribbon') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const ribbonData = series
      ? labels.map((l, i) => {
          const obj: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { obj[s.name] = s.values[i] ?? 0 })
          return obj
        })
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={ribbonData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {seriesKeys.map((key, i) => (
            <Line key={key} type="monotone" dataKey={key}
              stroke={COLORS[i % COLORS.length]} strokeWidth={3}
              dot={{ r: 5, fill: COLORS[i % COLORS.length], stroke: 'white', strokeWidth: 2 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // ── Dot Plot (Strip Plot) ─────────────────────────────────────────────────────
  if (ct === 'dot_plot') {
    const dotData = rechartData.map(r => ({
      x: Number(r[yKey] ?? 0),
      y: String(r[xKey] ?? ''),
    }))
    const dotH = Math.max(height, dotData.length * 24)
    return (
      <div style={{ width: '100%', height, overflowY: dotData.length > 12 ? 'auto' : 'hidden' }}>
        <ResponsiveContainer width="100%" height={dotH}>
          <ScatterChart margin={{ left: 100, right: 20, top: 10, bottom: 20 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" dataKey="x" name={y_axis_label || yKey} tick={{ fontSize: 11 }}
              label={{ value: y_axis_label, position: 'insideBottom', offset: -10 }} />
            <YAxis type="category" dataKey="y" width={95} tick={{ fontSize: 10 }} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }}
              content={({ payload }) => {
                if (!payload?.length) return null
                const d = payload[0]?.payload as { x: number; y: string }
                return <div className="bg-white border rounded px-2 py-1 text-xs shadow"><p className="font-medium">{d.y}</p><p>{d.x}</p></div>
              }} />
            <Scatter data={dotData} fill={COLORS[0]}>
              {dotData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} r={6} />)}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // ── Bullet Chart ──────────────────────────────────────────────────────────────
  if (ct === 'bullet') {
    const bItems = labels.map((l, i) => ({
      name: l,
      actual: Number(values[i] ?? 0),
      target: Number(targetVals?.[i] ?? 0),
    }))
    const maxVal = Math.max(...bItems.flatMap(b => [b.actual, b.target])) * 1.15 || 100
    const bH = Math.max(height, bItems.length * 44)
    return (
      <div style={{ width: '100%', height, overflowY: bItems.length > 6 ? 'auto' : 'hidden' }}>
        <ResponsiveContainer width="100%" height={bH}>
          <ComposedChart data={bItems} layout="vertical" margin={{ left: 100, right: 30, top: 10, bottom: 10 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" domain={[0, maxVal]} tick={{ fontSize: 11 }} />
            <YAxis type="category" dataKey="name" width={95} tick={{ fontSize: 10 }} />
            <Tooltip formatter={(v: number, n: string) => [v.toLocaleString(), n === 'actual' ? 'Actual' : 'Target']} />
            <Bar dataKey="actual" barSize={16} radius={[0, 4, 4, 0]}
              background={{ fill: '#F3F4F6', radius: 4 }}>
              {bItems.map((b, i) => {
                const pct = b.target > 0 ? b.actual / b.target : 1
                const fill = pct >= 1 ? '#16A34A' : pct >= 0.75 ? COLORS[0] : '#D97706'
                return <Cell key={i} fill={fill} />
              })}
            </Bar>
            {bItems.some(b => b.target > 0) && (
              <Bar dataKey="target" barSize={3} fill="#1F2937" radius={[2, 2, 2, 2]} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // ── Scorecard (Goals / Metrics) ────────────────────────────────────────────────
  if (ct === 'scorecard') {
    const items = labels.map((l, i) => ({
      name: l,
      actual: Number(values[i] ?? 0),
      target: targetVals?.[i] != null ? Number(targetVals[i]) : null,
    }))
    return (
      <div className="w-full overflow-auto p-2 space-y-2" style={{ height }}>
        {items.map((item, i) => {
          const pct = item.target ? Math.min(150, (item.actual / item.target) * 100) : null
          const color = pct == null ? COLORS[0] : pct >= 100 ? '#16A34A' : pct >= 75 ? '#D97706' : '#DC2626'
          return (
            <div key={i} className="flex items-center gap-3 p-2.5 rounded-xl bg-gray-50 border border-gray-100">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-gray-500 truncate mb-0.5">{item.name}</p>
                <div className="flex items-baseline gap-1.5">
                  <span className="text-xl font-bold" style={{ color }}>
                    {item.actual.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                  </span>
                  {item.target != null && (
                    <span className="text-xs text-gray-400">/ {item.target.toLocaleString()}</span>
                  )}
                </div>
              </div>
              {pct != null && (
                <div className="w-28 flex-shrink-0">
                  <div className="flex justify-between text-xs mb-1">
                    <span style={{ color }} className="font-semibold">{Math.min(100, pct).toFixed(0)}%</span>
                    {pct >= 100 && <span className="text-green-600">✓</span>}
                  </div>
                  <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, backgroundColor: color }} />
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  // ── Box Plot (Box & Whisker) ───────────────────────────────────────────────────
  if (ct === 'box_plot') {
    const cats = boxStats ? labels : rows.map(r => String(r[columns[0]] ?? ''))
    const stats = boxStats ?? rows.map(r => ({
      min: Number(r[columns[1]] ?? 0),
      q1: Number(r[columns[2]] ?? 0),
      median: Number(r[columns[3]] ?? 0),
      q3: Number(r[columns[4]] ?? 0),
      max: Number(r[columns[5]] ?? 0),
    }))
    if (!stats.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    // Build recharts data using stacked invisible+visible bars trick
    const globalMin = Math.min(...stats.map(s => s.min))
    const globalMax = Math.max(...stats.map(s => s.max))
    const boxData = stats.map((s, i) => ({
      name: cats[i] ?? String(i),
      // invisible base offset to min
      _base: s.min - globalMin * 0.95,
      lower: s.q1 - s.min,
      iqr: s.q3 - s.q1,
      upper: s.max - s.q3,
      // raw for tooltip
      _min: s.min, _q1: s.q1, _med: s.median, _q3: s.q3, _max: s.max,
    }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={boxData} margin={{ top: 10, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
          <YAxis domain={[globalMin * 0.95, globalMax * 1.05]} tick={{ fontSize: 11 }} />
          <Tooltip content={({ payload }) => {
            if (!payload?.length) return null
            const d = payload[0]?.payload as typeof boxData[0]
            return (
              <div className="bg-white border rounded-lg px-3 py-2 text-xs shadow space-y-0.5">
                <p className="font-semibold mb-1">{d.name}</p>
                <p>Max: {d._max.toLocaleString()}</p>
                <p>Q3: {d._q3.toLocaleString()}</p>
                <p className="font-bold">Median: {d._med.toLocaleString()}</p>
                <p>Q1: {d._q1.toLocaleString()}</p>
                <p>Min: {d._min.toLocaleString()}</p>
              </div>
            )
          }} />
          <Bar dataKey="_base" stackId="bp" fill="transparent" stroke="none" />
          <Bar dataKey="lower" stackId="bp" fill="transparent"
            stroke={COLORS[0]} strokeWidth={1.5} strokeDasharray="4 2" />
          <Bar dataKey="iqr" stackId="bp" barSize={32} radius={[2, 2, 2, 2]}>
            {boxData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} fillOpacity={0.5} stroke={COLORS[i % COLORS.length]} strokeWidth={1.5} />)}
          </Bar>
          <Bar dataKey="upper" stackId="bp" fill="transparent"
            stroke={COLORS[0]} strokeWidth={1.5} strokeDasharray="4 2" />
        </ComposedChart>
      </ResponsiveContainer>
    )
  }

  // ── Sankey Diagram ────────────────────────────────────────────────────────────
  if (ct === 'sankey') {
    const nodes = sankeyNodes ?? labels
    const links = sankeyLinks ?? []
    if (!nodes.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const W = 600, H = height - 10
    const nodeH = Math.max(20, Math.min(60, (H - 20) / Math.max(1, nodes.length / 2) - 6))
    const sources = Array.from(new Set(links.map(l => l.source)))
    const targets = Array.from(new Set(links.map(l => l.target)))
    const isSource = (i: number) => sources.includes(i)
    const nodeX = (i: number) => isSource(i) ? 20 : W - 100

    // Vertical positioning
    const leftNodes = nodes.filter((_, i) => isSource(i) && !targets.includes(i) || (isSource(i) && !targets.includes(i)))
    const rightNodes = nodes.filter((_, i) => targets.includes(i))
    const allLeft = nodes.filter((_, i) => isSource(i))
    const allRight = nodes.filter((_, i) => targets.includes(i))
    const nodeY = (idx: number): number => {
      if (isSource(idx)) {
        const pos = allLeft.indexOf(nodes[idx])
        return 10 + pos * ((H - 20) / Math.max(1, allLeft.length))
      }
      const pos = allRight.indexOf(nodes[idx])
      return 10 + pos * ((H - 20) / Math.max(1, allRight.length))
    }

    const totalFlow = links.reduce((s, l) => s + l.value, 0) || 1
    return (
      <div style={{ width: '100%', height, overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ fontFamily: 'inherit' }}>
          {links.map((link, i) => {
            const sx = nodeX(link.source) + 80
            const sy = nodeY(link.source) + nodeH / 2
            const tx = nodeX(link.target)
            const ty = nodeY(link.target) + nodeH / 2
            const strokeW = Math.max(2, (link.value / totalFlow) * 40)
            const mx = (sx + tx) / 2
            return (
              <path key={i} d={`M${sx},${sy} C${mx},${sy} ${mx},${ty} ${tx},${ty}`}
                stroke={COLORS[link.source % COLORS.length]} strokeWidth={strokeW}
                fill="none" opacity={0.4}>
                <title>{nodes[link.source]} → {nodes[link.target]}: {link.value}</title>
              </path>
            )
          })}
          {nodes.map((name, i) => {
            const x = nodeX(i), y = nodeY(i)
            const fill = COLORS[i % COLORS.length]
            return (
              <g key={i}>
                <rect x={x} y={y} width={80} height={nodeH} rx={4} fill={fill} opacity={0.85} />
                <text x={x + 40} y={y + nodeH / 2 + 4} textAnchor="middle" fontSize={Math.min(11, nodeH - 4)}
                  fill="white" fontWeight={600}>
                  {name.length > 10 ? name.slice(0, 9) + '…' : name}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    )
  }

  // ── Chord Diagram ─────────────────────────────────────────────────────────────
  if (ct === 'chord') {
    const entities = chordMatrix?.entities ?? labels
    const mat = chordMatrix?.matrix ?? []
    if (!entities.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const n = entities.length
    const cx = 200, cy = 200, outerR = 160, innerR = 140
    const arcGap = 0.03
    const sliceAngle = (2 * Math.PI - n * arcGap) / n
    const rowTotals = mat.map(row => row.reduce((s, v) => s + v, 0))
    const grand = rowTotals.reduce((s, v) => s + v, 0) || 1

    function polarToXY(angle: number, r: number) {
      return { x: cx + r * Math.cos(angle - Math.PI / 2), y: cy + r * Math.sin(angle - Math.PI / 2) }
    }
    function describeArc(start: number, end: number, r: number) {
      const s = polarToXY(start, r), e = polarToXY(end, r)
      const large = end - start > Math.PI ? 1 : 0
      return `M${s.x},${s.y} A${r},${r},0,${large},1,${e.x},${e.y}`
    }

    const startAngles = entities.map((_, i) => i * (sliceAngle + arcGap))
    return (
      <div style={{ width: '100%', height, overflowX: 'auto', display: 'flex', justifyContent: 'center' }}>
        <svg viewBox="0 0 400 400" width={Math.min(400, height)} height={Math.min(400, height)}>
          {/* Chords */}
          {mat.map((row, si) =>
            row.map((val, ti) => {
              if (val === 0 || si === ti) return null
              const sStart = startAngles[si], sEnd = sStart + sliceAngle
              const tStart = startAngles[ti], tEnd = tStart + sliceAngle
              const sMid = (sStart + sEnd) / 2, tMid = (tStart + tEnd) / 2
              const s1 = polarToXY(sMid, innerR), s2 = polarToXY(tMid, innerR)
              return (
                <path key={`${si}-${ti}`}
                  d={`M${s1.x},${s1.y} Q${cx},${cy} ${s2.x},${s2.y}`}
                  stroke={COLORS[si % COLORS.length]} strokeWidth={Math.max(1, (val / grand) * 20)}
                  fill="none" opacity={0.3}>
                  <title>{entities[si]} → {entities[ti]}: {val}</title>
                </path>
              )
            })
          )}
          {/* Arcs */}
          {entities.map((name, i) => {
            const start = startAngles[i], end = start + sliceAngle
            const mid = (start + end) / 2
            const label = polarToXY(mid, outerR + 18)
            return (
              <g key={i}>
                <path d={describeArc(start, end, outerR)}
                  stroke={COLORS[i % COLORS.length]} strokeWidth={18} fill="none" opacity={0.85} strokeLinecap="round" />
                <text x={label.x} y={label.y} textAnchor="middle" dominantBaseline="middle"
                  fontSize={9} fill="#374151" fontWeight={600}>
                  {name.length > 8 ? name.slice(0, 7) + '…' : name}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    )
  }

  // ── Network Graph ─────────────────────────────────────────────────────────────
  if (ct === 'network') {
    const nodeList = netNodes ?? labels
    const edgeList = netEdges ?? []
    if (!nodeList.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const W = 500, H = height - 10
    const n = nodeList.length
    // Place nodes in a circle
    const nodePos = nodeList.map((_, i) => ({
      x: W / 2 + (W / 2 - 50) * Math.cos((2 * Math.PI * i) / n),
      y: H / 2 + (H / 2 - 40) * Math.sin((2 * Math.PI * i) / n),
    }))
    const nodeIndex = Object.fromEntries(nodeList.map((name, i) => [name, i]))
    const maxWeight = Math.max(...edgeList.map(e => e.weight), 1)

    return (
      <div style={{ width: '100%', height, overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
          {edgeList.map((e, i) => {
            const si = nodeIndex[e.source], ti = nodeIndex[e.target]
            if (si == null || ti == null) return null
            const s = nodePos[si], t = nodePos[ti]
            return (
              <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                stroke="#94A3B8" strokeWidth={Math.max(1, (e.weight / maxWeight) * 4)} opacity={0.6}>
                <title>{e.source} → {e.target}: {e.weight}</title>
              </line>
            )
          })}
          {nodeList.map((name, i) => {
            const { x, y } = nodePos[i]
            const degree = edgeList.filter(e => e.source === name || e.target === name).length
            const r = Math.max(10, Math.min(22, 10 + degree * 2))
            return (
              <g key={i}>
                <circle cx={x} cy={y} r={r} fill={COLORS[i % COLORS.length]} opacity={0.85}
                  stroke="white" strokeWidth={2} />
                <text x={x} y={y + r + 12} textAnchor="middle" fontSize={9} fill="#374151">
                  {name.length > 10 ? name.slice(0, 9) + '…' : name}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    )
  }

  // ── Calendar Heatmap ──────────────────────────────────────────────────────────
  if (ct === 'calendar_heatmap') {
    // labels = ISO date strings, values = numeric values
    if (!labels.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const dateMap: Record<string, number> = {}
    labels.forEach((l, i) => { dateMap[l] = Number(values[i] ?? 0) })
    const allVals = Object.values(dateMap)
    const minV = Math.min(...allVals), maxV = Math.max(...allVals)
    const norm = (v: number) => maxV === minV ? 0.5 : (v - minV) / (maxV - minV)

    // Build weeks × days grid
    const dates = labels.map(l => new Date(l)).sort((a, b) => a.getTime() - b.getTime())
    if (!dates.length) return null
    const firstDate = dates[0]
    const lastDate = dates[dates.length - 1]
    const weeks: Array<Array<{ date: Date | null; val: number | null }>> = []
    let cur = new Date(firstDate)
    // Align to Sunday
    cur.setDate(cur.getDate() - cur.getDay())
    while (cur <= lastDate) {
      const week: Array<{ date: Date | null; val: number | null }> = []
      for (let d = 0; d < 7; d++) {
        const key = cur.toISOString().slice(0, 10)
        week.push({ date: new Date(cur), val: dateMap[key] != null ? dateMap[key] : null })
        cur.setDate(cur.getDate() + 1)
      }
      weeks.push(week)
    }

    const cellSz = Math.max(8, Math.min(16, Math.floor((height - 40) / 7)))
    const DAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']
    return (
      <div style={{ width: '100%', height, overflowX: 'auto', overflowY: 'hidden' }}>
        <div style={{ display: 'flex', gap: 2, padding: '20px 4px 4px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginRight: 4 }}>
            {DAYS.map((d, i) => (
              <div key={i} style={{ height: cellSz, fontSize: 8, color: '#9CA3AF', lineHeight: `${cellSz}px`, textAlign: 'right', paddingRight: 2 }}>{d}</div>
            ))}
          </div>
          {weeks.map((week, wi) => (
            <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {week.map((cell, di) => {
                if (!cell.date) return <div key={di} style={{ width: cellSz, height: cellSz }} />
                const t = cell.val != null ? norm(cell.val) : -1
                const bg = t < 0 ? '#F9FAFB' : `rgba(37,99,235,${0.07 + t * 0.85})`
                const monthDay = cell.date.getDate()
                return (
                  <div key={di} title={`${cell.date.toISOString().slice(0, 10)}: ${cell.val ?? 'no data'}`}
                    style={{ width: cellSz, height: cellSz, borderRadius: 2, backgroundColor: bg, border: '1px solid rgba(0,0,0,0.06)', cursor: 'default' }}>
                    {cellSz >= 14 && monthDay === 1 && (
                      <span style={{ fontSize: 7, color: '#6B7280', lineHeight: `${cellSz}px`, display: 'block', textAlign: 'center' }}>
                        {cell.date.toLocaleString('default', { month: 'short' })}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── Gantt Chart ────────────────────────────────────────────────────────────────
  if (ct === 'gantt') {
    const tasks = ganttTasks ?? rows.map(r => ({
      task: String(r[columns[0]] ?? ''),
      start: String(r[columns[1]] ?? ''),
      end: String(r[columns[2]] ?? ''),
      category: String(r[columns[3]] ?? '') || '',
    }))
    if (!tasks.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const dates = tasks.flatMap(t => [new Date(t.start), new Date(t.end)]).filter(d => !isNaN(d.getTime()))
    if (!dates.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">Invalid dates</div>
    const minMs = Math.min(...dates.map(d => d.getTime()))
    const maxMs = Math.max(...dates.map(d => d.getTime()))
    const totalMs = maxMs - minMs || 1
    const toX = (dateStr: string) => ((new Date(dateStr).getTime() - minMs) / totalMs) * 100

    const cats = Array.from(new Set(tasks.map(t => t.category).filter(Boolean)))
    const taskH = Math.max(24, Math.min(40, Math.floor((height - 50) / Math.max(1, tasks.length))))
    const labelW = 120

    return (
      <div style={{ width: '100%', height, overflowY: tasks.length > 10 ? 'auto' : 'hidden' }}>
        <div style={{ paddingTop: 4, paddingLeft: labelW }}>
          <div style={{ position: 'relative', height: 20, fontSize: 9, color: '#6B7280' }}>
            {[0, 25, 50, 75, 100].map(pct => {
              const ms = minMs + (pct / 100) * totalMs
              const d = new Date(ms)
              return (
                <span key={pct} style={{ position: 'absolute', left: `${pct}%`, transform: 'translateX(-50%)' }}>
                  {d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </span>
              )
            })}
          </div>
        </div>
        {tasks.map((task, i) => {
          const catIdx = cats.indexOf(task.category)
          const fill = catIdx >= 0 ? COLORS[catIdx % COLORS.length] : COLORS[i % COLORS.length]
          const startX = toX(task.start), endX = toX(task.end)
          const barW = Math.max(0.5, endX - startX)
          return (
            <div key={i} style={{ display: 'flex', alignItems: 'center', height: taskH, borderBottom: '1px solid #F3F4F6' }}>
              <div style={{ width: labelW, flexShrink: 0, fontSize: 10, color: '#374151', paddingRight: 8, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {task.task}
              </div>
              <div style={{ flex: 1, position: 'relative', height: Math.min(taskH - 6, 22), backgroundColor: '#F9FAFB', borderRadius: 4 }}>
                <div style={{
                  position: 'absolute', left: `${startX}%`, width: `${barW}%`,
                  height: '100%', backgroundColor: fill, borderRadius: 4, opacity: 0.85,
                  display: 'flex', alignItems: 'center', paddingLeft: 4, overflow: 'hidden',
                }}>
                  {barW > 8 && <span style={{ fontSize: 9, color: 'white', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{task.task}</span>}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
  }

  // ── Timeline ──────────────────────────────────────────────────────────────────
  if (ct === 'timeline') {
    if (!labels.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const events = labels.map((l, i) => ({ name: l, date: values[i] != null ? String(values[i]) : l }))
    return (
      <div className="w-full overflow-auto" style={{ height }}>
        <div style={{ minWidth: Math.max(400, events.length * 80), padding: '20px 24px', position: 'relative' }}>
          {/* Spine */}
          <div style={{ position: 'absolute', top: 46, left: 24, right: 24, height: 3, backgroundColor: COLORS[0], borderRadius: 2, opacity: 0.3 }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', position: 'relative' }}>
            {events.map((ev, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', maxWidth: 90, position: 'relative' }}>
                {/* Label above */}
                {i % 2 === 0 && (
                  <div style={{ fontSize: 10, color: '#374151', textAlign: 'center', marginBottom: 4, lineHeight: 1.3, maxWidth: 80, wordBreak: 'break-word', fontWeight: 600 }}>
                    {ev.name}
                  </div>
                )}
                {i % 2 !== 0 && <div style={{ height: 32 }} />}
                {/* Dot */}
                <div style={{ width: 14, height: 14, borderRadius: '50%', backgroundColor: COLORS[i % COLORS.length], border: '2px solid white', boxShadow: '0 0 0 2px ' + COLORS[i % COLORS.length], zIndex: 1, marginBottom: 4 }} />
                {/* Date below dot */}
                <div style={{ fontSize: 9, color: '#6B7280', textAlign: 'center', maxWidth: 80, wordBreak: 'break-word' }}>{ev.date}</div>
                {/* Label below for odd */}
                {i % 2 !== 0 && (
                  <div style={{ fontSize: 10, color: '#374151', textAlign: 'center', marginTop: 4, lineHeight: 1.3, maxWidth: 80, wordBreak: 'break-word', fontWeight: 600 }}>
                    {ev.name}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ── Word Cloud ────────────────────────────────────────────────────────────────
  if (ct === 'word_cloud') {
    if (!labels.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const maxVal = Math.max(...values.filter((v): v is number => typeof v === 'number')) || 1
    const minVal = Math.min(...values.filter((v): v is number => typeof v === 'number')) || 0
    const norm = (v: number) => maxVal === minVal ? 0.5 : (v - minVal) / (maxVal - minVal)

    // Simple spiral layout
    const words = labels.map((l, i) => {
      const t = norm(Number(values[i] ?? 0))
      const size = Math.max(10, Math.round(10 + t * 32))
      return { word: l, size, t, color: COLORS[i % COLORS.length] }
    }).sort((a, b) => b.size - a.size).slice(0, 60)

    const W = 600, H = height - 4
    // Place words in a spiral from center
    const placed: Array<{ x: number; y: number; w: string; sz: number; color: string }> = []
    let angle = 0, radius = 0
    words.forEach(({ word, size, color }) => {
      const estW = word.length * size * 0.6
      let px = W / 2, py = H / 2
      for (let iter = 0; iter < 200; iter++) {
        px = W / 2 + radius * Math.cos(angle)
        py = H / 2 + radius * Math.sin(angle)
        angle += 0.5
        radius += 0.3
        if (px > estW / 2 && px < W - estW / 2 && py > size && py < H - size) break
      }
      placed.push({ x: px, y: py, w: word, sz: size, color })
    })

    return (
      <div style={{ width: '100%', height, overflow: 'hidden' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ fontFamily: 'inherit' }}>
          {placed.map((p, i) => (
            <text key={i} x={p.x} y={p.y} textAnchor="middle" dominantBaseline="middle"
              fontSize={p.sz} fill={p.color} fontWeight={p.sz > 20 ? 700 : 500} opacity={0.85}>
              {p.w}
              <title>{p.w}: {values[labels.indexOf(p.w)]}</title>
            </text>
          ))}
        </svg>
      </div>
    )
  }

  // ── Org Chart (Tree) ──────────────────────────────────────────────────────────
  if (ct === 'org_chart') {
    const nodes = orgNodes ?? rows.map(r => ({
      id: String(r[columns[0]] ?? ''),
      name: String(r[columns[1]] ?? ''),
      parent: String(r[columns[2]] ?? '') || '',
    }))
    if (!nodes.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    // Build tree structure using BFS for layout
    const children: Record<string, string[]> = {}
    const nodeMap: Record<string, typeof nodes[0]> = {}
    nodes.forEach(n => {
      nodeMap[n.id] = n
      if (!children[n.id]) children[n.id] = []
      if (n.parent && n.parent !== n.id) {
        if (!children[n.parent]) children[n.parent] = []
        children[n.parent].push(n.id)
      }
    })
    const roots = nodes.filter(n => !n.parent || n.parent === n.id || !nodeMap[n.parent])
    const rootId = roots[0]?.id ?? nodes[0]?.id

    // BFS to assign levels and positions
    type NodePos = { id: string; level: number; idx: number; total: number }
    const positions: NodePos[] = []
    const queue: Array<{ id: string; level: number }> = [{ id: rootId, level: 0 }]
    const levelCounts: Record<number, number> = {}
    const levelIdx: Record<number, number> = {}
    const seen = new Set<string>()
    while (queue.length) {
      const { id, level } = queue.shift()!
      if (seen.has(id)) continue
      seen.add(id)
      levelCounts[level] = (levelCounts[level] ?? 0) + 1
      positions.push({ id, level, idx: 0, total: 0 })
      ;(children[id] ?? []).forEach(cid => queue.push({ id: cid, level: level + 1 }))
    }
    positions.forEach(p => {
      p.total = levelCounts[p.level] ?? 1
      p.idx = levelIdx[p.level] ?? 0
      levelIdx[p.level] = (levelIdx[p.level] ?? 0) + 1
    })

    const maxLevel = Math.max(...positions.map(p => p.level))
    const W = 600, H = height - 10
    const levelH = H / (maxLevel + 2)
    const nodeW = 90, nodeHPx = 32

    const getXY = (p: NodePos) => ({
      x: (p.idx + 0.5) * (W / p.total),
      y: (p.level + 0.5) * levelH,
    })

    return (
      <div style={{ width: '100%', height, overflowX: 'auto', overflowY: 'auto' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width={Math.max(W, positions.length * 60)} height={H}>
          {/* Edges */}
          {positions.map(p => {
            const parentPos = positions.find(q => q.id === nodes.find(n => n.id === p.id)?.parent)
            if (!parentPos) return null
            const { x: px, y: py } = getXY(parentPos)
            const { x: cx, y: cy } = getXY(p)
            return (
              <line key={`e-${p.id}`} x1={px} y1={py + nodeHPx / 2} x2={cx} y2={cy - nodeHPx / 2}
                stroke="#CBD5E1" strokeWidth={1.5} />
            )
          })}
          {/* Nodes */}
          {positions.map((p, i) => {
            const { x, y } = getXY(p)
            const name = nodeMap[p.id]?.name ?? p.id
            const fill = COLORS[p.level % COLORS.length]
            return (
              <g key={p.id}>
                <rect x={x - nodeW / 2} y={y - nodeHPx / 2} width={nodeW} height={nodeHPx}
                  rx={6} fill={fill} opacity={0.85} />
                <text x={x} y={y + 4} textAnchor="middle" fontSize={10} fill="white" fontWeight={600}>
                  {name.length > 12 ? name.slice(0, 11) + '…' : name}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    )
  }

  // ── Marimekko (Mosaic) ────────────────────────────────────────────────────────
  if (ct === 'marimekko') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const mData = series
      ? labels.map((l, i) => {
          const obj: Record<string, number> = {}
          series.forEach(s => { obj[s.name] = Number(s.values[i] ?? 0) })
          return { category: l, ...obj }
        })
      : rechartData.map(r => {
          const obj: Record<string, number> = {}
          seriesKeys.forEach(k => { obj[k] = Number(r[k] ?? 0) })
          return { category: String(r[xKey] ?? ''), ...obj }
        })

    // Total per category (for column width) and grand total
    const colTotals = mData.map(d => seriesKeys.reduce((s, k) => s + (d[k] ?? 0), 0))
    const grandTotal = colTotals.reduce((s, v) => s + v, 0) || 1

    const W = 600, H = height - 40
    let xOffset = 0
    const rects: Array<{ x: number; y: number; w: number; h: number; cat: string; seg: string; val: number; fill: string }> = []
    mData.forEach((d, ci) => {
      const colTotal = colTotals[ci] || 1
      const colW = (colTotals[ci] / grandTotal) * W
      let yOffset = 0
      seriesKeys.forEach((seg, si) => {
        const val = d[seg] ?? 0
        const segH = (val / colTotal) * H
        rects.push({ x: xOffset, y: yOffset, w: colW, h: segH, cat: d.category, seg, val, fill: COLORS[si % COLORS.length] })
        yOffset += segH
      })
      xOffset += colW
    })

    return (
      <div style={{ width: '100%', height, overflow: 'hidden' }}>
        <svg viewBox={`0 0 ${W} ${H + 40}`} width="100%" height={height}>
          {rects.map((r, i) => (
            <g key={i}>
              <rect x={r.x + 1} y={r.y} width={Math.max(0, r.w - 2)} height={r.h}
                fill={r.fill} opacity={0.85} stroke="white" strokeWidth={1}>
                <title>{r.cat} / {r.seg}: {r.val}</title>
              </rect>
              {r.h > 16 && r.w > 30 && (
                <text x={r.x + r.w / 2} y={r.y + r.h / 2 + 4} textAnchor="middle"
                  fontSize={Math.min(10, r.h / 3)} fill="white" fontWeight={600}>
                  {r.val > 0 ? r.val.toLocaleString() : ''}
                </text>
              )}
            </g>
          ))}
          {/* Category labels at bottom */}
          {mData.map((d, ci) => {
            const colW = (colTotals[ci] / grandTotal) * W
            const xOff = mData.slice(0, ci).reduce((s, _, i2) => s + (colTotals[i2] / grandTotal) * W, 0)
            return (
              <text key={ci} x={xOff + colW / 2} y={H + 20} textAnchor="middle" fontSize={10} fill="#374151" fontWeight={600}>
                {d.category.length > 10 ? d.category.slice(0, 9) + '…' : d.category}
              </text>
            )
          })}
          {/* Legend */}
          {seriesKeys.map((seg, i) => (
            <g key={seg} transform={`translate(${10 + i * 90}, ${H + 32})`}>
              <rect width={10} height={10} fill={COLORS[i % COLORS.length]} opacity={0.85} rx={2} />
              <text x={14} y={9} fontSize={9} fill="#374151">{seg.length > 9 ? seg.slice(0, 8) + '…' : seg}</text>
            </g>
          ))}
        </svg>
      </div>
    )
  }

  // ── Choropleth (Regional Heatmap) ─────────────────────────────────────────────
  if (ct === 'choropleth') {
    // Fallback: render as a sorted horizontal bar chart with color encoding
    // (A true choropleth needs topojson; this gives useful output without new deps)
    if (!labels.length) return <div style={{ height }} className="flex items-center justify-center text-xs text-gray-400">No data</div>

    const sortedData = labels
      .map((l, i) => ({ name: l, value: Number(values[i] ?? 0) }))
      .sort((a, b) => b.value - a.value)
    const maxV = sortedData[0]?.value || 1
    const norm = (v: number) => v / maxV
    const barH = Math.max(height, sortedData.length * 26)

    return (
      <div style={{ width: '100%', height, overflowY: 'auto' }}>
        <div style={{ padding: '8px 4px 8px 0' }}>
          {sortedData.map((d, i) => {
            const t = norm(d.value)
            const bg = `rgba(37,99,235,${0.08 + t * 0.82})`
            const fg = t > 0.6 ? 'white' : '#1F2937'
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <div style={{ width: 110, flexShrink: 0, fontSize: 10, color: '#374151', textAlign: 'right', paddingRight: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {d.name}
                </div>
                <div style={{ flex: 1, height: 20, backgroundColor: '#F3F4F6', borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                  <div style={{ width: `${Math.max(2, t * 100)}%`, height: '100%', backgroundColor: bg, borderRadius: 4, display: 'flex', alignItems: 'center', paddingLeft: 6 }}>
                    <span style={{ fontSize: 9, color: fg, whiteSpace: 'nowrap' }}>
                      {d.value.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                    </span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  // ── Grouped line (two overlaid series — used by comparison mode) ─────────────
  if (ct === 'grouped_line') {
    const seriesKeys = series?.map(s => s.name) ?? columns.slice(1)
    const lineData = series
      ? labels.map((l, i) => {
          const o: Record<string, unknown> = { [xKey]: l }
          series.forEach(s => { o[s.name] = s.values[i] ?? 0 })
          return o
        })
      : rechartData
    return (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={lineData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {seriesKeys.map((key, i) => (
            <Line key={key} type="monotone" dataKey={key}
              stroke={i === 0 ? COLORS[0] : COLORS[4]}
              strokeWidth={i === 0 ? 2.5 : 2}
              strokeDasharray={i > 0 ? '5 3' : undefined}
              dot={{ r: 3 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // ── Bar (vertical, default — also handles bar_vertical) ──────────────────────
  const anomalySet = new Set(anomalyIndices)
  const gradId = `barG-${yKey}`.replace(/[^a-z0-9-]/gi, '_')
  // If many categories and labels are long, auto-switch to horizontal bar
  const shouldAutoHBar = rechartData.length > 15 && rechartData.some(r => String(r[xKey] ?? '').length > 6)
  if (shouldAutoHBar) {
    const longestL = rechartData.reduce((m, r) => Math.max(m, String(r[xKey] ?? '').length), 0)
    const yAxisW = Math.min(200, Math.max(100, longestL * 6.5))
    const barH = Math.min(700, Math.max(height, Math.min(rechartData.length, 50) * 30))
    const capped = rechartData.length > 50 ? rechartData.slice(0, 50) : rechartData
    return (
      <div>
        {rechartData.length > 50 && <p style={{ fontSize: 10, color: '#9CA3AF', marginBottom: 4 }}>Showing top 50 of {rechartData.length}</p>}
        <ResponsiveContainer width="100%" height={barH}>
          <BarChart data={capped} layout="vertical" margin={{ left: 8, right: 56, top: 4, bottom: 4 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor={COLORS[0]} stopOpacity={0.7} />
                <stop offset="100%" stopColor={COLORS[0]} stopOpacity={1} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis dataKey={xKey} type="category" width={yAxisW} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip formatter={(v: number) => v.toLocaleString()} />
            {avgY !== null && (
              <ReferenceLine x={avgY} stroke="#F59E0B" strokeDasharray="4 2"
                label={{ value: fmtAvg, position: 'top', fill: '#F59E0B', fontSize: 9, fontWeight: 600 }} />
            )}
            <Bar dataKey={yKey} name={metricName} fill={legend ? undefined : `url(#${gradId})`} radius={[0, 3, 3, 0]} maxBarSize={22}
              onClick={onDataPointClick ? (data) => onDataPointClick(xKey, data[xKey]) : undefined}>
              {legend
                ? capped.map((_, i) => <Cell key={i} fill={showAnomalies && anomalySet.has(i) ? '#EF4444' : COLORS[i % COLORS.length]} />)
                : (showAnomalies && capped.map((_, i) => anomalySet.has(i) ? <Cell key={i} fill="#EF4444" /> : null))}
              <LabelList dataKey={yKey} position="right" style={{ fontSize: 9, fill: 'var(--dash-text-muted, #6B7280)' }}
                formatter={(v: number) => isNaN(v) ? '' : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v)} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }
  // Categorical bars get one colour per category + a swatch legend in chat mode;
  // time-series stay single-colour with just a metric legend so months don't turn
  // into a rainbow.
  const barCategories = rechartData.map(r => String(r[xKey] ?? ''))
  const barMultiColor = legend && !xIsTime && barCategories.length <= 16
  const barLegH = legend ? 24 : 0
  const barChart = (
    <ResponsiveContainer width="100%" height={legend ? height - barLegH : height}>
      <BarChart data={rechartData} style={onDataPointClick ? { cursor: 'pointer' } : undefined}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={COLORS[0]} stopOpacity={0.95} />
            <stop offset="100%" stopColor={COLORS[0]} stopOpacity={0.55} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey={xKey} tick={{ fontSize: 11 }} interval="preserveStartEnd" />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip />
        {avgY !== null && (
          <ReferenceLine y={avgY} stroke="#F59E0B" strokeDasharray="4 2"
            label={{ value: fmtAvg, position: 'insideTopRight', fill: '#F59E0B', fontSize: 9, fontWeight: 600 }} />
        )}
        <Bar dataKey={yKey} name={metricName} fill={barMultiColor ? undefined : `url(#${gradId})`} radius={[3, 3, 0, 0]}
          onClick={onDataPointClick ? (data) => onDataPointClick(xKey, data[xKey]) : undefined}>
          {barMultiColor
            ? rechartData.map((_, i) => <Cell key={i} fill={showAnomalies && anomalySet.has(i) ? '#EF4444' : COLORS[i % COLORS.length]} />)
            : (showAnomalies && rechartData.map((_, i) => anomalySet.has(i) ? <Cell key={i} fill="#EF4444" /> : null))}
          <LabelList dataKey={yKey} position="top" style={{ fontSize: 9, fill: 'var(--dash-text-muted, #6B7280)' }}
            formatter={(v: number) => isNaN(v) ? '' : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v)} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
  if (!legend) return barChart
  return (
    <div style={{ height }}>
      {barChart}
      <SwatchLegend
        items={barMultiColor ? barCategories : [metricName]}
        colors={barMultiColor ? COLORS : [COLORS[0]]}
        height={barLegH}
      />
    </div>
  )
}
