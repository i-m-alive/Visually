'use client'
import { useState } from 'react'
import { Palette, Check } from 'lucide-react'

const THEMES = [
  { id: 'frost',    label: 'Frost',    swatch: '#2563EB', dark: false },
  { id: 'slate',    label: 'Slate',    swatch: '#38bdf8', dark: true  },
  { id: 'sage',     label: 'Sage',     swatch: '#16A34A', dark: false },
  { id: 'ember',    label: 'Ember',    swatch: '#E8520A', dark: false },
  { id: 'obsidian', label: 'Obsidian', swatch: '#00E5C3', dark: true  },
]

interface Props {
  currentTheme: string
  onSelect: (theme: string) => void
  disabled?: boolean
}

export function ThemeSwitcher({ currentTheme, onSelect, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const current = THEMES.find((t) => t.id === currentTheme) ?? THEMES[0]

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="flex items-center gap-2 px-3 py-1.5 text-sm border border-gray-200 rounded-lg bg-white hover:bg-gray-50 transition-colors disabled:opacity-50"
        title="Change dashboard theme"
      >
        <span
          className="w-3.5 h-3.5 rounded-full"
          style={{ background: current.swatch }}
        />
        <Palette size={14} className="text-gray-500" />
        <span className="text-gray-700 font-500">{current.label}</span>
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          {/* Dropdown */}
          <div className="absolute right-0 top-full mt-1 z-20 bg-white border border-gray-200 rounded-xl shadow-lg py-1 min-w-[160px]">
            {THEMES.map((t) => (
              <button
                key={t.id}
                onClick={() => {
                  onSelect(t.id)
                  setOpen(false)
                }}
                className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors text-left"
              >
                <span
                  className="w-4 h-4 rounded-full flex-shrink-0"
                  style={{ background: t.swatch }}
                />
                <span className="text-sm text-gray-700 flex-1">{t.label}</span>
                {t.id === currentTheme && (
                  <Check size={14} className="text-blue-600 flex-shrink-0" />
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
