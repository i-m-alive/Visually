'use client'
import { MiniSparkline } from './MiniSparkline'
import { MomentumBadge } from './MomentumBadge'

export interface KpiData {
  title: string
  value: number
  values: number[]
  changePct: number
  isLead?: boolean
  prefix?: string
}

function fmt(n: number, prefix = ''): string {
  if (Math.abs(n) >= 1_000_000) return `${prefix}${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${prefix}${(n / 1_000).toFixed(1)}K`
  return `${prefix}${Number.isInteger(n) ? n : n.toFixed(1)}`
}

function LeadCard({ kpi }: { kpi: KpiData }) {
  return (
    <div
      className="relative rounded-2xl p-4 flex flex-col justify-between overflow-hidden"
      style={{
        background: 'linear-gradient(135deg, #0F172A 0%, #1E293B 100%)',
        minWidth: 180,
        flex: '1.4 1 0',
        boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
      }}
    >
      {/* Subtle radial glow */}
      <div
        className="absolute inset-0 opacity-20 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse at top right, #0D9488, transparent 70%)' }}
      />
      <div className="relative z-10">
        <p className="text-xs font-medium mb-2 truncate" style={{ color: '#94A3B8' }}>{kpi.title}</p>
        <p className="text-2xl font-bold tracking-tight" style={{ color: '#F1F5F9' }}>
          {fmt(kpi.value, kpi.prefix)}
        </p>
        <div className="mt-2">
          <MomentumBadge pct={kpi.changePct} size="md" />
        </div>
      </div>
      <div className="relative z-10 mt-3">
        <MiniSparkline values={kpi.values} width={120} height={28} color="#0D9488" filled />
      </div>
    </div>
  )
}

function MetricCard({ kpi }: { kpi: KpiData }) {
  return (
    <div
      className="rounded-2xl p-4 flex flex-col justify-between bg-white"
      style={{
        flex: '1 1 0',
        minWidth: 130,
        boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)',
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-gray-500 truncate leading-tight">{kpi.title}</p>
        <MomentumBadge pct={kpi.changePct} />
      </div>
      <p className="text-xl font-bold text-gray-900 mt-1">{fmt(kpi.value, kpi.prefix)}</p>
      <div className="mt-2">
        <MiniSparkline values={kpi.values} width={80} height={20} color="#2563EB" filled />
      </div>
    </div>
  )
}

export function KpiStrip({ kpis }: { kpis: KpiData[] }) {
  if (!kpis.length) return null
  const [lead, ...rest] = kpis

  return (
    <div className="flex gap-3 px-4 py-3 flex-shrink-0" style={{ background: '#F8FAFC' }}>
      <LeadCard kpi={{ ...lead, isLead: true }} />
      {rest.map((k, i) => <MetricCard key={i} kpi={k} />)}
    </div>
  )
}
