'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import {
  GripVertical, Pencil, Check, X, ChevronDown,
  Maximize2, Download, Trash2, Palette, Image as ImageIcon
} from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ChartResult } from '@/stores/pipelineStore'

const CHART_TYPES = [
  // Basic
  { key: 'bar_vertical',           label: 'Bar' },
  { key: 'bar_horizontal',         label: 'Bar H' },
  { key: 'line',                   label: 'Line' },
  { key: 'area',                   label: 'Area' },
  { key: 'pie',                    label: 'Pie' },
  { key: 'donut',                  label: 'Donut' },
  { key: 'scatter',                label: 'Scatter' },
  // Multi-series bar
  { key: 'stacked_bar',            label: 'Stacked Bar' },
  { key: 'stacked_bar_100',        label: 'Stacked 100%' },
  { key: 'stacked_bar_horizontal', label: 'Stacked H' },
  { key: 'grouped_bar',            label: 'Grouped Bar' },
  // Multi-series area
  { key: 'stacked_area',           label: 'Stacked Area' },
  // Combo
  { key: 'combo',                  label: 'Combo' },
  // Extended scatter
  { key: 'bubble',                 label: 'Bubble' },
  // Distribution / flow
  { key: 'histogram',              label: 'Histogram' },
  { key: 'waterfall',              label: 'Waterfall' },
  { key: 'funnel',                 label: 'Funnel' },
  // Hierarchical
  { key: 'treemap',                label: 'Treemap' },
  { key: 'heatmap',                label: 'Heatmap' },
  { key: 'sunburst',               label: 'Sunburst' },
  // KPI variants
  { key: 'kpi',                    label: 'KPI' },
  { key: 'gauge',                  label: 'Gauge' },
  { key: 'multi_row_card',         label: 'Multi KPI' },
  // Table variants
  { key: 'table',                  label: 'Table' },
  { key: 'data_table',             label: 'Data Table' },
  { key: 'pivot_table',            label: 'Pivot' },
  // ── New chart types ──────────────────────────────────────
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
]

export const COLOR_PALETTES: Record<string, string[]> = {
  brand:   ['#2563EB', '#0EA5E9', '#16A34A', '#D97706', '#DC2626', '#7C3AED', '#0D9488', '#E8520A'],
  ocean:   ['#0D9488', '#0EA5E9', '#2563EB', '#7C3AED', '#EC4899', '#F59E0B', '#10B981', '#6366F1'],
  forest:  ['#15803D', '#16A34A', '#22C55E', '#4ADE80', '#A3E635', '#65A30D', '#84CC16', '#BEF264'],
  sunset:  ['#DC2626', '#EA580C', '#D97706', '#CA8A04', '#65A30D', '#0891B2', '#7C3AED', '#DB2777'],
  mono:    ['#111827', '#374151', '#6B7280', '#9CA3AF', '#D1D5DB', '#E5E7EB', '#F3F4F6', '#F9FAFB'],
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
}

