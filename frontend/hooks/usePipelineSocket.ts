'use client'
import { useEffect, useRef } from 'react'
import { usePipelineStore } from '@/stores/pipelineStore'
import { agentApi } from '@/lib/api'

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001'

// Polling interval when WebSocket is unavailable (ms)
const POLL_INTERVAL_MS = 1500

export function usePipelineSocket(jobId: string | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const handleEvent = usePipelineStore((s) => s.handleEvent)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const wsEverOpened = useRef(false)

  useEffect(() => {
    if (!jobId) return

    wsEverOpened.current = false

    // ── WebSocket (primary) ──────────────────────────────────────────────────
    const ws = new WebSocket(`${WS_URL}/agent/stream/${jobId}`)
    wsRef.current = ws

    ws.onopen = () => {
      wsEverOpened.current = true
    }

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        handleEvent(jobId, event)
      } catch {}
    }

    ws.onerror = () => { /* error logged by browser; onclose fires next */ }

    ws.onclose = () => {
      console.log('Pipeline WS closed for job', jobId)
      // If WS never delivered results (Redis unavailable or WS returned 404),
      // start polling the REST endpoint so the result still appears.
      if (!wsEverOpened.current) {
        startPolling(jobId)
      }
    }

    // Safety net: if WS doesn't open within 4s, start polling anyway.
    const wsTimeoutId = setTimeout(() => {
      if (!wsEverOpened.current) startPolling(jobId)
    }, 4000)

    // ── REST polling fallback ────────────────────────────────────────────────
    function startPolling(jid: string) {
      if (pollTimerRef.current) return
      pollTimerRef.current = setInterval(async () => {
        try {
          const resp = await agentApi.getJob(jid)
          const job = resp.data as {
            status: string
            job_type?: string
            result?: Record<string, unknown> | null
            error?: string | null
          }

          if (job.status === 'completed' && job.result) {
            if (job.job_type === 'DASHBOARD') {
              handleEvent(jid, { type: 'dashboard.complete', result: job.result })
            } else {
              handleEvent(jid, {
                type: 'chart.confirmed',
                chart_data: job.result,
                score: (job.result.score as number) ?? 1,
                low_confidence: (job.result.low_confidence as boolean) ?? false,
              })
            }
            stopPolling()
          } else if (job.status === 'failed') {
            handleEvent(jid, { type: 'pipeline.error', message: job.error || 'Pipeline failed' })
            stopPolling()
          }
        } catch {
          // transient — keep polling
        }
      }, POLL_INTERVAL_MS)
    }

    function stopPolling() {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }

    return () => {
      clearTimeout(wsTimeoutId)
      ws.close()
      wsRef.current = null
      stopPolling()
    }
  }, [jobId, handleEvent])

  return wsRef
}
