// Display helpers. Every number shown comes from the backend; these only format.

export const BANDS = ['critical', 'warning', 'safe', 'unknown']

export const BAND_LABEL = {
  critical: 'Critical',
  warning: 'Warning',
  safe: 'Safe',
  unknown: 'Unknown',
}

export const BAND_SEVERITY = { critical: 3, warning: 2, safe: 1, unknown: 0 }

export function needsDonors(band) {
  return band === 'critical' || band === 'warning'
}

// Short names for tight spaces (list rows, tags). Tables use the full name from the API.
const MED_SHORT = {
  ors_packets: 'ORS',
  zinc_20mg: 'Zinc 20mg',
  iv_fluids_rl: 'IV fluids (RL)',
  paracetamol_500: 'Paracetamol',
  ciprofloxacin_500: 'Ciprofloxacin',
}

export function shortMed(id, fallback) {
  return MED_SHORT[id] || fallback || id || '—'
}

export const REASON_LABEL = {
  donor_safety_floor: 'Donor safety floor',
  out_of_radius: 'Outside transfer radius',
  no_stock: 'No stock',
  stock_expired: 'Stock expired',
  stock_expiring_in_window: 'Stock expires within the transit-and-use window',
  stock_undated: 'Stock has no expiry date',
  spare_below_one_unit: 'Spare is below one unit',
  expiry_guard: 'Expiry guard',
}

export function reasonLabel(code) {
  return REASON_LABEL[code] || code || 'Rejected'
}

export function fmtDays(d, digits = 1) {
  if (d === null || d === undefined) return '—'
  if (!Number.isFinite(d)) return '∞'
  return d.toFixed(digits)
}

export function fmtRate(r) {
  if (r === null || r === undefined) return '—'
  return Number(r).toFixed(2)
}

export function fmtQty(n) {
  if (n === null || n === undefined) return '—'
  const x = Number(n)
  if (Number.isInteger(x)) return x.toLocaleString('en-IN')
  return x.toLocaleString('en-IN', { maximumFractionDigits: 1 })
}

export function fmtKm(km) {
  if (km === null || km === undefined) return '—'
  return `${Number(km).toFixed(1)} km`
}

export function fmtScore(s) {
  if (s === null || s === undefined) return '—'
  return Number(s).toFixed(3)
}

export function fmtPct(x) {
  if (x === null || x === undefined) return '—'
  return `${Math.round(Number(x) * 100)}%`
}

export function paragraphs(text) {
  return String(text || '')
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
}

export function plural(n, one, many) {
  return n === 1 ? one : many || `${one}s`
}
