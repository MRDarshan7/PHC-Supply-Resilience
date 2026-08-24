// 04 · Approved — what actually happened to the ledger, read back from the
// POST /transfers/{id}/approve responses: movements written at both
// facilities, days of cover recomputed (not predicted), and whatever risk
// remains at the recipient. The per-medicine point is made here: one
// medicine turns safe while the facility marker stays red until the rest
// are cleared.
import { needsDonors, fmtDays, fmtQty, fmtRate, shortMed } from '../format.js'
import { SecHead, Chip, Steps, Elapsed, ArrowRight, CheckMark } from '../ui.jsx'

function estArrival(ts, transitDays) {
  if (!ts || transitDays == null) return null
  const d = new Date(`${String(ts).slice(0, 10)}T00:00:00`)
  if (Number.isNaN(d.getTime())) return null
  d.setDate(d.getDate() + transitDays)
  return d.toISOString().slice(0, 10)
}

function ImpactPair({ a, facilities }) {
  const med = shortMed(a.medicine_id, a.medicine_name)
  const rb = a.recipient.before
  const ra = a.recipient.after
  const db = a.donor.before
  const da = a.donor.after
  const floor = a.donor.floor_days
  const above = da.days_of_cover != null && floor != null ? da.days_of_cover - floor : null
  const donorName = facilities?.find((f) => f.id === db.facility_id)?.name || db.name
  const qty = a.transfer?.qty

  return (
    <>
      <div className="imp" data-impact={`recipient-${a.medicine_id}`}>
        <div className="imp-hd">
          <span className="lbl">
            Recipient · {rb.name} — {med}
          </span>
          <Chip band={ra.band} />
        </div>
        <div className="imp-bd">
          <div className="imp-n">
            <div className={`v ${rb.band === 'critical' ? 'crit' : ''}`}>{fmtDays(rb.days_of_cover)}</div>
            <div className="u">Before · {rb.band}</div>
          </div>
          <div className="imp-ar">
            <ArrowRight />
            <div className="d">+{fmtQty(qty)}</div>
          </div>
          <div className="imp-n">
            <div className="v">{fmtDays(ra.days_of_cover)}</div>
            <div className="u">After · {ra.band}</div>
          </div>
        </div>
        <p className="imp-ft">
          {fmtQty(ra.stock)} {a.unit} at {fmtRate(ra.burn_rate)} per day — recomputed from the ledger, not predicted.
          {a.recipient.worst_band_after && a.recipient.worst_band_after.band !== ra.band
            ? ` At this approval the facility's worst medicine was still ${a.recipient.worst_band_after.band}.`
            : ''}
        </p>
      </div>
      <div className="imp" data-impact={`donor-${a.medicine_id}`}>
        <div className="imp-hd">
          <span className="lbl">
            Donor · {donorName} — {med}
          </span>
          <Chip band={da.band} />
        </div>
        <div className="imp-bd">
          <div className="imp-n">
            <div className="v">{fmtDays(db.days_of_cover)}</div>
            <div className="u">Before · {db.band}</div>
          </div>
          <div className="imp-ar">
            <ArrowRight />
            <div className="d">−{fmtQty(qty)}</div>
          </div>
          <div className="imp-n">
            <div className="v">{fmtDays(da.days_of_cover)}</div>
            <div className="u">After · {da.band}</div>
          </div>
        </div>
        <p className="imp-ft">
          {fmtQty(da.stock)} {a.unit} at {fmtRate(da.burn_rate)} per day
          {above != null ? (
            <>
              {' '}
              — <strong>{fmtDays(above)} days above</strong> the {floor}-day donor safety floor. No recommendation is ever
              allowed to push a donor below it.
            </>
          ) : (
            '.'
          )}
        </p>
      </div>
    </>
  )
}