export function CanvasWidget({ widget, onDelete, onUpdate, onZoom }: Props) {
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState(widget.title)
  const [showTypeMenu, setShowTypeMenu] = useState(false)
  const [showColorMenu, setShowColorMenu] = useState(false)
  const [showOriginal, setShowOriginal] = useState(false)
  const [paletteName, setPaletteName] = useState(
    (widget.config?.color_palette as string) || 'brand'
  )
  // Measured height of the chart area — updated by ResizeObserver on every resize
  const [chartHeight, setChartHeight] = useState(200)

  const titleInputRef = useRef<HTMLInputElement>(null)
  const typeMenuRef = useRef<HTMLDivElement>(null)
  const colorMenuRef = useRef<HTMLDivElement>(null)
  const chartAreaRef = useRef<HTMLDivElement>(null)

  const colors = COLOR_PALETTES[paletteName] || COLOR_PALETTES.brand

  // ── ResizeObserver: reflow chart whenever widget is resized ──────────────────
  useEffect(() => {
    const el = chartAreaRef.current
    if (!el) return
    // Fire once immediately to get the initial size
    setChartHeight(Math.max(60, el.getBoundingClientRect().height))

    const obs = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // contentRect gives the content box height (inside padding)
        const h = entry.contentRect.height
        if (h > 0) setChartHeight(Math.max(60, h))
      }
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  useEffect(() => {
    if (isEditingTitle) titleInputRef.current?.focus()
  }, [isEditingTitle])

  // Close dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (typeMenuRef.current && !typeMenuRef.current.contains(e.target as Node)) setShowTypeMenu(false)
      if (colorMenuRef.current && !colorMenuRef.current.contains(e.target as Node)) setShowColorMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

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

  const result: ChartResult = {
    chart_type: widget.chart_type,
    title: widget.title,
    chart_data: {
      rows: (widget.chart_data?.rows as Record<string, unknown>[]) || [],
      columns: (widget.chart_data?.columns as string[]) || [],
      labels: (widget.chart_data?.labels as string[]) || [],
      values: (widget.chart_data?.values as number[]) || [],
    },
    x_axis_label: (widget.config?.x_axis_label as string) || '',
    y_axis_label: (widget.config?.y_axis_label as string) || '',
    sql: widget.sql_query || '',
    score: widget.validation_score || 0,
    low_confidence: false,
    table_used: '',
  }

  // Drag-handle bar height is ~32px; subtract so chart doesn't overflow
  const HANDLE_H = 34

  return (
    <div className="flex flex-col h-full bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden select-none">

      {/* ── Drag handle + controls ───────────────────────────────────────────── */}
      <div
        className="drag-handle flex items-center gap-1 px-2 py-1.5 bg-gray-50 border-b border-gray-100 cursor-grab active:cursor-grabbing flex-shrink-0"
        style={{ height: HANDLE_H }}
      >
        <GripVertical size={14} className="text-gray-300 flex-shrink-0" />

        {/* Title */}
        <div className="flex-1 min-w-0">
          {isEditingTitle ? (
            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              <input
                ref={titleInputRef}
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitTitle()
                  if (e.key === 'Escape') { setTitleDraft(widget.title); setIsEditingTitle(false) }
                }}
                onBlur={commitTitle}
                className="flex-1 text-xs font-medium text-gray-800 bg-white border border-brand/50 rounded px-1.5 py-0.5 outline-none min-w-0"
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
              onClick={() => setIsEditingTitle(true)}
              className="flex items-center gap-1 group w-full text-left"
              title="Click to edit title"
            >
              <span className="text-xs font-medium text-gray-700 truncate">{widget.title}</span>
              <Pencil size={10} className="text-gray-300 group-hover:text-brand flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>
          )}
        </div>

        {/* Chart type switcher */}
        <div ref={typeMenuRef} className="relative flex-shrink-0">
          <button
            onClick={() => { setShowTypeMenu(!showTypeMenu); setShowColorMenu(false) }}
            className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs text-gray-500 bg-white border border-gray-200 rounded hover:border-brand/40 hover:text-brand transition-colors"
          >
            {CHART_TYPES.find(t => t.key === widget.chart_type)?.label || widget.chart_type}
            <ChevronDown size={10} />
          </button>
          {showTypeMenu && (
            <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1 min-w-max">
              {CHART_TYPES.map(t => (
                <button
                  key={t.key}
                  onClick={() => handleTypeChange(t.key)}
                  className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 transition-colors ${
                    widget.chart_type === t.key ? 'text-brand font-medium' : 'text-gray-700'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Color palette */}
        <div ref={colorMenuRef} className="relative flex-shrink-0">
          <button
            onClick={() => { setShowColorMenu(!showColorMenu); setShowTypeMenu(false) }}
            className="p-1 text-gray-400 hover:text-brand rounded transition-colors"
            title="Color theme"
          >
            <Palette size={13} />
          </button>
          {showColorMenu && (
            <div className="absolute top-full right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-2 px-3">
              <p className="text-xs text-gray-500 mb-2 font-medium">Palette</p>
              <div className="flex flex-col gap-1.5">
                {Object.entries(COLOR_PALETTES).map(([name, cols]) => (
                  <button
                    key={name}
                    onClick={() => handlePaletteChange(name)}
                    className={`flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors ${
                      paletteName === name ? 'bg-brand/10' : ''
                    }`}
                  >
                    <div className="flex gap-0.5">
                      {cols.slice(0, 5).map((c, i) => (
                        <div key={i} className="w-3 h-3 rounded-sm" style={{ backgroundColor: c }} />
                      ))}
                    </div>
                    <span className="text-xs text-gray-700 capitalize">{name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* View Original */}
        {widget.chart_data && (widget.chart_data as Record<string, unknown>).image_data && (
          <button
            onClick={() => setShowOriginal(true)}
            className="p-1 text-gray-400 hover:text-brand rounded transition-colors flex-shrink-0"
            title="View original chart image"
          >
            <ImageIcon size={13} />
          </button>
        )}

        {/* Zoom */}
        <button
          onClick={() => onZoom(widget, colors)}
          className="p-1 text-gray-400 hover:text-brand rounded transition-colors flex-shrink-0"
          title="Fullscreen"
        >
          <Maximize2 size={13} />
        </button>

        {/* Download */}
        <button
          onClick={handleDownload}
          className="p-1 text-gray-400 hover:text-brand rounded transition-colors flex-shrink-0"
          title="Download PNG"
        >
          <Download size={13} />
        </button>

        {/* Delete */}
        <button
          onClick={() => onDelete(widget.id)}
          className="p-1 text-gray-400 hover:text-red-500 rounded transition-colors flex-shrink-0"
          title="Remove"
        >
          <Trash2 size={13} />
        </button>
      </div>

      {/* ── Chart area: flex-1 so it fills remaining widget height ──────────── */}
      <div
        id={`widget-chart-${widget.id}`}
        ref={chartAreaRef}
        className="flex-1 overflow-hidden min-h-0 p-2"
      >
        {widget.chart_data ? (
          // chartHeight is the live-measured pixel height of this div (content box)
          // ChartRenderer sizes every chart to exactly this height
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
      </div>

      {/* Original image modal */}
      {showOriginal && widget.chart_data && (widget.chart_data as Record<string, unknown>).image_data && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={() => setShowOriginal(false)}
        >
          <div
            className="relative bg-white rounded-2xl shadow-2xl max-w-4xl max-h-[90vh] overflow-auto p-4"
            onClick={(e) => e.stopPropagation()}
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
