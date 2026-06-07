'use client'
import { useState, useRef, useCallback } from 'react'
import { Upload, X, ImageIcon } from 'lucide-react'

interface FilePreview {
  file: File
  previewUrl: string
}

interface Props {
  onFilesSelected: (files: File[]) => void
  maxFiles?: number
  disabled?: boolean
}

export function UploadDropzone({ onFilesSelected, maxFiles = 5, disabled = false }: Props) {
  const [previews, setPreviews] = useState<FilePreview[]>([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((incoming: File[]) => {
    const valid = incoming.filter((f) => f.type.startsWith('image/'))
    const combined = [...previews, ...valid.map((f) => ({ file: f, previewUrl: URL.createObjectURL(f) }))]
      .slice(0, maxFiles)
    setPreviews(combined)
    onFilesSelected(combined.map((p) => p.file))
  }, [previews, maxFiles, onFilesSelected])

  const removeFile = (idx: number) => {
    URL.revokeObjectURL(previews[idx].previewUrl)
    const next = previews.filter((_, i) => i !== idx)
    setPreviews(next)
    onFilesSelected(next.map((p) => p.file))
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    addFiles(Array.from(e.dataTransfer.files))
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-8 flex flex-col items-center justify-center cursor-pointer transition-colors ${
          dragging ? 'border-brand bg-brand-light' :
          disabled ? 'border-gray-200 bg-gray-50 cursor-not-allowed' :
          'border-gray-200 hover:border-brand hover:bg-brand-light'
        }`}
      >
        <Upload size={28} className={dragging ? 'text-brand' : 'text-gray-400'} />
        <p className="mt-3 text-sm font-medium text-gray-700">
          Drop screenshots here or <span className="text-brand">browse</span>
        </p>
        <p className="mt-1 text-xs text-gray-400">PNG, JPEG, WebP — up to {maxFiles} files, 20 MB each</p>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          disabled={disabled}
          onChange={(e) => addFiles(Array.from(e.target.files || []))}
        />
      </div>

      {previews.length > 0 && (
        <div className="grid grid-cols-3 gap-2">
          {previews.map((p, i) => (
            <div key={i} className="relative group rounded-xl overflow-hidden border border-gray-200 aspect-video bg-gray-50">
              <img src={p.previewUrl} alt={p.file.name} className="w-full h-full object-cover" />
              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
              <button
                onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                className="absolute top-1 right-1 w-5 h-5 rounded-full bg-white shadow flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <X size={10} />
              </button>
              <p className="absolute bottom-0 left-0 right-0 bg-black/50 text-white text-xs px-1.5 py-1 truncate">
                {p.file.name}
              </p>
            </div>
          ))}
          {previews.length < maxFiles && (
            <button
              onClick={() => inputRef.current?.click()}
              disabled={disabled}
              className="aspect-video rounded-xl border-2 border-dashed border-gray-200 flex flex-col items-center justify-center text-gray-400 hover:border-brand hover:text-brand transition-colors"
            >
              <ImageIcon size={20} />
              <span className="text-xs mt-1">Add more</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
