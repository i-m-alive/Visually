'use client'
import { CheckCircle2, AlertCircle, Loader2, Clock, HelpCircle, Eye, Zap } from 'lucide-react'
import { usePipelineStore } from '@/stores/pipelineStore'

interface ChartState {
  chart_id: string
  status: string
  attempt_count: number
  validation_score?: number
  hint_requested?: boolean
  failure_type?: string
}

interface Props {
  charts: ChartState[]
  totalCharts: number
  jobId?: string | null
}

const STATUS_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  pending:        { icon: <Clock size={14} />,                             label: 'Waiting',        color: 'text-gray-400'   },
  querying:       { icon: <Loader2 size={14} className="animate-spin" />, label: 'Generating SQL', color: 'text-blue-500'   },
  validating:     { icon: <Loader2 size={14} className="animate-spin" />, label: 'Validating',     color: 'text-purple-500' },
  retrying:       { icon: <Loader2 size={14} className="animate-spin" />, label: 'Retrying',       color: 'text-amber-500'  },
  racing:         { icon: <Zap size={14} className="animate-pulse" />,    label: 'Racing',         color: 'text-indigo-500' },
  confirmed:      { icon: <CheckCircle2 size={14} />,                     label: 'Confirmed',      color: 'text-green-500'  },
  low_confidence: { icon: <AlertCircle size={14} />,                      label: 'Low confidence', color: 'text-amber-500'  },
  failed:         { icon: <AlertCircle size={14} />,                      label: 'Failed',         color: 'text-red-500'    },
}

const FAILURE_LABELS: Record<string, string> = {
  column_not_found:     'column missing',
  syntax_error:         'syntax error',
  wrong_table:          'wrong table',
  permission_denied:    'permission',
  zero_rows:            'zero rows',
  wrong_date_range:     'date range',
  wrong_scale:          'scale off',
  low_validation_score: 'low score',
}

export function ReplicationProgress({ charts, totalCharts, jobId }: Props) {
  const screenshotSteps = usePipelineStore(
    (s) => (jobId ? s.jobs[jobId]?.screenshotSteps : undefined)
  )

  const confirmed = charts.filter((c) => c.status === 'confirmed').length
  const lowConf   = charts.filter((c) => c.status === 'low_confidence').length
  const failed    = charts.filter((c) => c.status === 'failed').length

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-600 font-medium">
          {confirmed + lowConf} / {totalCharts || charts.length} charts replicated
        </span>
        <div className="flex gap-3 text-xs">
          {confirmed > 0 && <span className="text-green-600">{confirmed} confirmed</span>}
          {lowConf   > 0 && <span className="text-amber-600">{lowConf} low confidence</span>}
          {failed    > 0 && <span className="text-red-600">{failed} failed</span>}
        </div>
      </div>

      <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full bg-brand rounded-full transition-all duration-500"
          style={{ width: `${totalCharts ? ((confirmed + lowConf) / totalCharts) * 100 : 0}%` }}
        />
      </div>

      <div className="space-y-2">
        {charts.map((chart) => {
          const cid = chart.chart_id

          const racingVal   = screenshotSteps?.[`chart_${cid}_racing`]
          const isRacing    = racingVal !== undefined && racingVal !== 'done'
                              && chart.status !== 'confirmed' && chart.status !== 'low_confidence'
          const raceWinner  = screenshotSteps?.[`chart_${cid}_race_winner`]
          const visualOk    = screenshotSteps?.[`chart_${cid}_visual_ok`]

          const effectiveStatus = isRacing ? 'racing' : chart.status
          const config = STATUS_CONFIG[effectiveStatus] || STATUS_CONFIG.pending
          const failureLabel = chart.failure_type ? FAILURE_LABELS[chart.failure_type] : undefined

          return (
            <div
              key={cid}
              className="flex items-center justify-between py-2 px-3 rounded-xl bg-gray-50 border border-gray-100"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className={`${config.color} shrink-0`}>{config.icon}</span>
                <span className="text-xs font-medium text-gray-700 truncate">{cid}</span>

                {chart.hint_requested && chart.status !== 'confirmed' && (
                  <span className="flex items-center gap-0.5 text-xs text-amber-600 shrink-0">
                    <HelpCircle size={11} /> hint
                  </span>
                )}

                {isRacing && (
                  <span className="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded-full shrink-0">
                    🏁 racing {racingVal}
                  </span>
                )}

                {raceWinner && chart.status === 'confirmed' && (
                  <span className="text-xs text-indigo-400 shrink-0">via race</span>
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {visualOk !== undefined && (
                  <span title={`Visual check: ${visualOk === 'true' ? 'match' : 'mismatch'}`}>
                    <Eye size={11} className={visualOk === 'true' ? 'text-green-500' : 'text-amber-500'} />
                  </span>
                )}

                {failureLabel && chart.status === 'retrying' && (
                  <span className="text-xs text-red-400 bg-red-50 px-1 py-0.5 rounded">
                    {failureLabel}
                  </span>
                )}

                {chart.attempt_count > 0 && (
                  <span className="text-xs text-gray-400">×{chart.attempt_count}</span>
                )}

                {chart.validation_score !== undefined && chart.validation_score !== null && (
                  <span className={`text-xs font-semibold ${
                    chart.validation_score >= 0.95 ? 'text-green-600' :
                    chart.validation_score >= 0.70 ? 'text-amber-600' : 'text-red-500'
                  }`}>
                    {(chart.validation_score * 100).toFixed(0)}%
                  </span>
                )}

                <span className={`text-xs ${config.color}`}>{config.label}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
