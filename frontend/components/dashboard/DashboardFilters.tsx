'use client'
import React, { useState, useEffect } from 'react'
import { Search, Bookmark, Trash2, Plus, Check } from 'lucide-react'
import { useDashboardFilterStore, type FilterValue } from '@/stores/dashboardFilterStore'

export interface FilterConfig {
  id: string
  column: string
  display_name: string
  filter_type: 'multi_select' | 'single_select' | 'date_range'
  available_values?: string[]
  table?: string
}

interface FilterPreset {
  id: string
  name: string
  filters: Record<string, FilterValue>
}

interface Props {
  dashboardId: string
  filters: FilterConfig[]
  onApply: () => void
}

function loadPresets(dashboardId: string): FilterPreset[] {
  try {
    return JSON.parse(localStorage.getItem(`visually_presets_${dashboardId}`) ?? '[]')
  } catch { return [] }
}
function savePresets(dashboardId: string, presets: FilterPreset[]) {
  localStorage.setItem(`visually_presets_${dashboardId}`, JSON.stringify(presets))
}

export function DashboardFilters({ dashboardId, filters, onApply }: Props) {
  const { getFilters, setFilter, clearFilter, clearAll, hasActiveFilters } =
    useDashboardFilterStore()
  const activeFilters = getFilters(dashboardId)

  const [dateState,   setDateState]   = useState<Record<string, { start: string; end: string }>>({})
  const [searchState, setSearchState] = useState<Record<string, string>>({})
  const [presets,     setPresets]     = useState<FilterPreset[]>([])
  const [savingName,  setSavingName]  = useState('')
  const [showSave,    setShowSave]    = useState(false)

  useEffect(() => { setPresets(loadPresets(dashboardId)) }, [dashboardId])

  if (!filters || filters.length === 0) return null

  const labelStyle = { color: 'var(--dash-text, #111827)' }
  const mutedStyle = { color: 'var(--dash-text-muted, #6b7280)' }
  const inputStyle: React.CSSProperties = {
    background: 'var(--dash-card-bg, #fff)',
    color:       'var(--dash-text, #111827)',
    borderColor: 'var(--dash-card-border, #d1d5db)',
  }

  function toggleValue(column: string, value: string) {
    const current = (activeFilters[column] as string[] | undefined) || []
    const next = current.includes(value)
      ? current.filter(v => v !== value)
      : [...current, value]
    if (next.length === 0) clearFilter(dashboardId, column)
    else setFilter(dashboardId, column, next)
  }

  function setSingleValue(column: string, value: string) {
    if (!value) clearFilter(dashboardId, column)
    else setFilter(dashboardId, column, [value])
  }

  function applyDateRange(column: string) {
    const d = dateState[column]
    if (d?.start && d?.end) setFilter(dashboardId, column, { start: d.start, end: d.end })
  }

  function handleSavePreset() {
    const name = savingName.trim()
    if (!name) return
    const newPreset: FilterPreset = {
      id: Date.now().toString(),
      name,
      filters: { ...activeFilters },
    }
    const updated = [...presets, newPreset]
    setPresets(updated)
    savePresets(dashboardId, updated)
    setSavingName('')
    setShowSave(false)
  }

  function handleLoadPreset(preset: FilterPreset) {
    clearAll(dashboardId)
    for (const [col, val] of Object.entries(preset.filters)) setFilter(dashboardId, col, val)
    onApply()
  }

  function handleDeletePreset(id: string) {
    const updated = presets.filter(p => p.id !== id)
    setPresets(updated)
    savePresets(dashboardId, updated)
  }

  return (
    <div className="flex flex-col gap-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide" style={mutedStyle}>Filters</span>
        {hasActiveFilters(dashboardId) && (
          <button onClick={() => { clearAll(dashboardId); onApply() }} className="text-xs text-blue-500 hover:underline">
            Clear all
          </button>
        )}
      </div>

      {/* Filter controls */}
      {filters.map(f => {
        const activeVal   = activeFilters[f.column]
        const searchQuery = searchState[f.column] ?? ''
        const allValues   = f.available_values ?? []
        const showSearch  = allValues.length > 10
        const visibleVals = showSearch
          ? allValues.filter(v => v.toLowerCase().includes(searchQuery.toLowerCase()))
          : allValues

        if (f.filter_type === 'date_range') {
          const d = dateState[f.column] || { start: '', end: '' }
          return (
            <div key={f.id} className="flex flex-col gap-1">
              <span className="text-xs font-medium" style={labelStyle}>{f.display_name}</span>
              <input type="date" value={d.start}
                onChange={e => setDateState(s => ({ ...s, [f.column]: { ...d, start: e.target.value } }))}
                className="text-xs border rounded px-2 py-1" style={inputStyle}
              />
              <input type="date" value={d.end}
                onChange={e => setDateState(s => ({ ...s, [f.column]: { ...d, end: e.target.value } }))}
                className="text-xs border rounded px-2 py-1" style={inputStyle}
              />
              <button onClick={() => { applyDateRange(f.column); onApply() }}
                className="text-xs bg-blue-600 text-white rounded px-2 py-1 hover:bg-blue-700">
                Apply
              </button>
              {activeVal && (
                <button onClick={() => { clearFilter(dashboardId, f.column); onApply() }}
                  className="text-xs hover:underline" style={mutedStyle}>Clear</button>
              )}
            </div>
          )
        }

        if (f.filter_type === 'single_select') {
          const current = Array.isArray(activeVal) ? activeVal[0] : undefined
          return (
            <div key={f.id} className="flex flex-col gap-1">
              <span className="text-xs font-medium" style={labelStyle}>{f.display_name}</span>
              <select value={current || ''}
                onChange={e => { setSingleValue(f.column, e.target.value); onApply() }}
                className="text-xs border rounded px-2 py-1" style={inputStyle}>
                <option value="">All</option>
                {allValues.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
          )
        }

        // multi_select with optional search
        const selected = (activeVal as string[] | undefined) || []
        return (
          <div key={f.id} className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium" style={labelStyle}>{f.display_name}</span>
              {selected.length > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 font-semibold">{selected.length}</span>
              )}
            </div>

            {/* Search box — only for long lists */}
            {showSearch && (
              <div className="relative">
                <Search size={10} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search…"
                  value={searchQuery}
                  onChange={e => setSearchState(s => ({ ...s, [f.column]: e.target.value }))}
                  className="w-full text-xs border rounded pl-6 pr-2 py-1"
                  style={inputStyle}
                />
              </div>
            )}

            <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto">
              {visibleVals.length === 0 ? (
                <span className="text-[10px] italic" style={mutedStyle}>No matches</span>
              ) : visibleVals.map(v => (
                <label key={v} className="flex items-center gap-1.5 cursor-pointer py-0.5">
                  <input type="checkbox" checked={selected.includes(v)}
                    onChange={() => { toggleValue(f.column, v); onApply() }}
                    className="accent-blue-600" />
                  <span className="text-xs truncate" style={labelStyle}>{v}</span>
                </label>
              ))}
            </div>
          </div>
        )
      })}

      {/* ── Saved presets ── */}
      <div className="border-t pt-3" style={{ borderColor: 'var(--dash-card-border, #e5e7eb)' }}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold uppercase tracking-wide" style={mutedStyle}>Saved Views</span>
          {hasActiveFilters(dashboardId) && (
            <button
              onClick={() => setShowSave(v => !v)}
              className="flex items-center gap-1 text-xs text-blue-500 hover:underline"
            >
              <Plus size={11} /> Save
            </button>
          )}
        </div>

        {/* Save form */}
        {showSave && (
          <div className="flex items-center gap-1 mb-2">
            <input
              autoFocus
              type="text"
              placeholder="View name…"
              value={savingName}
              onChange={e => setSavingName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSavePreset(); if (e.key === 'Escape') setShowSave(false) }}
              className="flex-1 text-xs border rounded px-2 py-1"
              style={inputStyle}
            />
            <button onClick={handleSavePreset} disabled={!savingName.trim()}
              className="p-1 text-blue-600 hover:bg-blue-50 rounded disabled:opacity-40">
              <Check size={13} />
            </button>
          </div>
        )}

        {/* Preset list */}
        {presets.length === 0 ? (
          <p className="text-[10px] italic" style={mutedStyle}>No saved views yet</p>
        ) : (
          <div className="flex flex-col gap-0.5">
            {presets.map(p => (
              <div key={p.id} className="flex items-center gap-1 group">
                <button
                  onClick={() => handleLoadPreset(p)}
                  className="flex-1 text-left text-xs px-2 py-1 rounded hover:bg-blue-50 hover:text-blue-700 flex items-center gap-1.5"
                  style={labelStyle}
                >
                  <Bookmark size={11} className="opacity-50 flex-shrink-0" />
                  <span className="truncate">{p.name}</span>
                </button>
                <button
                  onClick={() => handleDeletePreset(p.id)}
                  className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 rounded transition-opacity"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
