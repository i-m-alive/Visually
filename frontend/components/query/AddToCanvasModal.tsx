'use client'
import { useState, useEffect } from 'react'
import { X, Plus, LayoutDashboard, Check, Loader2, ChevronRight, ExternalLink } from 'lucide-react'
import { canvasApi, type WidgetCreate } from '@/lib/api'
import type { ChartResult } from '@/stores/pipelineStore'

type ModalMode = 'choose' | 'new' | 'existing'

interface CanvasPage { id: string; name: string }
interface CanvasItem { id: string; name: string; pages: CanvasPage[] }

interface AddToCanvasModalProps {
  chart: ChartResult
  projectId: string
  connectionId?: string
  onClose: () => void
}

export function AddToCanvasModal({ chart, projectId, connectionId, onClose }: AddToCanvasModalProps) {
  const [mode, setMode] = useState<ModalMode>('choose')
  const [canvases, setCanvases] = useState<CanvasItem[]>([])
  const [loadingCanvases, setLoadingCanvases] = useState(false)
  const [selectedCanvasId, setSelectedCanvasId] = useState<string | null>(null)
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null)
  const [newCanvasName, setNewCanvasName] = useState('')
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState<{ canvasName: string; canvasId: string; pageName?: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (mode !== 'existing') return
    setLoadingCanvases(true)
    canvasApi.list(projectId)
      .then((r) => {
        // API returns { dashboards: [...] } or a plain array
        const rawList = ((r.data as Record<string, unknown>)?.dashboards ?? r.data ?? []) as unknown[]
        const items: CanvasItem[] = rawList.map((c: unknown) => {
          const d = c as Record<string, unknown>
          return {
            id: d.id as string,
            name: d.name as string,
            pages: ((d.layout_config as Record<string, unknown>)?.pages as CanvasPage[]) || [],
          }
        })
        setCanvases(items)
      })
      .catch(() => setError('Failed to load canvases'))
      .finally(() => setLoadingCanvases(false))
  }, [mode, projectId])

  const selectedCanvas = canvases.find((c) => c.id === selectedCanvasId)
  const selectedPage = selectedCanvas?.pages.find((p) => p.id === selectedPageId)

  const handleSelectCanvas = async (canvasId: string) => {
    setSelectedCanvasId(canvasId)
    setSelectedPageId(null)
    try {
      const r = await canvasApi.get(canvasId)
      const pages = ((r.data as Record<string, unknown>)?.layout_config as Record<string, unknown>)?.pages as CanvasPage[] | undefined
      if (pages) {
        setCanvases((prev) => prev.map((c) => c.id === canvasId ? { ...c, pages } : c))
      }
    } catch { /* ignore — pages will stay empty */ }
  }

  const buildWidget = (pageId?: string): WidgetCreate => ({
    title: chart.title,
    chart_type: chart.chart_type,
    sql_query: chart.sql,
    chart_data: chart.chart_data as Record<string, unknown>,
    connection_id: connectionId,
    width: 6,
    height: 4,
    config: pageId ? { page_id: pageId } : undefined,
  })

  const handleAddToExisting = async () => {
    if (!selectedCanvasId) return
    setSaving(true); setError(null)
    try {
      await canvasApi.addWidget(selectedCanvasId, buildWidget(selectedPageId ?? undefined))
      setSuccess({
        canvasId: selectedCanvasId,
        canvasName: selectedCanvas?.name ?? 'Canvas',
        pageName: selectedPage?.name,
      })
    } catch {
      setError('Failed to add chart. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const handleCreateNew = async () => {
    const name = newCanvasName.trim()
    if (!name) return
    setSaving(true); setError(null)
    try {
      const r = await canvasApi.create({ project_id: projectId, name })
      const newId = (r.data as Record<string, unknown>)?.id as string
      if (newId) {
        await canvasApi.addWidget(newId, buildWidget())
        setSuccess({ canvasId: newId, canvasName: name })
      }
    } catch {
      setError('Failed to create canvas. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h3 className="font-semibold text-gray-900 font-display text-sm">Add to Canvas</h3>
            <p className="text-xs text-gray-400 mt-0.5 truncate max-w-64">&ldquo;{chart.title}&rdquo;</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1">
            <X size={16} />
          </button>
        </div>

        <div className="p-5">
          {/* Success state */}
          {success ? (
            <div className="text-center space-y-4 py-2">
              <div className="mx-auto w-12 h-12 rounded-full bg-green-100 flex items-center justify-center">
                <Check size={22} className="text-green-600" />
              </div>
              <div>
                <p className="font-semibold text-gray-900 text-sm">Chart added!</p>
                <p className="text-xs text-gray-500 mt-1">
                  Added to <span className="font-medium text-gray-700">{success.canvasName}</span>
                  {success.pageName && (
                    <> &bull; page <span className="font-medium text-gray-700">{success.pageName}</span></>
                  )}
                </p>
              </div>
              {error && <p className="text-xs text-red-500">{error}</p>}
              <div className="flex gap-2 justify-center pt-1">
                <button onClick={onClose} className="text-xs px-4 py-2 text-gray-500 hover:text-gray-800 border border-gray-200 rounded-lg">
                  Close
                </button>
                <a
                  href={`/projects/${projectId}/canvas/${success.canvasId}`}
                  className="text-xs px-4 py-2 bg-brand text-white rounded-lg flex items-center gap-1.5 hover:bg-brand-dark transition-colors"
                >
                  View Canvas <ExternalLink size={11} />
                </a>
              </div>
            </div>
          ) : mode === 'choose' ? (
            /* Choose mode */
            <div className="space-y-3">
              <p className="text-sm text-gray-600">Where would you like to add this chart?</p>
              <button
                onClick={() => setMode('new')}
                className="w-full text-left border border-gray-200 hover:border-brand/50 hover:bg-blue-50/40 rounded-xl p-4 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-brand/10 flex items-center justify-center flex-shrink-0">
                    <Plus size={15} className="text-brand" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-gray-900 group-hover:text-brand transition-colors">Create new canvas</p>
                    <p className="text-xs text-gray-400 mt-0.5">Start a fresh canvas with just this chart</p>
                  </div>
                </div>
              </button>
              <button
                onClick={() => setMode('existing')}
                className="w-full text-left border border-gray-200 hover:border-brand/50 hover:bg-blue-50/40 rounded-xl p-4 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-purple-100 flex items-center justify-center flex-shrink-0">
                    <LayoutDashboard size={15} className="text-purple-600" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-gray-900 group-hover:text-brand transition-colors">Add to existing canvas</p>
                    <p className="text-xs text-gray-400 mt-0.5">Choose a canvas and optionally a specific page</p>
                  </div>
                </div>
              </button>
            </div>
          ) : mode === 'new' ? (
            /* Create new canvas */
            <div className="space-y-4">
              <button onClick={() => setMode('choose')} className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
                <ChevronRight size={11} className="rotate-180" /> Back
              </button>
              <div>
                <label className="block text-xs font-semibold text-gray-600 uppercase tracking-wide mb-2">Canvas name</label>
                <input
                  autoFocus
                  value={newCanvasName}
                  onChange={(e) => setNewCanvasName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateNew()}
                  placeholder="e.g. Sales Overview"
                  className="input-field w-full text-sm"
                />
              </div>
              {error && <p className="text-xs text-red-500">{error}</p>}
              <button
                onClick={handleCreateNew}
                disabled={!newCanvasName.trim() || saving}
                className="btn-primary w-full py-2.5 text-sm flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {saving ? 'Creating…' : 'Create canvas & add chart'}
              </button>
            </div>
          ) : (
            /* Add to existing canvas */
            <div className="space-y-4">
              <button onClick={() => setMode('choose')} className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
                <ChevronRight size={11} className="rotate-180" /> Back
              </button>

              {loadingCanvases ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 size={18} className="animate-spin text-gray-400" />
                </div>
              ) : canvases.length === 0 ? (
                <div className="text-center py-8">
                  <p className="text-sm text-gray-500">No canvases found.</p>
                  <p className="text-xs text-gray-400 mt-1">Create a new canvas instead.</p>
                </div>
              ) : (
                <>
                  <div>
                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Select canvas</label>
                    <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                      {canvases.map((c) => (
                        <button
                          key={c.id}
                          onClick={() => handleSelectCanvas(c.id)}
                          className={`w-full text-left border rounded-xl px-3 py-2.5 transition-all flex items-center gap-2 ${
                            selectedCanvasId === c.id
                              ? 'border-brand/60 bg-blue-50/50 text-brand'
                              : 'border-gray-200 hover:border-gray-300 text-gray-700'
                          }`}
                        >
                          <LayoutDashboard size={13} className={selectedCanvasId === c.id ? 'text-brand' : 'text-gray-400'} />
                          <span className="text-sm font-medium flex-1 truncate">{c.name}</span>
                          {selectedCanvasId === c.id && <Check size={12} className="text-brand flex-shrink-0" />}
                        </button>
                      ))}
                    </div>
                  </div>

                  {selectedCanvas && selectedCanvas.pages.length > 0 && (
                    <div>
                      <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                        Page (optional)
                      </label>
                      <div className="flex flex-wrap gap-1.5">
                        <button
                          onClick={() => setSelectedPageId(null)}
                          className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                            !selectedPageId ? 'bg-brand text-white border-brand' : 'border-gray-200 text-gray-500 hover:border-gray-300'
                          }`}
                        >
                          Default page
                        </button>
                        {selectedCanvas.pages.map((p) => (
                          <button
                            key={p.id}
                            onClick={() => setSelectedPageId(p.id)}
                            className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                              selectedPageId === p.id ? 'bg-brand text-white border-brand' : 'border-gray-200 text-gray-500 hover:border-gray-300'
                            }`}
                          >
                            {p.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}

              {error && <p className="text-xs text-red-500">{error}</p>}
              <button
                onClick={handleAddToExisting}
                disabled={!selectedCanvasId || saving}
                className="btn-primary w-full py-2.5 text-sm flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <LayoutDashboard size={14} />}
                {saving ? 'Adding…' : 'Add to canvas'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
