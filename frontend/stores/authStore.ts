import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthUser {
  id: string
  email: string
  username: string
  full_name: string
  role: 'builder' | 'end_user'
}

interface AuthState {
  user: AuthUser | null
  accessToken: string | null
  refreshToken: string | null
  _hasHydrated: boolean
  setAuth: (user: AuthUser, accessToken: string, refreshToken: string) => void
  updateUser: (partial: Partial<AuthUser>) => void
  clearAuth: () => void
  setHasHydrated: (v: boolean) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      _hasHydrated: false,
      setAuth: (user, accessToken, refreshToken) => set({ user, accessToken, refreshToken }),
      updateUser: (partial) => set((s) => ({ user: s.user ? { ...s.user, ...partial } : s.user })),
      clearAuth: () => set({ user: null, accessToken: null, refreshToken: null }),
      setHasHydrated: (v) => set({ _hasHydrated: v }),
    }),
    {
      name: 'visually-auth',
      // Called once storage has been read and state restored — safe to route now
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      },
    }
  )
)
