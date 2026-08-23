import { fmtDays, shortMed, BAND_SEVERITY } from '../format.js'
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
  const rows = facilities ? sortWorstFirst(facilities) : []
  return (
    <section className="panel" aria-labelledby="facilities-h">
      <div className="panel-head">
        <h2 id="facilities-h">Facilities</h2>
        <span className="meta">
          {facilities ? `${facilities.length} primary health centres · worst medicine first · click a row for the per-medicine picture` : ''}
        </span>
        {refreshing ? (
          <span className="right">
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
        <ul className="fac-list">
          {rows.map((f) => {
            const open = f.id === selectedId
            return (
              <li key={f.id} id={`fac-${f.id}`} className={`fac ${open ? 'fac-open' : ''}`}>
                <button type="button" className="fac-head" onClick={() => onToggle(f.id)} aria-expanded={open}>
                  <Dot band={f.band} />
                  <span className="fac-name">
                    {f.name}
                    {f.outbreak_surge ? (
                      <>
                        {' '}
                        <span className="tag tag-surge">outbreak surge</span>
                      </>
                    ) : null}
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
