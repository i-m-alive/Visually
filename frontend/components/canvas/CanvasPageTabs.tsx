'use client'
import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Plus, MoreHorizontal, Copy, Pencil, Trash2, ChevronLeft, ChevronRight } from 'lucide-react'

export interface CanvasPage {
  id: string
  name: string
  order: number
}

interface ContextMenu {
  pageId: string
  x: number
  y: number
}

interface Props {
  pages: CanvasPage[]
  activePageId: string
  onSwitch: (id: string) => void
  onAdd: () => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  onDuplicate: (id: string) => void
}

export function CanvasPageTabs({
  pages, activePageId, onSwitch, onAdd, onRename, onDelete, onDuplicate,
}: Props) {
  const [editingId, setEditingId]     = useState<string | null>(null)
  const [editDraft, setEditDraft]     = useState('')
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const editInputRef   = useRef<HTMLInputElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const scrollRef      = useRef<HTMLDivElement>(null)

  // Focus edit input when entering rename mode
  useEffect(() => {
    if (editingId) editInputRef.current?.focus()
  }, [editingId])

  // Close context menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const startRename = useCallback((page: CanvasPage) => {
    setContextMenu(null)
    setEditingId(page.id)
    setEditDraft(page.name)
  }, [])

  const commitRename = useCallback(() => {
    if (!editingId) return
    const trimmed = editDraft.trim()
    if (trimmed) onRename(editingId, trimmed)
    setEditingId(null)
  }, [editingId, editDraft, onRename])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') commitRename()
    if (e.key === 'Escape') setEditingId(null)
  }

  const openContextMenu = (e: React.MouseEvent, pageId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setContextMenu({ pageId, x: e.clientX, y: e.clientY })
  }

  const scrollLeft = () => scrollRef.current?.scrollBy({ left: -120, behavior: 'smooth' })
  const scrollRight = () => scrollRef.current?.scrollBy({ left: 120, behavior: 'smooth' })

  const sortedPages = [...pages].sort((a, b) => a.order - b.order)

  return (
    <div className="flex items-center h-9 bg-white border-t border-gray-200 flex-shrink-0 select-none">
      {/* Scroll left */}
      {pages.length > 6 && (
        <button
          onClick={scrollLeft}
          className="p-1.5 text-gray-400 hover:text-gray-700 flex-shrink-0 hover:bg-gray-100 transition-colors"
        >
          <ChevronLeft size={13} />
        </button>
      )}

      {/* Tab strip */}
      <div
        ref={scrollRef}
        className="flex items-end h-full overflow-x-auto flex-1 min-w-0 scrollbar-hide"
        style={{ scrollbarWidth: 'none' }}
      >
        {sortedPages.map(page => {
          const isActive = page.id === activePageId
          const isEditing = editingId === page.id

          return (
            <div
              key={page.id}
              className={`
                relative group flex items-center flex-shrink-0 h-full px-3 gap-1.5
                border-r border-gray-100 cursor-pointer transition-colors
                ${isActive
                  ? 'bg-white border-b-2 border-b-blue-600 text-gray-900'
                  : 'bg-gray-50 text-gray-500 hover:bg-gray-100 hover:text-gray-700'}
              `}
              onClick={() => !isEditing && onSwitch(page.id)}
              onDoubleClick={() => startRename(page)}
              onContextMenu={e => openContextMenu(e, page.id)}
            >
              {isEditing ? (
                <input
                  ref={editInputRef}
                  value={editDraft}
                  onChange={e => setEditDraft(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={handleKeyDown}
                  onClick={e => e.stopPropagation()}
                  className="w-24 text-xs font-medium bg-white border border-blue-400 rounded px-1 py-0 outline-none"
                />
              ) : (
                <span className="text-xs font-medium whitespace-nowrap max-w-[120px] truncate">
                  {page.name}
                </span>
              )}

              {/* Context menu trigger — visible on hover for active tab */}
              {!isEditing && (
                <button
                  onClick={e => openContextMenu(e, page.id)}
                  className={`p-0.5 rounded transition-opacity flex-shrink-0 ${
                    isActive
                      ? 'text-gray-400 hover:text-gray-700 opacity-0 group-hover:opacity-100'
                      : 'opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-600'
                  }`}
                >
                  <MoreHorizontal size={11} />
                </button>
              )}
            </div>
          )
        })}
      </div>

      {/* Scroll right */}
      {pages.length > 6 && (
        <button
          onClick={scrollRight}
          className="p-1.5 text-gray-400 hover:text-gray-700 flex-shrink-0 hover:bg-gray-100 transition-colors"
        >
          <ChevronRight size={13} />
        </button>
      )}

      {/* Add page */}
      <button
        onClick={onAdd}
        className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 flex-shrink-0 transition-colors border-l border-gray-100"
        title="Add page"
      >
        <Plus size={14} />
      </button>

      {/* Context menu */}
      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-xl py-1 min-w-[156px]"
          style={{ left: contextMenu.x, top: contextMenu.y - 8 }}
        >
          <button
            onClick={() => {
              const page = pages.find(p => p.id === contextMenu.pageId)
              if (page) startRename(page)
            }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <Pencil size={12} className="text-gray-400" /> Rename
          </button>
          <button
            onClick={() => { onDuplicate(contextMenu.pageId); setContextMenu(null) }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <Copy size={12} className="text-gray-400" /> Duplicate page
          </button>
          {pages.length > 1 && (
            <>
              <div className="my-1 border-t border-gray-100" />
              <button
                onClick={() => { onDelete(contextMenu.pageId); setContextMenu(null) }}
                className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 transition-colors"
              >
                <Trash2 size={12} /> Delete page
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
