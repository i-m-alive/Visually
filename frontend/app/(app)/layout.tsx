'use client'
import { useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useAuthStore } from '@/stores/authStore'
import {
  Database, MessageSquare, Settings, LogOut, BarChart2,
  LayoutDashboard, Camera, Layers, Home, Link2,
} from 'lucide-react'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const { user, clearAuth, _hasHydrated } = useAuthStore()

  useEffect(() => {
    if (!_hasHydrated) return
    if (!user) { router.push('/login'); return }
    // Redirect end_user away from builder-only routes
    if (user.role === 'end_user' && pathname.startsWith('/projects')) {
      router.push('/end-user/dashboard')
    }
  }, [user, router, _hasHydrated, pathname])

  if (!_hasHydrated) return null
  if (!user) return null

  const projectIdMatch = pathname.match(/\/projects\/([^/]+)/)
  const projectId = projectIdMatch?.[1]

  const handleLogout = () => {
    clearAuth()
    document.cookie = 'visually-role=; path=/; max-age=0'
    router.push('/login')
  }

  const isBuilder = user.role !== 'end_user'

  // Intelligence pages get a full-canvas layout — no sidebar
  if (pathname.includes('/intelligence/')) {
    return (
      <div className="flex h-screen bg-gray-50">
        <div className="flex-1 flex flex-col min-w-0">{children}</div>
      </div>
    )
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-100 flex flex-col">
        <div className="px-4 py-4 border-b border-gray-100">
          <Link
            href={isBuilder ? '/projects' : '/end-user/dashboard'}
            className="text-xl font-bold text-brand font-display"
          >
            Visually
          </Link>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {isBuilder ? (
            // ── Builder nav ──────────────────────────────────────────
            <>
              {projectId && (
                <>
                  <NavLink href={`/projects/${projectId}/dashboard`}  icon={<LayoutDashboard size={16} />} label="Dashboards"   current={pathname} />
                  <NavLink href={`/projects/${projectId}/canvas`}     icon={<Layers size={16} />}          label="Canvas"       current={pathname} />
                  <NavLink href={`/projects/${projectId}/query`}      icon={<MessageSquare size={16} />}   label="Query"        current={pathname} />
                  <NavLink href={`/projects/${projectId}/schema`}      icon={<Database size={16} />}        label="Schema"       current={pathname} />
                  <NavLink href={`/projects/${projectId}/screenshot`}  icon={<Camera size={16} />}          label="Screenshots"  current={pathname} />
                  <NavLink href={`/projects/${projectId}/connection`}  icon={<Link2 size={16} />}           label="Connection"   current={pathname} />
                </>
              )}
              <NavLink href="/projects"    icon={<BarChart2 size={16} />} label="Projects"     current={pathname} />
              <NavLink href="/settings"    icon={<Settings size={16} />}  label="Settings"     current={pathname} />
            </>
          ) : (
            // ── End-user nav ─────────────────────────────────────────
            <>
              <NavLink href="/end-user/dashboard" icon={<Home size={16} />}          label="My Dashboard"   current={pathname} />
              <NavLink href="/settings"           icon={<Settings size={16} />}      label="Settings"       current={pathname} />
            </>
          )}
        </nav>

        <div className="p-3 border-t border-gray-100">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-8 h-8 rounded-full bg-brand text-white text-sm flex items-center justify-center font-semibold">
              {user.full_name[0].toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-900 truncate">{user.full_name}</p>
              <p className="text-xs text-gray-500 truncate">{user.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 mb-1">
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
              isBuilder ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
            }`}>
              {isBuilder ? 'Builder' : 'Analyst'}
            </span>
          </div>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 text-xs text-gray-500 hover:text-red-600 w-full px-2 py-1 rounded hover:bg-red-50 transition-colors"
          >
            <LogOut size={12} /> Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {children}
      </div>
    </div>
  )
}

function NavLink({
  href, icon, label, current,
}: { href: string; icon: React.ReactNode; label: string; current: string }) {
  const isActive = current === href || current.startsWith(href + '/')
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
        isActive ? 'bg-brand-light text-brand font-medium' : 'text-gray-600 hover:bg-gray-50'
      }`}
    >
      {icon}
      {label}
    </Link>
  )
}
