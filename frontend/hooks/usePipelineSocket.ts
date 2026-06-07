'use client'
import { useEffect, useRef } from 'react'
import { usePipelineStore } from '@/stores/pipelineStore'

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001'

export function usePipelineSocket(jobId: string | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const handleEvent = usePipelineStore((s) => s.handleEvent)

  useEffect(() => {
    if (!jobId) return

    const ws = new WebSocket(`${WS_URL}/agent/stream/${jobId}`)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        handleEvent(jobId, event)
      } catch {}
    }

    ws.onerror = (e) => console.error('Pipeline WS error', e)
    ws.onclose = () => console.log('Pipeline WS closed for job', jobId)

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [jobId, handleEvent])

  return wsRef
}
