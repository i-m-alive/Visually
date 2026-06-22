// Minimal .xlsx writer — produces a single-sheet workbook that opens natively in
// Excel / Google Sheets (a real Office Open XML file, not the HTML-table-as-.xls
// trick, so there's no "format differs from extension" warning). Uses jszip, which
// is already a project dependency (.vly import), so it adds no new packages.

function _colLetter(idx: number): string {
  let n = idx + 1, s = ''
  while (n > 0) {
    const m = (n - 1) % 26
    s = String.fromCharCode(65 + m) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function _esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function _cell(ref: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return `<c r="${ref}"/>`
  if (typeof v === 'number' && isFinite(v)) return `<c r="${ref}"><v>${v}</v></c>`
  if (typeof v === 'boolean') return `<c r="${ref}" t="inlineStr"><is><t>${v ? 'TRUE' : 'FALSE'}</t></is></c>`
  return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${_esc(String(v))}</t></is></c>`
}

/** Download `rows` (keyed by `columns`) as a real .xlsx file. */
export async function downloadXlsx(
  filename: string,
  columns: string[],
  rows: Array<Record<string, unknown>>,
  sheetName = 'Data',
): Promise<void> {
  const { default: JSZip } = await import('jszip')
  const zip = new JSZip()

  const headerRow = `<row r="1">${columns.map((c, i) => _cell(`${_colLetter(i)}1`, c)).join('')}</row>`
  const bodyRows = rows.map((row, ri) =>
    `<row r="${ri + 2}">${columns.map((c, ci) => _cell(`${_colLetter(ci)}${ri + 2}`, row[c])).join('')}</row>`
  ).join('')

  const sheetXml =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
    `<sheetData>${headerRow}${bodyRows}</sheetData></worksheet>`

  const safeSheet = ((sheetName || 'Data').replace(/[\\/?*[\]:]/g, ' ').trim().slice(0, 31)) || 'Data'

  zip.file('[Content_Types].xml',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
    `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
    `<Default Extension="xml" ContentType="application/xml"/>` +
    `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
    `<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>` +
    `</Types>`)
  zip.file('_rels/.rels',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
    `</Relationships>`)
  zip.file('xl/workbook.xml',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
    `<sheets><sheet name="${_esc(safeSheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>`)
  zip.file('xl/_rels/workbook.xml.rels',
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>` +
    `</Relationships>`)
  zip.file('xl/worksheets/sheet1.xml', sheetXml)

  const blob = await zip.generateAsync({
    type: 'blob',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.toLowerCase().endsWith('.xlsx') ? filename : `${filename}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

/** Turn a chart's data rows into (columns, rows), dropping render-only keys
 *  (`fill`, `_`-prefixed helpers like `_trend`) and putting name/value first. */
export function chartToTable(
  data: Array<Record<string, unknown>>,
): { columns: string[]; rows: Array<Record<string, unknown>> } {
  if (!Array.isArray(data) || data.length === 0) return { columns: [], rows: [] }
  const cols: string[] = []
  for (const row of data) {
    for (const k of Object.keys(row)) {
      if (k === 'fill' || k.startsWith('_')) continue
      if (!cols.includes(k)) cols.push(k)
    }
  }
  const rank = (k: string) => (k === 'name' ? 0 : k === 'value' ? 1 : 2)
  cols.sort((a, b) => rank(a) - rank(b))
  return { columns: cols, rows: data }
}
