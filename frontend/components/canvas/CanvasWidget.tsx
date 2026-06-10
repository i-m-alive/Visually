'use client'
import React, { useState, useRef, useEffect, useCallback } from 'react'
import {
  GripVertical, Pencil, Check, X, ChevronDown,
  Maximize2, Download, Trash2, Palette, Image as ImageIcon,
  RefreshCw, StickyNote, Lock, Unlock, Copy, RotateCcw
} from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import SlicerWidget from '@/components/canvas/SlicerWidget'
import type { ChartResult } from '@/stores/pipelineStore'
import type { FilterItem } from '@/lib/api'

const CHART_TYPES = [
  { key: 'bar_vertical',           label: 'Bar' },
  { key: 'bar_horizontal',         label: 'Bar H' },
  { key: 'line',                   label: 'Line' },
  { key: 'area',                   label: 'Area' },
  { key: 'pie',                    label: 'Pie' },
  { key: 'donut',                  label: 'Donut' },
  { key: 'scatter',                label: 'Scatter' },
  { key: 'stacked_bar',            label: 'Stacked Bar' },
  { key: 'stacked_bar_100',        label: 'Stacked 100%' },
  { key: 'stacked_bar_horizontal', label: 'Stacked H' },
  { key: 'grouped_bar',            label: 'Grouped Bar' },
  { key: 'stacked_area',           label: 'Stacked Area' },
  { key: 'combo',                  label: 'Combo' },
  { key: 'bubble',                 label: 'Bubble' },
  { key: 'histogram',              label: 'Histogram' },
  { key: 'waterfall',              label: 'Waterfall' },
  { key: 'funnel',                 label: 'Funnel' },
  { key: 'treemap',                label: 'Treemap' },
  { key: 'heatmap',                label: 'Heatmap' },
  { key: 'sunburst',               label: 'Sunburst' },
  { key: 'kpi',                    label: 'KPI' },
  { key: 'gauge',                  label: 'Gauge' },
  { key: 'multi_row_card',         label: 'Multi KPI' },
  { key: 'table',                  label: 'Table' },
  { key: 'data_table',             label: 'Data Table' },
  { key: 'pivot_table',            label: 'Pivot' },
  { key: 'radar',                  label: 'Radar' },
  { key: 'dot_plot',               label: 'Dot Plot' },
  { key: 'bullet',                 label: 'Bullet' },
  { key: 'scorecard',              label: 'Scorecard' },
  { key: 'ribbon',                 label: 'Ribbon' },
  { key: 'box_plot',               label: 'Box Plot' },
  { key: 'sankey',                 label: 'Sankey' },
  { key: 'chord',                  label: 'Chord' },
  { key: 'network',                label: 'Network' },
  { key: 'gantt',                  label: 'Gantt' },
  { key: 'timeline',               label: 'Timeline' },
  { key: 'calendar_heatmap',       label: 'Calendar' },
  { key: 'word_cloud',             label: 'Word Cloud' },
  { key: 'org_chart',              label: 'Org Chart' },
  { key: 'marimekko',              label: 'Marimekko' },
  { key: 'choropleth',             label: 'Choropleth' },
  { key: 'slicer',                 label: 'Slicer' },
]

export const COLOR_PALETTES: Record<string, string[]> = {
  brand:   ['#2563EB', '#0EA5E9', '#16A34A', '#D97706', '#DC2626', '#7C3AED', '#0D9488', '#E8520A'],
  ocean:   ['#0D9488', '#0EA5E9', '#2563EB', '#7C3AED', '#EC4899', '#F59E0B', '#10B981', '#6366F1'],
  forest:  ['#15803D', '#16A34A', '#22C55E', '#4ADE80', '#A3E635', '#65A30D', '#84CC16', '#BEF264'],
  sunset:  ['#DC2626', '#EA580C', '#D97706', '#CA8A04', '#65A30D', '#0891B2', '#7C3AED', '#DB2777'],
  mono:    ['#111827', '#374151', '#6B7280', '#9CA3AF', '#D1D5DB', '#E5E7EB', '#F3F4F6', '#F9FAFB'],
}

