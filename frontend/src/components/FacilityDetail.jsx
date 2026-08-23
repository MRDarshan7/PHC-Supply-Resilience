import { useEffect, useRef, useState } from 'react'
import { fmtDays, fmtQty, fmtRate, needsDonors, shortMed, BAND_SEVERITY } from '../format.js'
import { Band, Waiting, ErrorNote } from './common.jsx'
import Recommendation from './Recommendation.jsx'

// Fixed row order so a medicine stays in place when its band changes —
// the ORS row turning green while the rows below stay red is the point.
const MED_ORDER = ['ors_packets', 'zinc_20mg', 'iv_fluids_rl', 'paracetamol_500', 'ciprofloxacin_500']
const medOrder = (m) => {
  const i = MED_ORDER.indexOf(m.medicine_id)
  return (i === -1 ? MED_ORDER.length : i) + (m.band === 'unknown' ? 100 : 0)
}

function joinNames(names) {
  if (names.length <= 1) return names[0] || ''
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

function ExpiryTag({ b }) {
  if (b.expired) return <span className="tag tag-expiry">expired</span>
  if (b.near_expiry) return <span className="tag tag-expiry">{b.days_to_expiry} d left</span>
  return null
}

// One lot: batch and expiry. Several: the count and the earliest expiry, so
// a near-expiry tag is never pushed out of view; the full list is the title.
function Lots({ batches }) {
  if (!batches?.length) return null
  const full = batches.map((b) => `${b.batch} exp ${b.expiry}`).join(', ')
  if (batches.length === 1) {
    const b = batches[0]
    return (
      <span className="lots" title={full}>
        {b.batch} exp {b.expiry} <ExpiryTag b={b} />
      </span>
    )
  }
  const earliest = [...batches].sort((a, b) => String(a.expiry).localeCompare(String(b.expiry)))[0]
  return (
    <span className="lots" title={full}>
      {batches.length} lots · exp {earliest.expiry} <ExpiryTag b={earliest} />
    </span>
  )
}

function MedicineRow({ m, rec, onFindDonors, busy, changed }) {
  const unknown = m.band === 'unknown'
  const approved = rec?.approvals?.length > 0
  return (
    <tr className={`m-${m.band} ${changed ? 'm-changed' : ''}`} data-med={m.medicine_id}>
      <td className="medname">
        {m.name}
        {unknown ? <span className="lots">no stock, no recorded demand</span> : <Lots batches={m.batches} />}
      </td>
      <td className="num tnum">
        {unknown ? '—' : fmtQty(m.stock)} <span className="muted xs">{unknown ? '' : m.unit}</span>
      </td>
      <td className="num tnum burn">
        {unknown ? '—' : fmtRate(m.burn_rate)}
        {!unknown && m.outbreak_surge > 0 ? (
          <span className="parts">
            {fmtRate(m.baseline_burn_rate)} + <span className="surge">{fmtRate(m.outbreak_surge)} surge</span>
          </span>
        ) : !unknown ? (
          <span className="parts">baseline, no surge</span>
        ) : null}
      </td>
      <td className="num days">
        {unknown ? '—' : fmtDays(m.days_of_cover)}
        {unknown ? null : <small>d</small>}
        <Band band={m.band} />
      </td>
      <td className="act">
        {approved ? (
          <span className="tag tag-ok">✓ approved</span>
        ) : needsDonors(m.band) ? (
          <button type="button" className="btn btn-sm btn-outline" onClick={() => onFindDonors(m.medicine_id)} disabled={busy || rec?.loading}>
            {rec?.loading ? 'Finding…' : rec?.data ? 'Refresh' : 'Find donors'}
          </button>
        ) : null}
      </td>
    </tr>
  )
}

// What the approvals so far mean for the facility as a whole. This is the
// line the demo turns on: one medicine safe, the facility still critical.
function FacilityStatus({ facility, meds, approvedMeds }) {
  if (!approvedMeds.length) return null
  const remaining = meds.filter((m) => needsDonors(m.band))
  const approvedNames = joinNames(approvedMeds.map((m) => shortMed(m.medicine_id, m.name)))
  if (!remaining.length) {
    return (
      <div className="status status-ok" data-status="resolved">
        <b>{approvedNames}</b> {approvedMeds.length === 1 ? 'transfer' : 'transfers'} approved — <b>{facility.name}</b> is now safe on every medicine and its map marker
        has turned green.
      </div>
    )
  }
  const band = remaining.some((m) => m.band === 'critical') ? 'critical' : 'warning'
  return (
    <div className={`status status-${band}`} data-status="partial">
      <b>{approvedNames}</b> approved and now safe here — but <b>{facility.name}</b> and its marker stay <Band band={band} />:{' '}
      <b>{joinNames(remaining.map((m) => shortMed(m.medicine_id, m.name)))}</b> {remaining.length === 1 ? 'is' : 'are'} still below the line. Risk belongs to each
      (facility, medicine) pair, not to the facility.
    </div>
  )
}

export default function FacilityDetail({ facility, detail, recs, medNames, approveAll, onFindDonors, onApprove, onApproveAll, onRetryDetail, onClose }) {
  const data = detail?.data
  const loading = detail?.loading
  const error = detail?.error

  // Flash a medicine row whose band just changed (an approval landed). The
  // previous payload is kept in state and compared during render — the
  // "store information from previous renders" pattern — so no effect is
  // needed. The component is keyed by facility, so state resets on switch.
  const [prevData, setPrevData] = useState(null)
  const [changed, setChanged] = useState(() => new Set())
  if (data !== prevData) {
    setPrevData(data)
    const next = new Set()
    if (prevData && data) {
      for (const m of data.medicines) {
        const before = prevData.medicines.find((x) => x.medicine_id === m.medicine_id)
        if (before && before.band !== m.band) next.add(m.medicine_id)
      }
    }
    setChanged(next)
  }

  const meds = data ? [...data.medicines].sort((a, b) => medOrder(a) - medOrder(b) || BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band]) : []
  const recFor = (m) => recs[`${facility.id}/${m.medicine_id}`]
  const pending = meds.filter((m) => needsDonors(m.band))
  const approvedMeds = meds.filter((m) => recFor(m)?.approvals?.length > 0)
  const running = approveAll?.running && approveAll.facilityId === facility.id
  const medsWithRecs = meds.filter((m) => recFor(m))
  // An approval collapses its recommendation to one line; bring the panel
  // back to the top so the facility header, the status banner and the
  // per-medicine table are in frame with the map.
  const rootRef = useRef(null)
  const nApproved = approvedMeds.length
  useEffect(() => {
    if (!nApproved) return
    rootRef.current?.closest('.rail-detail')?.scrollTo({ top: 0, behavior: 'smooth' })
  }, [nApproved])

  const worstBand = data?.band || facility.band
  const worstDays = data ? data.days_of_cover : facility.days_of_cover
  const worstMed = data ? data.worst_medicine_id : facility.worst_medicine_id

  return (
    <div data-detail={facility.id} ref={rootRef}>
      <div className="det-head">
        <div className="ident">
          <h2>{facility.name}</h2>
          <div className="sub">
            {facility.sub_district} · primary health centre
            {loading && data ? (
              <>
                {' '}
                · <Waiting inline label="updating" slow={null} />
              </>
            ) : null}
          </div>
        </div>
        <div className="worst">
          <span className={`days c-${worstBand}`}>
            {worstDays == null ? '—' : fmtDays(worstDays)}
            {worstDays == null ? null : <small>d</small>}
          </span>
          <span className="lbl">
            <Band band={worstBand} />
            {worstMed ? ` ${shortMed(worstMed, data?.worst_medicine_name || facility.worst_medicine_name)}` : ''}
          </span>
        </div>
        {pending.length > 0 ? (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => onApproveAll(facility.id)} disabled={running} data-action="approve-all">
            {running ? (
              <>
                <span className="spinner light" /> {approveAll.step || 'Working…'}
              </>
            ) : (
              `Approve all recommended (${pending.length})`
            )}
          </button>
        ) : null}
        <button type="button" className="btn-icon" onClick={onClose} aria-label="Close facility" title="Close">
          ×
        </button>
      </div>

      <div className="det-body">
        {loading && !data ? <Waiting label={`Loading per-medicine stock for ${facility.name}…`} /> : null}
        {error && !data ? <ErrorNote title="Could not load facility detail" error={error} onRetry={onRetryDetail} /> : null}
        {data ? (
          <>
            <div className="ctx">
              {data.outbreaks_affecting?.length ? (
                data.outbreaks_affecting.map((o) => (
                  <div key={o.outbreak_id}>
                    <b>{o.disease}</b>, {fmtQty(o.cases)} cases in {o.sub_district || o.district}
                    {o.localised_here ? ' — localised here' : ''}: <b>+{fmtRate(o.cases_per_day)} cases/day</b> allocated to this PHC →
                    <span className="burns">
                      {Object.entries(o.extra_burn).map(([mid, v]) => (
                        <span key={mid}>
                          {shortMed(mid, medNames[mid])} <span className="surge">+{fmtRate(v)}/day</span>
                        </span>
                      ))}
                    </span>
                    . Stock unchanged; consumption rose.
                  </div>
                ))
              ) : (
                <span>No outbreak surge at this facility — consumption is the HMIS baseline.</span>
              )}
            </div>

            {approveAll?.error && approveAll.facilityId === facility.id ? <ErrorNote title="Approve all stopped" error={approveAll.error} /> : null}
            <FacilityStatus facility={facility} meds={meds} approvedMeds={approvedMeds} />

            <div>
              <div className="sec-title" style={{ marginBottom: 5 }}>
                Stock by medicine
                <span className="right muted">
                  critical &lt; {data.thresholds.critical_days} d · warning {data.thresholds.critical_days}–{data.thresholds.warning_days} d · safe &gt;{' '}
                  {data.thresholds.warning_days} d
                </span>
              </div>
              <table className="med-tbl">
                <colgroup>
                  <col />
                  <col className="c-stock" />
                  <col className="c-burn" />
                  <col className="c-cover" />
                  <col className="c-act" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th className="num">Stock</th>
                    <th className="num">Burn /day</th>
                    <th className="num">Cover</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {meds.map((m) => (
                    <MedicineRow key={m.medicine_id} m={m} rec={recFor(m)} onFindDonors={onFindDonors} busy={running} changed={changed.has(m.medicine_id)} />
                  ))}
                </tbody>
              </table>
            </div>

            {medsWithRecs.map((m) => (
              <Recommendation
                key={`${facility.id}/${m.medicine_id}`}
                rec={recFor(m)}
                medicine={m}
                onApprove={() => onApprove(facility.id, m.medicine_id)}
                onRetry={() => onFindDonors(m.medicine_id)}
              />
            ))}
          </>
        ) : null}
      </div>
    </div>
  )
}
