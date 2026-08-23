import { fmtDays, fmtQty, fmtRate, needsDonors, shortMed, BAND_SEVERITY } from '../format.js'
import { Band, Waiting, ErrorNote } from './common.jsx'
import { useMediaQuery, PHONE_QUERY } from '../hooks.js'
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

function Lots({ batches }) {
  if (!batches?.length) return null
  return (
    <span className="lots">
      {batches.map((b, i) => (
        <span key={b.batch}>
          {i ? ' · ' : ''}
          <span className="nowrap">{b.batch}</span> exp <span className="nowrap">{b.expiry}</span>
          {b.expired ? (
            <>
              {' '}
              <span className="tag tag-expiry">expired</span>
            </>
          ) : b.near_expiry ? (
            <>
              {' '}
              <span className="tag tag-expiry">{b.days_to_expiry} d left</span>
            </>
          ) : null}
        </span>
      ))}
    </span>
  )
}

function action(m, rec, onFindDonors, busy) {
  if (rec?.approvals?.length > 0) return <span className="tag tag-ok">approved</span>
  if (!needsDonors(m.band)) return null
  return (
    <button type="button" className="btn btn-sm btn-primary" onClick={() => onFindDonors(m.medicine_id)} disabled={busy || rec?.loading}>
      {rec?.loading ? 'Finding…' : rec?.data ? 'Refresh donors' : 'Find donors'}
    </button>
  )
}

function MedicineRow({ m, rec, onFindDonors, busy }) {
  const unknown = m.band === 'unknown'
  return (
    <tr className={`m-${m.band}`}>
      <td className="medname">
        {m.name}
        {unknown ? <span className="lots">no stock, no recorded demand</span> : <Lots batches={m.batches} />}
      </td>
      <td className="num">
        {unknown ? '—' : fmtQty(m.stock)} <span className="muted small">{unknown ? '' : m.unit}</span>
      </td>
      <td className="num">{unknown ? '—' : fmtRate(m.baseline_burn_rate)}</td>
      <td className={`num ${m.outbreak_surge > 0 ? 'surge-pos' : 'muted'}`}>{unknown ? '—' : m.outbreak_surge > 0 ? `+${fmtRate(m.outbreak_surge)}` : '0'}</td>
      <td className="num">{unknown ? '—' : fmtRate(m.burn_rate)}</td>
      <td className="num days">{unknown ? '—' : fmtDays(m.days_of_cover)}</td>
      <td>
        <Band band={m.band} />
      </td>
      <td className="nowrap act">{action(m, rec, onFindDonors, busy)}</td>
    </tr>
  )
}

// Phone layout: one card per medicine, the verdict (days + band) first.
function MedicineCard({ m, rec, onFindDonors, busy }) {
  const unknown = m.band === 'unknown'
  return (
    <div className={`med-card m-${m.band}`}>
      <div className="med-card-head">
        <span className="medname">{m.name}</span>
        <span className="med-card-verdict">
          <span className="days">{unknown ? '—' : `${fmtDays(m.days_of_cover)} d`}</span>
          <Band band={m.band} />
        </span>
      </div>
      {unknown ? (
        <div className="small muted">no stock, no recorded demand</div>
      ) : (
        <div className="small">
          <b>
            {fmtQty(m.stock)} {m.unit}
          </b>{' '}
          · <b>{fmtRate(m.burn_rate)}/day</b>
          <span className="muted">
            {' '}
            ({fmtRate(m.baseline_burn_rate)} baseline
            {m.outbreak_surge > 0 ? (
              <>
                {' '}
                + <span className="surge-pos">{fmtRate(m.outbreak_surge)} surge</span>
              </>
            ) : null}
            )
          </span>
          <div className="muted">
            <Lots batches={m.batches} />
          </div>
        </div>
      )}
      <div className="med-card-action">{action(m, rec, onFindDonors, busy)}</div>
    </div>
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
      <div className="status status-ok">
        <b>{approvedNames}</b> {approvedMeds.length === 1 ? 'transfer' : 'transfers'} approved — <b>{facility.name}</b> is safe on every medicine. Its map marker is
        now green.
      </div>
    )
  }
  const band = remaining.some((m) => m.band === 'critical') ? 'critical' : 'warning'
  return (
    <div className={`status status-${band}`}>
      <b>{approvedNames}</b> approved and now safe here — but <b>{facility.name}</b> and its map marker stay <Band band={band} />:{' '}
      <b>{joinNames(remaining.map((m) => m.name))}</b> {remaining.length === 1 ? 'is' : 'are'} still below the line. Risk belongs to each (facility, medicine) pair,
      not to the facility.
    </div>
  )
}

