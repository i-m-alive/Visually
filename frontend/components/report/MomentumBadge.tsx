interface Props {
  pct: number
  size?: 'sm' | 'md'
}

export function MomentumBadge({ pct, size = 'sm' }: Props) {
  const up = pct >= 0
  const cls = size === 'sm' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2 py-1'
  return (
    <span
      className={`inline-flex items-center gap-0.5 font-semibold rounded-full ${cls}`}
      style={{ background: up ? '#F0FDF4' : '#FEF2F2', color: up ? '#15803D' : '#B91C1C' }}
    >
      {up ? '▲' : '▼'} {Math.abs(pct).toFixed(1)}%
    </span>
  )
}

export function computeMomentum(values: number[]): number {
  if (values.length < 2) return 0
  const first = values[0]
  const last = values[values.length - 1]
  return first !== 0 ? ((last - first) / Math.abs(first)) * 100 : 0
}
