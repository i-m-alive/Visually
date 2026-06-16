'use client'
import { useRouter } from 'next/navigation'
import { Zap, BarChart2, ArrowRight, Layers } from 'lucide-react'

const C = { navy: '#0a2540', teal: '#00a9d4', teal2: '#16c0e8', bg: '#f0f4f8' }

export default function IntelligenceLandingPage() {
  const router = useRouter()

  return (
    <div className="flex-1 flex flex-col min-h-0" style={{ background: C.bg }}>
      {/* Header */}
      <div style={{ background: 'white', borderBottom: '1px solid #e8eef5', padding: '14px 28px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 32, height: 32, borderRadius: 9, background: `linear-gradient(135deg,${C.teal},${C.teal2})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Zap size={15} style={{ color: 'white' }} />
        </div>
        <div>
          <h1 style={{ fontSize: 15, fontWeight: 700, color: C.navy, margin: 0 }}>Intelligence</h1>
          <p style={{ fontSize: 11, color: '#6b7c93', margin: 0 }}>Executive AI analytics for your reports</p>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
        <div style={{ maxWidth: 560, width: '100%', textAlign: 'center' }}>
          {/* Icon */}
          <div style={{
            width: 72, height: 72, borderRadius: 20,
            background: `linear-gradient(135deg,${C.teal},${C.teal2})`,
            display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px',
            boxShadow: `0 8px 24px ${C.teal}35`,
          }}>
            <Zap size={32} style={{ color: 'white' }} />
          </div>

          <h2 style={{ fontSize: 24, fontWeight: 800, color: C.navy, margin: '0 0 12px' }}>
            Select a Report to Open Intelligence
          </h2>
          <p style={{ fontSize: 14, color: '#6b7c93', lineHeight: 1.7, margin: '0 0 36px' }}>
            Intelligence is report-specific. Open any dashboard or canvas report to get
            AI-powered executive analytics, trend analysis, and an AI copilot scoped to that report&apos;s data.
          </p>

          {/* How to open */}
          <div style={{ background: 'white', borderRadius: 16, padding: 24, border: '1px solid #e8eef5', textAlign: 'left', marginBottom: 28 }}>
            <h3 style={{ fontSize: 13, fontWeight: 700, color: C.navy, margin: '0 0 16px' }}>How to open Intelligence</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {[
                { icon: <BarChart2 size={16} />, title: 'From Dashboard', desc: 'Hover over a report card → click the teal Zap icon, or use the Zap button in list view.' },
                { icon: <Layers size={16} />, title: 'From Canvas list', desc: 'Hover over a canvas card → click the Zap icon that appears in the top-right corner.' },
                { icon: <Zap size={16} />, title: 'From Canvas editor', desc: 'Open a canvas → click the teal "Intelligence" button in the toolbar next to "Visually".' },
              ].map(({ icon, title, desc }) => (
                <div key={title} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                  <div style={{ width: 32, height: 32, borderRadius: 9, background: `${C.teal}12`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, color: C.teal }}>
                    {icon}
                  </div>
                  <div>
                    <p style={{ fontSize: 13, fontWeight: 700, color: C.navy, margin: '0 0 3px' }}>{title}</p>
                    <p style={{ fontSize: 12, color: '#6b7c93', margin: 0, lineHeight: 1.5 }}>{desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <button
            onClick={() => router.push('/projects')}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8,
              padding: '10px 24px', borderRadius: 10,
              background: `linear-gradient(135deg,${C.teal},${C.teal2})`,
              color: 'white', fontSize: 13, fontWeight: 600,
              border: 'none', cursor: 'pointer',
              boxShadow: `0 4px 14px ${C.teal}40`,
            }}
          >
            Go to Projects <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}
