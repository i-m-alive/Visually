import axios from 'axios'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'

export const api = axios.create({ baseURL: API_URL })

api.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    try {
      const stored = localStorage.getItem('visually-auth')
      if (stored) {
        const parsed = JSON.parse(stored)
        const token = parsed?.state?.accessToken
        if (token) config.headers.Authorization = `Bearer ${token}`
      }
    } catch {}
  }
  return config
})

export const authApi = {
  register: (data: { email: string; password: string; full_name: string; role?: string }) =>
    api.post('/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post('/auth/login', data),
  refresh: (data: { refresh_token: string }) =>
    api.post('/auth/refresh', data),
  me: () => api.get('/auth/me'),
  updateMe: (data: { full_name?: string; role?: string }) =>
    api.patch('/auth/me', data),
}

export const projectApi = {
  create: (data: { name: string; description?: string }) =>
    api.post('/projects', data),
  list: () => api.get('/projects'),
  get: (id: string) => api.get(`/projects/${id}`),
  delete: (id: string) => api.delete(`/projects/${id}`),
  listConnections: (projectId: string) =>
    api.get(`/projects/${projectId}/connections`),
  addConnection: (projectId: string, data: Record<string, unknown>) =>
    api.post(`/projects/${projectId}/connections`, data),
  updateConnection: (projectId: string, connId: string, data: Record<string, unknown>) =>
    api.patch(`/projects/${projectId}/connections/${connId}`, data),
  testConnection: (projectId: string, connId: string) =>
    api.post(`/projects/${projectId}/connections/${connId}/test`),
  triggerCrawl: (projectId: string) =>
    api.post(`/projects/${projectId}/schema/crawl`),
  getCrawlStatus: (projectId: string, jobId: string) =>
    api.get(`/projects/${projectId}/schema/crawl/${jobId}`),
  getSchema: (projectId: string) =>
    api.get(`/projects/${projectId}/schema`),
  getSchemaMetadata: (projectId: string) =>
    api.get(`/projects/${projectId}/schema/metadata`),
}

export const agentApi = {
  submitIntent: (data: { text: string; project_id: string; connection_id?: string }) =>
    api.post('/agent/intent', data),
  getJob: (jobId: string) =>
    api.get(`/agent/jobs/${jobId}`),
}

export const chatApi = {
  send: (data: {
    session_id?: string
    message: string
    project_id: string
    dashboard_id?: string
    connection_id?: string
    active_page_id?: string
  }) => api.post('/agent/chat', data),
  clear: (sessionId: string) =>
    api.delete(`/agent/chat/${sessionId}`),
}

export const screenshotApi = {
  upload: (data: {
    projectId: string
    files: File[]
    connectionId?: string
    /** "db" (default) or "csv" */
    mode?: 'db' | 'csv'
    /** Table names to use as a hint — serialized as JSON string in form data */
    userTableHints?: string[]
    /** CSV data files (CSV mode only) */
    csvFiles?: File[]
    /** Free-text description of the screenshot (Mode 3 — Guided Replication) */
    userContext?: string
    /** Context document files — PDF, DOCX, PPTX, TXT (Mode 3) */
    contextFiles?: File[]
    /** Power BI Template file (.pbit) — provides ground-truth field bindings */
    pbitFile?: File
    /** Per-table column selections — [{table, dimension, metric, date, group_by}] */
    userColumnHints?: Array<{
      table: string
      dimension?: string
      metric?: string
      date?: string
      group_by?: string
    }>
  }) => {
    const form = new FormData()
    form.append('project_id', data.projectId)
    if (data.connectionId) form.append('connection_id', data.connectionId)
    form.append('mode', data.mode ?? 'db')
    if (data.userTableHints?.length)
      form.append('user_table_hints', JSON.stringify(data.userTableHints))
    if (data.userContext?.trim())
      form.append('user_context', data.userContext.trim())
    data.files.forEach((f) => form.append('files', f))
    data.csvFiles?.forEach((f) => form.append('csv_files', f))
    data.contextFiles?.forEach((f) => form.append('context_files', f))
    if (data.pbitFile) form.append('pbit_file', data.pbitFile)
    if (data.userColumnHints?.length)
      form.append('user_column_hints', JSON.stringify(data.userColumnHints))
    return api.post('/screenshot/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getJob: (jobId: string) => api.get(`/screenshot/jobs/${jobId}`),
  submitHint: (jobId: string, data: { hint_id: string; response: string }) =>
    api.post(`/screenshot/jobs/${jobId}/hint`, data),
}

export const exportApi = {
  trigger: (data: {
    dashboard_id: string
    project_id: string
    export_type: 'html' | 'pdf' | 'png'
    theme: string
    include_chat: boolean
    token_expiry_days: number
  }) => api.post('/export/trigger', data),
  getJob: (jobId: string) => api.get(`/export/jobs/${jobId}`),
  downloadUrl: (jobId: string) => `${API_URL}/export/jobs/${jobId}/download`,
}

export const dashboardApi = {
  listAll: () => api.get('/dashboards/all'),
  sharedWithMe: () => api.get('/dashboards/shared-with-me'),
  list: (projectId: string) => api.get(`/dashboards?project_id=${projectId}`),
  get: (dashboardId: string) => api.get(`/dashboards/${dashboardId}`),
  updateTheme: (dashboardId: string, theme: string) =>
    api.patch(`/dashboards/${dashboardId}`, { theme }),
  rename: (dashboardId: string, name: string) =>
    api.patch(`/dashboards/${dashboardId}`, { name }),
  delete: (dashboardId: string) => api.delete(`/dashboards/${dashboardId}`),
  duplicate: (dashboardId: string) => api.post(`/dashboards/${dashboardId}/duplicate`),
  requery: (
    dashboardId: string,
    filters: Record<string, string[] | { start: string; end: string }>,
  ) => api.post(`/dashboards/${dashboardId}/requery`, { filters }),
  updateFilterConfig: (dashboardId: string, filterConfig: unknown[]) =>
    api.patch(`/dashboards/${dashboardId}/filter-config`, { filter_config: filterConfig }),
}

export interface LayoutItem {
  widget_id: string
  x: number
  y: number
  w: number
  h: number
}

export interface WidgetPatch {
  title?: string
  chart_type?: string
  config?: Record<string, unknown>
}

export interface WidgetCreate {
  title: string
  chart_type?: string
  sql_query?: string
  chart_data?: Record<string, unknown>
  config?: Record<string, unknown>
  width?: number
  height?: number
  position_x?: number
  position_y?: number
  connection_id?: string
}

export const canvasApi = {
  create: (data: { project_id: string; name: string; description?: string }) =>
    api.post('/dashboards', data),
  list: (projectId: string) => api.get(`/dashboards?project_id=${projectId}`),
  get: (canvasId: string) => api.get(`/dashboards/${canvasId}`),
  delete: (canvasId: string) => api.delete(`/dashboards/${canvasId}`),
  rename: (canvasId: string, name: string) =>
    api.patch(`/dashboards/${canvasId}`, { name }),
  updateTheme: (canvasId: string, theme: string) =>
    api.patch(`/dashboards/${canvasId}`, { theme }),
  updateLayout: (canvasId: string, items: LayoutItem[]) =>
    api.patch(`/dashboards/${canvasId}/layout`, { items }),
  updateLayoutConfig: (canvasId: string, layoutConfig: Record<string, unknown>) =>
    api.patch(`/dashboards/${canvasId}`, { layout_config: layoutConfig }),
  addWidget: (canvasId: string, widget: WidgetCreate) =>
    api.post(`/dashboards/${canvasId}/widgets`, widget),
  requery: (
    canvasId: string,
    filters: Record<string, string[] | { start: string; end: string }>,
  ) => api.post(`/dashboards/${canvasId}/requery`, { filters }),
}

export const widgetApi = {
  update: (widgetId: string, data: WidgetPatch) =>
    api.patch(`/widgets/${widgetId}`, data),
  delete: (widgetId: string) =>
    api.delete(`/widgets/${widgetId}`),
}

// ─── AI Insights (end-user) ───────────────────────────────────────────────────

export const aiInsightsApi = {
  summary:   (dashboardId: string) =>
    api.post<{ summary: string }>(`/dashboards/${dashboardId}/ai-summary`),
  insight:   (dashboardId: string, widgetId: string) =>
    api.post<{ insight: string; widget_id: string }>(`/dashboards/${dashboardId}/ai-insight`, { widget_id: widgetId }),
  anomalies: (dashboardId: string) =>
    api.post<{ anomalies: { widget_id: string; severity: string; message: string }[] }>(`/dashboards/${dashboardId}/ai-anomalies`),
}

// ─── .vly export / import ─────────────────────────────────────────────────────

export const vlyApi = {
  /** Triggers a browser download of the .vly archive for a canvas. */
  exportVly: (canvasId: string): void => {
    const stored = typeof window !== 'undefined' ? localStorage.getItem('visually-auth') : null
    let token = ''
    if (stored) {
      try { token = JSON.parse(stored)?.state?.accessToken ?? '' } catch { /* ignore */ }
    }
    const url = `${API_URL}/dashboards/${canvasId}/export-vly`
    const a = document.createElement('a')
    a.href = url
    if (token) a.href = url  // auth header not possible via <a>, use fetch below
    // Use fetch to carry the Bearer token, then trigger download
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.blob())
      .then(blob => {
        const blobUrl = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = `canvas-${canvasId.slice(0, 8)}.vly`
        link.click()
        URL.revokeObjectURL(blobUrl)
      })
  },

  /** Import a .vly file into a project. */
  importVly: (file: File, projectId: string, connectionId?: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('project_id', projectId)
    if (connectionId) form.append('connection_id', connectionId)
    return api.post('/dashboards/import-vly', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// ─── Share links ──────────────────────────────────────────────────────────────

export const shareApi = {
  create: (canvasId: string, data: { mode?: string; label?: string; expires_days?: number | null }) =>
    api.post(`/dashboards/${canvasId}/shares`, data),
  list: (canvasId: string) =>
    api.get(`/dashboards/${canvasId}/shares`),
  revoke: (canvasId: string, tokenId: string) =>
    api.delete(`/dashboards/${canvasId}/shares/${tokenId}`),
  addCollaborator: (canvasId: string, data: { email: string; role: string }) =>
    api.post(`/dashboards/${canvasId}/collaborators`, data),
  listCollaborators: (canvasId: string) =>
    api.get(`/dashboards/${canvasId}/collaborators`),
  removeCollaborator: (canvasId: string, userId: string) =>
    api.delete(`/dashboards/${canvasId}/collaborators/${userId}`),
}

// ─── Public canvas (no-auth) ──────────────────────────────────────────────────

const publicApi = axios.create({ baseURL: API_URL })

export const publicCanvasApi = {
  get: (token: string) =>
    publicApi.get(`/public/canvas/${token}`),
  refresh: (token: string) =>
    publicApi.post(`/public/canvas/${token}/refresh`),
}

// ─── Tier 5: Row-Level Security ───────────────────────────────────────────────

export interface RLSPolicy {
  id: string
  dashboard_id: string
  user_id: string | null
  name: string
  clause: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export const rlsApi = {
  list: (canvasId: string) =>
    api.get<{ policies: RLSPolicy[] }>(`/dashboards/${canvasId}/rls-policies`),
  create: (canvasId: string, data: { name: string; clause: string; user_id?: string | null; is_active?: boolean }) =>
    api.post<RLSPolicy>(`/dashboards/${canvasId}/rls-policies`, data),
  update: (canvasId: string, policyId: string, data: Partial<{ name: string; clause: string; user_id: string | null; is_active: boolean }>) =>
    api.put<RLSPolicy>(`/dashboards/${canvasId}/rls-policies/${policyId}`, data),
  delete: (canvasId: string, policyId: string) =>
    api.delete(`/dashboards/${canvasId}/rls-policies/${policyId}`),
}

// ─── Tier 5: Scheduled Refresh ────────────────────────────────────────────────

export interface RefreshSchedule {
  enabled: boolean
  cron: string | null
  timezone: string
}

export const scheduleApi = {
  get: (canvasId: string) =>
    api.get<{ schedule: RefreshSchedule }>(`/dashboards/${canvasId}/refresh-schedule`),
  set: (canvasId: string, data: { cron?: string | null; enabled: boolean; timezone?: string }) =>
    api.patch<{ schedule: RefreshSchedule }>(`/dashboards/${canvasId}/refresh-schedule`, data),
  refreshNow: (canvasId: string) =>
    api.post(`/dashboards/${canvasId}/refresh-now`),
}

// ─── Tier 5: Calculated Measures ─────────────────────────────────────────────

export interface Measure {
  name: string
  label: string
  expression: string
  format: string
}

export const measuresApi = {
  list: (canvasId: string) =>
    api.get<{ measures: Measure[] }>(`/dashboards/${canvasId}/measures`),
  create: (canvasId: string, data: Omit<Measure, 'format'> & { format?: string }) =>
    api.post<{ measures: Measure[] }>(`/dashboards/${canvasId}/measures`, data),
  delete: (canvasId: string, measureName: string) =>
    api.delete<{ measures: Measure[] }>(`/dashboards/${canvasId}/measures/${measureName}`),
  generate: (canvasId: string, description: string) =>
    api.post<Measure>(`/dashboards/${canvasId}/measures/generate`, { description }),
}

// ─── Tier 5: Drilldown ────────────────────────────────────────────────────────

export interface DrilldownResult {
  widget_id: string
  drill_column: string
  drill_value: string
  child_sql: string
  chart_data: { rows: Record<string, unknown>[]; columns: string[] }
}

export const drilldownApi = {
  generate: (
    canvasId: string,
    data: { widget_id: string; drill_column: string; drill_value: string; connection_id?: string },
  ) => api.post<DrilldownResult>(`/dashboards/${canvasId}/drilldown`, data),
}

// ─── Tier 5: RLS-aware requery ────────────────────────────────────────────────

export const rlsRequeryApi = {
  requery: (
    canvasId: string,
    filters: Record<string, string[] | { start: string; end: string }>,
  ) => api.post(`/dashboards/${canvasId}/requery-rls`, { filters }),
}
