'use client'
import { useState, useRef, useCallback } from 'react'
import { X, Upload, FileUp, Loader2, AlertCircle } from 'lucide-react'
import { ConnectionPromptModal } from './ConnectionPromptModal'

interface Props {
  importing: boolean
  onImport: (file: File, connectionId?: string) => void
  onClose: () => void
}

export function VlyImportZone({ importing, onImport, onClose }: Props) {
  const [dragOver, setDragOver] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connHint, setConnHint] = useState<Record<string, any> | null>(null)
  const [showConnPrompt, setShowConnPrompt] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (f: File) => {
    if (!/\.(vly|ovly)$/i.test(f.name)) {
      setError('Only .vly / .ovly files are supported.')
      return
    }
    setError(null)
    setFile(f)

    // Peek inside the ZIP to find the connection fingerprint
    try {
      const { default: JSZip } = await import('jszip')
      const zip = await JSZip.loadAsync(f)
      const metaFile = zip.file('meta.json')
      if (metaFile) {
        const meta = JSON.parse(await metaFile.async('string'))
        const hint = meta?.connection_hint ?? {}
        if (hint.host) setConnHint(hint)
      }
    } catch {
      // jszip not available or parse failed — ignore, proceed without hint
    }
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const handleSubmit = () => {
    if (!file) return
    // If there's a connection hint, ask for credentials
    if (connHint?.host) {
      setShowConnPrompt(true)
      return
    }
    onImport(file)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #EFF6FF, #F5F3FF)' }}>
              <FileUp size={15} style={{ color: '#2563EB' }} />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900">Import .vly Report</p>
              <p className="text-xs text-gray-500">Drop a file exported from Visually</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg hover:bg-gray-50 transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className="border-2 border-dashed rounded-xl p-8 flex flex-col items-center gap-3 cursor-pointer transition-colors"
          style={{
            borderColor: dragOver ? '#2563EB' : file ? '#16A34A' : '#E5E7EB',
            background: dragOver ? '#EFF6FF' : file ? '#F0FDF4' : '#F9FAFB',
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".vly,.ovly"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
          {file ? (
            <>
              <div className="w-10 h-10 rounded-xl bg-green-100 flex items-center justify-center">
                <FileUp size={20} style={{ color: '#16A34A' }} />
              </div>
              <p className="text-sm font-semibold text-green-700">{file.name}</p>
              <p className="text-xs text-gray-500">
                {(file.size / 1024).toFixed(1)} KB — click to change
              </p>
            </>
          ) : (
            <>
              <div className="w-10 h-10 rounded-xl bg-gray-100 flex items-center justify-center">
                <Upload size={20} className="text-gray-400" />
              </div>
              <p className="text-sm font-medium text-gray-700">Drop a .vly file here</p>
              <p className="text-xs text-gray-400">or click to browse</p>
            </>
          )}
        </div>

        {error && (
          <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-red-50 border border-red-100 rounded-lg">
            <AlertCircle size={13} className="text-red-500 flex-shrink-0" />
            <p className="text-xs text-red-600">{error}</p>
          </div>
        )}

        <div className="flex gap-3 mt-5">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2.5 text-sm font-medium text-gray-600 border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!file || importing}
            className="flex-1 px-4 py-2.5 text-sm font-semibold text-white rounded-xl transition-colors disabled:opacity-40 flex items-center justify-center gap-2"
            style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
          >
            {importing ? <><Loader2 size={14} className="animate-spin" /> Importing…</> : 'Import'}
          </button>
        </div>
      </div>

      {/* Connection prompt for cross-org .vly */}
      {showConnPrompt && file && (
        <ConnectionPromptModal
          fileName={file.name}
          connectionHint={connHint ?? undefined}
          onConnect={async _details => {
            // For now, import without pre-created connectionId — the backend auto-matches by host/db
            setShowConnPrompt(false)
            onImport(file)
          }}
          onClose={() => setShowConnPrompt(false)}
        />
      )}
    </div>
  )
}
