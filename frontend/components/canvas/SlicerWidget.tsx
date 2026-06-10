'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { analystApi, type FilterItem } from '@/lib/api'
import { ChevronDown, X, Search } from 'lucide-react'

// ─── Types ───────────────────────────────────────────────────────────────────

interface SlicerWidgetProps {
  token: string
  widgetId: string
  title: string
  slicerColumn: string
  slicerType: 'dropdown' | 'checkbox' | 'date_range'
  filterValue: FilterItem | null
  onFilterChange: (filter: FilterItem | null) => void
  isEditMode?: boolean
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function useSlicerValues(token: string, widgetId: string) {
  const [values, setValues] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    analystApi.getSlicerValues(token, widgetId)
      .then(r => {
        if (!cancelled) setValues((r as any).values ?? [])
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [token, widgetId])

  return { values, loading }
}

// ─── Dropdown slicer ─────────────────────────────────────────────────────────

function DropdownSlicer({
  token, widgetId, title, slicerColumn, filterValue, onFilterChange, isEditMode,
}: SlicerWidgetProps) {
  const { values, loading } = useSlicerValues(token, widgetId)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  const selected = filterValue?.value as string | undefined

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const select = useCallback((v: string) => {
    onFilterChange({ column: slicerColumn, operator: '=', value: v })
    setOpen(false)
    setSearch('')
  }, [slicerColumn, onFilterChange])

  const clear = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onFilterChange(null)
  }, [onFilterChange])

  const filtered = values.filter(v => v.toLowerCase().includes(search.toLowerCase()))

