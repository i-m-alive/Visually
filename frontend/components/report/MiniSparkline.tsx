interface Props {
  values: number[]
  width?: number
  height?: number
  color?: string
  filled?: boolean
}

export function MiniSparkline({ values, width = 64, height = 22, color = '#2563EB', filled = false }: Props) {
  if (!values || values.length < 2) return null

  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1

  const toX = (i: number) => (i / (values.length - 1)) * width
  const toY = (v: number) => height - ((v - min) / range) * (height - 2) - 1

  const pts = values.map((v, i) => `${toX(i)},${toY(v)}`)
  const polyline = pts.join(' ')
  const fillPath = `M${pts[0]} ${pts.slice(1).map(p => `L${p}`).join(' ')} L${width},${height} L0,${height} Z`

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ overflow: 'visible', display: 'block' }}>
      {filled && <path d={fillPath} fill={color} fillOpacity={0.12} />}
      <polyline points={polyline} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
