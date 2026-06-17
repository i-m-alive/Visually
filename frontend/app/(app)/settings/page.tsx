'use client'
import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/stores/authStore'
import { authApi } from '@/lib/api'
import { User, Bell, Key, Shield, Check, Layers, BarChart2, Loader2, AlertCircle } from 'lucide-react'

type Tab = 'profile' | 'notifications' | 'api' | 'security'

export default function SettingsPage() {
  const router = useRouter()
  const { user, updateUser } = useAuthStore()
  const [tab, setTab] = useState<Tab>('profile')

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'profile',       label: 'Profile',       icon: <User size={15} /> },
    { id: 'notifications', label: 'Notifications', icon: <Bell size={15} /> },
    { id: 'api',           label: 'API Keys',      icon: <Key size={15} /> },
    { id: 'security',      label: 'Security',      icon: <Shield size={15} /> },
  ]

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <h2 className="text-lg font-semibold font-display text-gray-900">Settings</h2>
        <p className="text-sm text-gray-500 mt-0.5">Manage your account and preferences</p>
      </div>

      <div className="flex flex-1 min-h-0">
        <nav className="w-48 border-r border-gray-100 bg-white p-3 space-y-0.5 flex-shrink-0">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                tab === t.id ? 'bg-brand-light text-brand font-medium' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {t.icon}{t.label}
            </button>
          ))}
        </nav>

        <div className="flex-1 overflow-auto p-8">
          <div className="max-w-xl">
            {tab === 'profile' && <ProfileTab user={user} updateUser={updateUser} router={router} />}
            {tab === 'notifications' && <NotificationsTab />}
            {tab === 'api' && <ApiKeysTab />}
            {tab === 'security' && <SecurityTab />}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Profile Tab ──────────────────────────────────────────────────────────────
