'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Layers, BarChart2, ArrowRight, Loader2, Sparkles } from 'lucide-react'
import { authApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'

export default function OnboardingPage() {
  const router = useRouter()
  const { user, updateUser } = useAuthStore()
  const [role, setRole] = useState<'builder' | 'end_user'>(user?.role ?? 'builder')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleConfirm = async () => {
    setSaving(true); setError('')
    try {
      await authApi.updateMe({ role })
      updateUser({ role })
      document.cookie = `visually-role=${role}; path=/; SameSite=Lax`
      // Mark this user as onboarded so we don't show this again
      if (user?.id) localStorage.setItem(`visually-onboarded-${user.id}`, '1')
      router.push(role === 'end_user' ? '/end-user/dashboard' : '/projects')
    } catch {
      setError('Failed to save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-blue-950 to-indigo-900 px-4">
      <style>{`
        @keyframes floatUp { from{opacity:0;transform:translateY(28px)} to{opacity:1;transform:translateY(0)} }
        @keyframes popIn   { 0%{transform:scale(.88);opacity:0} 65%{transform:scale(1.04)} 100%{transform:scale(1);opacity:1} }
        .tile-enter { animation: popIn .35s cubic-bezier(.34,1.56,.64,1) both; }
        .page-enter { animation: floatUp .5s cubic-bezier(.21,1.02,.73,1) both; }
      `}</style>

      <div className="page-enter w-full max-w-md">
        {/* Logo / brand */}
        <div className="flex items-center justify-center gap-2.5 mb-8">
          <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg,#2563EB,#7C3AED)' }}>
            <Sparkles size={16} className="text-white" />
          </div>
          <span className="text-xl font-bold text-white tracking-tight">Visually</span>
        </div>

        <div className="bg-white rounded-3xl shadow-2xl p-8">
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-gray-900">Welcome{user?.full_name ? `, ${user.full_name.split(' ')[0]}` : ''}!</h1>
            <p className="text-gray-500 mt-2 text-sm leading-relaxed">
              How will you be using Visually? You can change this later in Settings.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4 mb-6">
            <RoleTile
              selected={role === 'builder'}
              onClick={() => setRole('builder')}
              delay="0ms"
              icon={<Layers size={28} />}
              title="Builder"
              subtitle="Create reports, connect data sources, build dashboards"
              gradient="from-blue-500 to-indigo-600"
            />
            <RoleTile
              selected={role === 'end_user'}
              onClick={() => setRole('end_user')}
              delay="80ms"
              icon={<BarChart2 size={28} />}
              title="Analyst"
              subtitle="View reports, explore insights, monitor KPIs"
              gradient="from-violet-500 to-purple-600"
            />
          </div>

          {error && (
            <p className="text-sm text-red-600 text-center mb-4">{error}</p>
          )}

          <button
            onClick={handleConfirm}
            disabled={saving}
            className="w-full flex items-center justify-center gap-2 py-3 text-sm font-semibold text-white rounded-xl transition-all disabled:opacity-60"
            style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
            onMouseEnter={e => { const el = e.currentTarget as HTMLElement; el.style.filter='brightness(1.1)'; el.style.transform='translateY(-1px)' }}
            onMouseLeave={e => { const el = e.currentTarget as HTMLElement; el.style.filter=''; el.style.transform='' }}
          >
            {saving
              ? <><Loader2 size={15} className="animate-spin" /> Setting up your workspace…</>
              : <>{role === 'builder' ? 'Start building' : 'Start exploring'} <ArrowRight size={15} /></>
            }
          </button>
        </div>

        <p className="text-center text-xs text-white/40 mt-6">
          You can switch roles any time from Settings → Profile.
        </p>
      </div>
    </div>
  )
}

function RoleTile({ selected, onClick, delay, icon, title, subtitle, gradient }: {
  selected: boolean; onClick: () => void; delay: string
  icon: React.ReactNode; title: string; subtitle: string; gradient: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`tile-enter relative flex flex-col items-center text-center gap-3 p-5 rounded-2xl border-2 transition-all focus:outline-none ${
        selected
          ? 'border-blue-500 shadow-lg shadow-blue-100'
          : 'border-gray-200 hover:border-gray-300 hover:shadow-sm'
      }`}
      style={{ animationDelay: delay }}
    >
      {/* Icon area */}
      <div className={`w-14 h-14 rounded-2xl bg-gradient-to-br ${gradient} flex items-center justify-center text-white shadow-md transition-transform ${selected ? 'scale-110' : 'scale-100'}`}
        style={{ transition: 'transform .2s cubic-bezier(.34,1.56,.64,1)' }}
      >
        {icon}
      </div>

      <div>
        <p className={`text-sm font-bold ${selected ? 'text-blue-700' : 'text-gray-800'}`}>{title}</p>
        <p className="text-[11px] text-gray-500 leading-tight mt-0.5">{subtitle}</p>
      </div>

      {/* Selected ring */}
      {selected && (
        <div className="absolute inset-0 rounded-2xl pointer-events-none" style={{ background: 'linear-gradient(135deg,rgba(37,99,235,0.06),rgba(124,58,237,0.06))' }} />
      )}
      {selected && (
        <div className="absolute top-2.5 right-2.5 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center" style={{ animation: 'popIn .2s ease both' }}>
          <svg width="8" height="8" viewBox="0 0 8 8" fill="none"><path d="M1.5 4l1.8 1.8L6.5 2.5" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
        </div>
      )}
    </button>
  )
}
