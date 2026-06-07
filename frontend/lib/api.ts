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
  register: (data: { email: string; password: string; full_name: string }) =>
    api.post('/auth/register', data),
  login: (data: { email: string; password: string }) =>
    api.post('/auth/login', data),
  refresh: (data: { refresh_token: string }) =>
    api.post('/auth/refresh', data),
  me: () => api.get('/auth/me'),
}

export const projectApi = {
  create: (data: { name: string; description?: string }) =>
    api.post('/projects', data),
  list: () => api.get('/projects'),
  get: (id: string) => api.get(`/projects/${id}`),
  delete: (id: string) => api.delete(`/projects/${id}`),
  addConnection: (projectId: string, data: Record<string, unknown>) =>
    api.post(`/projects/${projectId}/connections`, data),
  testConnection: (projectId: string, connId: string) =>
    api.post(`/projects/${projectId}/connections/${connId}/test`),
  triggerCrawl: (projectId: string) =>
    api.post(`/projects/${projectId}/schema/crawl`),
  getCrawlStatus: (projectId: string, jobId: string) =>
    api.get(`/projects/${projectId}/schema/crawl/${jobId}`),
  getSchema: (projectId: string) =>
    api.get(`/projects/${projectId}/schema`),
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
  }) => api.post('/agent/chat', data),
  clear: (sessionId: string) =>
    api.delete(`/agent/chat/${sessionId}`),
}

export const screenshotApi = {
  upload: (data: { projectId: string; files: File[]; connectionId?: string }) => {
    const form = new FormData()
    form.append('project_id', data.projectId)
    if (data.connectionId) form.append('connection_id', data.connectionId)
    data.files.forEach((f) => form.append('files', f))
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
  list: (projectId: string) => api.get(`/dashboards?project_id=${projectId}`),
  get: (dashboardId: string) => api.get(`/dashboards/${dashboardId}`),
  updateTheme: (dashboardId: string, theme: string) =>
    api.patch(`/dashboards/${dashboardId}`, { theme }),
  delete: (dashboardId: string) => api.delete(`/dashboards/${dashboardId}`),
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
  updateTheme: (canvasId: string, theme: string) =>
    api.patch(`/dashboards/${canvasId}`, { theme }),
  updateLayout: (canvasId: string, items: LayoutItem[]) =>
    api.patch(`/dashboards/${canvasId}/layout`, { items }),
  addWidget: (canvasId: string, widget: WidgetCreate) =>
    api.post(`/dashboards/${canvasId}/widgets`, widget),
}

export const widgetApi = {
  update: (widgetId: string, data: WidgetPatch) =>
    api.patch(`/widgets/${widgetId}`, data),
  delete: (widgetId: string) =>
    api.delete(`/widgets/${widgetId}`),
}