export default function FacilityDetail({ facility, detail, recs, medNames, approveAll, onFindDonors, onApprove, onApproveAll, onRetryDetail }) {
  const phone = useMediaQuery(PHONE_QUERY)
  if (!detail) return null
  const { loading, error, data } = detail
  if (loading && !data) {
    return (
      <div className="fac-detail">
        <Waiting label={`Loading per-medicine stock for ${facility.name}…`} />
      </div>
    )
  }
  if (error && !data) {
    return (
      <div className="fac-detail">
        <ErrorNote title="Could not load facility detail" error={error} onRetry={onRetryDetail} />
      </div>
    )
  }
  const meds = [...data.medicines].sort((a, b) => medOrder(a) - medOrder(b) || BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band])
  const recFor = (m) => recs[`${facility.id}/${m.medicine_id}`]
  const pending = meds.filter((m) => needsDonors(m.band))
  const approvedMeds = meds.filter((m) => recFor(m)?.approvals?.length > 0)
  const running = approveAll?.running && approveAll.facilityId === facility.id
  const medsWithRecs = meds.filter((m) => recFor(m))

  return (
    <div className="fac-detail">
      <div className="detail-bar">
        {data.outbreaks_affecting?.length ? (
          <span className="detail-ctx">
            {data.outbreaks_affecting.map((o) => (
              <span key={o.outbreak_id}>
                <b>{o.disease}</b>, {o.cases} cases, {o.sub_district || o.district}
                {o.localised_here ? ' — localised here' : ''}: +{fmtRate(o.cases_per_day)} cases/day →{' '}
                {Object.entries(o.extra_burn)
                  .map(([mid, v]) => `${shortMed(mid, medNames[mid])} +${fmtRate(v)}`)
                  .join(', ')}{' '}
                per day. Stock unchanged; consumption rose.
              </span>
            ))}
          </span>
        ) : (
          <span className="detail-ctx muted">No outbreak surge at this facility.</span>
        )}
        {loading ? <span className="small muted nowrap">updating…</span> : null}
        {pending.length > 0 ? (
          <button type="button" className="btn btn-approve btn-sm" onClick={() => onApproveAll(facility.id)} disabled={running}>
            {running ? (
              <>
                <span className="spinner light" /> {approveAll.step || 'Working…'}
              </>
            ) : (
              <>
                Approve all recommended ({pending.length} {pending.length === 1 ? 'medicine' : 'medicines'})
              </>
            )}
          </button>
        ) : null}
      </div>
      {approveAll?.error && approveAll.facilityId === facility.id ? <ErrorNote title="Approve all stopped" error={approveAll.error} /> : null}
      <FacilityStatus facility={facility} meds={meds} approvedMeds={approvedMeds} />

      {phone ? (
        <div className="med-cards">
          {meds.map((m) => (
            <MedicineCard key={m.medicine_id} m={m} rec={recFor(m)} onFindDonors={onFindDonors} busy={running} />
          ))}
        </div>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl med-tbl">
            <thead>
              <tr>
                <th>Medicine</th>
                <th className="num">Stock</th>
                <th className="num">Baseline /day</th>
                <th className="num">Surge /day</th>
                <th className="num">Burn /day</th>
                <th className="num">Days of cover</th>
                <th>Band</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {meds.map((m) => (
                <MedicineRow key={m.medicine_id} m={m} rec={recFor(m)} onFindDonors={onFindDonors} busy={running} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {medsWithRecs.map((m) => (
        <Recommendation
          key={`${facility.id}/${m.medicine_id}`}
          rec={recFor(m)}
          medicine={m}
          onApprove={() => onApprove(facility.id, m.medicine_id)}
          onRetry={() => onFindDonors(m.medicine_id)}
        />
      ))}
    </div>
  )
}
