'use client'
import React, { useState, useEffect, useCallback } from 'react'
import { X, Shield, Plus, Trash2, Loader2, AlertCircle, CheckCircle2, ToggleLeft, ToggleRight } from 'lucide-react'
import { rlsApi, type RLSPolicy } from '@/lib/api'

interface Props {
  canvasId: string
  onClose: () => void
}

export function RLSModal({ canvasId, onClose }: Props) {
  const [policies, setPolicies] = useState<RLSPolicy[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', clause: '', user_id: '', is_active: true })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await rlsApi.list(canvasId)
      setPolicies(resp.data.policies ?? [])
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } }; message?: string }
      const status = e?.response?.status
      const detail = e?.response?.data?.detail || e?.message || 'Unknown error'
      if (status === 404) {
        setError(`404 — route not found. Restart the backend server and try again.`)
      } else if (status === 401) {
        setError(`401 — not authenticated. Check DEV_MODE or Bearer token.`)
      } else {
        setError(`Failed to load RLS policies (${status ?? 'network error'}): ${detail}`)
      }
      console.error('[RLSModal] load error:', err)
    } finally {
      setLoading(false)
    }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!form.name.trim() || !form.clause.trim()) return
    setSaving(true)
    setError(null)
    try {
      const resp = await rlsApi.create(canvasId, {
        name: form.name,
        clause: form.clause,
        user_id: form.user_id.trim() || null,
        is_active: form.is_active,
      })
      setPolicies(prev => [resp.data, ...prev])
      setForm({ name: '', clause: '', user_id: '', is_active: true })
      setShowForm(false)
      setSuccess('Policy created')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail || 'Failed to create policy')
    } finally {
      setSaving(false)
    }
  }

  const handleToggle = async (policy: RLSPolicy) => {
    try {
      const resp = await rlsApi.update(canvasId, policy.id, { is_active: !policy.is_active })
      setPolicies(prev => prev.map(p => p.id === policy.id ? resp.data : p))
    } catch {
      setError('Failed to update policy')
    }
  }

  const handleDelete = async (policyId: string) => {
    try {
      await rlsApi.delete(canvasId, policyId)
      setPolicies(prev => prev.filter(p => p.id !== policyId))
    } catch {
      setError('Failed to delete policy')
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
          width: 440, height: '100vh', background: 'white',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.14)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{ padding: '18px 20px', borderBottom: '1px solid #F3F4F6', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 34, height: 34, borderRadius: 10, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Shield size={16} color="#EA580C" />
              </div>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#111827', margin: 0 }}>Row-Level Security</p>
                <p style={{ fontSize: 11, color: '#9CA3AF', margin: 0 }}>SQL WHERE clauses injected per user</p>
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }}>
              <X size={16} />
            </button>
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* How it works */}
          <div style={{ padding: '10px 14px', background: '#FFF7ED', borderRadius: 10, border: '1px solid #FED7AA' }}>
            <p style={{ fontSize: 11, color: '#9A3412', margin: 0, lineHeight: 1.5 }}>
              <strong>How it works:</strong> When a user queries this dashboard, their matching RLS clause is appended
              as a WHERE condition to every widget SQL. Leave User ID blank for a catch-all policy (applies to all users without a specific policy).
            </p>
          </div>

          {/* Feedback */}
          {error && (
            <div style={{ padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, display: 'flex', gap: 8 }}>
              <AlertCircle size={14} color="#DC2626" style={{ flexShrink: 0, marginTop: 1 }} />
              <p style={{ fontSize: 12, color: '#DC2626', margin: 0 }}>{error}</p>
            </div>
          )}
          {success && (
            <div style={{ padding: '10px 14px', background: '#ECFDF5', border: '1px solid #BBF7D0', borderRadius: 8, display: 'flex', gap: 8 }}>
              <CheckCircle2 size={14} color="#16A34A" />
              <p style={{ fontSize: 12, color: '#16A34A', margin: 0 }}>{success}</p>
            </div>
          )}

          {/* Create form */}
          {showForm && (
            <div style={{ padding: 16, border: '1px solid #E5E7EB', borderRadius: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: '#111827', margin: 0 }}>New Policy</p>
              <div>
                <label style={{ fontSize: 11, color: '#6B7280', display: 'block', marginBottom: 4 }}>Policy Name</label>
                <input
                  value={form.name}
                  onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                  placeholder="North region only"
                  style={{ width: '100%', padding: '6px 10px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none', boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: '#6B7280', display: 'block', marginBottom: 4 }}>SQL WHERE Clause</label>
                <textarea
                  value={form.clause}
                  onChange={e => setForm(p => ({ ...p, clause: e.target.value }))}
                  placeholder="region = 'North'"
                  rows={2}
                  style={{ width: '100%', padding: '6px 10px', fontSize: 12, fontFamily: '"JetBrains Mono",monospace', border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none', resize: 'vertical', boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: '#6B7280', display: 'block', marginBottom: 4 }}>
                  User ID <span style={{ color: '#9CA3AF' }}>(leave blank for catch-all)</span>
                </label>
                <input
                  value={form.user_id}
                  onChange={e => setForm(p => ({ ...p, user_id: e.target.value }))}
                  placeholder="UUID or blank for all users"
                  style={{ width: '100%', padding: '6px 10px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none', boxSizing: 'border-box' }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={handleCreate}
                  disabled={saving || !form.name.trim() || !form.clause.trim()}
                  style={{
                    flex: 1, padding: '8px', fontSize: 12, fontWeight: 600, border: 'none',
                    borderRadius: 8, cursor: saving ? 'wait' : 'pointer',
                    background: '#2563EB', color: 'white',
                  }}
                >
                  {saving ? 'Saving…' : 'Create Policy'}
                </button>
                <button
                  onClick={() => { setShowForm(false); setForm({ name: '', clause: '', user_id: '', is_active: true }) }}
                  style={{ padding: '8px 14px', fontSize: 12, border: '1px solid #E5E7EB', borderRadius: 8, cursor: 'pointer', background: 'white', color: '#6B7280' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {!showForm && (
            <button
              onClick={() => setShowForm(true)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                padding: '10px', fontSize: 12, fontWeight: 600,
                border: '1px dashed #D1D5DB', borderRadius: 10,
                cursor: 'pointer', background: 'transparent', color: '#6B7280',
              }}
            >
              <Plus size={13} /> Add Policy
            </button>
          )}

          {/* Policy list */}
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 20 }}>
              <Loader2 size={20} color="#9CA3AF" style={{ animation: 'visually-spin 1s linear infinite' }} />
            </div>
          ) : policies.length === 0 ? (
            <p style={{ fontSize: 12, color: '#9CA3AF', textAlign: 'center', paddingTop: 20 }}>
              No RLS policies configured.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {policies.map(policy => (
                <div
                  key={policy.id}
                  style={{
                    padding: '12px 14px', borderRadius: 10, border: '1px solid',
                    borderColor: policy.is_active ? '#BBF7D0' : '#E5E7EB',
                    background: policy.is_active ? '#F0FDF4' : '#FAFAFA',
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{policy.name}</span>
                      {!policy.user_id && (
                        <span style={{ fontSize: 10, padding: '1px 6px', background: '#FFF7ED', color: '#EA580C', borderRadius: 4 }}>catch-all</span>
                      )}
                    </div>
                    <code style={{ fontSize: 11, color: '#374151', fontFamily: '"JetBrains Mono",monospace', display: 'block', marginTop: 3, wordBreak: 'break-all' }}>
                      WHERE {policy.clause}
                    </code>
                    {policy.user_id && (
                      <p style={{ fontSize: 10, color: '#9CA3AF', margin: '3px 0 0' }}>user: {policy.user_id}</p>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    <button
                      onClick={() => handleToggle(policy)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: policy.is_active ? '#16A34A' : '#9CA3AF', padding: 4 }}
                      title={policy.is_active ? 'Disable' : 'Enable'}
                    >
                      {policy.is_active ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                    </button>
                    <button
                      onClick={() => handleDelete(policy.id)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#F87171', padding: 4 }}
                      title="Delete"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
