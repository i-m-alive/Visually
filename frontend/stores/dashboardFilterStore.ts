import { create } from 'zustand'

export type FilterValue = string[] | { start: string; end: string }

interface DashboardFilterState {
  /** dashboardId → { columnName → active filter value } */
  filters: Record<string, Record<string, FilterValue>>

  setFilter: (dashboardId: string, column: string, value: FilterValue) => void
  clearFilter: (dashboardId: string, column: string) => void
  clearAll: (dashboardId: string) => void
  getFilters: (dashboardId: string) => Record<string, FilterValue>
  hasActiveFilters: (dashboardId: string) => boolean
}

export const useDashboardFilterStore = create<DashboardFilterState>((set, get) => ({
  filters: {},

  setFilter: (dashboardId, column, value) =>
    set((state) => ({
      filters: {
        ...state.filters,
        [dashboardId]: {
          ...(state.filters[dashboardId] || {}),
          [column]: value,
        },
      },
    })),

  clearFilter: (dashboardId, column) =>
    set((state) => {
      const current = { ...(state.filters[dashboardId] || {}) }
      delete current[column]
      return { filters: { ...state.filters, [dashboardId]: current } }
    }),

  clearAll: (dashboardId) =>
    set((state) => ({ filters: { ...state.filters, [dashboardId]: {} } })),

  getFilters: (dashboardId) => get().filters[dashboardId] || {},

  hasActiveFilters: (dashboardId) => {
    const f = get().filters[dashboardId] || {}
    return Object.values(f).some((v) => {
      if (Array.isArray(v)) return v.length > 0
      return !!(v as { start: string; end: string }).start
    })
  },
}))
