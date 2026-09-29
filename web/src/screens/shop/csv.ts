/**
 * CSV built in the browser for the Online orders screens. UTF-8 with a BOM so
 * Excel opens it as UTF-8 (the "£" survives), CRLF rows, every cell quoted when
 * it needs to be. Money arrives as integer pence and leaves as "£12.40" text:
 * the sheet gets a figure a person reads, never a float.
 */

/** "£12.40", "-£4.20", or "" for a missing figure (invariant 8: never 0 for unknown). */
export function csvMoney(pence: number | null | undefined): string {
  if (pence === null || pence === undefined) return ''
  const sign = pence < 0 ? '-' : ''
  const abs = Math.abs(pence)
  const whole = Math.floor(abs / 100)
  const frac = abs % 100
  return `${sign}£${whole}.${String(frac).padStart(2, '0')}`
}

function cell(v: string): string {
  return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
}

/** The file's text: a header row, then one row per record. */
export function buildCsv(header: readonly string[], rows: ReadonlyArray<readonly string[]>): string {
  const lines = [header, ...rows].map((r) => r.map(cell).join(','))
  return `﻿${lines.join('\r\n')}\r\n`
}

/** Hands the file to the browser as a download. */
export function downloadCsv(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
