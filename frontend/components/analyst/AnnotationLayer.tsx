'use client'
import { useState } from 'react'
import { MessageCircle, Plus, X, Check, Trash2 } from 'lucide-react'
import type { AnnotationData } from '@/lib/api'

interface AnnotationLayerProps {
  widgetId: string
  annotations: AnnotationData[]
  onAdd: (widgetId: string, content: string, authorName: string, xPct: number, yPct: number) => Promise<void>
  onDelete: (annotationId: string) => Promise<void>
  onResolve: (annotationId: string) => Promise<void>
}

export function AnnotationLayer({ widgetId, annotations, onAdd, onDelete, onResolve }: AnnotationLayerProps) {
  const [pinning, setPinning] = useState(false)
  const [pendingPin, setPendingPin] = useState<{ x: number; y: number } | null>(null)
  const [newContent, setNewContent] = useState('')
  const [authorName, setAuthorName] = useState('')
  const [activePin, setActivePin] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const widgetAnnotations = annotations.filter(a => a.widget_id === widgetId)

  const handleLayerClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!pinning) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * 100
    const y = ((e.clientY - rect.top) / rect.height) * 100
    setPendingPin({ x, y })
    setPinning(false)
  }

  const handleSavePin = async () => {
    if (!pendingPin || !newContent.trim() || saving) return
    setSaving(true)
    try {
      await onAdd(widgetId, newContent.trim(), authorName.trim() || 'Anonymous', pendingPin.x, pendingPin.y)
      setPendingPin(null)
      setNewContent('')
      setAuthorName('')
    } finally {
      setSaving(false)
    }
  }

  const COLORS = ['#3B82F6', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#06B6D4', '#EC4899']

  return (
    <div
      className="absolute inset-0 z-10"
      style={{ cursor: pinning ? 'crosshair' : 'default', pointerEvents: pinning ? 'auto' : 'none' }}
      onClick={handleLayerClick}
    >
      {/* Pin button — always clickable */}
      <div className="absolute top-1 right-1 z-20" style={{ pointerEvents: 'auto' }}>
        <button
          onClick={e => { e.stopPropagation(); setPinning(v => !v); setPendingPin(null) }}
          className={`p-1 rounded-md transition-all ${pinning ? 'bg-purple-100 text-purple-600' : 'bg-white/80 text-gray-400 hover:text-purple-600 hover:bg-purple-50'} shadow-sm border border-gray-200`}
          title={pinning ? 'Cancel — click anywhere to pin a note' : 'Pin a note'}
        >
          <Plus size={11} />
        </button>
      </div>

      {/* Existing annotation pins */}
      {widgetAnnotations.map((a, i) => (
        a.x_percent != null && a.y_percent != null ? (
          <div key={a.id} style={{ position: 'absolute', left: `${a.x_percent}%`, top: `${a.y_percent}%`, zIndex: 30, pointerEvents: 'auto' }}
            onClick={e => { e.stopPropagation(); setActivePin(activePin === a.id ? null : a.id) }}>
            <div
              className="w-5 h-5 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-md cursor-pointer hover:scale-110 transition-transform"
              style={{ background: COLORS[i % COLORS.length], transform: 'translate(-50%, -50%)' }}
            >
              <MessageCircle size={10} />
            </div>
            {activePin === a.id && (
              <div className="absolute z-40 bg-white border border-gray-200 rounded-xl shadow-xl p-3 w-52"
                style={{ top: '20px', left: '0', transform: 'translateX(-50%)' }}
                onClick={e => e.stopPropagation()}>
                <div className="flex items-start justify-between mb-1.5">
                  <span className="text-xs font-semibold text-gray-700">{a.author_name}</span>
                  <div className="flex gap-1">
                    <button onClick={() => onResolve(a.id)} className="p-0.5 text-gray-300 hover:text-green-500" title="Resolve"><Check size={11} /></button>
                    <button onClick={() => onDelete(a.id)} className="p-0.5 text-gray-300 hover:text-red-500" title="Delete"><Trash2 size={11} /></button>
                    <button onClick={() => setActivePin(null)} className="p-0.5 text-gray-300 hover:text-gray-600"><X size={11} /></button>
                  </div>
                </div>
                <p className="text-xs text-gray-600 leading-relaxed">{a.content}</p>
                <p className="text-xs text-gray-300 mt-1.5">{new Date(a.created_at).toLocaleDateString()}</p>
              </div>
            )}
          </div>
        ) : null
      ))}

      {/* Pending pin form */}
      {pendingPin && (
        <div
          style={{ position: 'absolute', left: `${pendingPin.x}%`, top: `${pendingPin.y}%`, zIndex: 40 }}
          onClick={e => e.stopPropagation()}
        >
          <div className="w-3 h-3 rounded-full bg-purple-500 shadow-md" style={{ transform: 'translate(-50%, -50%)' }} />
          <div className="absolute bg-white border border-gray-200 rounded-xl shadow-xl p-3 w-56"
            style={{ top: '8px', left: '0', transform: 'translateX(-10%)' }}>
            <p className="text-xs font-semibold text-gray-700 mb-2">Add note</p>
            <input value={authorName} onChange={e => setAuthorName(e.target.value)}
              placeholder="Your name (optional)" className="w-full text-xs px-2.5 py-1.5 border border-gray-200 rounded-lg outline-none focus:border-purple-400 mb-1.5" />
            <textarea value={newContent} onChange={e => setNewContent(e.target.value)}
              placeholder="Note content…" rows={2}
              className="w-full text-xs px-2.5 py-1.5 border border-gray-200 rounded-lg outline-none focus:border-purple-400 resize-none mb-2" />
            <div className="flex gap-2">
              <button onClick={handleSavePin} disabled={!newContent.trim() || saving}
                className="flex-1 py-1.5 text-xs font-semibold text-white rounded-lg disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg, #7C3AED, #4F46E5)' }}>
                {saving ? '…' : 'Pin note'}
              </button>
              <button onClick={() => setPendingPin(null)} className="px-2.5 py-1.5 text-xs text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg">
                <X size={11} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
