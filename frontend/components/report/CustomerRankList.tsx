'use client'
import { useState } from 'react'
import { MomentumBadge, computeMomentum } from './MomentumBadge'
import { MiniSparkline } from './MiniSparkline'

export interface CustomerRow {
  label: string
  value: number
  values: number[]
}

interface Props {
  title: string
  rows: CustomerRow[]
  onRowClick?: (label: string) => void
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(Math.round(n))
}

export function CustomerRankList({ title, rows, onRowClick }: Props) {
  const [selected, setSelected] = useState<string | null>(null)

  if (!rows.length) return null

  const sorted = [...rows].sort((a, b) => b.value - a.value).slice(0, 10)
  const max = sorted[0]?.value || 1

  const handleClick = (label: string) => {
    const next = selected === label ? null : label
    setSelected(next)
    onRowClick?.(next ?? '')
  }

  const sel = sorted.find(r => r.label === selected)

  return (
    <div className="bg-white rounded-2xl overflow-hidden" style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)' }}>
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
        <span className="text-xs text-gray-400">{sorted.length} accounts</span>
      </div>

      <div className="divide-y divide-gray-50">
        {sorted.map((row, i) => {
          const barPct = (row.value / max) * 100
          const pct = computeMomentum(row.values)
          const isSelected = selected === row.label

          return (
            <button
              key={row.label}
              onClick={() => handleClick(row.label)}
              className="w-full px-4 py-2.5 text-left transition-colors hover:bg-gray-50 focus:outline-none"
              style={{ background: isSelected ? '#EFF6FF' : undefined }}
            >
              <div className="flex items-center gap-3">
                {/* Rank */}
                <span className="text-xs font-mono text-gray-400 w-4 flex-shrink-0">{i + 1}</span>

                {/* Name + progress bar */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-gray-800 truncate">{row.label}</span>
                    {row.values.length > 1 && <MomentumBadge pct={pct} />}
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden" style={{ background: '#F1F5F9' }}>
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${barPct}%`, background: isSelected ? '#2563EB' : '#94A3B8' }}
                    />
                  </div>
                </div>

                {/* Value + sparkline */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  {row.values.length > 1 && (
                    <MiniSparkline values={row.values} width={40} height={14} color={isSelected ? '#2563EB' : '#94A3B8'} />
                  )}
                  <span className="text-xs font-semibold text-gray-700 w-14 text-right">{fmt(row.value)}</span>
                </div>
              </div>
            </button>
          )
        })}
      </div>

      {/* Selected customer drill-down chart */}
      {sel && sel.values.length > 1 && (
        <div className="px-4 py-3 border-t border-blue-100" style={{ background: '#F8FAFF' }}>
          <p className="text-xs font-semibold text-blue-700 mb-2">{sel.label} — monthly trend</p>
          <MiniSparkline values={sel.values} width={300} height={48} color="#2563EB" filled />
        </div>
      )}
    </div>
  )
}
