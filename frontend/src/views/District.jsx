// 01 · District — census, risk map, outbreaks, facility index.
// Everything on screen is read from GET /facilities, GET /outbreaks and the
// thresholds object served by the facility-detail endpoint.
import { BANDS, fmtRate, shortMed } from '../format.js'
import { SecHead, Swatch, Chip, Dc, ErrBox, Steps } from '../ui.jsx'
import MapPanel from '../MapPanel.jsx'

function bandCounts(facilities) {
  const c = { critical: 0, warning: 0, safe: 0, unknown: 0 }
  for (const f of facilities || []) c[f.band] = (c[f.band] || 0) + 1
  return c
}

function bandSub(b, t) {
  if (!t) return ''
  if (b === 'critical') return `under ${t.critical_days} days`
  if (b === 'warning') return `${t.critical_days}–${t.warning_days} days`
  if (b === 'safe') return `over ${t.warning_days} days`
  return 'burn rate zero'
}

export default function District({
  facilities,
  facState,
  outbreaks,
  obState,
  thresholds,
  lastIngest,
  ingested,
  onRetry,
  onOpenIngest,
  onOpenFacility,
}) {
  if (!facilities) {
    return (
      <div className="waitstage">
        <span className="lbl lbl-red">01 — District Overview</span>
        <h1 className="display" style={{ marginTop: 14 }}>GUNTUR</h1>
        {facState.error ? (
          <div className="mt6">
            <ErrBox title="Could not load the district" error={facState.error} onRetry={onRetry} />
          </div>
        ) : (
          <Steps items={[{ state: 'on', label: 'Loading facilities and outbreaks', rs: 'GET /facilities' }]} />
        )}
      </div>
    )
  }

  const counts = bandCounts(facilities)
  const prior = lastIngest?.band_counts_before || null
  const total = facilities.length
  const medCount = Object.keys(facilities.find((f) => f.medicines)?.medicines || {}).length
  const worst = facilities[0]
  const labelId = worst && worst.band === 'critical' ? worst.id : null

  const driving = (outbreaks?.outbreaks || []).filter((o) => o.drives_surge)
  const others = outbreaks ? outbreaks.count - driving.length : 0

  return (
    <div>
      <div className="hero">
        <div>
          <span className="lbl lbl-red">01 — District Overview</span>
          <h1 className="display" style={{ marginTop: 14 }}>GUNTUR</h1>
          <p className="lede" style={{ marginTop: 18 }}>
            {total} primary health centres, {medCount || 'five'} essential medicines, one number that decides everything:{' '}
            <strong>stock on the shelf divided by daily consumption</strong>. An outbreak never changes the numerator — it
            changes the denominator, before the division happens.
          </p>
        </div>
        <div className="hero-side">
          <span className="lbl grey">Risk basis</span>
          <div style={{ fontSize: 13, fontWeight: 900, letterSpacing: '-.02em', marginTop: 8, maxWidth: '26ch' }}>
            {ingested ? 'HMIS baseline + IDSP outbreak surge' : 'HMIS routine caseload only'}
          </div>
        </div>
      </div>

      <div>
        <SecHead n="A" title="Band census" aside="Worst medicine per facility · never a blended score" />
        <div className="cells" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))' }}>
          {BANDS.map((b) => {
            let d = null
            if (prior && prior[b] !== counts[b]) {
              const diff = counts[b] - prior[b]
              const worse = b === 'critical' || b === 'warning' ? diff > 0 : diff < 0
              d = (
                <span className={`d ${worse ? 'd-up' : ''}`}>
                  {diff > 0 ? '+' : ''}
                  {diff}
                </span>
              )
            }
            return (
              <div className="cell" key={b}>
                <span className="lbl">
                  <Swatch band={b} /> &nbsp;{b}
                </span>
                <div className="v" data-census={b}>
                  {counts[b]}
                  {d}
                </div>
                <div className="sub">{bandSub(b, thresholds)}</div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="split mt6">
        <div>
          <SecHead n="B" title="Facility risk map" aside={`${total} PHCs at published coordinates`} />
          <div className="mapbox">
            <MapPanel facilities={facilities} onSelect={onOpenFacility} labelId={labelId} />
            <div className="maplegend">
              {BANDS.map((b) => (
                <span className="it" key={b}>
                  <Swatch band={b} /> {b} <b data-legend-count={b}>{counts[b]}</b>
                </span>
              ))}
            </div>
            <p className="mapnote">
              Coordinates are real, from the data.gov.in facility directory. Leaflet over OpenStreetMap data rendered by
              CARTO — no billing account, no Maps Platform key. Click a marker to open the facility.
            </p>
          </div>
        </div>

        <div className="stackv">
          <div className="ingest tex tex-diag">
            <span className="lbl">Gemini · Job 01</span>
            <h3 className="h2">Ingest outbreak report</h3>
            <p>
              A real MoHFW IDSP weekly PDF. Gemini extracts every outbreak row from both tables, including sub-district
              buried in free-text comments. No regex survives the format variation between weeks.
            </p>
            <button type="button" className="btn" onClick={onOpenIngest} data-action="open-ingest">
              Select report →
            </button>
          </div>

          <div>
            <SecHead n="C" title="Outbreaks" />
            {obState.error ? <ErrBox title="Could not load outbreaks" error={obState.error} onRetry={onRetry} /> : null}
            {outbreaks && driving.length
              ? driving.map((o) => (
                  <div className="ob" key={o.outbreak_id}>
                    <div className="t">{o.disease}</div>
                    <p className="m">
                      {o.sub_district
                        ? `Reported in ${o.sub_district} sub-district. Cases are allocated across facilities by each one's share of the district caseload, with facilities localised to ${o.sub_district} boosted — patients present at the nearest centre, not in proportion to district-wide caseload.`
                        : 'Cases are allocated across facilities by each one’s share of the district caseload for this disease.'}
                    </p>
                    <dl>
                      <dt>Outbreak</dt>
                      <dd>{o.outbreak_id}</dd>
                      <dt>Cases</dt>
                      <dd>
                        {o.cases} · {o.deaths} deaths
                      </dd>
                      <dt>Week</dt>
                      <dd>
                        {o.week} / {o.year}
                      </dd>
                      {o.status ? (
                        <>
                          <dt>Status</dt>
                          <dd>{o.status}</dd>
                        </>
                      ) : null}
                    </dl>
                  </div>
                ))
              : null}
            {outbreaks && others > 0 ? (
              <div className="ob quiet">
                <div className="t">
                  {others} further outbreak{others === 1 ? '' : 's'} on record
                </div>
                <p className="m">
                  Extracted and stored, but out of scope: the district does not appear in the facilities table for Andhra
                  Pradesh, or the disease is not in the curated rule table. Recorded and flagged — never guessed at, and no
                  surge applied.
                </p>
              </div>
            ) : null}
            {outbreaks && !driving.length && !others ? (
              <div className="ob quiet">
                <div className="t">No report ingested</div>
                <p className="m">
                  Burn rates reflect routine HMIS caseloads only. Ingest a weekly IDSP report to apply an outbreak surge and
                  recompute every facility's days of cover.
                </p>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt7">
        <SecHead n="D" title="Facility index" aside="Ordered by severity, then by days remaining" />
        <div className="box">
          <div className="scrollx">
            <table className="tbl" data-table="facility-index">
              <thead>
                <tr>
                  <th>Facility</th>
                  <th>Sub-district</th>
                  <th className="r">Days of cover</th>
                  <th>Worst medicine</th>
                  <th>Band</th>
                  <th className="r">Surge /day</th>
                </tr>
              </thead>
              <tbody>
                {facilities.map((f) => {
                  const surge = f.medicines?.[f.worst_medicine_id]?.outbreak_surge || 0
                  return (
                    <tr
                      key={f.id}
                      className={`click${f.band === 'critical' ? ' hot' : ''}`}
                      data-id={f.id}
                      tabIndex={0}
                      onClick={() => onOpenFacility(f.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onOpenFacility(f.id)
                        }
                      }}
                    >
                      <td className="nm">{f.name}</td>
                      <td className="grey">{f.sub_district}</td>
                      <td className="r">
                        <Dc days={f.days_of_cover} band={f.band} />
                      </td>
                      <td>{shortMed(f.worst_medicine_id, f.worst_medicine_name)}</td>
                      <td>
                        <Chip band={f.band} />
                      </td>
                      <td className="r num">{surge > 0 ? `+${fmtRate(surge)}` : '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
