import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '@/stores/authStore'

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

// ─── Auto-refresh on 401 ──────────────────────────────────────────────────────
// Access tokens are short-lived (15 min). Without this, the first request after
// expiry returns 401 and every subsequent call keeps 401-ing — the user appears
// "logged out" / loses their DB connection mid-task (e.g. during a .vly import).
// Here we transparently exchange the 30-day refresh token for a fresh access
// token, retry the original request once, and only bounce to /login if the
// refresh itself fails (expired/revoked refresh token, or JWT secret change).
//
// A single-flight promise ensures that a burst of concurrent 401s triggers just
// ONE refresh, then all retry with the new token.
let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = useAuthStore.getState().refreshToken
  if (!refreshToken) return null
  try {
    // Bare axios (not `api`) so this call skips the interceptors and can't recurse.
    const resp = await axios.post(`${API_URL}/auth/refresh`, { refresh_token: refreshToken })
    const d = resp.data as {
      access_token: string; refresh_token: string
      user_id: string; email: string; username: string; full_name: string; role: 'builder' | 'end_user'
    }
    // Persist BOTH new tokens — the backend rotates (revokes) the refresh token,
    // so reusing the old one on the next refresh would fail.
    useAuthStore.getState().setAuth(
      { id: d.user_id, email: d.email, username: d.username, full_name: d.full_name, role: d.role },
      d.access_token,
      d.refresh_token,
    )
    return d.access_token
  } catch {
    return null
  }
}

/** Single-flight wrapper: a burst of concurrent 401s triggers exactly ONE refresh. */
function getRefreshedToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

/** The session is genuinely dead — clear it and bounce to the login page. */
function forceReLogin(): void {
  useAuthStore.getState().clearAuth()
  if (typeof window !== 'undefined') {
    document.cookie = 'visually-role=; path=/; max-age=0'
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
  }
}

