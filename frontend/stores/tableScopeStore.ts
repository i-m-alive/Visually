import { create } from 'zustand'
import { projectApi } from '@/lib/api'

// Shared "table scope" state for a canvas — used by BOTH the canvas toolbar picker
// and the Canvas Assistant chat, so a selection made in one reflects in the other.
// Keyed by canvasId so multiple canvases don't bleed into each other, and the table
// list is fetched ONCE per canvas (on canvas load) rather than every time the chat opens.

export type ScopeMode = 'selected' | 'database'
export interface ScopeTable { name: string; columns: number }

export interface CanvasScope {
  scope: ScopeMode
  selectedTables: string[]
  selectedHops: number      // 0 = only picked, 1 = +1-hop, 2 = +2-hop
  tables: ScopeTable[]      // full table list for the picker
  loading: boolean
  loaded: boolean
  error: boolean
}

export const DEFAULT_SCOPE: CanvasScope = {
  scope: 'selected',
  selectedTables: [],
  selectedHops: 2,
  tables: [],
  loading: false,
  loaded: false,
  error: false,
}

interface TableScopeState {
  byCanvas: Record<string, CanvasScope>
  patch: (canvasId: string, p: Partial<CanvasScope>) => void
  setScope: (canvasId: string, scope: ScopeMode) => void
  setSelectedTables: (canvasId: string, tables: string[]) => void
  toggleTable: (canvasId: string, name: string) => void
  clearTables: (canvasId: string) => void
  setSelectedHops: (canvasId: string, hops: number) => void
  /** Fetch the lightweight table list once per canvas (no-op if loaded/loading). */
  loadTables: (canvasId: string, projectId: string, connectionId?: string, dashboardId?: string) => Promise<void>
}

export const useTableScopeStore = create<TableScopeState>((set, get) => ({
  byCanvas: {},

  patch: (canvasId, p) =>
    set((s) => ({
      byCanvas: {
        ...s.byCanvas,
        [canvasId]: { ...DEFAULT_SCOPE, ...s.byCanvas[canvasId], ...p },
      },
    })),

  setScope: (canvasId, scope) => get().patch(canvasId, { scope }),
  setSelectedTables: (canvasId, selectedTables) => get().patch(canvasId, { selectedTables }),
  toggleTable: (canvasId, name) => {
    const cur = get().byCanvas[canvasId]?.selectedTables ?? []
    const next = cur.includes(name) ? cur.filter((t) => t !== name) : [...cur, name]
    get().patch(canvasId, { selectedTables: next })
  },
  clearTables: (canvasId) => get().patch(canvasId, { selectedTables: [] }),
  setSelectedHops: (canvasId, hops) => get().patch(canvasId, { selectedHops: hops }),

  loadTables: async (canvasId, projectId, connectionId, dashboardId) => {
    const cur = get().byCanvas[canvasId]
    if (cur?.loaded || cur?.loading) return
    get().patch(canvasId, { loading: true, error: false })
    try {
      const resp = await projectApi.getSchemaTables(projectId, connectionId, dashboardId)
      const tables = (resp.data?.tables ?? []).filter((t) => t.name)
      get().patch(canvasId, { tables, loaded: true, loading: false })
    } catch {
      get().patch(canvasId, { loading: false, error: true })
    }
  },
}))
