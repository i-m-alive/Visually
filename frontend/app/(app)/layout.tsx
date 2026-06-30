'use client'
import { useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useAuthStore } from '@/stores/authStore'
import {
  Database, MessageSquare, Settings, LogOut, BarChart2,
  LayoutDashboard, Layers, Home, Link2, ChevronLeft, ChevronRight, TrendingUp,
} from 'lucide-react'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router   = useRouter()
  const pathname = usePathname()
  const { user, clearAuth, _hasHydrated } = useAuthStore()

  // Persist collapsed state in localStorage so it survives navigation
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    setCollapsed(localStorage.getItem('sidebar-collapsed') === 'true')
  }, [])

  const toggleCollapsed = () => {
    setCollapsed(prev => {
      const next = !prev
      localStorage.setItem('sidebar-collapsed', String(next))
      return next
    })
  }

  useEffect(() => {
    if (!_hasHydrated) return
    if (!user) { router.push('/login'); return }
    if (user.role === 'end_user' && pathname.startsWith('/projects')) {
      router.push('/end-user/dashboard')
    }
  }, [user, router, _hasHydrated, pathname])

  if (!_hasHydrated) return null
  if (!user) return null

  const projectIdMatch = pathname.match(/\/projects\/([^/]+)/)
  const projectId = projectIdMatch?.[1]

  const handleLogout = () => {
    // Clear store + persisted auth so the request interceptor (which reads the
    // token from localStorage on every call) can no longer authenticate.
    try {
      clearAuth()
      localStorage.removeItem('visually-auth')
    } catch { /* ignore */ }
    // Clear the role cookie (path must match the one set at login in login/page.tsx).
    document.cookie = 'visually-role=; path=/; max-age=0; SameSite=Lax'
    // HARD navigation (not router.push): fully tears down in-memory state — the
    // zustand singleton, the axios refresh single-flight, and any page/SWR caches —
    // so no stale session or another user's cached data survives into the next login.
    // Mirrors forceReLogin() in lib/api.ts, which is why the 401-path logout is reliable.
    window.location.href = '/login'
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

  const initials = user.full_name?.[0]?.toUpperCase() ?? '?'

  return (
    <div className="flex h-screen bg-gray-50">
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside
        style={{ width: collapsed ? 56 : 224, transition: 'width 0.2s ease', flexShrink: 0 }}
        className="bg-white border-r border-gray-100 flex flex-col relative overflow-hidden"
      >
        {/* Logo + toggle */}
        <div className="flex items-center border-b border-gray-100 flex-shrink-0"
          style={{ height: 52, padding: collapsed ? '0 8px' : '0 12px', justifyContent: collapsed ? 'center' : 'space-between' }}>
          {!collapsed && (
            <Link
              href={isBuilder ? '/projects' : '/end-user/dashboard'}
              className="text-xl font-bold text-brand font-display truncate"
            >
              Visually
            </Link>
          )}
          <button
            onClick={toggleCollapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors flex-shrink-0"
          >
            {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto overflow-x-hidden" style={{ padding: collapsed ? '10px 6px' : '10px 8px' }}>
          <div className="space-y-0.5">
            {isBuilder ? (
              <>
                {projectId && (
                  <>
                    <NavLink href={`/projects/${projectId}/dashboard`} icon={<LayoutDashboard size={17} />} label="Dashboards"  current={pathname} collapsed={collapsed} />
                    <NavLink href={`/projects/${projectId}/canvas`}    icon={<Layers size={17} />}          label="Canvas"      current={pathname} collapsed={collapsed} />
                    <NavLink href={`/projects/${projectId}/query`}     icon={<MessageSquare size={17} />}   label="Query"       current={pathname} collapsed={collapsed} />
                    <NavLink href={`/projects/${projectId}/schema`}    icon={<Database size={17} />}        label="Schema"      current={pathname} collapsed={collapsed} />
                    <NavLink href={`/projects/${projectId}/connection`}icon={<Link2 size={17} />}           label="Connection"  current={pathname} collapsed={collapsed} />
                    <div style={{ height: 1, background: '#f1f5f9', margin: '6px 0' }} />
                  </>
                )}
                <NavLink href="/projects" icon={<BarChart2 size={17} />} label="Projects"  current={pathname} collapsed={collapsed} />
                <NavLink href="/settings" icon={<Settings size={17} />}  label="Settings"  current={pathname} collapsed={collapsed} />
              </>
            ) : (
              <>
                <NavLink href="/end-user/dashboard" icon={<Home size={17} />}        label="My Dashboard" current={pathname} collapsed={collapsed} />
                {/* <NavLink href="/end-user/query"     icon={<TrendingUp size={17} />}  label="Query Chat"   current={pathname} collapsed={collapsed} /> */}
                <NavLink href="/settings"           icon={<Settings size={17} />}    label="Settings"     current={pathname} collapsed={collapsed} />
              </>
            )}
          </div>
        </nav>

        {/* User profile */}
        <div className="border-t border-gray-100 flex-shrink-0" style={{ padding: collapsed ? '10px 6px' : '10px 10px' }}>
          {collapsed ? (
            // Collapsed: show only avatar with tooltip
            <div className="flex justify-center">
              <div
                title={`${user.full_name} (${user.email})`}
                className="w-8 h-8 rounded-full bg-brand text-white text-sm flex items-center justify-center font-semibold cursor-default select-none"
              >
                {initials}
              </div>
            </div>
          ) : (
            // Expanded: full profile block
            <>
              <div className="flex items-center gap-2 mb-2">
                <div className="w-8 h-8 rounded-full bg-brand text-white text-sm flex items-center justify-center font-semibold flex-shrink-0">
                  {initials}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{user.full_name}</p>
                </div>
              </div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                  isBuilder ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'
                }`}>
                  {isBuilder ? 'Builder' : 'Analyst'}
                </span>
              </div>
            </>
          )}
          <button
            onClick={handleLogout}
            title="Sign out"
            className={`flex items-center text-xs text-gray-500 hover:text-red-600 w-full rounded hover:bg-red-50 transition-colors ${
              collapsed ? 'justify-center py-2' : 'gap-2 px-2 py-1.5 mt-0.5'
            }`}
          >
            <LogOut size={13} />
            {!collapsed && 'Sign out'}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {children}
      </div>
    </div>
  )
}

function NavLink({
  href, icon, label, current, collapsed,
}: {
  href: string
  icon: React.ReactNode
  label: string
  current: string
  collapsed: boolean
}) {
  const isActive = current === href || current.startsWith(href + '/')
  return (
    <Link
      href={href}
      title={collapsed ? label : undefined}
      className={`relative group flex items-center rounded-lg text-sm transition-colors ${
        isActive
          ? 'bg-blue-50 text-blue-700 font-medium'
          : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
      } ${collapsed ? 'justify-center p-2.5' : 'gap-2.5 px-3 py-2'}`}
    >
      <span className="flex-shrink-0">{icon}</span>
      {!collapsed && <span className="truncate">{label}</span>}

      {/* Floating tooltip shown in collapsed mode */}
      {collapsed && (
        <span
          className="pointer-events-none absolute left-full ml-2.5 px-2.5 py-1 text-xs font-medium text-white rounded-md whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity z-50"
          style={{ background: '#1e293b', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}
        >
          {label}
          {/* Arrow */}
          <span
            className="absolute top-1/2 right-full -translate-y-1/2"
            style={{ borderWidth: '4px', borderStyle: 'solid', borderColor: 'transparent #1e293b transparent transparent' }}
          />
        </span>
      )}
    </Link>
  )
}
