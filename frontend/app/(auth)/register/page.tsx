'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { authApi } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'

export default function RegisterPage() {
  const router = useRouter()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (password !== confirm) { setError('Passwords do not match'); return }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return }
    setLoading(true)
    try {
      const resp = await authApi.register({ email, password, full_name: fullName })
      const data = resp.data
      setAuth(
        { id: data.user_id, email: data.email, full_name: data.full_name },
        data.access_token,
        data.refresh_token,
      )
      router.push('/projects/new')
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
        <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          className="input-field" placeholder="Min. 8 characters" required />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Confirm password</label>
        <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
          className="input-field" placeholder="••••••••" required />
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