function ProfileTab({ user, updateUser, router }: any) {
  const nameRef = useRef<HTMLInputElement>(null)
  const [selectedRole, setSelectedRole] = useState<'builder' | 'end_user'>(user?.role ?? 'builder')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const roleChanged = selectedRole !== user?.role

  const handleSave = async () => {
    setSaving(true); setError('')
    const newName = nameRef.current?.value.trim() || user?.full_name
    try {
      await authApi.updateMe({
        full_name: newName,
        role: selectedRole,
      })
      updateUser({ full_name: newName, role: selectedRole })
      // Update cookie so middleware sees new role immediately
      document.cookie = `visually-role=${selectedRole}; path=/; SameSite=Lax`
      setSaved(true); setTimeout(() => setSaved(false), 2500)
      // If role changed, redirect to the right home after a moment
      if (roleChanged) {
        setTimeout(() => router.push(selectedRole === 'end_user' ? '/end-user/dashboard' : '/projects'), 800)
      }
    } catch {
      setError('Failed to save changes. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <h3 className="text-base font-semibold text-gray-900">Profile</h3>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Full name</label>
          <input ref={nameRef} className="input-field" defaultValue={user?.full_name ?? ''} placeholder="Your name" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
          <input className="input-field" defaultValue={user?.email ?? ''} type="email" readOnly
            style={{ background: '#F9FAFB', cursor: 'not-allowed' }} />
          <p className="text-xs text-gray-400 mt-1">Email cannot be changed.</p>
        </div>

        {/* Role switcher */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">Role</label>
          <p className="text-xs text-gray-500 mb-3">Switch your role to change what you can access.</p>
          <div className="grid grid-cols-2 gap-3">
            <RoleTile
              active={selectedRole === 'builder'}
              onClick={() => setSelectedRole('builder')}
              icon={<Layers size={22} />}
              title="Builder"
              desc="Build reports & dashboards"
              current={user?.role === 'builder'}
            />
            <RoleTile
              active={selectedRole === 'end_user'}
              onClick={() => setSelectedRole('end_user')}
              icon={<BarChart2 size={22} />}
              title="Analyst"
              desc="View & explore reports"
              current={user?.role === 'end_user'}
            />
          </div>
          {roleChanged && (
            <p className="text-xs text-amber-600 mt-2 flex items-center gap-1">
              <AlertCircle size={11} /> Saving will switch you to the {selectedRole === 'builder' ? 'Builder' : 'Analyst'} experience.
            </p>
          )}
        </div>

        {error && <p className="text-sm text-red-600 flex items-center gap-1.5"><AlertCircle size={13} />{error}</p>}

        <button onClick={handleSave} disabled={saving} className="btn-primary flex items-center gap-2 disabled:opacity-60">
          {saving ? <><Loader2 size={14} className="animate-spin" /> Saving…</> : saved ? <><Check size={14} /> Saved!</> : 'Save changes'}
        </button>
      </div>
    </div>
  )
}

function RoleTile({ active, onClick, icon, title, desc, current }: {
  active: boolean; onClick: () => void
  icon: React.ReactNode; title: string; desc: string; current: boolean
}) {
  return (
    <button type="button" onClick={onClick}
      className={`relative flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all focus:outline-none ${
        active ? 'border-blue-500 bg-blue-50/60' : 'border-gray-200 hover:border-gray-300 bg-white'
      }`}
    >
      <div className={`p-2 rounded-lg flex-shrink-0 transition-colors ${active ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-500'}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-semibold ${active ? 'text-blue-700' : 'text-gray-800'}`}>{title}</p>
        <p className="text-[11px] text-gray-500 mt-0.5 leading-tight">{desc}</p>
        {current && (
          <span className="inline-block mt-1 text-[10px] font-medium text-green-600 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded-full">Current</span>
        )}
      </div>
      {active && (
        <div className="absolute top-2 right-2 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center">
          <svg width="8" height="8" viewBox="0 0 8 8" fill="none"><path d="M1.5 4l1.8 1.8L6.5 2.5" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </div>
      )}
    </button>
  )
}

// ─── Notifications Tab ────────────────────────────────────────────────────────
function NotificationsTab() {
  const [saved, setSaved] = useState(false)
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold text-gray-900 mb-4">Notifications</h3>
      {[
        { label: 'Scheduled refresh completed', desc: 'Notify when a data refresh finishes' },
        { label: 'Refresh failed',              desc: 'Alert when a scheduled refresh fails' },
        { label: 'Canvas shared with you',      desc: 'When someone shares a canvas with you' },
        { label: 'Anomaly detected',            desc: 'When an AI anomaly alert fires on your reports' },
      ].map(item => (
        <div key={item.label} className="flex items-center justify-between p-4 rounded-xl border border-gray-100 bg-white">
          <div>
            <p className="text-sm font-medium text-gray-900">{item.label}</p>
            <p className="text-xs text-gray-500">{item.desc}</p>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" defaultChecked className="sr-only peer" />
            <div className="w-9 h-5 bg-gray-200 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-brand" />
          </label>
        </div>
      ))}
      <button onClick={() => { setSaved(true); setTimeout(() => setSaved(false), 2000) }} className="btn-primary flex items-center gap-2">
        {saved ? <><Check size={14} /> Saved!</> : 'Save preferences'}
      </button>
    </div>
  )
}

// ─── API Keys Tab ─────────────────────────────────────────────────────────────
function ApiKeysTab() {
  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold text-gray-900 mb-1">API Keys</h3>
      <p className="text-sm text-gray-500 mb-4">Use API keys to access Visually programmatically.</p>
      <div className="p-4 rounded-xl border border-dashed border-gray-200 bg-gray-50 text-center">
        <Key size={24} className="mx-auto text-gray-300 mb-2" />
        <p className="text-sm text-gray-500">No API keys yet.</p>
        <button className="btn-secondary mt-3 text-sm mx-auto flex items-center gap-2">
          <Key size={13} /> Generate API key
        </button>
      </div>
    </div>
  )
}

// ─── Security Tab ─────────────────────────────────────────────────────────────
function SecurityTab() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setError(null)
    if (!current || !next || !confirm) { setError('Please fill in all fields.'); return }
    if (next.length < 8) { setError('New password must be at least 8 characters.'); return }
    if (next !== confirm) { setError('New password and confirmation do not match.'); return }
    if (next === current) { setError('New password must be different from your current password.'); return }

    setSaving(true)
    try {
      await authApi.changePassword({ current_password: current, new_password: next })
      setSaved(true)
      setCurrent(''); setNext(''); setConfirm('')
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not update password. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <h3 className="text-base font-semibold text-gray-900 mb-4">Security</h3>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Current password</label>
          <input type="password" className="input-field" placeholder="••••••••"
            value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">New password</label>
          <input type="password" className="input-field" placeholder="Min. 8 characters"
            value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Confirm new password</label>
          <input type="password" className="input-field" placeholder="••••••••"
            value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password"
            onKeyDown={e => { if (e.key === 'Enter') submit() }} />
        </div>
        {error && (
          <div className="flex items-center gap-2 text-sm text-red-600">
            <AlertCircle size={14} /> {error}
          </div>
        )}
        <button onClick={submit} disabled={saving} className="btn-primary flex items-center gap-2 disabled:opacity-60">
          {saving ? <><Loader2 size={14} className="animate-spin" /> Updating…</>
            : saved ? <><Check size={14} /> Updated!</>
            : 'Update password'}
        </button>
      </div>
    </div>
  )
}
