'use client'
import { useState, useEffect } from 'react'
import { X, Calendar, Plus, Trash2, Loader2, Check, Clock, Mail } from 'lucide-react'
import { analystApi } from '@/lib/api'
import type { ScheduleData } from '@/lib/api'

interface ScheduleModalProps {
  token: string
  dashboardName: string
  onClose: () => void
}

const FREQUENCIES = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
]

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const HOURS = Array.from({ length: 24 }, (_, i) => ({ value: i, label: `${String(i).padStart(2, '0')}:00 UTC` }))

export function ScheduleModal({ token, dashboardName, onClose }: ScheduleModalProps) {
  const [schedules, setSchedules] = useState<ScheduleData[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [form, setForm] = useState({ email: '', frequency: 'daily', day_of_week: 0, hour_utc: 8, include_ai_summary: true })

  useEffect(() => {
    analystApi.listSchedules(token).then(r => setSchedules(r.data.schedules)).finally(() => setLoading(false))
  }, [token])

  const save = async () => {
    if (!form.email.trim() || saving) return
    setSaving(true)
    try {
      const r = await analystApi.createSchedule(token, {
        email: form.email.trim(),
        frequency: form.frequency as 'daily' | 'weekly' | 'monthly',
        day_of_week: form.frequency === 'weekly' ? form.day_of_week : undefined,
        hour_utc: form.hour_utc,
        include_ai_summary: form.include_ai_summary,
      })
      setSchedules(prev => [r.data, ...prev])
      setForm({ email: '', frequency: 'daily', day_of_week: 0, hour_utc: 8, include_ai_summary: true })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch { /* ignore */ } finally {
      setSaving(false)
    }
  }

  const remove = async (id: string) => {
    try {
      await analystApi.deleteSchedule(token, id)
      setSchedules(prev => prev.filter(s => s.id !== id))
    } catch { /* ignore */ }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.4)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <Calendar size={15} className="text-purple-500" /> Email Snapshots
          </h3>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100"><X size={16} /></button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* New schedule form */}
          <div className="px-5 py-4 border-b border-gray-100 space-y-3">
            <p className="text-xs text-gray-500">Get <strong>{dashboardName}</strong> emailed as a snapshot</p>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Email address</label>
              <div className="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-lg focus-within:border-blue-400">
                <Mail size={13} className="text-gray-400 flex-shrink-0" />
                <input
                  type="email"
                  value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  placeholder="you@company.com"
                  className="flex-1 text-xs outline-none bg-transparent text-gray-700"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Frequency</label>
                <select value={form.frequency} onChange={e => setForm(f => ({ ...f, frequency: e.target.value }))}
                  className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400 bg-white">
                  {FREQUENCIES.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Time</label>
                <select value={form.hour_utc} onChange={e => setForm(f => ({ ...f, hour_utc: parseInt(e.target.value) }))}
                  className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400 bg-white">
                  {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                </select>
              </div>
            </div>
            {form.frequency === 'weekly' && (
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Day of week</label>
                <select value={form.day_of_week} onChange={e => setForm(f => ({ ...f, day_of_week: parseInt(e.target.value) }))}
                  className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400 bg-white">
                  {DAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                </select>
              </div>
            )}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input type="checkbox" checked={form.include_ai_summary}
                onChange={e => setForm(f => ({ ...f, include_ai_summary: e.target.checked }))}
                className="w-4 h-4 accent-blue-600" />
              <span className="text-xs text-gray-700">Include AI summary</span>
            </label>
            <button onClick={save} disabled={!form.email.trim() || saving}
              className="w-full flex items-center justify-center gap-2 py-2 text-xs font-semibold text-white rounded-xl disabled:opacity-50 transition-all"
              style={{ background: saved ? '#10B981' : 'linear-gradient(135deg, #2563EB, #7C3AED)' }}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : saved ? <Check size={13} /> : <Plus size={13} />}
              {saved ? 'Scheduled!' : 'Create Schedule'}
            </button>
          </div>

          {/* Existing schedules */}
          <div>
            <div className="px-5 py-2.5 bg-gray-50 border-b border-gray-100">
              <span className="text-xs font-semibold text-gray-600">Active Schedules</span>
            </div>
            {loading ? (
              <div className="flex items-center justify-center h-20"><Loader2 size={16} className="animate-spin text-blue-400" /></div>
            ) : schedules.length === 0 ? (
              <div className="flex items-center justify-center h-20 text-xs text-gray-400">No schedules yet</div>
            ) : (
              schedules.map(s => (
                <div key={s.id} className="flex items-center gap-3 px-5 py-3 border-b border-gray-50 hover:bg-gray-50 group">
                  <Mail size={13} className="text-purple-400 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-gray-800 truncate">{s.email}</p>
                    <p className="text-xs text-gray-400 flex items-center gap-1">
                      <Clock size={9} />
                      {s.frequency} at {String(s.hour_utc).padStart(2, '0')}:00 UTC
                      {s.next_send_at && ` · next: ${new Date(s.next_send_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.is_active ? 'bg-green-400' : 'bg-gray-300'}`} />
                  <button onClick={() => remove(s.id)} className="p-1 text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-all rounded"><Trash2 size={12} /></button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
