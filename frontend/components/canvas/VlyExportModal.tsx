'use client'
import React, { useState } from 'react'
import { X, FileArchive, Loader2, Database, ShieldAlert } from 'lucide-react'
import { vlyApi } from '@/lib/api'

interface Props {
  canvasId: string
  /** Optional AI analysis to bundle into intelligence.json (intelligence page passes this). */
  intelligence?: object
  onClose: () => void
}

/**
 * Export-options dialog. The key choice is whether to bundle the FULL raw table
 * data so the archive can be opened and queried with NO live database (offline
 * mode). Off by default because it embeds the complete underlying data.
 */
export function VlyExportModal({ canvasId, intelligence, onClose }: Props) {
  const [includeTableData, setIncludeTableData] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const doExport = async () => {
    setBusy(true); setErr('')
    try {
      await vlyApi.exportVly(canvasId, intelligence, { includeTableData })
      onClose()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed')
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(4px)' }}
      onClick={e => { if (e.target === e.currentTarget && !busy) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #0d948822, #2563EB22)' }}>
              <FileArchive size={16} style={{ color: '#0d9488' }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Export .vly Canvas</h2>
              <p className="text-xs text-gray-400">Choose what to bundle into the archive</p>
            </div>
          </div>
          {!busy && (
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded-lg transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="p-5 space-y-4">
          <label className="flex items-start gap-3 p-3 rounded-xl border border-gray-200 cursor-pointer hover:bg-gray-50 transition-colors">
            <input
              type="checkbox"
              checked={includeTableData}
              onChange={e => setIncludeTableData(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-indigo-600"
            />
            <div>
              <div className="flex items-center gap-1.5">
                <Database size={13} className="text-indigo-500" />
                <span className="text-sm font-medium text-gray-800">Include full table data (offline mode)</span>
              </div>
              <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                Bundles the complete rows of every table this report uses, so the canvas,
                intelligence page, and AI copilot work with <strong>no database connection</strong>.
              </p>
            </div>
          </label>

          {includeTableData && (
            <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
              <ShieldAlert size={14} className="flex-shrink-0 mt-0.5" />
              <span>
                The archive will contain the <strong>complete underlying data</strong> of the report’s
                tables (up to 50,000 rows each). Share it only with people allowed to see that data.
              </span>
            </div>
          )}

          {err && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-200">{err}</p>}

          <button
            onClick={doExport}
            disabled={busy}
            className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50 transition-opacity hover:opacity-90"
            style={{ background: 'linear-gradient(135deg, #0d9488, #2563EB)' }}
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <FileArchive size={14} />}
            {busy ? 'Building archive…' : 'Export .vly'}
          </button>
        </div>
      </div>
    </div>
  )
}
