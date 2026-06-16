'use client'
import {
  LayoutDashboard, DollarSign, Users, Building2,
  UserCheck, ArrowLeftRight, BarChart2, TrendingUp,
  PieChart, FileText,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface Page {
  id: string
  name: string
  order: number
}

interface Props {
  pages: Page[]
  activePageId: string
  onPageChange: (id: string) => void
  reportName: string
}

const ICON_MAP: { key: string; Icon: LucideIcon }[] = [
  { key: 'overview',   Icon: LayoutDashboard },
  { key: 'revenue',    Icon: DollarSign },
  { key: 'customer',   Icon: Users },
  { key: 'firm',       Icon: Building2 },
  { key: 'workforce',  Icon: UserCheck },
  { key: 'new',        Icon: ArrowLeftRight },
  { key: 'lost',       Icon: ArrowLeftRight },
  { key: 'trend',      Icon: TrendingUp },
  { key: 'summary',    Icon: PieChart },
  { key: 'report',     Icon: FileText },
]

function pageIcon(name: string): LucideIcon {
  const lower = name.toLowerCase()
  return ICON_MAP.find(e => lower.includes(e.key))?.Icon ?? BarChart2
}

export function LeftRail({ pages, activePageId, onPageChange, reportName }: Props) {
  const sorted = [...pages].sort((a, b) => a.order - b.order)

  return (
    <nav
      className="flex flex-col items-center py-3 gap-1 flex-shrink-0 select-none z-20"
      style={{ width: 74, background: '#0F172A', minHeight: '100vh' }}
    >
      {/* Brand orb */}
      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center mb-3 flex-shrink-0"
        style={{ background: 'conic-gradient(from 180deg, #0D9488, #2563EB, #7C3AED, #0D9488)' }}
        title={reportName}
      >
        <BarChart2 size={16} className="text-white" />
      </div>

      <div className="w-10 h-px mb-1" style={{ background: '#1E293B' }} />

      {sorted.map(page => {
        const Icon = pageIcon(page.name)
        const active = page.id === activePageId
        return (
          <button
            key={page.id}
            onClick={() => onPageChange(page.id)}
            title={page.name}
            className="relative w-full flex flex-col items-center gap-1 py-2 transition-all"
            style={{ borderLeft: active ? '3px solid #0D9488' : '3px solid transparent' }}
          >
            <Icon
              size={17}
              style={{ color: active ? '#0D9488' : '#64748B' }}
            />
            <span
              className="text-center leading-tight px-1"
              style={{
                fontSize: 9,
                color: active ? '#94A3B8' : '#475569',
                maxWidth: 62,
                wordBreak: 'break-word',
              }}
            >
              {page.name}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
