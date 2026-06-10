'use client'
import React, { useState, useEffect } from 'react'
import { Calendar, X } from 'lucide-react'

export interface DateRange {
  start: string  // ISO date string YYYY-MM-DD
  end: string
}

interface Props {
  title: string
  columnName: string
  value: DateRange | null
  onChange: (column: string, range: DateRange | null) => void
  theme?: {
    surface?: string
    border?: string
    text?: string
    muted?: string
    accent?: string
    accentBg?: string
    bg?: string
    cardRadius?: number
  }
}

export function DateRangeSlicer({ title, columnName, value, onChange, theme }: Props) {
  const [start, setStart] = useState(value?.start ?? '')
  const [end, setEnd] = useState(value?.end ?? '')

  useEffect(() => {
    setStart(value?.start ?? '')
    setEnd(value?.end ?? '')
  }, [value])

  const apply = () => {
    if (start && end) {
      onChange(columnName, { start, end })
    }
  }

  const clear = () => {
    setStart('')
    setEnd('')
    onChange(columnName, null)
  }

  const isActive = !!(value?.start && value?.end)
  const bg      = theme?.surface ?? '#FFFFFF'
  const border  = theme?.border ?? 'rgba(0,0,0,0.08)'
  const text    = theme?.text ?? '#111827'
  const muted   = theme?.muted ?? '#9CA3AF'
  const accent  = theme?.accent ?? '#2563EB'
  const radius  = theme?.cardRadius ?? 14

  return (
    <div
      style={{
        background: bg,
        border: `1px solid ${isActive ? accent : border}`,
        borderRadius: radius,
        padding: '12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        boxShadow: isActive ? `0 0 0 2px ${accent}22` : undefined,
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Calendar size={13} color={isActive ? accent : muted} />
          <span style={{ fontSize: 12, fontWeight: 600, color: text }}>{title}</span>
          {isActive && (
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '1px 6px',
              background: `${accent}18`, color: accent, borderRadius: 4,
            }}>
              Active
            </span>
          )}
        </div>
        {isActive && (
          <button
            onClick={clear}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: muted, padding: 2, display: 'flex', alignItems: 'center' }}
            title="Clear filter"
          >
            <X size={12} />
          </button>
        )}
      </div>

      {/* Date inputs */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label style={{ fontSize: 10, color: muted, display: 'block', marginBottom: 3 }}>From</label>
          <input
            type="date"
            value={start}
            onChange={e => setStart(e.target.value)}
            style={{
              width: '100%', padding: '5px 8px', fontSize: 12,
              border: `1px solid ${border}`, borderRadius: 8,
              background: theme?.bg ?? '#F9FAFB', color: text, outline: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label style={{ fontSize: 10, color: muted, display: 'block', marginBottom: 3 }}>To</label>
          <input
            type="date"
            value={end}
            min={start || undefined}
            onChange={e => setEnd(e.target.value)}
            style={{
              width: '100%', padding: '5px 8px', fontSize: 12,
              border: `1px solid ${border}`, borderRadius: 8,
              background: theme?.bg ?? '#F9FAFB', color: text, outline: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <button
          onClick={apply}
          disabled={!start || !end}
          style={{
            marginTop: 16, padding: '6px 14px', fontSize: 12, fontWeight: 600,
            background: start && end ? accent : border,
            color: start && end ? 'white' : muted,
            border: 'none', borderRadius: 8, cursor: start && end ? 'pointer' : 'not-allowed',
            transition: 'background 0.15s',
            flexShrink: 0,
          }}
        >
          Apply
        </button>
      </div>

      {/* Active range display */}
      {isActive && (
        <p style={{ fontSize: 11, color: accent, margin: 0 }}>
          {value!.start} → {value!.end}
        </p>
      )}
    </div>
  )
}
