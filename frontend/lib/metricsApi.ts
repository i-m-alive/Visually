import { api } from './api'

export interface MetricDefinition {
  name: string
  synonyms: string[]
  table: string
  expression: string
  date_column?: string | null
  filter?: string | null
  description?: string | null
}

export const metricsApi = {
  list: (projectId: string, connId: string) =>
    api.get<{ metrics: MetricDefinition[] }>(`/projects/${projectId}/connections/${connId}/metrics`),
  upsert: (projectId: string, connId: string, metric: MetricDefinition) =>
    api.put(`/projects/${projectId}/connections/${connId}/metrics`, metric),
  remove: (projectId: string, connId: string, name: string) =>
    api.delete(`/projects/${projectId}/connections/${connId}/metrics/${encodeURIComponent(name)}`),
}
