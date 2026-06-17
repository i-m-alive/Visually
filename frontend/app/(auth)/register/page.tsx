'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { authApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'
import { Layers, BarChart2 } from 'lucide-react'

export default function RegisterPage() {
  const router = useRouter()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [role, setRole] = useState<'builder' | 'end_user'>('builder')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (password !== confirm) { setError('Passwords do not match'); return }
    setLoading(true)
    try {
      const resp = await authApi.register({ email, username, password, full_name: fullName, role })
      const data = resp.data
      const resolvedRole: 'builder' | 'end_user' = data.role === 'end_user' ? 'end_user' : 'builder'
      setAuth(
        { id: data.user_id, email: data.email, username: data.username, full_name: data.full_name, role: resolvedRole },
        data.access_token,
        data.refresh_token,
      )
      document.cookie = `visually-role=${resolvedRole}; path=/; SameSite=Lax`
      // Mark as onboarded — they just selected their role during registration
      localStorage.setItem(`visually-onboarded-${data.user_id}`, '1')
      router.push(resolvedRole === 'end_user' ? '/end-user/dashboard' : '/projects')
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: unknown } } }
      const detail = e.response?.data?.detail
      if (Array.isArray(detail)) {
        setError(detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(', ') || 'Registration failed')
      } else {
        setError(typeof detail === 'string' ? detail : 'Registration failed')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold font-display text-gray-900">Create account</h2>

      {error && (
        <div className="bg-red-50 border border-red-100 text-red-700 text-sm p-3 rounded-lg">{error}</div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Full name</label>
        <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)}
          className="input-field" placeholder="Your name" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          className="input-field" placeholder="you@example.com" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
        <input type="text" value={username} onChange={(e) => setUsername(e.target.value)}
          className="input-field" placeholder="Choose a unique User ID" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          className="input-field" placeholder="Enter a password" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Confirm password</label>
        <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
          className="input-field" placeholder="••••••••" required />
      </div>

      {/* Role selection */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">I am a…</label>
        <div className="grid grid-cols-2 gap-3">
          <button
            type="button"
            onClick={() => setRole('builder')}
            className={`flex flex-col items-center gap-2 p-3 rounded-xl border-2 transition-colors text-left ${
              role === 'builder'
                ? 'border-brand bg-brand-light text-brand'
                : 'border-gray-200 text-gray-500 hover:border-gray-300'
            }`}
          >
            <Layers size={20} />
            <div>
              <p className="text-xs font-semibold">Builder</p>
              <p className="text-[10px] leading-tight opacity-70">Build reports & dashboards</p>
            </div>
          </button>
          <button
            type="button"
            onClick={() => setRole('end_user')}
            className={`flex flex-col items-center gap-2 p-3 rounded-xl border-2 transition-colors text-left ${
              role === 'end_user'
                ? 'border-brand bg-brand-light text-brand'
                : 'border-gray-200 text-gray-500 hover:border-gray-300'
            }`}
          >
            <BarChart2 size={20} />
            <div>
              <p className="text-xs font-semibold">Analyst</p>
              <p className="text-[10px] leading-tight opacity-70">View & analyse reports</p>
            </div>
          </button>
        </div>
      </div>

      <button type="submit" disabled={loading} className="btn-primary w-full">
        {loading ? 'Creating account...' : 'Create account'}
      </button>

      <p className="text-center text-sm text-gray-500">
        Already have an account?{' '}
        <Link href="/login" className="text-brand hover:underline font-medium">Sign in</Link>
      </p>
    </form>
  )
}
