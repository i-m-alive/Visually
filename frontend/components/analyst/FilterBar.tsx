'use client'
import { useState } from 'react'
import { X, Plus, Filter, SlidersHorizontal } from 'lucide-react'
import type { FilterItem } from '@/lib/api'

interface FilterBarProps {
  filters: FilterItem[]
  onFiltersChange: (filters: FilterItem[]) => void
  columns: string[]
}

export function FilterBar({ filters, onFiltersChange, columns }: FilterBarProps) {
  const [adding, setAdding] = useState(false)
  const [newFilter, setNewFilter] = useState<Partial<FilterItem>>({ operator: '=' })

  const OPERATORS = [
    { value: '=', label: '=' },
    { value: '!=', label: '≠' },
    { value: '>', label: '>' },
    { value: '<', label: '<' },
    { value: '>=', label: '≥' },
    { value: '<=', label: '≤' },
    { value: 'like', label: 'contains' },
  ]

  const handleAdd = () => {
    if (!newFilter.column || newFilter.value === undefined || newFilter.value === '') return
    onFiltersChange([...filters, newFilter as FilterItem])
    setNewFilter({ operator: '=' })
    setAdding(false)
  }

  const handleRemove = (idx: number) => {
    onFiltersChange(filters.filter((_, i) => i !== idx))
  }

  const COLORS = ['#3B82F6', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#06B6D4']

  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-100 flex-wrap min-h-[44px]">
      <div className="flex items-center gap-1.5 text-xs text-gray-400 flex-shrink-0">
        <SlidersHorizontal size={12} />
        <span className="font-medium">Filters</span>
      </div>

      {filters.map((f, i) => (
        <div
          key={i}
          className="flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium border text-white flex-shrink-0"
          style={{ background: COLORS[i % COLORS.length], borderColor: 'transparent' }}
        >
          <Filter size={10} />
          <span>{f.column} {f.operator} {String(f.value)}</span>
          <button onClick={() => handleRemove(i)} className="ml-0.5 opacity-70 hover:opacity-100">
            <X size={10} />
          </button>
        </div>
      ))}

      {adding ? (
        <div className="flex items-center gap-1.5 bg-gray-50 border border-gray-200 rounded-lg px-2 py-1">
          <select
            value={newFilter.column || ''}
            onChange={e => setNewFilter(f => ({ ...f, column: e.target.value }))}
            className="text-xs border-0 bg-transparent outline-none min-w-[100px] text-gray-700"
          >
            <option value="">Column...</option>
            {columns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <select
            value={newFilter.operator || '='}
            onChange={e => setNewFilter(f => ({ ...f, operator: e.target.value as FilterItem['operator'] }))}
            className="text-xs border-0 bg-transparent outline-none text-gray-700"
          >
            {OPERATORS.map(op => <option key={op.value} value={op.value}>{op.label}</option>)}
          </select>
          <input
            type="text"
            value={String(newFilter.value ?? '')}
            onChange={e => setNewFilter(f => ({ ...f, value: e.target.value }))}
            placeholder="value"
            className="text-xs border-0 bg-transparent outline-none w-20 text-gray-700"
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
          />
          <button onClick={handleAdd} className="text-blue-600 hover:text-blue-700 text-xs font-semibold">Apply</button>
          <button onClick={() => { setAdding(false); setNewFilter({ operator: '=' }) }} className="text-gray-400 hover:text-gray-600">
            <X size={12} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 px-2 py-1 rounded-full text-xs text-gray-500 border border-dashed border-gray-300 hover:border-blue-400 hover:text-blue-600 transition-colors flex-shrink-0"
        >
          <Plus size={10} />
          Add filter
        </button>
      )}

      {filters.length > 0 && (
        <button
          onClick={() => onFiltersChange([])}
          className="text-xs text-gray-400 hover:text-red-500 transition-colors flex-shrink-0 ml-auto"
        >
          Clear all
        </button>
      )}
    </div>
  )
}
