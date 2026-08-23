import { fmtDays, fmtQty, fmtRate, needsDonors, BAND_SEVERITY } from '../format.js'

import { Band, Waiting, ErrorNote } from './common.jsx'
import { useMediaQuery, PHONE_QUERY } from '../hooks.js'
import Recommendation from './Recommendation.jsx'

const MED_ORDER = ['ors_packets', 'zinc_20mg', 'iv_fluids_rl', 'paracetamol_500', 'ciprofloxacin_500']
const medOrder = (m) => {
  const i = MED_ORDER.indexOf(m.medicine_id)
  return (i === -1 ? MED_ORDER.length : i) + (m.band === 'unknown' ? 100 : 0)
}

function LotsLine({ batches, unit }) {
  if (!batches?.length) return null
  return (
    <span className="lots">
      {batches.map((b) => (
        <span key={b.batch}>
          lot <span className="nowrap">{b.batch}</span> · {fmtQty(b.qty)} {unit} · exp <span className="nowrap">{b.expiry}</span>
          {b.expired ? (
            <>
              {' '}
              <span className="tag tag-expiry">expired</span>
            </>
          ) : b.near_expiry ? (
            <>
              {' '}
              <span className="tag tag-expiry">{b.days_to_expiry} d to expiry</span>
            </>
          ) : null}{' '}
        </span>
      ))}
    </span>
  )
}

function medicineAction(m, rec, onFindDonors, busy) {
  if (rec?.approvals?.length > 0) {
    return (
      <span className="tag" style={{ color: 'var(--safe)', borderColor: '#a9d5ad' }}>
        transfer approved
      </span>
    )
  }
  if (!needsDonors(m.band)) return null
  return (
    <button type="button" className="btn btn-sm btn-primary" onClick={() => onFindDonors(m.medicine_id)} disabled={busy || rec?.loading}>
      {rec?.loading ? 'Finding…' : rec?.data ? 'Refresh donors' : 'Find donors'}
    </button>
  )
}

function MedicineRow({ m, rec, onFindDonors, busy }) {
  const isUnknown = m.band === 'unknown'
  return (
    <tr className={`m-${m.band}`}>
      <td className="medname">
        {m.name}
        {isUnknown ? <span className="lots">no stock and no recorded demand</span> : <LotsLine batches={m.batches} unit={m.unit} />}
      </td>
      <td className="num">
        {isUnknown ? '—' : fmtQty(m.stock)} <span className="muted small">{isUnknown ? '' : m.unit}</span>
      </td>
      <td className="num">{isUnknown ? '—' : fmtRate(m.baseline_burn_rate)}</td>
      <td className={`num ${m.outbreak_surge > 0 ? 'surge-pos' : 'muted'}`}>{isUnknown ? '—' : m.outbreak_surge > 0 ? `+${fmtRate(m.outbreak_surge)}` : '0'}</td>
      <td className="num">{isUnknown ? '—' : fmtRate(m.burn_rate)}</td>
      <td className="num days">{isUnknown ? '—' : fmtDays(m.days_of_cover)}</td>
      <td>
        <Band band={m.band} />
      </td>
      <td className="nowrap">{medicineAction(m, rec, onFindDonors, busy)}</td>
    </tr>
  )
}

// Phone layout: one card per medicine, the verdict (days + band) first.
function MedicineCard({ m, rec, onFindDonors, busy }) {
  const isUnknown = m.band === 'unknown'
  return (
    <div className={`med-card m-${m.band}`}>
      <div className="med-card-head">
        <span className="medname">{m.name}</span>
        <span className="med-card-verdict">
          <span className="days">{isUnknown ? '—' : `${fmtDays(m.days_of_cover)} d`}</span>
          <Band band={m.band} />
        </span>
      </div>
      {isUnknown ? (
        <div className="small muted">no stock and no recorded demand</div>
      ) : (
        <>
          <div className="small">
            Stock{' '}
            <b>
              {fmtQty(m.stock)} {m.unit}
            </b>{' '}
            · burn <b>{fmtRate(m.burn_rate)}/day</b>
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
          </div>
          <div className="small muted">
            <LotsLine batches={m.batches} unit={m.unit} />
          </div>
        </>
      )}
      <div className="med-card-action">{medicineAction(m, rec, onFindDonors, busy)}</div>
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
  // Fixed row order so a medicine stays in place when its band changes —
  // the ORS row turning green while the rows below stay red is the point.
  const meds = [...data.medicines].sort((a, b) => medOrder(a) - medOrder(b) || BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band])
  const pending = meds.filter((m) => needsDonors(m.band))
  const running = approveAll?.running && approveAll.facilityId === facility.id
  const medsWithRecs = meds.filter((m) => recs[`${facility.id}/${m.medicine_id}`])
  const t = data.thresholds || {}

  return (
    <div className="fac-detail">
      <div className="detail-top">
        <span className="ctx">
          Worst medicine: <b>{data.worst_medicine_name || '—'}</b> at <b>{fmtDays(data.days_of_cover)} days</b> of cover. Bands: critical below{' '}
          {t.critical_days} days · warning {t.critical_days}–{t.warning_days} · safe above {t.warning_days}.
        </span>
        {loading ? <span className="small muted">updating…</span> : null}
        {pending.length > 0 ? (
          <button type="button" className="btn btn-approve btn-sm" onClick={() => onApproveAll(facility.id)} disabled={running} style={{ marginLeft: 'auto' }}>
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
      {approveAll?.summary && approveAll.facilityId === facility.id && !running ? (
        <div className="note note-ok">
          <b>{approveAll.summary}</b>
        </div>
      ) : null}

      {data.outbreaks_affecting?.length ? (
        <div className="note note-warn" style={{ marginTop: 0 }}>
          {data.outbreaks_affecting.map((o) => (
            <div key={o.outbreak_id}>
              <b>{o.disease}</b> outbreak, {o.cases} cases in {o.sub_district || o.district} (IDSP {o.outbreak_id})
              {o.localised_here ? ' — localised to this facility' : ''}: {fmtRate(o.cases_per_day)} extra cases/day allocated here, raising
              consumption of{' '}
              {Object.entries(o.extra_burn)
                .map(([mid, v]) => `${medNames[mid] || mid} by ${fmtRate(v)}/day`)
                .join(', ')}
              . Stock is unchanged; the burn rate is what moved.
            </div>
          ))}
        </div>
      ) : null}

      {phone ? (
        <div className="med-cards">
          {meds.map((m) => (
            <MedicineCard key={m.medicine_id} m={m} rec={recs[`${facility.id}/${m.medicine_id}`]} onFindDonors={onFindDonors} busy={running} />
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
                <th className="num">Outbreak surge /day</th>
                <th className="num">Total burn /day</th>
                <th className="num">Days of cover</th>
                <th>Band</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {meds.map((m) => (
                <MedicineRow key={m.medicine_id} m={m} rec={recs[`${facility.id}/${m.medicine_id}`]} onFindDonors={onFindDonors} busy={running} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {medsWithRecs.map((m) => {
        const key = `${facility.id}/${m.medicine_id}`
        const remaining = meds.filter((x) => x.medicine_id !== m.medicine_id && needsDonors(x.band)).map((x) => ({ name: x.name, band: x.band }))
        return (
          <Recommendation
            key={key}
            rec={recs[key]}
            medicine={m}
            recipientName={facility.name}
            medNames={medNames}
            remaining={remaining}
            onApprove={() => onApprove(facility.id, m.medicine_id)}
            onRetry={() => onFindDonors(m.medicine_id)}
          />
        )
      })}
    </div>
  )
}
