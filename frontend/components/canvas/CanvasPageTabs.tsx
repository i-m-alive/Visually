'use client'
import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Plus, MoreHorizontal, Copy, Pencil, Trash2, ChevronLeft, ChevronRight, GripVertical } from 'lucide-react'

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
  onReorder?: (newPages: CanvasPage[]) => void
}

export function CanvasPageTabs({
  pages, activePageId, onSwitch, onAdd, onRename, onDelete, onDuplicate, onReorder,
}: Props) {
  const [editingId, setEditingId]     = useState<string | null>(null)
  const [editDraft, setEditDraft]     = useState('')
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null)
  const [draggedId, setDraggedId]     = useState<string | null>(null)
  const [dragOverId, setDragOverId]   = useState<string | null>(null)
  const editInputRef   = useRef<HTMLInputElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const scrollRef      = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (editingId) editInputRef.current?.focus()
  }, [editingId])

  // Close context menu on outside click or Escape
  useEffect(() => {
    const onMouse = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null)
      }
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setContextMenu(null) }
    document.addEventListener('mousedown', onMouse)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onMouse)
      document.removeEventListener('keydown', onKey)
    }
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
    // Clamp so the menu never overflows off-screen
    const MENU_W = 168
    const MENU_H = 180  // generous estimate
    const x = Math.min(e.clientX, window.innerWidth - MENU_W - 4)
    // Tabs are at the bottom — open ABOVE the click point
    const y = e.clientY - MENU_H > 8 ? e.clientY - MENU_H : e.clientY + 4
    setContextMenu({ pageId, x, y })
  }

  // ── Drag-and-drop reorder ───────────────────────────────────────────────────

  const handleDragStart = (e: React.DragEvent, pageId: string) => {
    if (!onReorder) return
    setDraggedId(pageId)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', pageId)
  }

  const handleDragOver = (e: React.DragEvent, pageId: string) => {
    if (!draggedId || draggedId === pageId) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOverId(pageId)
  }

  const handleDrop = (e: React.DragEvent, targetId: string) => {
    e.preventDefault()
    if (!draggedId || draggedId === targetId || !onReorder) return
    const arr = [...sortedPages]
    const fromIdx = arr.findIndex(p => p.id === draggedId)
    const toIdx   = arr.findIndex(p => p.id === targetId)
    const [moved] = arr.splice(fromIdx, 1)
    arr.splice(toIdx, 0, moved)
    onReorder(arr.map((p, i) => ({ ...p, order: i })))
    setDraggedId(null)
    setDragOverId(null)
  }

  const handleDragEnd = () => { setDraggedId(null); setDragOverId(null) }

  const scrollLeft  = () => scrollRef.current?.scrollBy({ left: -120, behavior: 'smooth' })
  const scrollRight = () => scrollRef.current?.scrollBy({ left:  120, behavior: 'smooth' })

  const sortedPages = [...pages].sort((a, b) => a.order - b.order)

  return (
    <div className="flex items-center h-9 bg-white border-t border-gray-200 flex-shrink-0 select-none">
      {/* Scroll left */}
      {pages.length > 6 && (
        <button onClick={scrollLeft} className="p-1.5 text-gray-400 hover:text-gray-700 flex-shrink-0 hover:bg-gray-100 transition-colors">
          <ChevronLeft size={13} />
        </button>
      )}

      {/* Tab strip */}
      <div
        ref={scrollRef}
        className="flex items-end h-full overflow-x-auto flex-1 min-w-0"
        style={{ scrollbarWidth: 'none' }}
      >
        {sortedPages.map(page => {
          const isActive   = page.id === activePageId
          const isEditing  = editingId === page.id
          const isDragging = draggedId === page.id
          const isOver     = dragOverId === page.id

          return (
            <div
              key={page.id}
              draggable={!!onReorder && !isEditing}
              onDragStart={e => handleDragStart(e, page.id)}
              onDragOver={e => handleDragOver(e, page.id)}
              onDrop={e => handleDrop(e, page.id)}
              onDragEnd={handleDragEnd}
              className={`
                relative group flex items-center flex-shrink-0 h-full px-2 gap-1
                border-r border-gray-100 cursor-pointer transition-colors
                ${isActive   ? 'bg-white border-b-2 border-b-blue-600 text-gray-900' : 'bg-gray-50 text-gray-500 hover:bg-gray-100 hover:text-gray-700'}
                ${isDragging ? 'opacity-40' : ''}
                ${isOver     ? 'bg-blue-50 border-l-2 border-l-blue-400' : ''}
              `}
              onClick={() => !isEditing && onSwitch(page.id)}
              onDoubleClick={() => startRename(page)}
              onContextMenu={e => openContextMenu(e, page.id)}
            >
              {/* Drag handle — visible on hover when reorder is enabled */}
              {onReorder && !isEditing && (
                <GripVertical
                  size={10}
                  className="text-gray-300 group-hover:text-gray-400 transition-colors flex-shrink-0 cursor-grab active:cursor-grabbing"
                />
              )}

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
                <span className="text-xs font-medium whitespace-nowrap max-w-[100px] truncate">
                  {page.name}
                </span>
              )}

              {/* Context menu trigger */}
              {!isEditing && (
                <button
                  onClick={e => openContextMenu(e, page.id)}
                  className="p-0.5 rounded transition-opacity flex-shrink-0 opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-700"
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
        <button onClick={scrollRight} className="p-1.5 text-gray-400 hover:text-gray-700 flex-shrink-0 hover:bg-gray-100 transition-colors">
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

      {/* Context menu — clamped to viewport, opens above tabs */}
      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="fixed z-50 bg-white border border-gray-200 rounded-xl shadow-2xl py-1 min-w-[168px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            onClick={() => { const p = pages.find(p => p.id === contextMenu.pageId); if (p) startRename(p) }}
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

          {/* Move left / Move right (context-menu fallback for reorder) */}
          {onReorder && (() => {
            const idx = sortedPages.findIndex(p => p.id === contextMenu.pageId)
            return (
              <>
                {idx > 0 && (
                  <button
                    onClick={() => {
                      const arr = sortedPages.map(p => ({ ...p }))
                      ;[arr[idx].order, arr[idx - 1].order] = [arr[idx - 1].order, arr[idx].order]
                      onReorder(arr)
                      setContextMenu(null)
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 transition-colors"
                  >
                    <ChevronLeft size={12} className="text-gray-400" /> Move left
                  </button>
                )}
                {idx < sortedPages.length - 1 && (
                  <button
                    onClick={() => {
                      const arr = sortedPages.map(p => ({ ...p }))
                      ;[arr[idx].order, arr[idx + 1].order] = [arr[idx + 1].order, arr[idx].order]
                      onReorder(arr)
                      setContextMenu(null)
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 transition-colors"
                  >
                    <ChevronRight size={12} className="text-gray-400" /> Move right
                  </button>
                )}
              </>
            )
          })()}

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
