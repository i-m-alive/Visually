'use client'
import { useState, useEffect } from 'react'
import { X, Bookmark, Plus, Trash2, Loader2, Check } from 'lucide-react'
import { analystApi } from '@/lib/api'
import type { BookmarkData, FilterItem } from '@/lib/api'

interface BookmarkModalProps {
  token: string
  currentFilters: FilterItem[]
  currentPageIndex: number
  onLoad: (filters: FilterItem[], pageIndex: number) => void
  onClose: () => void
}

export function BookmarkModal({ token, currentFilters, currentPageIndex, onLoad, onClose }: BookmarkModalProps) {
  const [bookmarks, setBookmarks] = useState<BookmarkData[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    analystApi.listBookmarks(token).then(r => setBookmarks(r.data.bookmarks)).finally(() => setLoading(false))
  }, [token])

  const save = async () => {
    if (!name.trim() || saving) return
    setSaving(true)
    try {
      const r = await analystApi.createBookmark(token, {
        name: name.trim(),
        filter_state: { filters: currentFilters },
        page_index: currentPageIndex,
      })
      setBookmarks(prev => [r.data, ...prev])
      setName('')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch { /* ignore */ } finally {
      setSaving(false)
    }
  }

  const remove = async (id: string) => {
    try {
      await analystApi.deleteBookmark(token, id)
      setBookmarks(prev => prev.filter(b => b.id !== id))
    } catch { /* ignore */ }
  }

  const load = (bookmark: BookmarkData) => {
    const state = bookmark.filter_state as { filters?: FilterItem[] }
    onLoad(state.filters ?? [], bookmark.page_index)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.4)' }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <Bookmark size={15} className="text-amber-500" /> Saved Views
          </h3>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-100"><X size={16} /></button>
        </div>

        {/* Save current state */}
        <div className="px-5 py-4 border-b border-gray-100">
          <p className="text-xs text-gray-500 mb-2">Save current filters as a named view</p>
          <div className="flex gap-2">
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && save()}
              placeholder="View name…"
              className="flex-1 px-3 py-2 text-xs border border-gray-200 rounded-lg outline-none focus:border-blue-400"
            />
            <button
              onClick={save}
              disabled={!name.trim() || saving}
              className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-white rounded-lg disabled:opacity-50 transition-all"
              style={{ background: saved ? '#10B981' : 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
            >
              {saving ? <Loader2 size={11} className="animate-spin" /> : saved ? <Check size={11} /> : <Plus size={11} />}
              {saved ? 'Saved!' : 'Save'}
            </button>
          </div>
          {currentFilters.length > 0 && (
            <p className="text-xs text-gray-400 mt-1.5">{currentFilters.length} active filter{currentFilters.length !== 1 ? 's' : ''} will be saved</p>
          )}
        </div>

        {/* Bookmark list */}
        <div className="max-h-72 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-24"><Loader2 size={16} className="animate-spin text-blue-400" /></div>
          ) : bookmarks.length === 0 ? (
            <div className="flex items-center justify-center h-24 text-xs text-gray-400">No saved views yet</div>
          ) : (
            bookmarks.map(b => (
              <div key={b.id} className="flex items-center gap-3 px-5 py-3 border-b border-gray-50 hover:bg-gray-50 group">
                <Bookmark size={13} className="text-amber-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-gray-800 truncate">{b.name}</p>
                  <p className="text-xs text-gray-400">
                    {((b.filter_state as { filters?: unknown[] }).filters?.length ?? 0)} filter{((b.filter_state as { filters?: unknown[] }).filters?.length ?? 0) !== 1 ? 's' : ''}
                    {' · '}{new Date(b.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button onClick={() => load(b)} className="text-xs font-semibold text-blue-600 hover:text-blue-800 opacity-0 group-hover:opacity-100 transition-opacity">Load</button>
                <button onClick={() => remove(b.id)} className="p-1 text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-all rounded"><Trash2 size={12} /></button>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
