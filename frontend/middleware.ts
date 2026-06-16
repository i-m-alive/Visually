import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

// Routes only builders can access
const BUILDER_ONLY = [
  '/projects',
  '/projects/new',
]

// Routes only end_users can access
const END_USER_ONLY = ['/end-user']

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl

  // Read the persisted auth state from the visually-auth cookie/localStorage
  // Next.js middleware can only read cookies, not localStorage.
  // We'll store role in a cookie set at login time.
  const roleCookie = req.cookies.get('visually-role')?.value ?? ''

  // Public routes — always allow
  if (
    pathname.startsWith('/login') ||
    pathname.startsWith('/register') ||
    pathname.startsWith('/share') ||
    pathname.startsWith('/embed') ||
    pathname.startsWith('/_next') ||
    pathname.startsWith('/api') ||
    pathname === '/'
  ) {
    return NextResponse.next()
  }

  // If no role cookie, let the client-side layout handle the redirect
  if (!roleCookie) return NextResponse.next()

  // end_user tries to access builder routes
  if (roleCookie === 'end_user') {
    const isBuilderRoute = BUILDER_ONLY.some(p => pathname === p || pathname.startsWith(p + '/'))
    if (isBuilderRoute) {
      return NextResponse.redirect(new URL('/end-user/dashboard', req.url))
    }
  }

  // builder tries to access end_user routes
  if (roleCookie === 'builder') {
    const isEndUserRoute = END_USER_ONLY.some(p => pathname === p || pathname.startsWith(p + '/'))
    if (isEndUserRoute) {
      return NextResponse.redirect(new URL('/projects', req.url))
    }
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
