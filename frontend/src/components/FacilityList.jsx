import { useState } from 'react'
import { fmtDays, needsDonors, shortMed, BAND_SEVERITY } from '../format.js'
import { Band, Dot, Waiting, ErrorNote } from './common.jsx'
import FacilityDetail from './FacilityDetail.jsx'

function sortWorstFirst(list) {
  return [...list].sort(
    (a, b) =>
      BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] ||
      (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9) ||
      a.name.localeCompare(b.name),
  )
}

export default function FacilityList({ facilities, loading, error, refreshing, onRetry, selectedId, onToggle, details, recs, medNames, approveAll, onFindDonors, onApprove, onApproveAll, onRetryDetail }) {
  // Safe facilities are already visible as green dots; the list defaults to
  // the ones that need attention. The selected facility always stays listed,
  // so a row does not vanish the moment its transfers make it safe.
  const [showAll, setShowAll] = useState(false)
  const all = facilities ? sortWorstFirst(facilities) : []
  const attention = all.filter((f) => needsDonors(f.band))
  const rows = showAll ? all : all.filter((f) => needsDonors(f.band) || f.id === selectedId)

  return (
    <section className="panel" aria-labelledby="facilities-h">
      <div className="panel-head">
        <h2 id="facilities-h">Facilities</h2>
        {facilities ? (
          <span className="meta">
            {showAll ? (
              <>
                all {all.length} PHCs, worst first
              </>
            ) : attention.length ? (
              <>
                <b>{attention.length}</b> of {all.length} PHCs at warning or worse, worst first
              </>
            ) : (
              <>none of the {all.length} PHCs is at warning or worse</>
            )}
          </span>
        ) : null}
        {facilities ? (
          <button type="button" className="btn-link right" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Show only warning and critical' : `Show all ${all.length}`}
          </button>
        ) : null}
        {refreshing ? (
          <span className="small muted">
            <Waiting inline label="Updating…" />
          </span>
        ) : null}
      </div>
      <div className="panel-body tight">
        {loading && !facilities ? (
          <div style={{ padding: 12 }}>
            <Waiting label="Loading facilities and risk bands…" />
          </div>
        ) : null}
        {error && !facilities ? (
          <div style={{ padding: 12 }}>
            <ErrorNote title="Could not load facilities" error={error} onRetry={onRetry} />
          </div>
        ) : null}
        {facilities && rows.length === 0 ? (
          <p className="muted small" style={{ margin: 0, padding: '8px 12px' }}>
            Every facility is above the warning line. Click a marker on the map to inspect one.
          </p>
        ) : null}
        <ul className="fac-list">
          {rows.map((f) => {
            const open = f.id === selectedId
            return (
              <li key={f.id} id={`fac-${f.id}`} className={`fac ${open ? 'fac-open' : ''}`}>
                <button type="button" className="fac-head" onClick={() => onToggle(f.id)} aria-expanded={open}>
                  <Dot band={f.band} />
                  <span className="fac-name">
                    {f.name}
                    <span className="sub">{f.sub_district}</span>
                  </span>
                  <span className="fac-worst">
                    <Band band={f.band} />
                    <span className="fac-days">{f.days_of_cover == null ? '—' : `${fmtDays(f.days_of_cover)} d`}</span>
                    <span className="fac-med">{f.worst_medicine_id ? shortMed(f.worst_medicine_id, f.worst_medicine_name) : ''}</span>
                  </span>
                  <span className="chev" aria-hidden="true">
                    {open ? '▾' : '▸'}
                  </span>
                </button>
                {open ? (
                  <FacilityDetail
                    facility={f}
                    detail={details[f.id]}
                    recs={recs}
                    medNames={medNames}
                    approveAll={approveAll}
                    onFindDonors={(mid) => onFindDonors(f.id, mid)}
                    onApprove={onApprove}
                    onApproveAll={onApproveAll}
                    onRetryDetail={() => onRetryDetail(f.id)}
                  />
                ) : null}
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
