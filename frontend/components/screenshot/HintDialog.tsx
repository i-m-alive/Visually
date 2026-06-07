'use client'
import { useState } from 'react'
import { HelpCircle, X, ChevronRight } from 'lucide-react'

interface HintOption {
  value: string
  label: string
}

interface Props {
  chartId: string
  chartTitle?: string
  chartType?: string
  message: string
  options: HintOption[]
  onSubmit: (value: string) => void
  onSkip: () => void
}

export function HintDialog({ chartId, chartTitle, chartType, message, options, onSubmit, onSkip }: Props) {
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4">
        <div className="flex items-start justify-between p-5 border-b border-gray-100">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
              <HelpCircle size={18} className="text-amber-600" />
            </div>
            <div>
              <p className="font-semibold text-gray-900 text-sm">Help needed</p>
              {chartTitle && <p className="text-xs text-gray-400 truncate max-w-60">{chartTitle}</p>}
            </div>
          </div>
          <button onClick={onSkip} className="text-gray-400 hover:text-gray-600 mt-0.5">
            <X size={16} />
          </button>
        </div>

        <div className="p-5">
          <p className="text-sm text-gray-700 mb-4">{message}</p>
          <div className="space-y-2 max-h-60 overflow-y-auto">
            {options.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSelected(opt.value)}
                className={`w-full text-left px-3 py-2.5 rounded-xl border text-sm transition-colors ${
                  selected === opt.value
                    ? 'border-brand bg-brand-light text-brand font-medium'
                    : 'border-gray-200 hover:border-gray-300 text-gray-700'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between px-5 pb-5 gap-3">
          <button onClick={onSkip} className="text-sm text-gray-400 hover:text-gray-600">
            Skip (auto-select)
          </button>
          <button
            onClick={() => selected && onSubmit(selected)}
            disabled={!selected}
            className="btn-primary flex items-center gap-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Use this table
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}
