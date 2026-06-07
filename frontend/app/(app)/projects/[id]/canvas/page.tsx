'use client'
import { useState, useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Plus, Layers, ArrowRight, Loader2, AlertCircle, BarChart2, Trash2 } from 'lucide-react'
import { canvasApi } from '@/lib/api'

interface Canvas {
  id: string
  name: string
  description: string
  theme: string
  created_at: string
  widget_count?: number
}

export default function CanvasListPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const router = useRouter()
  const [canvases, setCanvases] = useState<Canvas[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const resp = await canvasApi.list(projectId)
        const list = resp.data?.dashboards ?? resp.data ?? []
        setCanvases(list)
      } catch {
        setError('Failed to load canvases')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [projectId])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const resp = await canvasApi.create({ project_id: projectId, name: newName.trim() })
      const newCanvas = resp.data
      router.push(`/projects/${projectId}/canvas/${newCanvas.id}`)
    } catch {
      setError('Failed to create canvas')
      setCreating(false)
    }
  }

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (confirmId !== id) {
      setConfirmId(id)
      return
    }
    setDeletingId(id)
    setConfirmId(null)
    try {
      await canvasApi.delete(id)
      setCanvases(prev => prev.filter(c => c.id !== id))
    } catch {
      setError('Failed to delete canvas')
    } finally {
      setDeletingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-brand animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-gray-50">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-100">
        <div className="flex items-center gap-2">
          <Layers size={18} className="text-brand" />
          <h1 className="text-base font-semibold text-gray-900">Canvas</h1>
        </div>
        <button
          onClick={() => setShowNew(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand text-white text-sm font-medium rounded-lg hover:bg-brand/90 transition-colors"
        >
          <Plus size={14} /> New Canvas
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {error && (
          <div className="flex items-center gap-2 text-red-600 text-sm mb-4 bg-red-50 px-4 py-3 rounded-lg">
            <AlertCircle size={16} /> {error}
          </div>
        )}

        {/* New canvas form */}
        {showNew && (
          <div className="bg-white border border-brand/30 rounded-xl p-5 mb-6 shadow-sm">
            <p className="text-sm font-semibold text-gray-800 mb-3">Create new canvas</p>
            <div className="flex gap-3">
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                placeholder="Canvas name (e.g. Sales Q2 2025)"
                className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand/30"
              />
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="px-4 py-2 bg-brand text-white text-sm font-medium rounded-lg hover:bg-brand/90 disabled:opacity-50 transition-colors"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : 'Create'}
              </button>
              <button
                onClick={() => { setShowNew(false); setNewName('') }}
                className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {canvases.length === 0 && !showNew ? (
          <div className="flex flex-col items-center justify-center h-64 text-gray-400 gap-4">
            <Layers className="w-12 h-12 text-gray-200" />
            <div className="text-center">
              <p className="font-medium text-gray-600">No canvases yet</p>
              <p className="text-sm mt-1">Create a canvas or run a screenshot pipeline to get started.</p>
            </div>
            <button
              onClick={() => setShowNew(true)}
              className="flex items-center gap-2 px-4 py-2 bg-brand text-white text-sm rounded-lg hover:bg-brand/90"
            >
              <Plus size={14} /> New Canvas
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {canvases.map((canvas) => (
              <div key={canvas.id} className="relative group">
                <button
                  onClick={() => router.push(`/projects/${projectId}/canvas/${canvas.id}`)}
                  className="w-full bg-white border border-gray-200 rounded-xl p-5 text-left hover:border-brand/40 hover:shadow-md transition-all"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="w-10 h-10 rounded-lg bg-brand/10 flex items-center justify-center">
                      <BarChart2 size={18} className="text-brand" />
                    </div>
                    <ArrowRight size={16} className="text-gray-300 group-hover:text-brand transition-colors" />
                  </div>
                  <p className="font-semibold text-gray-900 mb-1 truncate">{canvas.name}</p>
                  {canvas.description && (
                    <p className="text-xs text-gray-500 mb-2 truncate">{canvas.description}</p>
                  )}
                  <p className="text-xs text-gray-400">
                    {new Date(canvas.created_at).toLocaleDateString('en-US', {
                      month: 'short', day: 'numeric', year: 'numeric'
                    })}
                  </p>
                </button>

                {/* Delete button overlay */}
                <div className="absolute top-3 right-3">
                  {confirmId === canvas.id ? (
                    <div className="flex items-center gap-1.5 bg-white border border-red-200 rounded-lg px-2 py-1 shadow-sm z-10">
                      <span className="text-xs text-red-600 font-medium">Delete?</span>
                      <button
                        onClick={(e) => handleDelete(canvas.id, e)}
                        disabled={deletingId === canvas.id}
                        className="text-xs font-semibold text-white bg-red-500 hover:bg-red-600 px-2 py-0.5 rounded transition-colors"
                      >
                        {deletingId === canvas.id ? <Loader2 size={10} className="animate-spin" /> : 'Yes'}
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmId(null) }}
                        className="text-xs text-gray-500 hover:text-gray-700"
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => handleDelete(canvas.id, e)}
                      className="opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all bg-white shadow-sm border border-gray-100"
                      title="Delete canvas"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
