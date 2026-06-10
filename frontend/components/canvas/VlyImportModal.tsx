'use client'
import React, { useState, useRef, useCallback } from 'react'
import { X, Upload, FileArchive, CheckCircle2, Loader2, AlertCircle, ExternalLink } from 'lucide-react'
import { vlyApi } from '@/lib/api'
import { useRouter } from 'next/navigation'

interface Props {
  projectId: string
  /** If provided, auto-link widgets to this DB connection on import */
  connectionId?: string
  onClose: () => void
  onImported?: (dashboardId: string) => void
}

type State = 'idle' | 'dragging' | 'importing' | 'done' | 'error'

export function VlyImportModal({ projectId, connectionId, onClose, onImported }: Props) {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const [state, setState] = useState<State>('idle')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<{
    dashboard_id: string
    name: string
    widget_count: number
    connection_linked: boolean
    original_name: string | null
  } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const handleFile = useCallback(async (f: File) => {
    if (!f.name.endsWith('.vly') && !f.name.endsWith('.zip')) {
      setErrorMsg('Please select a .vly file exported from Visually.')
      setState('error')
      return
    }
    setFile(f)
    setState('importing')
    setErrorMsg('')
    try {
      const resp = await vlyApi.importVly(f, projectId, connectionId)
      setResult(resp.data)
      setState('done')
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErrorMsg(msg || 'Import failed — the file may be corrupted or from an incompatible version.')
      setState('error')
    }
  }, [projectId, connectionId])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setState('idle')
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }, [handleFile])

  const openCanvas = () => {
    if (!result) return
    if (onImported) onImported(result.dashboard_id)
    else router.push(`/projects/${projectId}/canvas/${result.dashboard_id}`)
    onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget && state !== 'importing') onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #2563EB22, #7C3AED22)' }}>
              <FileArchive size={16} style={{ color: '#6366F1' }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Import .vly Canvas</h2>
              <p className="text-xs text-gray-400">Restore a canvas from a Visually export file</p>
            </div>
          </div>
          {state !== 'importing' && (
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="p-5">
          {state === 'idle' || state === 'dragging' ? (
            <div
              onDragOver={e => { e.preventDefault(); setState('dragging') }}
              onDragLeave={() => setState('idle')}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className="border-2 border-dashed rounded-xl flex flex-col items-center justify-center py-10 cursor-pointer transition-all"
              style={{
                borderColor: state === 'dragging' ? '#6366F1' : '#E5E7EB',
                background: state === 'dragging' ? '#F5F3FF' : '#FAFAFA',
              }}
            >
              <Upload size={28} style={{ color: state === 'dragging' ? '#6366F1' : '#D1D5DB', marginBottom: 10 }} />
              <p className="text-sm font-medium text-gray-600">
                {state === 'dragging' ? 'Drop to import' : 'Drop your .vly file here'}
              </p>
              <p className="text-xs text-gray-400 mt-1">or click to browse</p>
              <input ref={inputRef} type="file" accept=".vly,.zip" className="hidden" onChange={onFileChange} />
            </div>
          ) : state === 'importing' ? (
            <div className="flex flex-col items-center justify-center py-10 gap-4">
              <Loader2 size={28} className="animate-spin text-blue-500" />
              <div className="text-center">
                <p className="text-sm font-medium text-gray-700">Importing canvas…</p>
                <p className="text-xs text-gray-400 mt-1">{file?.name}</p>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-purple-500 animate-pulse" style={{ width: '60%' }} />
              </div>
            </div>
          ) : state === 'done' && result ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3 p-4 bg-green-50 rounded-xl border border-green-200">
                <CheckCircle2 size={20} className="text-green-500 flex-shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-green-800">Canvas imported successfully</p>
                  <p className="text-xs text-green-600 mt-0.5">{result.widget_count} widget{result.widget_count !== 1 ? 's' : ''} restored</p>
                </div>
              </div>

              <div className="border border-gray-100 rounded-xl p-4 space-y-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">New canvas name</span>
                  <span className="font-medium text-gray-800">{result.name}</span>
                </div>
                {result.original_name && result.original_name !== result.name && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">Original name</span>
                    <span className="text-gray-600">{result.original_name}</span>
                  </div>
                )}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">Widgets</span>
                  <span className="font-medium text-gray-800">{result.widget_count}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">Live data</span>
                  <span className={`font-medium ${result.connection_linked ? 'text-green-600' : 'text-amber-600'}`}>
                    {result.connection_linked ? '✓ DB connection linked' : '⚠ No DB connection — using cached data'}
                  </span>
                </div>
              </div>

              {!result.connection_linked && (
                <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2 border border-amber-200">
                  The canvas has been restored with cached data. Open it and connect a database to enable live data refresh.
                </p>
              )}

              <button
                onClick={openCanvas}
                className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 transition-opacity hover:opacity-90"
                style={{ background: 'linear-gradient(135deg, #2563EB, #7C3AED)' }}
              >
                Open Canvas <ExternalLink size={13} />
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center py-8 gap-4">
              <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
                <AlertCircle size={20} className="text-red-500" />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-gray-700">Import failed</p>
                <p className="text-xs text-gray-500 mt-1 max-w-xs">{errorMsg}</p>
              </div>
              <button
                onClick={() => { setState('idle'); setFile(null); setErrorMsg('') }}
                className="px-4 py-2 rounded-lg text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
              >
                Try again
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
