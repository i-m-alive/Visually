'use client'
import { MiniSparkline } from './MiniSparkline'
import { MomentumBadge, computeMomentum } from './MomentumBadge'

export interface YoYRow {
  label: string
  yearValues: { year: string; value: number }[]
}

interface Props {
  title: string
  rows: YoYRow[]
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(Math.round(n))
}

function yoyPct(curr: number, prev: number): number {
  return prev !== 0 ? ((curr - prev) / Math.abs(prev)) * 100 : 0
}

export function YoYTable({ title, rows }: Props) {
  if (!rows.length) return null

  const years = rows[0]?.yearValues.map(y => y.year) ?? []
  if (years.length < 2) return null

  const sorted = [...rows]
    .sort((a, b) => {
      const aLast = a.yearValues.at(-1)?.value ?? 0
      const bLast = b.yearValues.at(-1)?.value ?? 0
      return bLast - aLast
    })
    .slice(0, 20)

  return (
    <div className="bg-white rounded-2xl overflow-hidden" style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)' }}>
      <div className="px-4 py-3 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ background: '#F8FAFC' }}>
              <th className="text-left px-4 py-2 font-medium text-gray-500 w-40">Account</th>
              {years.map(y => (
                <th key={y} className="text-right px-3 py-2 font-medium text-gray-500">{y}</th>
              ))}
              <th className="text-right px-3 py-2 font-medium text-gray-500">YoY</th>
              <th className="text-right px-4 py-2 font-medium text-gray-500 w-20">Trend</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {sorted.map(row => {
              const vals = row.yearValues.map(y => y.value)
              const momentum = computeMomentum(vals)
              const last = vals.at(-1) ?? 0
              const prev = vals.at(-2) ?? 0
              const pct = yoyPct(last, prev)

              return (
                <tr key={row.label} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-2.5 font-medium text-gray-800 truncate max-w-[160px]">{row.label}</td>
                  {row.yearValues.map((yv, i) => {
                    const prevVal = row.yearValues[i - 1]?.value
                    const change = prevVal != null ? yoyPct(yv.value, prevVal) : null
                    return (
                      <td key={yv.year} className="text-right px-3 py-2.5">
                        <span className="text-gray-700">{fmt(yv.value)}</span>
                        {change != null && (
                          <span
                            className="block text-[9px] mt-0.5"
                            style={{ color: change >= 0 ? '#15803D' : '#B91C1C' }}
                          >
                            {change >= 0 ? '+' : ''}{change.toFixed(0)}%
                          </span>
                        )}
                      </td>
                    )
                  })}
                  <td className="text-right px-3 py-2.5">
                    <MomentumBadge pct={pct} />
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="flex justify-end">
                      <MiniSparkline values={vals} width={52} height={16} color={momentum >= 0 ? '#16A34A' : '#DC2626'} />
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
