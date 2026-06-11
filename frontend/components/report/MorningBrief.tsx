'use client'
import { useState, useEffect, useRef } from 'react'
import { Loader2, X, Lightbulb } from 'lucide-react'
import { analystApi } from '@/lib/api'

interface Props {
  token: string
  pageName: string
}

export function MorningBrief({ token, pageName }: Props) {
  const [bullets, setBullets] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [error, setError] = useState(false)
  const lastPage = useRef('')

  useEffect(() => {
    if (!pageName || pageName === lastPage.current) return
    lastPage.current = pageName
    setDismissed(false)
    setBullets([])
    setError(false)

    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const prompt =
          `Give exactly 3 short executive bullet-point insights for the "${pageName}" data. ` +
          `Each bullet must start with "•" and be under 15 words. No preamble.`
        const resp = await analystApi.chat(token, { message: prompt })
        const text: string = resp.data.text || ''
        const parsed = text
          .split('\n')
          .map(l => l.replace(/^[•\-\*]\s*/, '').trim())
          .filter(l => l.length > 4)
          .slice(0, 3)
        setBullets(parsed.length ? parsed : [text.slice(0, 120)])
      } catch {
        setError(true)
      } finally {
        setLoading(false)
      }
    }, 400)

    return () => clearTimeout(timer)
  }, [pageName, token])

  if (dismissed) return null
  if (!loading && !bullets.length && !error) return null

  return (
    <div
      className="flex items-start gap-3 px-4 py-3 mx-4 mb-3 rounded-xl flex-shrink-0"
      style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}
    >
      <Lightbulb size={15} className="flex-shrink-0 mt-0.5" style={{ color: '#D97706' }} />

      {loading ? (
        <div className="flex items-center gap-2">
          <Loader2 size={12} className="animate-spin" style={{ color: '#D97706' }} />
          <span className="text-xs" style={{ color: '#92400E' }}>Generating {pageName} insights…</span>
        </div>
      ) : error ? (
        <span className="text-xs" style={{ color: '#92400E' }}>Could not load insights for this page.</span>
      ) : (
        <div className="flex-1 min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wide mb-1.5" style={{ color: '#D97706' }}>
            Morning Brief · {pageName}
          </p>
          <ul className="space-y-1">
            {bullets.map((b, i) => (
              <li key={i} className="flex items-start gap-1.5">
                <span className="mt-0.5 flex-shrink-0" style={{ color: '#D97706', fontSize: 10 }}>•</span>
                <span className="text-xs leading-snug" style={{ color: '#78350F' }}>{b}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        onClick={() => setDismissed(true)}
        className="flex-shrink-0 p-0.5 rounded hover:bg-amber-100 transition-colors"
        style={{ color: '#D97706' }}
      >
        <X size={12} />
      </button>
    </div>
  )
}
