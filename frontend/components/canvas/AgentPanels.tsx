'use client'
/**
 * Agent-feature panels for the canvas editor:
 *  - HistoryDrawer  — version timeline with prose diffs + restore
 *  - AlertsModal    — plain-language data alerts (create/manage, last trigger shown)
 *  - ExplainPopover — "why is this value what it is?" on a clicked data point
 * Backends: routers/versions.py, routers/alerts.py, routers/explain.py
 */
import { useCallback, useEffect, useState } from 'react'
import {
  X, History, RotateCcw, Loader2, Bell, BellOff, Trash2, Plus,
  Sparkles, AlertTriangle,
} from 'lucide-react'
import {
  versionsApi, alertsApi, explainApi,
  type VersionMeta, type AlertRuleItem,
} from '@/lib/api'

// ═══════════════════════ History drawer ═══════════════════════

export function HistoryDrawer({ canvasId, onClose, onRestored }: {
  canvasId: string
  onClose: () => void
  onRestored: () => void
}) {
  const [versions, setVersions] = useState<VersionMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [diffFor, setDiffFor] = useState<string | null>(null)
  const [diffProse, setDiffProse] = useState<string>('')
  const [diffLoading, setDiffLoading] = useState(false)
  const [restoring, setRestoring] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await versionsApi.list(canvasId)
      setVersions(r.data.versions || [])
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const showDiff = async (versionId: string) => {
    if (diffFor === versionId) { setDiffFor(null); return }
    setDiffFor(versionId)
    setDiffLoading(true)
    setDiffProse('')
    try {
      const r = await versionsApi.diff(canvasId, versionId, 'current')
      setDiffProse(r.data.prose || 'No changes vs current.')
    } catch { setDiffProse('Diff unavailable.') } finally { setDiffLoading(false) }
  }

  const restore = async (versionId: string, vnum: number) => {
    if (!confirm(`Restore version ${vnum}? Current state is snapshotted first, so this is reversible.`)) return
    setRestoring(versionId)
    try {
      await versionsApi.restore(canvasId, versionId)
      onRestored()
      onClose()
    } catch { alert('Restore failed') } finally { setRestoring(null) }
  }

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-[360px] bg-white border-l border-gray-200 shadow-2xl flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <h3 className="font-bold text-gray-900 flex items-center gap-2"><History size={16} className="text-blue-600" /> Version history</h3>
        <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 rounded"><X size={16} /></button>
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-2">
        {loading ? (
          <div className="flex justify-center py-10 text-gray-400"><Loader2 size={18} className="animate-spin" /></div>
        ) : versions.length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-10">No versions yet — snapshots are captured automatically when you save.</p>
        ) : versions.map(v => (
          <div key={v.id} className="border border-gray-200 rounded-xl p-3 hover:border-blue-200 transition-colors">
            <div className="flex items-center justify-between gap-2">
              <button onClick={() => showDiff(v.id)} className="text-left min-w-0 flex-1">
                <p className="text-sm font-semibold text-gray-800">v{v.version_number} · {v.widget_count} widgets</p>
                <p className="text-[11px] text-gray-400">
                  {v.created_at ? new Date(v.created_at + (v.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString() : ''}
                  {v.change_summary ? ` — ${v.change_summary}` : ''}
                </p>
              </button>
              <button
                onClick={() => restore(v.id, v.version_number)}
                disabled={!!restoring}
                className="flex items-center gap-1 text-[11px] px-2 py-1 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-md flex-shrink-0"
                title="Restore this version"
              >
                {restoring === v.id ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} Restore
              </button>
            </div>
            {diffFor === v.id && (
              <div className="mt-2 text-xs text-gray-600 bg-blue-50/60 border border-blue-100 rounded-lg px-2.5 py-2">
                {diffLoading ? <Loader2 size={12} className="animate-spin text-blue-400" /> : (
                  <><Sparkles size={11} className="inline text-blue-500 mr-1" />{diffProse}</>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════ Alerts modal ═══════════════════════

export function AlertsModal({ canvasId, widgets, onClose }: {
  canvasId: string
  widgets: { id: string; title: string; chart_type: string }[]
  onClose: () => void
}) {
  const [alerts, setAlerts] = useState<AlertRuleItem[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    name: '', condition_text: '', widget_id: '', cadence_minutes: 60, channel: 'inapp', email: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await alertsApi.list(canvasId)
      setAlerts(r.data.alerts || [])
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [canvasId])

  useEffect(() => { load() }, [load])

  const create = async () => {
    if (!form.name.trim() || !form.condition_text.trim() || !form.widget_id) return
    setSaving(true)
    try {
      await alertsApi.create(canvasId, {
        name: form.name,
        condition_text: form.condition_text,
        widget_id: form.widget_id,
        cadence_minutes: form.cadence_minutes,
        channel: form.channel,
        email: form.channel === 'email' ? form.email : undefined,
      })
      setCreating(false)
      setForm({ name: '', condition_text: '', widget_id: '', cadence_minutes: 60, channel: 'inapp', email: '' })
      await load()
    } catch { alert('Failed to create alert') } finally { setSaving(false) }
  }

  const widgetTitle = (id: string | null) => widgets.find(w => w.id === id)?.title || '—'

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h2 className="font-bold text-gray-900 flex items-center gap-2"><Bell size={17} className="text-amber-500" /> Data alerts</h2>
          <div className="flex items-center gap-2">
            <button onClick={() => setCreating(v => !v)} className="flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700">
              <Plus size={13} /> New alert
            </button>
            <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600"><X size={17} /></button>
          </div>
        </div>

        {creating && (
          <div className="px-5 py-4 border-b border-gray-100 bg-blue-50/40 space-y-2.5">
            <div className="grid grid-cols-2 gap-2.5">
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Alert name" className="text-sm border border-gray-300 rounded-lg px-3 py-2" />
              <select value={form.widget_id} onChange={e => setForm(f => ({ ...f, widget_id: e.target.value }))}
                className="text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white">
                <option value="">Watch which widget?</option>
                {widgets.filter(w => w.chart_type !== 'text' && w.chart_type !== 'slicer').map(w => (
                  <option key={w.id} value={w.id}>{w.title}</option>
                ))}
              </select>
            </div>
            <input
              value={form.condition_text}
              onChange={e => setForm(f => ({ ...f, condition_text: e.target.value }))}
              placeholder='Describe the condition in plain language — e.g. "tell me if daily applications drop below 5" or "alert me when something unusual happens"'
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
            />
            <div className="flex items-center gap-2.5 flex-wrap">
              <select value={form.cadence_minutes} onChange={e => setForm(f => ({ ...f, cadence_minutes: Number(e.target.value) }))}
                className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 bg-white">
                <option value={15}>Check every 15 min</option>
                <option value={60}>Check hourly</option>
                <option value={360}>Check every 6 h</option>
                <option value={1440}>Check daily</option>
              </select>
              <select value={form.channel} onChange={e => setForm(f => ({ ...f, channel: e.target.value }))}
                className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 bg-white">
                <option value="inapp">Notify in-app</option>
                <option value="email">Notify by email</option>
              </select>
              {form.channel === 'email' && (
                <input value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  placeholder="you@company.com" className="text-xs border border-gray-300 rounded-lg px-2 py-1.5 flex-1 min-w-[180px]" />
              )}
              <button onClick={create} disabled={saving} className="ml-auto flex items-center gap-1.5 px-3.5 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50">
                {saving && <Loader2 size={11} className="animate-spin" />} Create alert
              </button>
            </div>
            <p className="text-[11px] text-gray-400">The AI parses your sentence into a rule (threshold or anomaly detection) and explains every trigger in plain language.</p>
          </div>
        )}

        <div className="flex-1 overflow-auto p-4 space-y-2.5">
          {loading ? (
            <div className="flex justify-center py-10 text-gray-400"><Loader2 size={18} className="animate-spin" /></div>
          ) : alerts.length === 0 ? (
            <div className="text-center py-10">
              <Bell size={26} className="text-gray-200 mx-auto mb-2" />
              <p className="text-sm text-gray-400">No alerts yet. Ask the agent to watch any chart — it checks on schedule and explains what changed when it fires.</p>
            </div>
          ) : alerts.map(a => (
            <div key={a.id} className={`border rounded-xl p-3.5 ${a.last_result?.triggered ? 'border-amber-300 bg-amber-50/50' : 'border-gray-200'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                    {a.last_result?.triggered && <AlertTriangle size={13} className="text-amber-500" />}
                    {a.name}
                    {!a.is_active && <span className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-400 rounded-full">paused</span>}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">&ldquo;{a.condition_text}&rdquo; · watching <strong>{widgetTitle(a.widget_id)}</strong> · every {a.cadence_minutes >= 60 ? `${Math.round(a.cadence_minutes / 60)}h` : `${a.cadence_minutes}m`} · {a.channel}</p>
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button
                    onClick={async () => { await alertsApi.update(a.id, { is_active: !a.is_active }); load() }}
                    className="p-1.5 text-gray-400 hover:text-amber-500 rounded" title={a.is_active ? 'Pause' : 'Resume'}
                  >
                    {a.is_active ? <BellOff size={13} /> : <Bell size={13} />}
                  </button>
                  <button
                    onClick={async () => { if (confirm('Delete this alert?')) { await alertsApi.remove(a.id); load() } }}
                    className="p-1.5 text-gray-400 hover:text-red-500 rounded" title="Delete"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              {a.last_result?.triggered && a.last_result.explanation && (
                <div className="mt-2 text-xs text-amber-800 bg-amber-100/60 border border-amber-200 rounded-lg px-2.5 py-2">
                  <Sparkles size={11} className="inline mr-1" />{a.last_result.explanation}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════ Explain popover ═══════════════════════

export function ExplainPopover({ canvasId, widgetId, column, value, onClose }: {
  canvasId: string
  widgetId: string
  column: string
  value: string
  onClose: () => void
}) {
  const [loading, setLoading] = useState(true)
  const [explanation, setExplanation] = useState('')
  const [breakdowns, setBreakdowns] = useState<{ dimension: string; rows: Record<string, unknown>[] }[]>([])

  useEffect(() => {
    let cancelled = false
    explainApi.explainPoint(canvasId, { widget_id: widgetId, column, value })
      .then(r => {
        if (cancelled) return
        setExplanation(r.data.explanation || 'No explanation available.')
        const ev = r.data.evidence as { breakdowns?: { dimension: string; rows: Record<string, unknown>[] }[] }
        setBreakdowns(ev?.breakdowns || [])
      })
      .catch(() => !cancelled && setExplanation('Analysis failed — the widget may not have re-runnable SQL.'))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [canvasId, widgetId, column, value])

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-5" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold text-gray-900 flex items-center gap-2">
            <Sparkles size={16} className="text-blue-600" /> Why {column} = {value}?
          </h2>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600"><X size={16} /></button>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 py-8 justify-center text-gray-400 text-sm">
            <Loader2 size={16} className="animate-spin" /> Running decomposition queries…
          </div>
        ) : (
          <>
            <p className="text-sm text-gray-700 leading-relaxed">{explanation}</p>
            {breakdowns.length > 0 && (
              <div className="mt-4 space-y-3 max-h-64 overflow-auto">
                {breakdowns.map(b => (
                  <div key={b.dimension}>
                    <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1">by {b.dimension}</p>
                    <div className="space-y-0.5">
                      {b.rows.slice(0, 5).map((r, i) => {
                        const vals = Object.values(r)
                        return (
                          <div key={i} className="flex justify-between text-xs bg-gray-50 rounded px-2 py-1">
                            <span className="text-gray-600 truncate">{String(vals[0] ?? '')}</span>
                            <span className="text-gray-900 font-semibold tabular-nums ml-2">{String(vals[1] ?? '')}</span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
