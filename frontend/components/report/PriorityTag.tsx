export type Priority = 'High' | 'Medium' | 'Low'

const CFG: Record<Priority, { bg: string; text: string }> = {
  High:   { bg: '#FEE2E2', text: '#991B1B' },
  Medium: { bg: '#FEF3C7', text: '#92400E' },
  Low:    { bg: '#DCFCE7', text: '#166534' },
}

export function classifyPriority(value: number, avg: number): Priority {
  if (value >= avg) return 'High'
  if (value >= avg * 0.5) return 'Medium'
  return 'Low'
}

export function PriorityTag({ priority }: { priority: Priority }) {
  const cfg = CFG[priority]
  return (
    <span
      className="inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
      style={{ background: cfg.bg, color: cfg.text }}
    >
      {priority}
    </span>
  )
}
