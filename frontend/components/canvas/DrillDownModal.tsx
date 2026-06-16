'use client'
import React, { useState, useEffect } from 'react'
import { X, Loader2, ChevronRight, AlertCircle, ZoomIn, Home } from 'lucide-react'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import { drilldownApi, type DrilldownResult } from '@/lib/api'
import type { ChartResult } from '@/stores/pipelineStore'

interface DrillLevel {
  label: string
  result: DrilldownResult
}

interface Props {
  canvasId: string
  widgetId: string
  widgetTitle: string
  drillColumn: string
  drillValue: string
  connectionId?: string
  onClose: () => void
}

function drillToChartResult(result: DrilldownResult): ChartResult {
  const { rows, columns } = result.chart_data
  return {
    chart_type: 'table',
    title: `${result.drill_column} = ${result.drill_value}`,
    sql: result.child_sql,
    score: 1,
    low_confidence: false,
    x_axis_label: columns[0] || 'x',
    y_axis_label: columns[1] || 'y',
    table_used: '',
    chart_data: {
      rows,
      columns,
      labels: rows.map(r => String(r[columns[0]] ?? '')),
      values: rows.map(r => Number(r[columns[1]] ?? 0)),
    },
  }
}

export function DrillDownModal({
  canvasId, widgetId, widgetTitle,
  drillColumn, drillValue, connectionId, onClose,
}: Props) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [stack, setStack] = useState<DrillLevel[]>([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    drilldownApi.generate(canvasId, {
      widget_id: widgetId,
      drill_column: drillColumn,
      drill_value: drillValue,
      connection_id: connectionId,
    }).then(resp => {
      if (cancelled) return
      setStack([{
        label: `${drillColumn}: ${drillValue}`,
        result: resp.data,
      }])
    }).catch(err => {
      if (cancelled) return
      setError(err?.response?.data?.detail || err?.message || 'Drilldown failed')
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [canvasId, widgetId, drillColumn, drillValue, connectionId])

  const handleDrillFurther = async (col: string, val: string) => {
    const currentResult = stack[stack.length - 1]?.result
    if (!currentResult) return
    setLoading(true)
    setError(null)
    try {
      const resp = await drilldownApi.generate(canvasId, {
        widget_id: widgetId,
        drill_column: col,
        drill_value: val,
        connection_id: connectionId,
      })
      setStack(prev => [...prev, {
        label: `${col}: ${val}`,
        result: resp.data,
      }])
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail || e?.message || 'Drilldown failed')
    } finally {
      setLoading(false)
    }
  }

  const currentLevel = stack[stack.length - 1]

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 600,
        background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32,
        animation: 'visually-fadeIn 0.15s ease both',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: 20, boxShadow: '0 24px 60px rgba(0,0,0,0.22)',
          width: '100%', maxWidth: 780, maxHeight: '85vh',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{ padding: '18px 24px', borderBottom: '1px solid #F3F4F6', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: '#EFF6FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <ZoomIn size={16} color="#2563EB" />
              </div>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#111827', margin: 0 }}>Drill Down</p>
                <p style={{ fontSize: 11, color: '#9CA3AF', margin: 0 }}>{widgetTitle}</p>
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }}>
              <X size={16} />
            </button>
          </div>

          {/* Breadcrumb */}
          {stack.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 12, flexWrap: 'wrap' }}>
              <button
                onClick={() => setStack([])}
                style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 6, background: '#F3F4F6', border: 'none', cursor: 'pointer', fontSize: 11, color: '#6B7280' }}
              >
                <Home size={10} /> Root
              </button>
              {stack.map((level, i) => (
                <React.Fragment key={i}>
                  <ChevronRight size={10} color="#D1D5DB" />
                  <button
                    onClick={() => setStack(prev => prev.slice(0, i + 1))}
                    style={{
                      padding: '3px 8px', borderRadius: 6, border: 'none', cursor: 'pointer',
                      fontSize: 11, fontWeight: i === stack.length - 1 ? 700 : 400,
                      background: i === stack.length - 1 ? '#EFF6FF' : '#F3F4F6',
                      color: i === stack.length - 1 ? '#2563EB' : '#6B7280',
                    }}
                  >
                    {level.label}
                  </button>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
          {loading && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 200, gap: 12 }}>
              <Loader2 size={24} color="#2563EB" style={{ animation: 'visually-spin 1s linear infinite' }} />
              <p style={{ fontSize: 13, color: '#9CA3AF', margin: 0 }}>AI is generating drilldown SQL…</p>
            </div>
          )}

          {!loading && error && (
            <div style={{ padding: '16px 20px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 12, display: 'flex', gap: 10 }}>
              <AlertCircle size={16} color="#DC2626" style={{ flexShrink: 0, marginTop: 1 }} />
              <p style={{ fontSize: 13, color: '#DC2626', margin: 0 }}>{error}</p>
            </div>
          )}

          {!loading && !error && currentLevel && (
            <div>
              {/* SQL snippet */}
              <details style={{ marginBottom: 16 }}>
                <summary style={{ fontSize: 11, color: '#9CA3AF', cursor: 'pointer', userSelect: 'none' }}>
                  View generated SQL
                </summary>
                <pre style={{
                  marginTop: 8, padding: '10px 14px', background: '#F9FAFB',
                  borderRadius: 8, fontSize: 11, color: '#374151',
                  overflow: 'auto', maxHeight: 120, border: '1px solid #E5E7EB',
                  fontFamily: '"JetBrains Mono","SF Mono",monospace',
                }}>
                  {currentLevel.result.child_sql}
                </pre>
              </details>

              {/* Chart */}
              <div style={{ height: 320, background: '#FAFAFA', borderRadius: 12, border: '1px solid #E5E7EB', padding: 12 }}>
                {currentLevel.result.chart_data.rows.length === 0 ? (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9CA3AF', fontSize: 13 }}>
                    No rows returned
                  </div>
                ) : (
                  <ChartRenderer
                    result={drillToChartResult(currentLevel.result)}
                    height={296}
                    onDataPointClick={loading ? undefined : (col, val) => handleDrillFurther(col, String(val))}
                  />
                )}
              </div>

              <p style={{ fontSize: 11, color: '#9CA3AF', marginTop: 10, textAlign: 'center' }}>
                Click any data point to drill down further
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