api.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    const status = error.response?.status
    const url = original?.url ?? ''
    const isAuthCall =
      url.includes('/auth/refresh') || url.includes('/auth/login') || url.includes('/auth/register')

    if (status === 401 && original && !original._retry && !isAuthCall) {
      original._retry = true
      const newToken = await getRefreshedToken()
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`
        return api(original)
      }
      // Refresh failed. For BACKGROUND calls (polling, AI summaries) we must NOT
      // redirect — doing so would yank the user out of whatever they're doing
      // (e.g. typing into the Connect-a-Database modal). Let those reject silently;
      // a genuine foreground action will surface the dead session and redirect.
      const skipRedirect = original.headers?.['X-Skip-Auth-Redirect'] === '1'
      if (!skipRedirect) forceReLogin()
    }
    return Promise.reject(error)
  },
)

export const authApi = {
  register: (data: { email: string; username: string; password: string; full_name: string; role?: string }) =>
    api.post('/auth/register', data),
  // identifier accepts either an email address or a username (User ID)
  login: (data: { identifier: string; password: string }) =>
    api.post('/auth/login', data),
  refresh: (data: { refresh_token: string }) =>
    api.post('/auth/refresh', data),
  me: () => api.get('/auth/me'),
  updateMe: (data: { full_name?: string; username?: string; role?: string }) =>
    api.patch('/auth/me', data),
  changePassword: (data: { current_password: string; new_password: string }) =>
    api.post('/auth/change-password', data),
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
  // Lightweight: table names + column counts only (few KB) — for table pickers.
  // Pass connectionId/dashboardId so imported canvases (no project-level connection)
  // still resolve their bound connection's schema.
  getSchemaTables: (projectId: string, connectionId?: string, dashboardId?: string) =>
    api.get<{ tables: { name: string; columns: number }[]; total: number; version: number }>(
      `/projects/${projectId}/schema/tables`,
      { params: { ...(connectionId ? { connection_id: connectionId } : {}), ...(dashboardId ? { dashboard_id: dashboardId } : {}) } },
    ),
  getSchemaMetadata: (projectId: string) =>
    api.get(`/projects/${projectId}/schema/metadata`),
}

export const agentApi = {
  submitIntent: (data: { text: string; project_id: string; connection_id?: string }) =>
    api.post('/agent/intent', data),
  getJob: (jobId: string) =>
    api.get(`/agent/jobs/${jobId}`),
}

// Persistent Query-feature chat history (sessions + message tree / branching)
export const querySessionApi = {
  list: (projectId: string) => api.get('/query/sessions', { params: { project_id: projectId } }),
  create: (projectId: string, title?: string) => api.post('/query/sessions', { project_id: projectId, title }),
  get: (sid: string) => api.get(`/query/sessions/${sid}`),
  rename: (sid: string, title: string) => api.patch(`/query/sessions/${sid}`, { title }),
  setActiveLeaf: (sid: string, active_leaf_id: string) => api.patch(`/query/sessions/${sid}`, { active_leaf_id }),
  remove: (sid: string) => api.delete(`/query/sessions/${sid}`),
  addMessage: (
    sid: string,
    body: { role: 'user' | 'assistant'; content: string; parent_id?: string | null; result?: unknown; job_id?: string },
  ) => api.post(`/query/sessions/${sid}/messages`, body),
}

export interface ChatSendData {
  session_id?: string
  message: string
  project_id: string
  dashboard_id?: string
  connection_id?: string
  active_page_id?: string
  model_preference?: 'opus' | 'sonnet'
  // Builder schema scope for the Canvas Assistant:
  //   'database' → full enriched schema
  //   'selected' → only selected_tables + their selected_hops-hop FK neighbours
  scope?: 'database' | 'selected'
  selected_tables?: string[]
  selected_hops?: number  // 0 = only picked, 1 = +1-hop, 2 = +2-hop
}

export const chatApi = {
  send: (data: ChatSendData) => api.post('/agent/chat', data),
  clear: (sessionId: string) =>
    api.delete(`/agent/chat/${sessionId}`),
}

export interface StreamChatHandlers {
  onText: (delta: string) => void
  onChart: (chart: Record<string, unknown>) => void
  onAction?: (action: Record<string, unknown>) => void
  onDone?: (meta: { session_id: string; turn_count: number }) => void
  onError: (message: string) => void
}

/**
 * Consume the /agent/chat/stream Server-Sent-Events endpoint.
 * Streams the assistant's prose (onText deltas), then a final chart (onChart).
 * Uses fetch because axios can't read a streaming response body in the browser.
 */
export async function streamChat(
  data: ChatSendData,
  handlers: StreamChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  // This uses raw fetch (axios can't read a streaming body), so it bypasses the
  // axios 401-refresh interceptor — we replicate that logic here so an expired
  // access token doesn't silently break the chat copilot mid-session.
  const readToken = (): string | undefined => {
    if (typeof window === 'undefined') return undefined
    try {
      const stored = localStorage.getItem('visually-auth')
      if (stored) return JSON.parse(stored)?.state?.accessToken
    } catch {}
    return undefined
  }

  const doFetch = (token: string | undefined) =>
    fetch(`${API_URL}/agent/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(data),
      signal,
    })

  let resp = await doFetch(readToken())

  // On 401, transparently refresh the token and retry once.
  if (resp.status === 401) {
    const newToken = await getRefreshedToken()
    if (newToken) {
      resp = await doFetch(newToken)
    } else {
      forceReLogin()
      handlers.onError('Session expired — please sign in again.')
      return
    }
  }

  if (!resp.ok || !resp.body) {
    handlers.onError(`Stream request failed (${resp.status})`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  const handle = (evt: {
    type?: string; delta?: string; chart?: Record<string, unknown>;
    action?: Record<string, unknown>; message?: string;
    session_id?: string; turn_count?: number
  }) => {
    switch (evt.type) {
      case 'text':   handlers.onText(evt.delta ?? ''); break
      case 'chart':  if (evt.chart) handlers.onChart(evt.chart); break
      case 'action': if (evt.action) handlers.onAction?.(evt.action); break
      case 'error':  handlers.onError(evt.message ?? 'stream error'); break
      case 'done':   handlers.onDone?.({ session_id: evt.session_id ?? '', turn_count: evt.turn_count ?? 0 }); break
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const line = chunk.split('\n').find(l => l.startsWith('data:'))
      if (!line) continue
      try { handle(JSON.parse(line.slice(5).trim())) } catch { /* skip malformed frame */ }
    }
  }
}

// ─── Intelligence Report Copilot (forked from the canvas chat transport) ──────
// Dedicated transport for the "Report Copilot" on the intelligence page. It hits
// the forked backend endpoints (/intelligence/chat*) so the Report Copilot path
// is fully independent of the Canvas Assistant's /agent/chat path, even though
// the request/response shapes are intentionally identical for now.

export interface IntelChatSendData {
  session_id?: string
  message: string
  project_id: string
  dashboard_id?: string
  connection_id?: string
  active_page_id?: string
  model_preference?: 'opus' | 'sonnet'
  // 'report'  → schema scoped to the report's tables + 2-hop FK neighbours (default)
  // 'database' → full enriched schema (the copilot can query any table/view)
  scope?: 'report' | 'database'
}

export const intelligenceChatApi = {
  send: (data: IntelChatSendData) => api.post('/intelligence/chat', data),
  clear: (sessionId: string) => api.delete(`/intelligence/chat/${sessionId}`),
}

/**
 * Consume the /intelligence/chat/stream SSE endpoint (Report Copilot).
 * Mirrors streamChat() but targets the forked intelligence-copilot backend.
 */
export async function streamIntelligenceChat(
  data: IntelChatSendData,
  handlers: StreamChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const readToken = (): string | undefined => {
    if (typeof window === 'undefined') return undefined
    try {
      const stored = localStorage.getItem('visually-auth')
      if (stored) return JSON.parse(stored)?.state?.accessToken
    } catch {}
    return undefined
  }

  const doFetch = (token: string | undefined) =>
    fetch(`${API_URL}/intelligence/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(data),
      signal,
    })

  let resp = await doFetch(readToken())

  if (resp.status === 401) {
    const newToken = await getRefreshedToken()
    if (newToken) {
      resp = await doFetch(newToken)
    } else {
      forceReLogin()
      handlers.onError('Session expired — please sign in again.')
      return
    }
  }

  if (!resp.ok || !resp.body) {
    handlers.onError(`Stream request failed (${resp.status})`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  const handle = (evt: {
    type?: string; delta?: string; chart?: Record<string, unknown>;
    action?: Record<string, unknown>; message?: string;
    session_id?: string; turn_count?: number
  }) => {
    switch (evt.type) {
      case 'text':   handlers.onText(evt.delta ?? ''); break
      case 'chart':  if (evt.chart) handlers.onChart(evt.chart); break
      case 'action': if (evt.action) handlers.onAction?.(evt.action); break
      case 'error':  handlers.onError(evt.message ?? 'stream error'); break
      case 'done':   handlers.onDone?.({ session_id: evt.session_id ?? '', turn_count: evt.turn_count ?? 0 }); break
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const line = chunk.split('\n').find(l => l.startsWith('data:'))
      if (!line) continue
      try { handle(JSON.parse(line.slice(5).trim())) } catch { /* skip malformed frame */ }
    }
  }
}

export const intelligenceApi = {
  /**
   * Execute every widget's sql_query in parallel on the backend and return
   * fresh rows + columns so the AI agent has real data instead of stale cache.
   *
   * Pass dateRange to filter all widget queries to a specific time window.
   * The backend detects date columns and injects a WHERE clause automatically.
   */
  fetchWidgetData: (
    dashboardId: string,
    dateRange?: { from: string; to: string } | null,
    force = false,
  ) =>
    api.post<{
      widget_data: Array<{
        widget_id: string
        ok: boolean
        rows?: Record<string, unknown>[]
        columns?: string[]
        labels?: string[]
        values?: unknown[]
        error?: string
      }>
    }>(
      `/dashboards/${dashboardId}/intelligence-data`,
      {
        ...(dateRange ? { date_from: dateRange.from, date_to: dateRange.to } : {}),
        force,
      },
    ),

  /**
   * Send the pre-built analysis prompt to a dedicated Bedrock endpoint that:
   * - Uses a clean system prompt focused on JSON generation only
   * - Has 32 768 max_tokens (enough for a full 6-10 section report)
   * - Has NO conversation history or chart-creation system prompt contamination
   */
  analyze: (data: { prompt: string; canvas_name?: string; force?: boolean }) =>
    api.post<{ text: string }>('/intelligence/analyze', data),

  /**
   * Fetch table/column metadata for every table referenced in this dashboard's
   * widget SQL queries so the agent prompt includes DDL-level context.
   */
  fetchSchemaContext: (dashboardId: string) =>
    api.get<{
      tables: Array<{
        name: string
        business_name?: string
        description?: string
        grain?: string
        is_fact?: boolean
        key_metrics: string[]
        key_dimensions: string[]
        key_dates: string[]
        columns: Array<{
          name: string
          business_name?: string
          description?: string
          type?: string
          is_metric?: boolean
          is_dimension?: boolean
          fk_target?: string
          examples: unknown[]
        }>
      }>
      referenced_tables: string[]
      message?: string
    }>(`/dashboards/${dashboardId}/schema-context`),
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

// Header that tells the 401 interceptor to refresh-but-never-redirect, so a
// background poll failing can't navigate the user away mid-task.
export const BACKGROUND_REQ = { headers: { 'X-Skip-Auth-Redirect': '1' } } as const

export const dashboardApi = {
  listAll: () => api.get('/dashboards/all'),
  sharedWithMe: (config?: import('axios').AxiosRequestConfig) =>
    api.get('/dashboards/shared-with-me', config),
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

// ─── End-user (analyst) connections ───────────────────────────────────────────

export interface EndUserConnectionInput {
  db_type: string
  host: string
  database_name: string
  username: string
  password?: string
  port?: number
  name?: string
  ssl_enabled?: boolean
  iam_role_arn?: string
}

export const endUserApi = {
  /**
   * Create + test a DB connection in the analyst's implicit personal project.
   * Returns the new connection id, which the caller then binds to a dashboard
   * via vlyApi.bindConnection. Throws (400) if the database can't be reached.
   */
  createConnection: (data: EndUserConnectionInput) =>
    api.post<{ connection_id: string; project_id: string; name: string; ok: boolean }>(
      '/end-user/connections', data,
    ),
  // Delete a report from the analyst's dashboard. Imported (owned) canvases are
  // fully deleted; shared-with-me reports are just removed from the analyst's list.
  deleteReport: (dashboardId: string) =>
    api.delete<{ deleted: boolean; mode: 'deleted' | 'removed_from_list'; dashboard_id: string }>(
      `/end-user/reports/${dashboardId}`,
    ),
}

// ─── AI Insights (end-user) ───────────────────────────────────────────────────

export const aiInsightsApi = {
  summary:   (dashboardId: string, config?: import('axios').AxiosRequestConfig) =>
    api.post<{ summary: string }>(`/dashboards/${dashboardId}/ai-summary`, undefined, config),
  insight:   (dashboardId: string, widgetId: string) =>
    api.post<{ insight: string; widget_id: string }>(`/dashboards/${dashboardId}/ai-insight`, { widget_id: widgetId }),
  anomalies: (dashboardId: string) =>
    api.post<{ anomalies: { widget_id: string; severity: string; message: string }[] }>(`/dashboards/${dashboardId}/ai-anomalies`),
}

// ─── .vly export / import ─────────────────────────────────────────────────────

export const vlyApi = {
  /**
   * Export canvas as .vly archive.
   * - Opens a native OS "Save As" dialog (File System Access API) when supported,
   *   so the user can rename the file and choose the save location.
   * - Falls back to a plain <a download> on unsupported browsers.
   * - Pass `intelligence` to bundle the AI analysis into intelligence.json.
   */
  exportVly: async (canvasId: string, intelligence?: object): Promise<void> => {
    const stored = typeof window !== 'undefined' ? localStorage.getItem('visually-auth') : null
    let token = ''
    if (stored) {
      try { token = JSON.parse(stored)?.state?.accessToken ?? '' } catch { /* ignore */ }
    }
    // POST the intelligence in the body — a bundled AI report can be tens of KB,
    // which overflows the URL-length limit if sent as a query string.
    const url = `${API_URL}/dashboards/${canvasId}/export-vly`
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ intelligence: intelligence ?? null }),
    })
    if (!response.ok) throw new Error(`Export failed: ${response.status}`)

    // Read server-supplied filename from the now-exposed Content-Disposition header
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const match       = disposition.match(/filename="([^"]+)"/)
    const canvasName  = response.headers.get('X-Vly-Canvas') ?? ''
    // Prefer the server filename (canvas name); last-resort fallback only
    const suggestedName = match?.[1] ?? (canvasName ? `${canvasName}.vly` : `canvas-${canvasId.slice(0, 8)}.vly`)

    const blob = await response.blob()

    // ── File System Access API: native Save As dialog (Chrome/Edge 86+) ──────
    if (typeof window !== 'undefined' && 'showSaveFilePicker' in window) {
      try {
        const handle = await (window as any).showSaveFilePicker({
          suggestedName,
          types: [{
            description: 'Visually Canvas Archive',
            accept: { 'application/vnd.visually.canvas+zip': ['.vly'] },
          }],
        })
        const writable = await handle.createWritable()
        await writable.write(blob)
        await writable.close()
        return
      } catch (e: any) {
        if (e?.name === 'AbortError') return  // user cancelled — do nothing
        // Any other error (permissions, etc.) — fall through to <a> download
      }
    }

    // ── Fallback: <a download> (Firefox, Safari, older browsers) ─────────────
    const blobUrl = URL.createObjectURL(blob)
    const link    = document.createElement('a')
    link.href     = blobUrl
    link.download = suggestedName
    link.click()
    URL.revokeObjectURL(blobUrl)
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

  /**
   * Bind a live DB connection to a canvas (e.g. one just imported with cached data).
   * Sets the connection on every widget + the layout, crawls the schema so the AI
   * copilot is live-aware, and refreshes all widgets with live results.
   */
  bindConnection: (
    dashboardId: string,
    connectionId: string,
    opts?: { crawl?: boolean; refresh?: boolean },
  ) => api.post<{
    status: string
    dashboard_id: string
    connection_id: string
    widgets_bound: number
    crawl_triggered: boolean
    refreshed: boolean
  }>(`/dashboards/${dashboardId}/bind-connection`, {
    connection_id: connectionId,
    crawl:   opts?.crawl   ?? true,
    refresh: opts?.refresh ?? true,
  }),
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

// ─── Analyst (public canvas analyst features) ─────────────────────────────────
// All analyst endpoints use the share token as auth — no Bearer needed.

export interface FilterItem {
  column: string
  operator: '=' | '!=' | '>' | '<' | '>=' | '<=' | 'like' | 'in' | 'between'
  value: string | number | string[]
}

export interface AnnotationData {
  id: string
  widget_id: string | null
  content: string
  author_name: string
  color: string
  x_percent: number | null
  y_percent: number | null
  is_resolved: boolean
  created_at: string
}

export interface BookmarkData {
  id: string
  name: string
  description: string | null
  filter_state: Record<string, unknown>
  page_index: number
  created_at: string
}

export interface ScheduleData {
  id: string
  email: string
  frequency: 'daily' | 'weekly' | 'monthly'
  day_of_week: number | null
  hour_utc: number
  timezone: string
  include_ai_summary: boolean
  is_active: boolean
  last_sent_at: string | null
  next_send_at: string | null
  created_at: string
}

export interface TableInfo {
  name: string
  columns: { name: string; type: string; nullable: boolean; sample_values: unknown[] }[]
  column_count: number
}

export const analystApi = {
  // Live widget data with filters applied server-side
  getWidgetData: (token: string, widgetId: string, filters: FilterItem[] = []) =>
    publicApi.post(`/analyst/canvas/${token}/widgets/${widgetId}/data`, { filters }),

  // Distinct values for slicer widgets (dropdown / checkbox)
  getSlicerValues: (token: string, widgetId: string) =>
    publicApi.get<{ values: string[] }>(`/analyst/canvas/${token}/widgets/${widgetId}/slicer-values`),

  // Scoped AI chat — only tables in this canvas are accessible
  chat: (token: string, data: { message: string; session_id?: string }) =>
    publicApi.post<{
      session_id: string
      text: string
      inline_chart: unknown
      inline_charts: unknown[]
      turn_count: number
      schema_source: 'live' | 'recent' | 'cached' | 'embedded' | 'none'
      schema_age_minutes: number | null
    }>(`/analyst/canvas/${token}/chat`, data),

  // Schema browser — tables used by canvas widgets
  getSchema: (token: string) =>
    publicApi.get<{ tables: TableInfo[]; total: number }>(`/analyst/canvas/${token}/schema`),

  // Table data preview (max 500 rows)
  previewTable: (token: string, tableName: string, limit = 100) =>
    publicApi.get(`/analyst/canvas/${token}/schema/${encodeURIComponent(tableName)}/preview?limit=${limit}`),

  // Ad-hoc sandboxed query — SELECT only, restricted to canvas tables
  query: (token: string, sql: string) =>
    publicApi.post(`/analyst/canvas/${token}/query`, { sql }),

  // Drill-down: raw rows for a specific data point
  drilldown: (token: string, widgetId: string, data: { x_column: string; x_value: string; filters?: FilterItem[] }) =>
    publicApi.post(`/analyst/canvas/${token}/widgets/${widgetId}/drilldown`, data),

  // CSV export — returns download URL (open in new tab or fetch as blob)
  csvExportUrl: (token: string, widgetId: string) =>
    `${API_URL}/analyst/canvas/${token}/widgets/${widgetId}/export/csv`,

  // PDF export of full dashboard
  exportPdf: (token: string) =>
    publicApi.post(`/analyst/canvas/${token}/export/pdf`),

  // Annotations
  createAnnotation: (token: string, data: {
    widget_id?: string; content: string; author_name?: string;
    x_percent?: number; y_percent?: number; color?: string
  }) => publicApi.post<AnnotationData>(`/analyst/canvas/${token}/annotations`, data),

  listAnnotations: (token: string, widgetId?: string) =>
    publicApi.get<{ annotations: AnnotationData[] }>(
      `/analyst/canvas/${token}/annotations${widgetId ? `?widget_id=${widgetId}` : ''}`
    ),

  resolveAnnotation: (token: string, annotationId: string) =>
    publicApi.patch(`/analyst/canvas/${token}/annotations/${annotationId}/resolve`),

  deleteAnnotation: (token: string, annotationId: string) =>
    publicApi.delete(`/analyst/canvas/${token}/annotations/${annotationId}`),

  // Bookmarks — save filter state + page as named views
  createBookmark: (token: string, data: {
    name: string; description?: string;
    filter_state?: Record<string, unknown>; page_index?: number
  }) => publicApi.post<BookmarkData>(`/analyst/canvas/${token}/bookmarks`, data),

  listBookmarks: (token: string) =>
    publicApi.get<{ bookmarks: BookmarkData[] }>(`/analyst/canvas/${token}/bookmarks`),

  deleteBookmark: (token: string, bookmarkId: string) =>
    publicApi.delete(`/analyst/canvas/${token}/bookmarks/${bookmarkId}`),

  // Scheduled email snapshots
  createSchedule: (token: string, data: {
    email: string; frequency?: 'daily' | 'weekly' | 'monthly';
    day_of_week?: number; hour_utc?: number; timezone?: string; include_ai_summary?: boolean
  }) => publicApi.post<ScheduleData>(`/analyst/canvas/${token}/schedules`, data),

  listSchedules: (token: string) =>
    publicApi.get<{ schedules: ScheduleData[] }>(`/analyst/canvas/${token}/schedules`),

  deleteSchedule: (token: string, scheduleId: string) =>
    publicApi.delete(`/analyst/canvas/${token}/schedules/${scheduleId}`),
}