// Colored left-border accent by chart type family
const TYPE_BORDER: Record<string, string> = {
  kpi: '#D97706', kpi_card: '#D97706', gauge: '#D97706', multi_row_card: '#D97706',
  bullet: '#D97706', scorecard: '#D97706',
  pie: '#7C3AED', donut: '#7C3AED', sunburst: '#7C3AED',
  treemap: '#0D9488', heatmap: '#0D9488', calendar_heatmap: '#0D9488',
  table: '#6B7280', data_table: '#6B7280', pivot_table: '#6B7280',
  line: '#2563EB', area: '#2563EB', stacked_area: '#2563EB',
  scatter: '#DC2626', bubble: '#DC2626',
  sankey: '#16A34A', chord: '#16A34A', network: '#16A34A',
}
function getBorderColor(chartType: string) {
  return TYPE_BORDER[chartType] ?? '#2563EB'
}

export interface CanvasWidgetData {
  id: string
  title: string
  chart_type: string
  chart_data: Record<string, unknown> | null
  config?: Record<string, unknown>
  connection_id?: string
  validation_score?: number
  sql_query?: string
}

interface Props {
  widget: CanvasWidgetData
  onDelete: (id: string) => void
  onUpdate: (id: string, data: { title?: string; chart_type?: string; config?: Record<string, unknown> }) => void
  onZoom: (widget: CanvasWidgetData, colors: string[]) => void
  onDuplicate?: (widget: CanvasWidgetData) => void
  onRefresh?: (widgetId: string) => void
  onToggleLock?: (widgetId: string) => void
  isLocked?: boolean
  isRefreshing?: boolean
  // Slicer support (optional; only provided in view/share mode)
  token?: string
  filterValue?: FilterItem | null
  onFilterChange?: (filter: FilterItem | null) => void
}

