'use client'
import React, { useState, useEffect, useCallback } from 'react'
import { X, Clock, Loader2, CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react'
import { scheduleApi, type RefreshSchedule } from '@/lib/api'

interface Props {
  canvasId: string
  onClose: () => void
  onRefreshedNow?: () => void
}

const PRESETS = [
  { label: 'Every hour',        cron: '0 * * * *' },
  { label: 'Every 6 hours',     cron: '0 */6 * * *' },
  { label: 'Daily at 8am',      cron: '0 8 * * *' },
  { label: 'Weekdays at 8am',   cron: '0 8 * * 1-5' },
  { label: 'Weekly (Monday)',   cron: '0 8 * * 1' },
  { label: 'Monthly (1st)',     cron: '0 8 1 * *' },
]

export function ScheduleRefreshModal({ canvasId, onClose, onRefreshedNow }: Props) {
  const [schedule, setSchedule] = useState<RefreshSchedule>({ enabled: false, cron: null, timezone: 'UTC' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [cronDraft, setCronDraft] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await scheduleApi.get(canvasId)
      const s = resp.data.schedule
      setSchedule(s)
      setCronDraft(s.cron || '')
    } catch {
      setError('Failed to load schedule')
    } finally {
      setLoading(false)
    }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const resp = await scheduleApi.set(canvasId, {
        enabled: schedule.enabled,
        cron: cronDraft.trim() || null,
        timezone: schedule.timezone,
      })
      setSchedule(resp.data.schedule)
      setCronDraft(resp.data.schedule.cron || '')
      setSuccess('Schedule saved!')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e?.response?.data?.detail || 'Failed to save schedule')
    } finally {
      setSaving(false)
    }
  }

  const handleRefreshNow = async () => {
    setRefreshing(true)
    setError(null)
    try {
      await scheduleApi.refreshNow(canvasId)
      setSuccess('Dashboard refreshed!')
      setTimeout(() => setSuccess(null), 3000)
      onRefreshedNow?.()
    } catch {
      setError('Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const selectPreset = (cron: string) => {
    setCronDraft(cron)
    setSchedule(prev => ({ ...prev, enabled: true }))
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 600,
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32,
        animation: 'visually-fadeIn 0.15s ease both',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: 20,
          boxShadow: '0 24px 60px rgba(0,0,0,0.18)',
          width: '100%', maxWidth: 480,
          animation: 'visually-slideUp 0.2s ease both',
        }}
      >
        {/* Header */}
        <div style={{ padding: '18px 24px', borderBottom: '1px solid #F3F4F6' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: '#ECFDF5', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Clock size={16} color="#16A34A" />
              </div>
              <div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#111827', margin: 0 }}>Scheduled Refresh</p>
                <p style={{ fontSize: 11, color: '#9CA3AF', margin: 0 }}>Auto-refresh data on a cron schedule</p>
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9CA3AF', padding: 4 }}>
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {loading && (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}>
              <Loader2 size={22} color="#9CA3AF" style={{ animation: 'visually-spin 1s linear infinite' }} />
            </div>
          )}

          {!loading && (
            <>
              {/* Feedback banners */}
              {error && (
                <div style={{ padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, display: 'flex', gap: 8 }}>
                  <AlertCircle size={14} color="#DC2626" />
                  <p style={{ fontSize: 12, color: '#DC2626', margin: 0 }}>{error}</p>
                </div>
              )}
              {success && (
                <div style={{ padding: '10px 14px', background: '#ECFDF5', border: '1px solid #BBF7D0', borderRadius: 8, display: 'flex', gap: 8 }}>
                  <CheckCircle2 size={14} color="#16A34A" />
                  <p style={{ fontSize: 12, color: '#16A34A', margin: 0 }}>{success}</p>
                </div>
              )}

              {/* Enable toggle */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', background: '#F9FAFB', borderRadius: 10 }}>
                <div>
                  <p style={{ fontSize: 13, fontWeight: 600, color: '#111827', margin: 0 }}>Enable scheduled refresh</p>
                  <p style={{ fontSize: 11, color: '#9CA3AF', margin: '2px 0 0' }}>
                    {schedule.enabled ? 'Refresh is active' : 'Refresh is paused'}
                  </p>
                </div>
                <div
                  onClick={() => setSchedule(prev => ({ ...prev, enabled: !prev.enabled }))}
                  style={{
                    width: 40, height: 22, borderRadius: 11, cursor: 'pointer',
                    background: schedule.enabled ? '#16A34A' : '#D1D5DB',
                    position: 'relative', transition: 'background 0.2s',
                    flexShrink: 0,
                  }}
                >
                  <div style={{
                    position: 'absolute', top: 3, left: schedule.enabled ? 21 : 3,
                    width: 16, height: 16, borderRadius: '50%', background: 'white',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.2)', transition: 'left 0.2s',
                  }} />
                </div>
              </div>

              {/* Presets */}
              <div>
                <p style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 8 }}>Quick presets</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {PRESETS.map(p => (
                    <button
                      key={p.cron}
                      onClick={() => selectPreset(p.cron)}
                      style={{
                        padding: '5px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer',
                        border: '1px solid',
                        borderColor: cronDraft === p.cron ? '#2563EB' : '#E5E7EB',
                        background: cronDraft === p.cron ? '#EFF6FF' : 'white',
                        color: cronDraft === p.cron ? '#2563EB' : '#374151',
                        fontWeight: cronDraft === p.cron ? 600 : 400,
                        transition: 'all 0.12s',
                      }}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Custom cron */}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#374151', display: 'block', marginBottom: 6 }}>
                  Cron expression
                </label>
                <input
                  value={cronDraft}
                  onChange={e => setCronDraft(e.target.value)}
                  placeholder="0 8 * * 1-5"
                  style={{
                    width: '100%', padding: '8px 12px', fontSize: 13,
                    border: '1px solid #E5E7EB', borderRadius: 8, outline: 'none',
                    fontFamily: '"JetBrains Mono",monospace', boxSizing: 'border-box',
                  }}
                />
                <p style={{ fontSize: 10, color: '#9CA3AF', marginTop: 4 }}>
                  Format: minute hour day-of-month month day-of-week  ·  Timezone: {schedule.timezone}
                </p>
              </div>

              {/* Actions */}
              <div style={{ display: 'flex', gap: 10 }}>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  style={{
                    flex: 1, padding: '10px', fontSize: 13, fontWeight: 600,
                    border: 'none', borderRadius: 10, cursor: saving ? 'wait' : 'pointer',
                    background: '#2563EB', color: 'white',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  }}
                >
                  {saving ? <Loader2 size={14} style={{ animation: 'visually-spin 1s linear infinite' }} /> : null}
                  Save Schedule
                </button>
                <button
                  onClick={handleRefreshNow}
                  disabled={refreshing}
                  style={{
                    padding: '10px 16px', fontSize: 13, fontWeight: 600,
                    border: '1px solid #E5E7EB', borderRadius: 10, cursor: refreshing ? 'wait' : 'pointer',
                    background: 'white', color: '#374151',
                    display: 'flex', alignItems: 'center', gap: 6,
                    flexShrink: 0,
                  }}
                >
                  <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
                  Refresh Now
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
