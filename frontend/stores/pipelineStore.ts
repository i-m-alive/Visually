import { create } from 'zustand'

export type StepStatus = 'idle' | 'active' | 'done' | 'error'

export interface ChartResult {
  chart_type: string
  title: string
  image_url?: string
  chart_data: {
    rows: Record<string, unknown>[]
    columns: string[]
    labels: string[]
    values: (number | null)[]
  }
  sql: string
  score: number
  low_confidence: boolean
  x_axis_label: string
  y_axis_label: string
  table_used: string
  validation_details?: Record<string, unknown>
  // Extra metadata passed through for slicer widgets
  extra_config?: Record<string, unknown>
}

interface PipelineSteps {
  intent: StepStatus
  schema: StepStatus
  query: StepStatus
  execute: StepStatus
  render: StepStatus
  validate: StepStatus
}

export interface DashboardResult {
  charts: ChartResult[]
  layout: { chart_index: number; x: number; y: number; w: number; h: number }[]
}

export interface ScreenshotJobState {
  vision_started?: string
  vision_parsed?: string
  hint_requested?: string
  dashboard_assembled?: string
  schema_matching?: string
  [key: string]: string | undefined
}

interface PipelineJobState {
  steps: PipelineSteps
  screenshotSteps: ScreenshotJobState
  currentIntent?: string
  generatedSql?: string
  tableUsed?: string
  validationScore?: number
  retryAttempt?: number
  chartResult?: ChartResult
  dashboardResult?: DashboardResult
  error?: string
  events: unknown[]
}

interface PipelineStore {
  jobs: Record<string, PipelineJobState>
  activeJobId: string | null
  drawerOpen: boolean
  setActiveJob: (jobId: string) => void
  setDrawerOpen: (open: boolean) => void
  handleEvent: (jobId: string, event: Record<string, unknown>) => void
  resetJob: (jobId: string) => void
}

const defaultSteps = (): PipelineSteps => ({
  intent: 'idle', schema: 'idle', query: 'idle',
  execute: 'idle', render: 'idle', validate: 'idle',
})