export function CanvasWidget({
  widget, onDelete, onUpdate, onZoom,
  onDuplicate, onRefresh, onToggleLock,
  isLocked = false, isRefreshing = false,
  token, filterValue = null, onFilterChange,
}: Props) {
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState(widget.title)
  const [showTypeMenu, setShowTypeMenu] = useState(false)
  const [showColorMenu, setShowColorMenu] = useState(false)
  const [showOriginal, setShowOriginal] = useState(false)
  const [paletteName, setPaletteName] = useState(
    (widget.config?.color_palette as string) || 'brand'
  )
  const [chartHeight, setChartHeight] = useState(200)
  const [containerWidth, setContainerWidth] = useState(400)
  const [showNote, setShowNote] = useState(false)
  const [noteDraft, setNoteDraft] = useState((widget.config?.note as string) || '')
  const [pendingDelete, setPendingDelete] = useState(false)
  const [mounted, setMounted] = useState(false)

  const titleInputRef = useRef<HTMLInputElement>(null)
  const typeMenuRef   = useRef<HTMLDivElement>(null)
  const colorMenuRef  = useRef<HTMLDivElement>(null)
  const chartAreaRef  = useRef<HTMLDivElement>(null)
  const containerRef  = useRef<HTMLDivElement>(null)
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const colors = COLOR_PALETTES[paletteName] || COLOR_PALETTES.brand
  const isCompact = containerWidth < 260
  const rowCount = (widget.chart_data?.rows as unknown[])?.length ?? 0
  const updatedAt = widget.config?.updated_at as number | undefined
  const isStale = updatedAt ? (Date.now() - updatedAt) > 3_600_000 : false

  // Entrance animation
  useEffect(() => { const t = setTimeout(() => setMounted(true), 30); return () => clearTimeout(t) }, [])

  // ResizeObserver for chart height
  useEffect(() => {
    const el = chartAreaRef.current
    if (!el) return
    setChartHeight(Math.max(60, el.getBoundingClientRect().height))
    const obs = new ResizeObserver(entries => {
      for (const entry of entries) {
        const h = entry.contentRect.height
        if (h > 0) setChartHeight(Math.max(60, h))
      }
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // ResizeObserver for container width (compact mode)
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    setContainerWidth(el.getBoundingClientRect().width)
    const obs = new ResizeObserver(entries => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width)
      }
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  useEffect(() => {
    if (isEditingTitle) titleInputRef.current?.focus()
  }, [isEditingTitle])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (typeMenuRef.current && !typeMenuRef.current.contains(e.target as Node)) setShowTypeMenu(false)
      if (colorMenuRef.current && !colorMenuRef.current.contains(e.target as Node)) setShowColorMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Clean up delete timer on unmount
  useEffect(() => () => { if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current) }, [])

  const commitTitle = useCallback(() => {
    const trimmed = titleDraft.trim()
    if (trimmed && trimmed !== widget.title) onUpdate(widget.id, { title: trimmed })
    else setTitleDraft(widget.title)
    setIsEditingTitle(false)
  }, [titleDraft, widget.id, widget.title, onUpdate])

  const handleTypeChange = (type: string) => {
    setShowTypeMenu(false)
    onUpdate(widget.id, { chart_type: type })
  }

  const handlePaletteChange = (name: string) => {
    setPaletteName(name)
    setShowColorMenu(false)
    onUpdate(widget.id, { config: { color_palette: name } })
  }

  const handleDownload = async () => {
    const el = document.getElementById(`widget-chart-${widget.id}`)
    if (!el) return
    try {
      const html2canvas = (await import('html2canvas')).default
      const canvas = await html2canvas(el, { backgroundColor: '#ffffff', scale: 2 })
      const link = document.createElement('a')
      link.download = `${widget.title.replace(/\s+/g, '_')}.png`
      link.href = canvas.toDataURL('image/png')
      link.click()
    } catch { /* skip */ }
  }

  const handleDeleteClick = () => {
    if (isLocked) return
    setPendingDelete(true)
    deleteTimerRef.current = setTimeout(() => {
      onDelete(widget.id)
    }, 4000)
  }

  const cancelDelete = () => {
    setPendingDelete(false)
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current)
  }

  const saveNote = () => {
    onUpdate(widget.id, { config: { note: noteDraft } })
    setShowNote(false)
  }

  const result: ChartResult = {
    chart_type: widget.chart_type,
    title: widget.title,
    chart_data: {
      rows:    (widget.chart_data?.rows    as Record<string, unknown>[]) || [],
      columns: (widget.chart_data?.columns as string[]) || [],
      labels:  (widget.chart_data?.labels  as string[]) || [],
      values:  (widget.chart_data?.values  as number[]) || [],
    },
    x_axis_label: (widget.config?.x_axis_label as string) || '',
    y_axis_label: (widget.config?.y_axis_label as string) || '',
    sql:   widget.sql_query || '',
    score: widget.validation_score || 0,
    low_confidence: false,
    table_used: '',
  }

  const HANDLE_H = 34
  const borderColor = getBorderColor(widget.chart_type)

  // Extracted to typed vars to satisfy TypeScript 5.9 + @types/react 18.3 JSX inference
  const typeSwitcherNode: React.ReactNode = !isCompact ? (
    <div ref={typeMenuRef} className="relative flex-shrink-0">
      <button
        disabled={isLocked}
        onClick={() => { setShowTypeMenu(!showTypeMenu); setShowColorMenu(false) }}
        className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs text-gray-500 bg-white border border-gray-200 rounded hover:border-blue-400/40 hover:text-blue-500 transition-colors disabled:opacity-40"
      >
        {CHART_TYPES.find(t => t.key === widget.chart_type)?.label || widget.chart_type}
        <ChevronDown size={10} />
      </button>
      {showTypeMenu ? (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1 min-w-max max-h-60 overflow-y-auto">
          {CHART_TYPES.map(t => (
            <button
              key={t.key}
              onClick={() => handleTypeChange(t.key)}
              className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 transition-colors ${
                widget.chart_type === t.key ? 'text-blue-600 font-medium' : 'text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  ) : null

  const colorPaletteNode: React.ReactNode = !isCompact ? (
    <div ref={colorMenuRef} className="relative flex-shrink-0">
      <button
        disabled={isLocked}
        onClick={() => { setShowColorMenu(!showColorMenu); setShowTypeMenu(false) }}
        className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors disabled:opacity-40"
        title="Color theme"
      >
        <Palette size={13} />
      </button>
      {showColorMenu ? (
        <div className="absolute top-full right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-2 px-3">
          <p className="text-xs text-gray-500 mb-2 font-medium">Palette</p>
          <div className="flex flex-col gap-1.5">
            {(Object.entries(COLOR_PALETTES) as Array<[string, string[]]>).map(([name, cols]) => (
              <button
                key={name}
                onClick={() => handlePaletteChange(name)}
                className={`flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors ${
                  paletteName === name ? 'bg-blue-50' : ''
                }`}
              >
                <div className="flex gap-0.5">
                  {cols.slice(0, 5).map((c: string, i: number) => (
                    <div key={i} className="w-3 h-3 rounded-sm" style={{ backgroundColor: c }} />
                  ))}
                </div>
                <span className="text-xs text-gray-700 capitalize">{name}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  ) : null

  const notePanelNode: React.ReactNode = showNote ? (
    <div className="flex-shrink-0 border-b border-amber-100 bg-amber-50 px-3 py-2">
      <textarea
        value={noteDraft}
        onChange={e => setNoteDraft(e.target.value)}
        placeholder="Add a note about this chart"
        rows={2}
        className="w-full text-xs text-gray-700 bg-white border border-amber-200 rounded px-2 py-1 outline-none resize-none focus:ring-1 focus:ring-amber-300"
      />
      <div className="flex gap-2 mt-1.5">
        <button
          onClick={saveNote}
          className="px-2 py-0.5 text-xs font-medium text-white bg-amber-500 rounded hover:bg-amber-600 transition-colors"
        >
          Save
        </button>
        <button
          onClick={() => setShowNote(false)}
          className="px-2 py-0.5 text-xs text-gray-500 hover:text-gray-700 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  ) : null

  return (
    <div
      ref={containerRef}
      className="flex flex-col h-full bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden select-none"
      style={{
        borderLeft: `3px solid ${borderColor}`,
        opacity: mounted ? 1 : 0,
        transform: mounted ? 'translateY(0)' : 'translateY(8px)',
        transition: 'opacity 0.22s ease, transform 0.22s ease',
        outline: isLocked ? '2px solid #F59E0B' : undefined,
      }}
    >
      {/* Pending-delete banner */}
      {pendingDelete && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-red-50 border-b border-red-100 flex-shrink-0">
          <span className="text-xs text-red-600 font-medium">Deleting in 4s…</span>
          <button
            onClick={cancelDelete}
            className="flex items-center gap-1 text-xs text-red-700 font-semibold hover:underline"
          >
            <RotateCcw size={11} /> Undo
          </button>
        </div>
      )}

      {/* Drag handle + controls */}
      <div
        className="drag-handle flex items-center gap-1 px-2 py-1.5 bg-gray-50 border-b border-gray-100 cursor-grab active:cursor-grabbing flex-shrink-0"
        style={{ height: HANDLE_H }}
      >
        {isLocked ? (
          <Lock size={12} className="text-amber-400 flex-shrink-0" />
        ) : (
          <GripVertical size={14} className="text-gray-300 flex-shrink-0" />
        )}

        {/* Staleness dot */}
        {isStale && (
          <span
            className="w-2 h-2 rounded-full bg-amber-400 flex-shrink-0"
            title="Data may be stale (>1 hour old)"
          />
        )}

        {/* Title */}
        <div className="flex-1 min-w-0">
          {isEditingTitle ? (
            <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
              <input
                ref={titleInputRef}
                value={titleDraft}
                onChange={e => setTitleDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') commitTitle()
                  if (e.key === 'Escape') { setTitleDraft(widget.title); setIsEditingTitle(false) }
                }}
                onBlur={commitTitle}
                className="flex-1 text-xs font-medium text-gray-800 bg-white border border-blue-400/50 rounded px-1.5 py-0.5 outline-none min-w-0"
              />
              <button onClick={commitTitle} className="text-green-600 hover:text-green-700 flex-shrink-0">
                <Check size={13} />
              </button>
              <button
                onClick={() => { setTitleDraft(widget.title); setIsEditingTitle(false) }}
                className="text-gray-400 hover:text-gray-600 flex-shrink-0"
              >
                <X size={13} />
              </button>
            </div>
          ) : (
            <button
              onClick={() => !isLocked && setIsEditingTitle(true)}
              className="flex items-center gap-1 group w-full text-left"
              title={isLocked ? 'Unlock to edit' : 'Click to edit title'}
            >
              <span className="text-xs font-medium text-gray-700 truncate">{widget.title}</span>
              {!isLocked && (
                <Pencil size={10} className="text-gray-300 group-hover:text-blue-500 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              )}
            </button>
          )}
        </div>

        {/* Chart type switcher — hidden in compact mode */}
        {typeSwitcherNode}

        {/* Color palette */}
        {colorPaletteNode as any}

        {/* Note button */}
        <button
          onClick={() => setShowNote(v => !v)}
          className={`p-1 rounded transition-colors flex-shrink-0 ${
            (widget.config?.note as string) ? 'text-amber-500 hover:text-amber-600' : 'text-gray-400 hover:text-blue-500'
          }`}
          title="Widget note"
        >
          <StickyNote size={13} />
        </button>

        {/* Refresh */}
        {onRefresh && (
          <button
            onClick={() => onRefresh(widget.id)}
            className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors flex-shrink-0"
            title="Refresh data"
          >
            <RefreshCw size={13} className={isRefreshing ? 'animate-spin' : ''} />
          </button>
        )}

        {/* Lock / Unlock */}
        {onToggleLock && (
          <button
            onClick={() => onToggleLock(widget.id)}
            className={`p-1 rounded transition-colors flex-shrink-0 ${
              isLocked ? 'text-amber-500 hover:text-amber-600' : 'text-gray-400 hover:text-blue-500'
            }`}
            title={isLocked ? 'Unlock widget' : 'Lock widget'}
          >
            {isLocked ? <Lock size={13} /> : <Unlock size={13} />}
          </button>
        )}

        {/* View Original */}
        {widget.chart_data && (widget.chart_data as Record<string, unknown>).image_data && (
          <button
            onClick={() => setShowOriginal(true)}
            className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors flex-shrink-0"
            title="View original chart image"
          >
            <ImageIcon size={13} />
          </button>
        )}

        {/* Zoom */}
        <button
          onClick={() => onZoom(widget, colors)}
          className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors flex-shrink-0"
          title="Fullscreen"
        >
          <Maximize2 size={13} />
        </button>

        {/* Duplicate */}
        {onDuplicate && !isCompact && (
          <button
            onClick={() => onDuplicate(widget)}
            className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors flex-shrink-0"
            title="Duplicate widget"
          >
            <Copy size={13} />
          </button>
        )}

        {/* Download */}
        <button
          onClick={handleDownload}
          className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors flex-shrink-0"
          title="Download PNG"
        >
          <Download size={13} />
        </button>

        {/* Delete */}
        <button
          onClick={handleDeleteClick}
          disabled={isLocked}
          className="p-1 text-gray-400 hover:text-red-500 rounded transition-colors flex-shrink-0 disabled:opacity-30"
          title={isLocked ? 'Unlock to delete' : 'Remove'}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {notePanelNode as any}

      {/* Chart area */}
      <div
        id={`widget-chart-${widget.id}`}
        ref={chartAreaRef}
        className="flex-1 overflow-hidden min-h-0 p-2 relative"
      >
        {widget.chart_type === 'slicer' ? (
          token ? (
            <SlicerWidget
              token={token}
              widgetId={widget.id}
              title=""
              slicerColumn={(widget.config?.slicer_column as string) || ''}
              slicerType={(widget.config?.slicer_type as 'dropdown' | 'checkbox' | 'date_range') || 'dropdown'}
              filterValue={filterValue}
              onFilterChange={onFilterChange ?? (() => {})}
              isEditMode={isLocked}
            />
          ) : (
            <div className="h-full flex flex-col items-center justify-center gap-1 text-gray-400">
              <span className="text-[11px] font-medium">{(widget.config?.slicer_type as string) || 'dropdown'} slicer</span>
              <span className="text-[10px]">{(widget.config?.slicer_column as string) || 'column'}</span>
              <span className="text-[10px] text-gray-300">Active in live view</span>
            </div>
          )
        ) : widget.chart_data ? (
          <ChartRenderer
            result={result}
            colors={colors}
            height={Math.max(60, chartHeight)}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-gray-300 text-xs">
            No data
          </div>
        )}

        {/* Row count badge */}
        {rowCount > 0 && (
          <span
            className="absolute bottom-2 right-2 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 bg-white border border-gray-200 rounded-full pointer-events-none select-none shadow-sm"
            title={`${rowCount} rows`}
          >
            {rowCount > 9999 ? `${Math.round(rowCount / 1000)}k` : rowCount} rows
          </span>
        )}
      </div>

      {/* Original image modal */}
      {showOriginal && widget.chart_data && (widget.chart_data as Record<string, unknown>).image_data && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setShowOriginal(false)}
        >
          <div
            className="relative bg-white rounded-2xl shadow-2xl max-w-4xl max-h-[90vh] overflow-auto p-4"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold text-gray-700">Original Chart — {widget.title}</span>
              <button
                onClick={() => setShowOriginal(false)}
                className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <X size={16} />
              </button>
            </div>
            <img
              src={`data:image/png;base64,${(widget.chart_data as Record<string, unknown>).image_data as string}`}
              alt="Original chart"
              className="max-w-full rounded-lg border border-gray-100"
            />
          </div>
        </div>
      )}
    </div>
  )
}
