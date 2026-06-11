'use client'
import { PriorityTag, classifyPriority } from './PriorityTag'

export interface WinLossRow {
  label: string
  value: number
}

interface Props {
  wins: WinLossRow[]
  losses: WinLossRow[]
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(Math.round(n))
}

function HorizontalBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  return (
    <div className="flex items-center gap-2 py-1.5">
      <span className="text-xs text-gray-700 w-36 truncate flex-shrink-0">{label}</span>
      <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: '#F1F5F9' }}>
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs font-semibold w-14 text-right flex-shrink-0" style={{ color }}>{fmt(value)}</span>
    </div>
  )
}

export function NewLostSection({ wins, losses }: Props) {
  if (!wins.length && !losses.length) return null

  const top5Wins = [...wins].sort((a, b) => b.value - a.value).slice(0, 5)
  const top5Losses = [...losses].sort((a, b) => b.value - a.value).slice(0, 5)
  const totalWins = wins.reduce((s, r) => s + r.value, 0)
  const totalLosses = losses.reduce((s, r) => s + r.value, 0)
  const netNew = totalWins - totalLosses
  const maxWin = top5Wins[0]?.value || 1
  const maxLoss = top5Losses[0]?.value || 1

  const lossAvg = losses.length ? totalLosses / losses.length : 0

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">

      {/* Net new KPI */}
      <div
        className="md:col-span-2 rounded-2xl px-5 py-4 flex items-center gap-6"
        style={{
          background: netNew >= 0 ? '#F0FDF4' : '#FEF2F2',
          border: `1px solid ${netNew >= 0 ? '#BBF7D0' : '#FECACA'}`,
        }}
      >
        <div>
          <p className="text-xs font-medium mb-1" style={{ color: netNew >= 0 ? '#15803D' : '#B91C1C' }}>Net New Business</p>
          <p className="text-2xl font-bold" style={{ color: netNew >= 0 ? '#15803D' : '#B91C1C' }}>
            {netNew >= 0 ? '+' : ''}{fmt(netNew)}
          </p>
        </div>
        <div className="flex gap-6">
          <div>
            <p className="text-[10px] text-gray-500 mb-0.5">New Wins</p>
            <p className="text-sm font-semibold text-green-700">+{fmt(totalWins)}</p>
          </div>
          <div>
            <p className="text-[10px] text-gray-500 mb-0.5">Lost</p>
            <p className="text-sm font-semibold text-red-700">−{fmt(totalLosses)}</p>
          </div>
        </div>
      </div>

      {/* Top 5 wins */}
      {top5Wins.length > 0 && (
        <div className="bg-white rounded-2xl overflow-hidden" style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)' }}>
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-500" />
            <h3 className="text-sm font-semibold text-gray-800">Top New Wins</h3>
          </div>
          <div className="px-4 py-3">
            {top5Wins.map(r => (
              <HorizontalBar key={r.label} label={r.label} value={r.value} max={maxWin} color="#16A34A" />
            ))}
          </div>
        </div>
      )}

      {/* Top 5 losses with priority tags */}
      {top5Losses.length > 0 && (
        <div className="bg-white rounded-2xl overflow-hidden" style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)' }}>
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-red-500" />
            <h3 className="text-sm font-semibold text-gray-800">Lost Accounts</h3>
          </div>
          <div className="px-4 py-2">
            {top5Losses.map(r => (
              <div key={r.label} className="flex items-center gap-2 py-1.5">
                <span className="text-xs text-gray-700 w-32 truncate flex-shrink-0">{r.label}</span>
                <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: '#F1F5F9' }}>
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${Math.min((r.value / maxLoss) * 100, 100)}%`, background: '#EF4444' }}
                  />
                </div>
                <span className="text-xs font-semibold text-red-700 w-12 text-right flex-shrink-0">{fmt(r.value)}</span>
                <PriorityTag priority={classifyPriority(r.value, lossAvg)} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