export default function Approved({
  approvals,
  facilities,
  recipientDetail,
  remainState,
  onApproveRemaining,
  onOpenFacility,
  onReturnDistrict,
}) {
  const last = approvals[approvals.length - 1]
  const totalMovements = approvals.reduce((n, a) => n + (a.movements?.length || 0), 0)
  const detail = recipientDetail?.data
  const remaining = (detail?.medicines || []).filter((m) => needsDonors(m.band))
  const cleared = (detail?.medicines || []).filter((m) => !needsDonors(m.band) && m.band !== 'unknown')
  const est = estArrival(last?.transfer?.ts, last?.parameters?.transfer_transit_days)

  return (
    <div>
      <div className="hero">
        <div>
          <span className="lbl lbl-red">04 — Transfer Applied</span>
          <h1 className="display" style={{ marginTop: 14 }}>APPROVED</h1>
          <p className="lede" style={{ marginTop: 18 }}>
            {approvals.map((a, i) => (
              <span key={a.transfer.id}>
                {i > 0 ? ' ' : ''}
                Transfer <strong>#{a.transfer.id}</strong> — {fmtQty(a.transfer.qty)} {a.unit} of{' '}
                {shortMed(a.medicine_id, a.medicine_name)} from {facilities?.find((f) => f.id === a.transfer.from_facility)?.name || a.donor.before.name} PHC
                to {a.recipient.before.name} PHC, approved by the District Medical Officer.
              </span>
            ))}{' '}
            {totalMovements} movement rows were written, at both ends of every transfer, each carrying its lot's batch
            number and expiry.
          </p>
        </div>
      </div>

      <div>
        <SecHead n="A" title="Impact · days of cover" aside="Recomputed from the ledger, not predicted" />
        <div className="impact">
          {approvals.map((a) => (
            <ImpactPair key={a.transfer.id} a={a} facilities={facilities} />
          ))}
        </div>
      </div>

      {detail ? (
        <div className="mt7">
          <SecHead
            n="B"
            title={remaining.length ? `Still below the line at ${detail.name}` : `Every medicine above the line`}
            aside="Risk is a property of the (facility, medicine) pair"
          />
          {remaining.length ? (
            <div className="split">
              <div className="box">
                <table className="tbl" data-table="remaining">
                  <thead>
                    <tr>
                      <th>Medicine</th>
                      <th className="r">Days of cover</th>
                      <th>Band</th>
                    </tr>
                  </thead>
                  <tbody>
                    {remaining.map((m) => (
                      <tr key={m.medicine_id} className={m.band === 'critical' ? 'hot' : ''}>
                        <td className="nm">{m.name}</td>
                        <td className="r num">{fmtDays(m.days_of_cover)}D</td>
                        <td>
                          <Chip band={m.band} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="box-bd-tight">
                  <p className="footnote">
                    {cleared.length ? (
                      <>
                        {cleared.map((m) => shortMed(m.medicine_id, m.name)).join(' and ')} {cleared.length === 1 ? 'is' : 'are'} now
                        above the critical line, but the map marker for {detail.name} stays <strong>red</strong>:{' '}
                      </>
                    ) : null}
                    {remaining.map((m) => shortMed(m.medicine_id, m.name)).join(' and ')}{' '}
                    {remaining.length === 1 ? 'remains' : 'remain'} below it, and the marker shows the worst medicine — never a
                    blended score.
                  </p>
                </div>
              </div>
              <div className="stackv">
                {remainState.running ? (
                  <div>
                    <Steps
                      items={[
                        { state: 'on', label: remainState.step || 'Working…', rs: <Elapsed /> },
                        { state: 'wait', label: 'Approve each recommendation, re-checking the floor' },
                      ]}
                    />
                    <p className="stephint">
                      One recommendation per remaining medicine: the donor engine runs, Gemini writes each memo (a live call
                      can take around 45 seconds), then each transfer is approved with the safety check re-run.
                    </p>
                  </div>
                ) : (
                  <div className="approve" style={{ borderWidth: 2 }}>
                    <div className="notice">
                      <span className="lbl">Clear the rest</span>
                      <p>
                        Run the donor engine for {remaining.map((m) => shortMed(m.medicine_id, m.name)).join(', ')} and approve
                        each recommendation. Every donor passes the same safety floor; the marker turns from red only when the
                        worst medicine does.
                      </p>
                      {remainState.error ? <p style={{ color: 'var(--red-ink)', fontWeight: 700, marginTop: 8 }}>{remainState.error.message}</p> : null}
                      {remainState.summary ? <p style={{ fontWeight: 700, marginTop: 8 }}>{remainState.summary}</p> : null}
                    </div>
                    <button type="button" className="btn btn-primary" data-action="approve-remaining" onClick={() => onApproveRemaining(detail.id)}>
                      Approve remaining · {remaining.length} medicine{remaining.length === 1 ? '' : 's'}
                    </button>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="box">
              <div className="box-bd">
                <p className="lede">
                  {detail.name} now holds more than the critical line on every medicine with recorded consumption — its map
                  marker is no longer red. {remainState.summary || ''}
                </p>
                <div style={{ marginTop: 16 }}>
                  <button type="button" className="btn btn-sm" onClick={() => onOpenFacility(detail.id)}>
                    Open {detail.name}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      ) : null}

      <div className="mt7">
        <SecHead n="C" title="Movements written" aside="source = transfer · append-only" />
        <div className="box">
          <div className="scrollx">
            <table className="tbl" data-table="movements">
              <thead>
                <tr>
                  <th>Facility</th>
                  <th className="r">Delta</th>
                  <th>Batch</th>
                  <th>Expiry</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {approvals.flatMap((a) =>
                  (a.movements || []).map((mv) => (
                    <tr key={mv.id}>
                      <td className="nm">{facilities?.find((f) => f.id === mv.facility_id)?.name || mv.facility_id}</td>
                      <td className="r num" style={{ fontWeight: 900, ...(mv.delta < 0 ? { color: 'var(--red-ink)' } : {}) }}>
                        {mv.delta < 0 ? `−${fmtQty(Math.abs(mv.delta))}` : `+${fmtQty(mv.delta)}`}
                      </td>
                      <td className="num">{mv.batch || '—'}</td>
                      <td className="num">{mv.expiry || '—'}</td>
                      <td className="grey">{mv.note}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
          <div className="box-bd-tight">
            <p className="footnote">
              Nothing was mutated and nothing was deleted. Each moved lot keeps its own batch number and expiry, so the days
              remaining on it follow the stock to the recipient and appear in its next risk calculation.
            </p>
          </div>
        </div>
      </div>

      <div className="mt7">
        <SecHead
          n="D"
          title="Logistics"
          aside={last?.parameters ? `${last.parameters.transfer_transit_days}-day transit assumption` : 'transit assumed'}
        />
        <div className="box">
          <div className="track">
            <div className="trk done">
              <div className="dot">
                <CheckMark />
              </div>
              <div>
                <span className="lbl">Approved</span>
                <div className="tm">{last?.transfer?.ts || ''}</div>
              </div>
            </div>
            <div className="trk now">
              <div className="dot" />
              <div>
                <span className="lbl">In transit</span>
                <div className="tm">{est ? `Est. ${est}` : 'Transit time not modelled live'}</div>
              </div>
            </div>
            <div className="trk pend">
              <div className="dot" />
              <div>
                <span className="lbl">Received</span>
                <div className="tm">Pending</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="approve mt7">
        <div className="notice">
          <span className="lbl">Decision support only</span>
          <p>{last?.notice}</p>
        </div>
        <button type="button" className="btn btn-primary btn-lg" onClick={onReturnDistrict} data-action="return-district">
          Return to district
        </button>
      </div>
    </div>
  )
}
