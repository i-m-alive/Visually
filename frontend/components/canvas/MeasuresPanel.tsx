'use client'
import React, { useState, useEffect, useCallback } from 'react'
import { X, Plus, Trash2, Loader2, Sparkles, AlertCircle, FunctionSquare } from 'lucide-react'
import { measuresApi, type Measure } from '@/lib/api'

interface Props {
  canvasId: string
  onClose: () => void
}

const FORMAT_OPTIONS = ['number', 'percent', 'currency']

export function MeasuresPanel({ canvasId, onClose }: Props) {
  const [measures, setMeasures] = useState<Measure[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiDesc, setAiDesc] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [form, setForm] = useState({ name: '', label: '', expression: '', format: 'number' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await measuresApi.list(canvasId)
      setMeasures(resp.data.measures ?? [])
    } catch {
      setError('Failed to load measures')
    } finally {
      setLoading(false)
    }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    if (!form.name.trim() || !form.label.trim() || !form.expression.trim()) return
    setSaving(true)
    setError(null)
    try {
      const resp = await measuresApi.create(canvasId, form)
      setMeasures(resp.data.measures)
      setForm({ name: '', label: '', expression: '', format: 'number' })
      setShowAddForm(false)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail || 'Failed to save measure')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (name: string) => {
    try {
      const resp = await measuresApi.delete(canvasId, name)
      setMeasures(resp.data.measures)
    } catch {
      setError('Failed to delete measure')
    }
  }

  const handleGenerate = async () => {
    if (!aiDesc.trim()) return
    setAiLoading(true)
    setError(null)
    try {
      const resp = await measuresApi.generate(canvasId, aiDesc)
      setForm({
        name: resp.data.name || '',
        label: resp.data.label || '',
        expression: resp.data.expression || '',
        format: resp.data.format || 'number',
      })
      setShowAddForm(true)
      setAiDesc('')
    } catch {
      setError('AI generation failed. Try describing the measure differently.')
    } finally {
      setAiLoading(false)
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 600,
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end',
        animation: 'visually-fadeIn 0.15s ease both',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 400, height: '100vh', background: 'white',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.14)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          animation: 'visually-slideUp 0.2s ease both',
        }}
      >
        {/* Header */}
        <div style={{ padding: '18px 20px', borderBottom: '1px solid #F3F4F6', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 34, height: 34, borderRadius: 10, background: '#F5F3FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <FunctionSquare size={16} color="#7C3AED" />
              </div>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#111827', margin: 0 }}>Calculated Measures</p>
                <p style={{ fontSize: 11, color: '#9CA3AF', margin: 0 }}>{measures.length} measure{measures.length !== 1 ? 's' : ''}</p>
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }}>
              <X size={16} />
            </button>
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div style={{ padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <AlertCircle size={14} color="#DC2626" style={{ flexShrink: 0, marginTop: 1 }} />
              <p style={{ fontSize: 12, color: '#DC2626', margin: 0 }}>{error}</p>
            </div>
          )}

          {/* AI generator */}
          <div style={{ padding: '14px 16px', background: '#FAF5FF', borderRadius: 12, border: '1px solid #E9D5FF' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
              <Sparkles size={13} color="#7C3AED" />
              <span style={{ fontSize: 12, fontWeight: 600, color: '#7C3AED' }}>AI Measure Generator</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={aiDesc}
                onChange={e => setAiDesc(e.target.value)}
                placeholder="e.g. profit margin as percentage"
                onKeyDown={e => e.key === 'Enter' && handleGenerate()}
                style={{ flex: 1, padding: '7px 10px', fontSize: 12, border: '1px solid #E9D5FF', borderRadius: 8, background: 'white', outline: 'none' }}
              />
              <button
                onClick={handleGenerate}
                disabled={aiLoading || !aiDesc.trim()}
                style={{
                  padding: '7px 14px', fontSize: 12, fontWeight: 600, border: 'none',
                  borderRadius: 8, cursor: aiLoading || !aiDesc.trim() ? 'not-allowed' : 'pointer',
                  background: aiLoading || !aiDesc.trim() ? '#E9D5FF' : '#7C3AED',
                  color: 'white', display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
                }}
              >
                {aiLoading ? <Loader2 size={12} style={{ animation: 'visually-spin 1s linear infinite' }} /> : 'Generate'}
              </button>
            </div>
          </div>

          {/* Add form */}
          {showAddForm && (
            <div style={{ padding: 16, border: '1px solid #E5E7EB', borderRadius: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: '#111827', margin: 0 }}>New Measure</p>
              {[
                { key: 'name', label: 'Name (identifier)', placeholder: 'profit_margin' },
                { key: 'label', label: 'Display Label', placeholder: 'Profit Margin %' },
                { key: 'expression', label: 'SQL Expression', placeholder: 'SUM(profit) / SUM(revenue) * 100' },
              ].map(({ key, label, placeholder }) => (
                <div key={key}>
                  <label style={{ fontSize: 11, color: '#6B7280', display: 'block', marginBottom: 4 }}>{label}</label>
                  <input
                    value={form[key as keyof typeof form]}
                    onChange={e => setForm(prev => ({ ...prev, [key]: e.target.value }))}
                    placeholder={placeholder}
                    style={{ width: '100%', padding: '6px 10px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none', boxSizing: 'border-box' }}
                  />
                </div>
              ))}
              <div>
                <label style={{ fontSize: 11, color: '#6B7280', display: 'block', marginBottom: 4 }}>Format</label>
                <select
                  value={form.format}
                  onChange={e => setForm(prev => ({ ...prev, format: e.target.value }))}
                  style={{ width: '100%', padding: '6px 10px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none', background: 'white' }}
                >
                  {FORMAT_OPTIONS.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={handleSave}
                  disabled={saving || !form.name.trim() || !form.expression.trim()}
                  style={{
                    flex: 1, padding: '8px', fontSize: 12, fontWeight: 600, border: 'none',
                    borderRadius: 8, cursor: saving ? 'wait' : 'pointer',
                    background: '#2563EB', color: 'white',
                  }}
                >
                  {saving ? 'Saving…' : 'Save Measure'}
                </button>
                <button
                  onClick={() => { setShowAddForm(false); setForm({ name: '', label: '', expression: '', format: 'number' }) }}
                  style={{ padding: '8px 14px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, cursor: 'pointer', background: 'white', color: '#6B7280' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Add button */}
          {!showAddForm && (
            <button
              onClick={() => setShowAddForm(true)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                padding: '10px', fontSize: 12, fontWeight: 600,
                border: '1px dashed #D1D5DB', borderRadius: 10,
                cursor: 'pointer', background: 'transparent', color: '#6B7280',
              }}
            >
              <Plus size={13} /> Add Measure Manually
            </button>
          )}

          {/* Measures list */}
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 20 }}>
              <Loader2 size={20} color="#9CA3AF" style={{ animation: 'visually-spin 1s linear infinite' }} />
            </div>
          ) : measures.length === 0 ? (
            <p style={{ fontSize: 12, color: '#9CA3AF', textAlign: 'center', paddingTop: 20 }}>
              No calculated measures yet.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {measures.map(m => (
                <div
                  key={m.name}
                  style={{ padding: '12px 14px', border: '1px solid #E5E7EB', borderRadius: 10, display: 'flex', alignItems: 'flex-start', gap: 10 }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{m.label}</span>
                      <span style={{ fontSize: 10, padding: '1px 6px', background: '#F3F4F6', borderRadius: 4, color: '#6B7280' }}>{m.format}</span>
                    </div>
                    <code style={{ fontSize: 11, color: '#6B7280', fontFamily: '"JetBrains Mono",monospace' }}>{m.name}</code>
                    <p style={{ fontSize: 11, color: '#9CA3AF', margin: '4px 0 0', wordBreak: 'break-all' }}>{m.expression}</p>
                  </div>
                  <button
                    onClick={() => handleDelete(m.name)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#F87171', padding: 4, flexShrink: 0 }}
                    title="Delete measure"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
