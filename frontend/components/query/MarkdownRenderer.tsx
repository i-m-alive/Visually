'use client'

import React from 'react'

interface Props {
  text: string
  className?: string
}

// Handles **bold**, *italic*, `inline code`
// Uses split with capturing group — more reliable than exec loop for long bold spans
function renderInline(text: string): React.ReactNode[] {
  // Order matters: ** before * so double-asterisk bold is matched first
  const segments = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/)
  return segments.map((seg, i) => {
    if (seg.startsWith('**') && seg.endsWith('**') && seg.length > 4)
      return <strong key={i} className="font-semibold text-gray-900">{seg.slice(2, -2)}</strong>
    if (seg.startsWith('*') && seg.endsWith('*') && seg.length > 2)
      return <em key={i} className="italic">{seg.slice(1, -1)}</em>
    if (seg.startsWith('`') && seg.endsWith('`') && seg.length > 2)
      return <code key={i} className="bg-gray-100 text-gray-800 text-[0.82em] px-1 py-0.5 rounded font-mono">{seg.slice(1, -1)}</code>
    return seg
  })
}

export function MarkdownRenderer({ text, className = '' }: Props) {
  if (!text) return null

  const lines = text.split('\n')
  const nodes: React.ReactNode[] = []
  let k = 0
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // ── h1
    if (line.startsWith('# ') && !line.startsWith('## ')) {
      nodes.push(
        <h1 key={k++} className="text-base font-bold text-gray-900 mt-3 mb-1 pb-1 border-b border-gray-200">
          {line.slice(2)}
        </h1>
      )
      i++
      continue
    }

    // ── h2
    if (line.startsWith('## ')) {
      nodes.push(
        <h2 key={k++} className="text-sm font-bold text-gray-900 mt-3 mb-1 pb-1 border-b border-gray-100">
          {line.slice(3)}
        </h2>
      )
      i++
      continue
    }

    // ── h3
    if (line.startsWith('### ')) {
      nodes.push(
        <h3 key={k++} className="text-sm font-semibold text-gray-800 mt-2 mb-0.5">
          {line.slice(4)}
        </h3>
      )
      i++
      continue
    }

    // ── h4
    if (line.startsWith('#### ')) {
      nodes.push(
        <h4 key={k++} className="text-xs font-semibold text-gray-700 mt-1.5 uppercase tracking-wide">
          {line.slice(5)}
        </h4>
      )
      i++
      continue
    }

    // ── horizontal rule
    if (/^[-*_]{3,}$/.test(line.trim())) {
      nodes.push(<hr key={k++} className="my-3 border-gray-200" />)
      i++
      continue
    }

    // ── ordered list — collect consecutive numbered lines
    if (/^\d+\.\s/.test(line)) {
      const items: React.ReactNode[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        const content = lines[i].replace(/^\d+\.\s+/, '')
        items.push(<li key={i} className="leading-relaxed">{renderInline(content)}</li>)
        i++
      }
      nodes.push(
        <ol key={k++} className="list-decimal list-outside pl-4 space-y-0.5 my-1">
          {items}
        </ol>
      )
      continue
    }

    // ── unordered list — collect consecutive bullet lines
    if (/^[-*•]\s/.test(line)) {
      const items: React.ReactNode[] = []
      while (i < lines.length && /^[-*•]\s/.test(lines[i])) {
        const content = lines[i].replace(/^[-*•]\s+/, '')
        items.push(<li key={i} className="leading-relaxed">{renderInline(content)}</li>)
        i++
      }
      nodes.push(
        <ul key={k++} className="list-disc list-outside pl-4 space-y-0.5 my-1">
          {items}
        </ul>
      )
      continue
    }

    // ── markdown table — collect pipe-starting lines, allowing blank lines between rows
    if (line.trimStart().startsWith('|')) {
      const tableLines: string[] = []
      while (i < lines.length) {
        const cur = lines[i]
        if (cur.trimStart().startsWith('|')) {
          tableLines.push(cur)
          i++
        } else if (cur.trim() === '') {
          // Allow blank lines between rows only if the next non-blank line is also a pipe row
          let j = i + 1
          while (j < lines.length && lines[j].trim() === '') j++
          if (j < lines.length && lines[j].trimStart().startsWith('|')) {
            i++ // skip blank, keep collecting
          } else {
            break
          }
        } else {
          break
        }
      }

      // Drop blank lines, keep only real pipe rows
      const pipeRows = tableLines.filter(l => l.trim() !== '')

      // Parse cells from a single row, stripping outer pipes
      const parseCells = (row: string) =>
        row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim())

      // Separator line matches |---|--- pattern; skip it wherever it appears
      const contentRows = pipeRows.filter(r => !/^\s*\|[\s\-|:]+\|\s*$/.test(r))
      const headerCells = parseCells(contentRows[0] ?? '')
      const bodyRows = contentRows.slice(1)

      nodes.push(
        <div key={k++} className="overflow-x-auto my-2">
          <table className="min-w-full text-xs border-collapse">
            <thead>
              <tr className="bg-gray-50">
                {headerCells.map((cell, ci) => (
                  <th
                    key={ci}
                    className="px-3 py-1.5 text-left font-semibold text-gray-700 border border-gray-200 whitespace-nowrap"
                  >
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, ri) => (
                <tr key={ri} className={ri % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {parseCells(row).map((cell, ci) => (
                    <td
                      key={ci}
                      className="px-3 py-1.5 text-gray-700 border border-gray-200"
                    >
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }

    // ── blank line — skip (spacing comes from the container's space-y)
    if (line.trim() === '') {
      i++
      continue
    }

    // ── regular paragraph
    nodes.push(
      <p key={k++} className="leading-relaxed">
        {renderInline(line)}
      </p>
    )
    i++
  }

  return (
    <div className={`text-sm text-gray-800 space-y-1 ${className}`}>
      {nodes}
    </div>
  )
}
