// 02 · Facility — per-medicine breakdown from GET /facilities/{id}.
// The unit of risk is the (facility, medicine) pair: one PHC can be
// critical on ORS and safe on paracetamol at the same time, and this view
// never blends them into one score.
import { needsDonors, fmtDays, fmtRate, fmtQty, shortMed } from '../format.js'
import { Chip, Dc, Gauge, BackLink, ErrBox, Steps } from '../ui.jsx'

function nearestBatch(m) {
  let best = null
  for (const b of m.batches || []) {
    if (b.days_to_expiry == null) continue
    if (!best || b.days_to_expiry < best.days_to_expiry) best = b
  }
  return best
}

export default function Facility({ facilityId, detail, ingested, approvedMeds, onBack, onFindDonors, onRetry }) {
  const d = detail?.data

  if (!d) {
    return (
      <div className="waitstage">
        <div className="crumb">
          <BackLink onClick={onBack}>District</BackLink>
          <span className="lbl grey">{facilityId}</span>
        </div>
        {detail?.error ? (
          <ErrBox title="Could not load the facility" error={detail.error} onRetry={onRetry} />
        ) : (
          <Steps items={[{ state: 'on', label: 'Loading facility detail', rs: `GET /facilities/${facilityId}` }]} />
        )}
      </div>
    )
  }

  const meds = d.medicines || []
  const unknowns = meds.filter((m) => m.band === 'unknown')
  const withCover = meds.filter((m) => m.days_of_cover != null)
  const gaugeMax = Math.max(36, Math.ceil((Math.max(...withCover.map((m) => m.days_of_cover), 0) + 4) / 12) * 12)
  const t = d.thresholds

  return (
    <div>
      <div className="crumb">
        <BackLink onClick={onBack}>District</BackLink>
        <span className="lbl grey">{d.id}</span>
      </div>

      <div className="hero">
        <div>
          <span className="lbl lbl-red">02 — Facility Detail</span>
          <h1 className="h1" style={{ marginTop: 14 }}>
            {d.name}
            <span style={{ color: 'var(--grey)' }}> PHC</span>
          </h1>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 16 }}>
            <Chip band={d.band} />
            <span className="lbl grey">
              {d.sub_district} sub-district · {d.lat?.toFixed(5)}, {d.lon?.toFixed(5)}
            </span>
          </div>
        </div>
        <div className="hero-side">
          <span className="lbl grey">Worst medicine</span>
          <div style={{ marginTop: 8 }}>
            <Dc days={d.days_of_cover} band={d.band} size="2.6rem" />
          </div>
          <div className="lbl grey" style={{ marginTop: 4 }}>
            {shortMed(d.worst_medicine_id, d.worst_medicine_name)}
          </div>
        </div>
      </div>

      {(d.outbreaks_affecting || []).map((o) => (
        <div className="ob" style={{ marginBottom: 32 }} key={o.outbreak_id}>
          <div className="t">Outbreak surge applied — {o.disease}</div>
          <p className="m">
            {o.localised_here
              ? 'This facility is localised to the reported sub-district, so its share of the cases carries the local boost — patients present at the nearest centre. An outbreak does not change how much stock is held; it changes how fast that stock is consumed.'
              : "Allocated by this facility's share of the district caseload for the disease. It is not in the reported sub-district, so no local boost applies."}
          </p>
          <dl>
            <dt>Outbreak</dt>
            <dd>{o.outbreak_id}</dd>
            <dt>Cases allocated</dt>
            <dd>
              {fmtQty(o.cases_allocated)} of {o.cases} · {o.cases_per_day} per day
            </dd>
            <dt>Added to burn</dt>
            <dd>
              {Object.entries(o.extra_burn || {})
                .filter(([, v]) => v > 0)
                .map(([mid, v]) => `${shortMed(mid)} +${fmtRate(v)}/d`)
                .join(' · ') || '—'}
            </dd>
          </dl>
        </div>
      ))}

      <div className="split split-wide">
        <div className="stackv">
          <div className="box">
            <div className="box-hd">
              <span className="h3">Core medicine inventory</span>
              <span className="lbl grey">{meds.length} medicines · NLEM 2022</span>
            </div>
            <div className="scrollx">
              <table className="tbl tbl-pin" data-table="inventory">
                <thead>
                  <tr>
                    <th>Medicine</th>
                    <th className="r">Days of cover</th>
                    <th className="r">On hand</th>
                    <th className="r">Daily burn</th>
                    <th className="r">Nearest expiry</th>
                    <th className="r">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {meds.map((m) => {
                    const nb = nearestBatch(m)
                    const ap = approvedMeds?.[m.medicine_id]
                    return (
                      <tr key={m.medicine_id} className={m.band === 'critical' ? 'hot' : ''} data-med={m.medicine_id} data-band={m.band}>
                        <td className="nm">{m.name}</td>
                        <td className="r">
                          <Dc days={m.days_of_cover} band={m.band} />
                          <span className="subline">
                            <Chip band={m.band} />
                          </span>
                        </td>
                        <td className="r num">
                          {fmtQty(m.stock)}
                          <span className="subline">{m.unit}</span>
                        </td>
                        <td className="r num">
                          {m.burn_rate > 0 ? fmtRate(m.burn_rate) : '—'}
                          {m.outbreak_surge > 0 ? (
                            <span className="subline" style={{ color: 'var(--red-ink)' }}>+{fmtRate(m.outbreak_surge)} surge</span>
                          ) : null}
                        </td>
                        <td className="r num" style={nb?.near_expiry || nb?.expired ? { color: 'var(--red-ink)', fontWeight: 900 } : undefined}>
                          {nb ? nb.expiry : '—'}
                        </td>
                        <td className="r act">
                          {ap ? (
                            <span className="ptag ptag-ok" data-approved={m.medicine_id} title={`Transfer approved this session: +${fmtQty(ap.qty)} ${ap.unit || m.unit}`}>
                              Approved +{fmtQty(ap.qty)}
                            </span>
                          ) : null}
                          {needsDonors(m.band) ? (
                            ingested ? (
                              <button
                                type="button"
                                className="btn btn-sm"
                                data-action="find-donors"
                                data-med={m.medicine_id}
                                onClick={() => onFindDonors(d.id, m.medicine_id)}
                              >
                                Find donors
                              </button>
                            ) : (
                              <span className="lbl grey" title="Ingest an IDSP report first — the transfer stage unlocks after ingest">
                                GET /recommend
                              </span>
                            )
                          ) : null}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            {unknowns.length ? (
              <div className="box-bd-tight">
                <p className="footnote">
                  {unknowns.map((m) => m.name).join(' and ')} report <strong>unknown</strong>, not critical. No loaded caseload
                  maps to them, so their burn rate is zero and days of cover is undefined. A medicine nobody in the district
                  has needed cannot masquerade as an emergency.
                </p>
              </div>
            ) : null}
          </div>

          {withCover.length ? (
            <div className="box">
              <div className="box-hd">
                <span className="h3">Cover against thresholds</span>
              </div>
              <div className="box-bd stackv-sm">
                {[...withCover]
                  .sort((a, b) => a.days_of_cover - b.days_of_cover)
                  .map((m) => (
                    <div key={m.medicine_id}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
                        <span className="lbl">{shortMed(m.medicine_id, m.name)}</span>
                        <span className="lbl">{fmtDays(m.days_of_cover)} days</span>
                      </div>
                      <Gauge days={m.days_of_cover} band={m.band} thresholds={t} max={gaugeMax} />
                    </div>
                  ))}
              </div>
            </div>
          ) : null}

          <div className="box">
            <div className="box-hd">
              <span className="h3">Ledger movements</span>
              <span className="lbl grey">Append-only · last {Math.min((d.movements || []).length, 30)}</span>
            </div>
            <div className="scrollx">
              <table className="tbl">
                <thead>
                  <tr>
                    <th className="r">Delta</th>
                    <th>Medicine</th>
                    <th>Note</th>
                    <th>Batch</th>
                    <th>Source</th>
                    <th className="r">Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.movements || []).map((mv) => (
                    <tr key={mv.id}>
                      <td className="r num" style={{ fontWeight: 900, ...(mv.delta < 0 ? { color: 'var(--red-ink)' } : {}) }}>
                        {mv.delta < 0 ? `−${fmtQty(Math.abs(mv.delta))}` : `+${fmtQty(mv.delta)}`}
                      </td>
                      <td>{shortMed(mv.medicine_id)}</td>
                      <td style={{ maxWidth: '34ch' }}>{mv.note}</td>
                      <td className="num grey">{mv.batch || '—'}</td>
                      <td>
                        <span className="lbl">{mv.source}</span>
                      </td>
                      <td className="r num grey" style={{ whiteSpace: 'nowrap' }}>{mv.ts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="box-bd-tight">
              <p className="footnote">
                Stock on hand is always <strong>SUM(delta)</strong> over this log. There is no mutable quantity column
                anywhere in the schema — a cached quantity drifts out of sync with the movements that produced it.
              </p>
            </div>
          </div>
        </div>

        <div className="stackv">
          <div className="box">
            <div className="box-hd">
              <span className="h3">Facility record</span>
            </div>
            <table className="tbl">
              <tbody>
                {[
                  ['Type', 'Primary Health Centre'],
                  ['Sub-district', d.sub_district],
                  ['Latitude', d.lat?.toFixed(5)],
                  ['Longitude', d.lon?.toFixed(5)],
                  ['Facility ID', d.id],
                ].map(([k, v]) => (
                  <tr key={k}>
                    <td className="lbl grey" style={{ whiteSpace: 'nowrap' }}>{k}</td>
                    <td className="r num" style={{ fontWeight: 700, wordBreak: 'break-all' }}>{v}</td>
                  </tr>
                ))}
                {(d.pending_transfers || []).length ? (
                  <tr>
                    <td className="lbl grey">Pending transfers</td>
                    <td className="r num" style={{ fontWeight: 900 }}>{d.pending_transfers.length}</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          {t ? (
            <div className="box">
              <div className="box-hd">
                <span className="h3">Thresholds</span>
              </div>
              <table className="tbl">
                <tbody>
                  {[
                    ['Critical below', `${t.critical_days} days`],
                    ['Warning through', `${t.warning_days} days`],
                    ['Donor safety floor', `${t.donor_safety_floor_days} days`],
                    ['Near-expiry window', `${t.near_expiry_days} days`],
                  ].map(([k, v]) => (
                    <tr key={k}>
                      <td className="lbl grey">{k}</td>
                      <td className="r num" style={{ fontWeight: 900 }}>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