export const usePipelineStore = create<PipelineStore>((set, get) => ({
  jobs: {},
  activeJobId: null,
  drawerOpen: false,

  setActiveJob: (jobId) => set({ activeJobId: jobId }),
  setDrawerOpen: (open) => set({ drawerOpen: open }),

  resetJob: (jobId) => set((state) => ({
    jobs: {
      ...state.jobs,
      [jobId]: { steps: defaultSteps(), screenshotSteps: {}, events: [] },
    },
  })),

  handleEvent: (jobId, event) => {
    set((state) => {
      const job: PipelineJobState = state.jobs[jobId] || { steps: defaultSteps(), screenshotSteps: {}, events: [] }
      const type = event.type as string
      const updated = {
        ...job,
        events: [...job.events, event],
        steps: { ...job.steps },
        screenshotSteps: { ...job.screenshotSteps },
      }

      switch (type) {
        case 'intent.classified':
          updated.steps.intent = 'done'
          updated.steps.schema = 'active'
          updated.currentIntent = event.intent_type as string
          break
        case 'schema.fetched':
          updated.steps.schema = 'done'
          updated.steps.query = 'active'
          break
        case 'query.generated':
          updated.steps.query = 'done'
          updated.steps.execute = 'active'
          updated.generatedSql = event.sql as string
          updated.tableUsed = event.table_used as string
          break
        case 'query.executed':
          updated.steps.execute = 'done'
          updated.steps.render = 'active'
          break
        case 'chart.rendered':
          updated.steps.render = 'done'
          updated.steps.validate = 'active'
          break
        case 'dashboard.decomposing':
          updated.steps.intent = 'active'
          break
        case 'dashboard.decomposed':
          updated.steps.intent = 'done'
          updated.steps.query = 'active'
          break
        case 'dashboard.chart_done':
          // individual chart done — accumulate
          break
        case 'dashboard.complete': {
          const res = event.result as Record<string, unknown>
          const rawCharts = (res?.charts as Record<string, unknown>[]) || []
          updated.dashboardResult = {
            charts: rawCharts.map((c) => ({
              chart_type: c.chart_type as string,
              title: c.title as string,
              chart_data: {
                rows: (c.rows as Record<string, unknown>[]) || (c.chart_data as Record<string, unknown>)?.rows as Record<string, unknown>[] || [],
                columns: (c.columns as string[]) || (c.chart_data as Record<string, unknown>)?.columns as string[] || [],
                labels: (c.labels as string[]) || (c.chart_data as Record<string, unknown>)?.labels as string[] || [],
                values: (c.values as number[]) || (c.chart_data as Record<string, unknown>)?.values as number[] || [],
              },
              sql: c.sql as string || '',
              score: c.score as number || 0,
              low_confidence: c.low_confidence as boolean || false,
              x_axis_label: c.x_axis_label as string || '',
              y_axis_label: c.y_axis_label as string || '',
              table_used: c.table_used as string || '',
            })),
            layout: (res?.layout as DashboardResult['layout']) || [],
          }
          updated.steps.validate = 'done'
          break
        }
        // --- Screenshot pipeline events ---
        case 'schema.matching':
          updated.screenshotSteps.schema_matching = 'active'
          break
        case 'chart.racing':
          if (event.chart_id !== undefined) {
            updated.screenshotSteps[`chart_${event.chart_id}_racing`] = String(event.candidate_count ?? 2)
          }
          break
        case 'chart.race_winner':
          if (event.chart_id !== undefined) {
            updated.screenshotSteps[`chart_${event.chart_id}_racing`] = 'done'
            updated.screenshotSteps[`chart_${event.chart_id}_race_winner`] = JSON.stringify(event.winning_tables ?? [])
          }
          break
        case 'chart.visual_comparison':
          if (event.chart_id !== undefined) {
            updated.screenshotSteps[`chart_${event.chart_id}_visual_ok`] = event.match ? 'true' : 'false'
          }
          break
        case 'vision.started':
          updated.screenshotSteps.vision_started = 'active'
          break
        case 'vision.parsed':
          updated.screenshotSteps.vision_parsed = String(
            (event.chart_count as number | undefined) ?? JSON.stringify(event)
          )
          break
        case 'validation.retry':
          if (event.chart_id !== undefined) {
            // Per-chart retry tracking for screenshot pipeline
            updated.screenshotSteps[
              `chart_${event.chart_id}_attempt_${event.attempt}`
            ] = String(event.attempt)
          } else {
            // Existing single-chart pipeline behaviour
            updated.retryAttempt = event.attempt as number
            updated.steps.query = 'active'
            updated.steps.execute = 'idle'
            updated.steps.render = 'idle'
            updated.steps.validate = 'idle'
          }
          break
        case 'validation.scored':
          if (event.chart_id !== undefined) {
            // Per-chart score tracking for screenshot pipeline
            updated.screenshotSteps[`chart_${event.chart_id}_score`] = String(event.score)
          } else {
            // Existing single-chart pipeline behaviour
            updated.validationScore = event.score as number
            if (event.passed) {
              updated.steps.validate = 'done'
            }
          }
          break
        case 'chart.confirmed':
          if (event.chart_id !== undefined) {
            // Per-chart confirmation for screenshot pipeline
            updated.screenshotSteps[`chart_${event.chart_id}_status`] = 'confirmed'
          } else {
            // Existing single-chart pipeline behaviour
            updated.steps.validate = 'done'
            const chartData = event.chart_data as Record<string, unknown>
            updated.chartResult = {
              chart_type: chartData?.chart_type as string,
              title: chartData?.title as string,
              chart_data: {
                rows: (chartData?.rows as Record<string, unknown>[]) || [],
                columns: (chartData?.columns as string[]) || [],
                labels: (chartData?.labels as string[]) || [],
                values: (chartData?.values as number[]) || [],
              },
              sql: chartData?.sql as string,
              score: event.score as number,
              low_confidence: event.low_confidence as boolean,
              x_axis_label: chartData?.x_axis_label as string,
              y_axis_label: chartData?.y_axis_label as string,
              table_used: chartData?.table_used as string,
              validation_details: chartData?.validation_details as Record<string, unknown>,
            }
          }
          break
        case 'chart.low_confidence':
          updated.screenshotSteps[`chart_${event.chart_id}_status`] = 'low_confidence'
          break
        case 'hint.requested':
          updated.screenshotSteps.hint_requested = JSON.stringify(event)
          break
        case 'dashboard.assembled':
          updated.screenshotSteps.dashboard_assembled = event.dashboard_id as string
          if ((event as Record<string, unknown>).dashboard_result !== undefined) {
            updated.dashboardResult = event.dashboard_result as DashboardResult
          }
          break
        // --- Verification loop events ---
        case 'verification.started':
          updated.screenshotSteps.verification_loop = `loop_${event.loop as number}`
          updated.screenshotSteps.verification_status = 'active'
          break
        case 'verification.chart.result':
          if (event.chart_id !== undefined) {
            updated.screenshotSteps[`verify_${event.chart_id}_score`] = String(
              Math.round((event.overall_score as number) * 100)
            )
            updated.screenshotSteps[`verify_${event.chart_id}_passed`] = event.passed ? 'true' : 'false'
          }
          break
        case 'verification.retry.started':
          updated.screenshotSteps.verification_status = 'retrying'
          updated.screenshotSteps.verification_failed_count = String(event.failed_count ?? 0)
          break
        case 'verification.complete':
          updated.screenshotSteps.verification_status = event.passed ? 'passed' : 'partial'
          updated.screenshotSteps.verification_overall_score = String(
            Math.round((event.overall_score as number) * 100)
          )
          updated.screenshotSteps.verification_passed_charts = String(event.passed_charts ?? 0)
          break
        // --- End verification events ---
        // --- End screenshot events ---
        case 'pipeline.error':
          updated.error = event.message as string
          Object.keys(updated.steps).forEach((k) => {
            if (updated.steps[k as keyof PipelineSteps] === 'active') {
              updated.steps[k as keyof PipelineSteps] = 'error'
            }
          })
          break
      }

      return { jobs: { ...state.jobs, [jobId]: updated } }
    })
  },
}))
