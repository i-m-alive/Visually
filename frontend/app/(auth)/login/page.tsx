'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { authApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'

export default function LoginPage() {
  const router = useRouter()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const resp = await authApi.login({ email, password })
      const data = resp.data
      const role: 'builder' | 'end_user' = data.role === 'end_user' ? 'end_user' : 'builder'
      setAuth(
        { id: data.user_id, email: data.email, full_name: data.full_name, role },
        data.access_token,
        data.refresh_token,
      )
      document.cookie = `visually-role=${role}; path=/; SameSite=Lax`
      // First-time login → show role onboarding
      const onboarded = localStorage.getItem(`visually-onboarded-${data.user_id}`)
      if (!onboarded) {
        router.push('/onboarding')
      } else {
        router.push(role === 'end_user' ? '/end-user/dashboard' : '/projects')
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: unknown } } }
      const detail = e.response?.data?.detail
      if (Array.isArray(detail)) {
        setError(detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(', ') || 'Login failed')
      } else {
        setError(typeof detail === 'string' ? detail : 'Login failed')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold font-display text-gray-900">Sign in</h2>

      {error && (
        <div className="bg-red-50 border border-red-100 text-red-700 text-sm p-3 rounded-lg">{error}</div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          className="input-field" placeholder="you@example.com" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          className="input-field" placeholder="••••••••" required />
      </div>

      <button type="submit" disabled={loading} className="btn-primary w-full">
        {loading ? 'Signing in...' : 'Sign in'}
      </button>

      <p className="text-center text-sm text-gray-500">
        No account?{' '}
        <Link href="/register" className="text-brand hover:underline font-medium">Create one</Link>
      </p>
    </form>
  )
}
