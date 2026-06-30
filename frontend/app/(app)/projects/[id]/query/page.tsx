'use client'
import { useParams } from 'next/navigation'
import { QueryChatPanel } from '@/components/query/QueryChatPanel'

export default function QueryPage() {
  const { id: projectId } = useParams<{ id: string }>()
  return (
    <div className="h-full overflow-hidden">
      <QueryChatPanel projectId={projectId} />
    </div>
  )
}
