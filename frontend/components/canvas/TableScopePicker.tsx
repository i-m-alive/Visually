'use client'
/**
 * TableScopePicker — shared "Selected tables / Full DB" control for a canvas.
 *
 * Reads/writes the per-canvas scope from tableScopeStore, so the SAME selection is
 * reflected wherever this is rendered — the canvas toolbar AND the Canvas Assistant
 * chat. The table list is loaded once (on canvas mount) into the store; this
 * component never fetches.
 */
import React, { useState } from 'react'
import { Search, Database, FileText, Check, ChevronDown, Table2, AlertCircle, Loader2 } from 'lucide-react'
import { useTableScopeStore, DEFAULT_SCOPE } from '@/stores/tableScopeStore'

export function TableScopePicker({ canvasId }: { canvasId: string }) {
  const cs = useTableScopeStore((s) => s.byCanvas[canvasId]) ?? DEFAULT_SCOPE
  const setScope         = useTableScopeStore((s) => s.setScope)
  const toggleTable      = useTableScopeStore((s) => s.toggleTable)
  const clearTables      = useTableScopeStore((s) => s.clearTables)
  const setSelectedHops  = useTableScopeStore((s) => s.setSelectedHops)

  const { scope, selectedTables, selectedHops, tables, loading, error } = cs
  const [pickerOpen, setPickerOpen] = useState(false)
  const [tableSearch, setTableSearch] = useState('')

  return (
    <div className="px-3 py-2.5" style={{ background: 'linear-gradient(180deg,#f8fafc,#f1f5f9)' }}>
      {/* Mode segmented control */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-0.5 p-0.5 rounded-lg flex-shrink-0" style={{ background: '#e6ecf3', border: '1px solid #dbe3ec' }}>
          <button
            onClick={() => setScope(canvasId, 'selected')}
            title="Limit the assistant to tables you pick (plus their related tables). Faster, more focused, cheaper."
            className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[11px] font-semibold transition-all"
            style={scope === 'selected'
              ? { background: '#fff', color: '#0d3060', boxShadow: '0 1px 4px rgba(10,33,58,0.14)' }
              : { background: 'transparent', color: '#64748b' }}
          >
            <FileText size={11} /> Selected tables
          </button>
          <button
            onClick={() => { setScope(canvasId, 'database'); setPickerOpen(false) }}
            title="Give the assistant the full database schema — it can query any table or view."
            className="flex items-center gap-1.5 px-3 py-1 rounded-md text-[11px] font-semibold transition-all"
            style={scope === 'database'
              ? { background: '#fff', color: '#0d3060', boxShadow: '0 1px 4px rgba(10,33,58,0.14)' }
              : { background: 'transparent', color: '#64748b' }}
          >
            <Database size={11} /> Full DB
          </button>
        </div>
        {scope === 'database' && <span className="text-[10px] text-gray-400 truncate">Whole database in context</span>}
      </div>

      {scope === 'selected' && (
        <div className="mt-2 flex flex-col gap-2">
          {/* Table selector + overlay dropdown */}
          <div className="relative">
            <button
              onClick={() => setPickerOpen((o) => !o)}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white transition-colors"
              style={{ border: `1px solid ${pickerOpen ? '#93c5fd' : '#dbe3ec'}` }}
            >
              <Table2 size={13} className="flex-shrink-0" style={{ color: '#2563EB' }} />
              <span className="flex-1 text-left text-[11px] font-semibold truncate" style={{ color: selectedTables.length ? '#1e293b' : '#94a3b8' }}>
                {selectedTables.length
                  ? `${selectedTables.length} table${selectedTables.length !== 1 ? 's' : ''} selected`
                  : 'Choose tables to focus on…'}
              </span>
              <ChevronDown size={13} className="text-gray-400 flex-shrink-0" style={{ transform: pickerOpen ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
            </button>

            {pickerOpen && (
              <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-xl bg-white overflow-hidden" style={{ border: '1px solid #e2e8f0', boxShadow: '0 12px 32px rgba(10,33,58,0.16)' }}>
                <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-gray-100">
                  <Search size={12} className="text-gray-400 flex-shrink-0" />
                  <input
                    autoFocus
                    value={tableSearch}
                    onChange={(e) => setTableSearch(e.target.value)}
                    placeholder="Search tables…"
                    className="flex-1 bg-transparent text-[11px] outline-none text-gray-700 placeholder-gray-400"
                  />
                  {selectedTables.length > 0 && (
                    <button onClick={() => clearTables(canvasId)} className="text-[10px] font-semibold text-gray-400 hover:text-red-500 flex-shrink-0 transition-colors">Clear all</button>
                  )}
                </div>
                <div className="max-h-56 overflow-y-auto py-1">
                  {loading ? (
                    <div className="flex items-center gap-2 px-2.5 py-4 text-[11px] text-gray-400">
                      <Loader2 size={12} className="animate-spin" /> Loading tables…
                    </div>
                  ) : error ? (
                    <div className="flex items-center gap-2 px-2.5 py-4 text-[11px] text-red-400">
                      <AlertCircle size={12} /> Couldn’t load tables. Crawl the schema first.
                    </div>
                  ) : (() => {
                    const q = tableSearch.trim().toLowerCase()
                    const list = q ? tables.filter((t) => t.name.toLowerCase().includes(q)) : tables
                    if (!list.length) {
                      return <div className="px-2.5 py-4 text-[11px] text-gray-400">{tables.length ? 'No matching tables' : 'No tables found'}</div>
                    }
                    return list.slice(0, 300).map((t) => {
                      const checked = selectedTables.includes(t.name)
                      return (
                        <button
                          key={t.name}
                          onClick={() => toggleTable(canvasId, t.name)}
                          className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-blue-50"
                        >
                          <span className="w-3.5 h-3.5 rounded flex items-center justify-center flex-shrink-0 transition-colors" style={checked ? { background: '#2563EB' } : { border: '1.5px solid #cbd5e1' }}>
                            {checked && <Check size={9} className="text-white" />}
                          </span>
                          <span className="flex-1 text-[11px] font-mono truncate" style={{ color: checked ? '#1e293b' : '#475569' }}>{t.name}</span>
                          <span className="text-[9px] text-gray-400 flex-shrink-0">{t.columns} cols</span>
                        </button>
                      )
                    })
                  })()}
                </div>
                <div className="flex items-center justify-between px-2.5 py-1.5 border-t border-gray-100" style={{ background: '#f8fafc' }}>
                  <span className="text-[10px] text-gray-400">{loading ? '…' : `${tables.length} tables · ${selectedTables.length} selected`}</span>
                  <button onClick={() => setPickerOpen(false)} className="text-[10px] font-semibold text-blue-600 hover:text-blue-800">Done</button>
                </div>
              </div>
            )}
          </div>

          {/* Selected chips */}
          {selectedTables.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {selectedTables.map((t) => (
                <span key={t} className="inline-flex items-center gap-1 text-[10px] pl-2 pr-1 py-0.5 rounded-full font-mono" style={{ background: '#eef2ff', color: '#3730a3', border: '1px solid #c7d2fe' }}>
                  {t.split('.').pop()}
                  <button onClick={() => toggleTable(canvasId, t)} className="rounded-full p-0.5 hover:bg-indigo-200/60 transition-colors" title="Remove">
                    <svg width={9} height={9} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3}><path d="M18 6 6 18M6 6l12 12" /></svg>
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Hop selector */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-gray-400 mr-0.5">Related:</span>
            {([[0, 'None'], [1, '+1 hop'], [2, '+2 hops']] as const).map(([h, label]) => (
              <button
                key={h}
                onClick={() => setSelectedHops(canvasId, h)}
                title={h === 0 ? 'Only the tables you picked' : `Also include tables within ${h} join-hop(s)`}
                className="text-[10px] font-semibold px-2 py-0.5 rounded-full transition-colors"
                style={selectedHops === h
                  ? { background: '#0d3060', color: '#fff' }
                  : { background: '#fff', color: '#64748b', border: '1px solid #dbe3ec' }}
              >
                {label}
              </button>
            ))}
          </div>

          {selectedTables.length === 0 && !pickerOpen && (
            <span className="text-[10px] leading-tight" style={{ color: '#94a3b8' }}>
              Pick tables to focus the assistant — otherwise it falls back to the full database.
            </span>
          )}
        </div>
      )}
    </div>
  )
}