  return (
    <div ref={ref} className="relative w-full h-full flex flex-col px-3 py-2 gap-1.5">
      <span className="text-[11px] font-bold text-gray-500 uppercase tracking-wider truncate">{title || slicerColumn}</span>
      <button
        disabled={isEditMode}
        onClick={() => !isEditMode && setOpen(o => !o)}
        className="flex items-center justify-between gap-2 border border-gray-200 rounded-lg px-3 py-1.5 text-sm bg-white hover:border-indigo-400 transition-colors min-w-0"
        style={{ cursor: isEditMode ? 'default' : 'pointer' }}
      >
        <span className="truncate text-gray-700 flex-1 text-left">
          {loading ? 'Loading…' : selected ?? 'Select value…'}
        </span>
        <div className="flex items-center gap-1 shrink-0">
          {selected && !isEditMode && (
            <span onClick={clear} className="text-gray-400 hover:text-red-500 transition-colors">
              <X size={13} />
            </span>
          )}
          <ChevronDown size={14} className={`text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {open && (
        <div className="absolute left-3 right-3 top-full mt-1 z-50 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
          {values.length > 8 && (
            <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
              <Search size={13} className="text-gray-400 shrink-0" />
              <input
                autoFocus
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search…"
                className="flex-1 text-sm outline-none placeholder-gray-400"
              />
            </div>
          )}
          <div className="max-h-56 overflow-y-auto">
            {filtered.length === 0 && (
              <div className="px-3 py-2 text-sm text-gray-400">No results</div>
            )}
            {filtered.map(v => (
              <button
                key={v}
                onClick={() => select(v)}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 transition-colors ${selected === v ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-700'}`}
              >
                {v}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Checkbox slicer ─────────────────────────────────────────────────────────

function CheckboxSlicer({
  token, widgetId, title, slicerColumn, filterValue, onFilterChange, isEditMode,
}: SlicerWidgetProps) {
  const { values, loading } = useSlicerValues(token, widgetId)
  const [search, setSearch] = useState('')

  const selected: string[] = Array.isArray(filterValue?.value)
    ? (filterValue!.value as string[])
    : filterValue?.value ? [filterValue.value as string] : []

  const toggle = useCallback((v: string) => {
    if (isEditMode) return
    const next = selected.includes(v) ? selected.filter(s => s !== v) : [...selected, v]
    if (next.length === 0) {
      onFilterChange(null)
    } else {
      onFilterChange({ column: slicerColumn, operator: 'in', value: next })
    }
  }, [selected, slicerColumn, onFilterChange, isEditMode])

  const clearAll = () => { if (!isEditMode) onFilterChange(null) }

  const filtered = values.filter(v => v.toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="w-full h-full flex flex-col overflow-hidden bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-3 pt-2 pb-1 flex-shrink-0">
        <span className="text-[11px] font-bold text-gray-500 uppercase tracking-wider truncate">{title || slicerColumn}</span>
        {selected.length > 0 && !isEditMode && (
          <button onClick={clearAll} className="text-[10px] text-indigo-500 hover:text-indigo-700 shrink-0 ml-2">Clear</button>
        )}
      </div>

      {/* Search — always visible for checkbox slicer (Power BI style) */}
      <div className="mx-2 mb-1 flex items-center gap-1.5 border border-gray-300 rounded px-2 py-1 bg-white flex-shrink-0">
        <Search size={11} className="text-gray-400 shrink-0" />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search"
          className="flex-1 text-xs outline-none placeholder-gray-400 bg-transparent"
        />
        {search && (
          <button onClick={() => setSearch('')} className="text-gray-300 hover:text-gray-500 text-[10px] leading-none">✕</button>
        )}
      </div>

      {/* Checkbox list */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading && <div className="text-xs text-gray-400 px-3 py-2">Loading…</div>}
        {!loading && filtered.length === 0 && <div className="text-xs text-gray-400 px-3 py-2">No results</div>}
        {filtered.map(v => {
          const checked = selected.includes(v)
          return (
            <label
              key={v}
              className={`flex items-center gap-2 px-3 py-1 border-b border-gray-50 last:border-0 ${isEditMode ? 'cursor-default' : 'cursor-pointer hover:bg-indigo-50'} transition-colors`}
            >
              {/* Custom checkbox matching Power BI style */}
              <span className={`w-3.5 h-3.5 shrink-0 border rounded-sm flex items-center justify-center transition-colors ${checked ? 'bg-indigo-600 border-indigo-600' : 'bg-white border-gray-400'}`}>
                {checked && (
                  <svg viewBox="0 0 10 10" className="w-2.5 h-2.5 fill-white">
                    <path d="M1.5 5l2.5 2.5 4.5-4.5" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(v)}
                disabled={isEditMode}
                className="sr-only"
              />
              <span className={`text-xs truncate ${checked ? 'text-indigo-700 font-medium' : 'text-gray-700'}`}>{v}</span>
            </label>
          )
        })}
      </div>
    </div>
  )
}

// ─── Date range slicer ───────────────────────────────────────────────────────

function DateRangeSlicerWidget({
  slicerColumn, filterValue, onFilterChange, title, isEditMode,
}: SlicerWidgetProps) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  // Sync from props
  useEffect(() => {
    if (filterValue?.operator === '>=' && typeof filterValue.value === 'string') setFrom(filterValue.value)
    else if (!filterValue) setFrom('')
  }, [filterValue])

  const apply = (newFrom: string, newTo: string) => {
    if (!newFrom && !newTo) { onFilterChange(null); return }
    if (newFrom && newTo) {
      onFilterChange({ column: slicerColumn, operator: 'between', value: [newFrom, newTo] })
    } else if (newFrom) {
      onFilterChange({ column: slicerColumn, operator: '>=', value: newFrom })
    } else {
      onFilterChange({ column: slicerColumn, operator: '<=', value: newTo })
    }
  }

  return (
    <div className="w-full h-full flex flex-col px-3 py-2 gap-1">
      <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide truncate">{title}</span>
      <div className="flex gap-2 items-center">
        <div className="flex-1">
          <div className="text-[10px] text-gray-400 mb-0.5">From</div>
          <input
            type="date"
            disabled={isEditMode}
            value={from}
            onChange={e => { setFrom(e.target.value); apply(e.target.value, to) }}
            className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs bg-white focus:outline-none focus:border-indigo-400"
          />
        </div>
        <div className="flex-1">
          <div className="text-[10px] text-gray-400 mb-0.5">To</div>
          <input
            type="date"
            disabled={isEditMode}
            value={to}
            onChange={e => { setTo(e.target.value); apply(from, e.target.value) }}
            className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs bg-white focus:outline-none focus:border-indigo-400"
          />
        </div>
      </div>
      {(from || to) && !isEditMode && (
        <button
          onClick={() => { setFrom(''); setTo(''); onFilterChange(null) }}
          className="self-end text-[11px] text-indigo-500 hover:text-indigo-700"
        >
          Clear
        </button>
      )}
    </div>
  )
}

// ─── Main export ─────────────────────────────────────────────────────────────

export default function SlicerWidget(props: SlicerWidgetProps) {
  const { slicerType } = props

  if (slicerType === 'checkbox') return <CheckboxSlicer {...props} />
  if (slicerType === 'date_range') return <DateRangeSlicerWidget {...props} />
  return <DropdownSlicer {...props} />
}
