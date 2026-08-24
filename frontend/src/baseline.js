// Session pre-ingest view (the demo's starting state, regardless of what the
// backend database already stores). The API always reports risk with the
// stored outbreak surge applied; until the user ingests a report in THIS
// session, the district is shown at its routine HMIS baseline by removing
// the surge from every burn rate and recomputing cover and band with the
// backend's own formula and the API-served thresholds. Every input is an
// API number — this file only re-runs stock ÷ burn without the surge term.
import { BAND_SEVERITY } from './format.js'

export function bandFor(days, t) {
  if (days == null || !Number.isFinite(days)) return 'unknown'
  if (days < t.critical_days) return 'critical'
  if (days <= t.warning_days) return 'warning'
  return 'safe'
}

function worse(a, b) {
  if (!a) return b
  if (BAND_SEVERITY[b.band] > BAND_SEVERITY[a.band]) return b
  if (b.band === a.band && b.days != null && (a.days == null || b.days < a.days)) return b
  return a
}

const round2 = (x) => (x == null ? null : Math.round(x * 100) / 100)

export function stripSurge(facilities, t) {
  const out = facilities.map((f) => {
    const meds = {}
    let w = null
    for (const [mid, m] of Object.entries(f.medicines || {})) {
      const burn = Math.max(0, (m.burn_rate || 0) - (m.outbreak_surge || 0))
      const days = burn > 0 ? round2(m.stock / burn) : null
      const band = bandFor(days, t)
      meds[mid] = { ...m, burn_rate: round2(burn), outbreak_surge: 0, days_of_cover: days, band }
      w = worse(w, { mid, name: m.name, days, band })
    }
    return {
      ...f,
      medicines: meds,
      outbreak_surge: false,
      band: w ? w.band : 'unknown',
      worst_medicine_id: w ? w.mid : null,
      worst_medicine_name: w ? w.name : null,
      days_of_cover: w ? w.days : null,
    }
  })
  out.sort(
    (a, b) =>
      BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] ||
      (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9) ||
      a.name.localeCompare(b.name),
  )
  return out
}

export function stripSurgeDetail(detail) {
  const t = detail.thresholds
  const medicines = (detail.medicines || []).map((m) => {
    const burn = m.baseline_burn_rate ?? Math.max(0, (m.burn_rate || 0) - (m.outbreak_surge || 0))
    const days = burn > 0 ? round2(m.stock / burn) : null
    return { ...m, burn_rate: round2(burn), outbreak_surge: 0, days_of_cover: days, band: bandFor(days, t) }
  })
  medicines.sort((a, b) => BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] || a.medicine_id.localeCompare(b.medicine_id))
  let w = null
  for (const m of medicines) w = worse(w, { mid: m.medicine_id, name: m.name, days: m.days_of_cover, band: m.band })
  return {
    ...detail,
    medicines,
    outbreaks_affecting: [],
    band: w ? w.band : 'unknown',
    worst_medicine_id: w ? w.mid : null,
    worst_medicine_name: w ? w.name : null,
    days_of_cover: w ? w.days : null,
  }
}

export function censusOf(facilities) {
  const c = { critical: 0, warning: 0, safe: 0, unknown: 0 }
  for (const f of facilities || []) c[f.band] = (c[f.band] || 0) + 1
  return c
}

// Per-facility {band, days} snapshot, taken the moment Ingest is clicked, so
// the "what changed" list compares the session's before against the live
// after even when the database already held the outbreak rows.
export function bandSnapshot(facilities) {
  const out = {}
  for (const f of facilities || []) out[f.id] = { band: f.band, days: f.days_of_cover }
  return out
}
