import { useState } from 'react'
import { fmtDays, fmtKm, fmtQty, fmtRate, fmtScore, paragraphs, reasonLabel, plural } from '../format.js'
import { Band, Waiting, ErrorNote } from './common.jsx'
import { useMediaQuery, PHONE_QUERY } from '../hooks.js'

const SCORE_ORDER = ['sufficiency', 'proximity', 'expiry_benefit', 'donor_comfort']
const SCORE_LABEL = {
  sufficiency: 'Sufficiency',
  proximity: 'Proximity',
  expiry_benefit: 'Expiry benefit',
  donor_comfort: 'Donor comfort',
}

function ScoreBreakdown({ score, weights }) {
  if (!score) return null
  return (
    <div className="score">
      <div className="score-row">
        <span className="score-total">Score {fmtScore(score.total)}</span>
        <span className="muted small">component × weight</span>
        <span />
      </div>
      {SCORE_ORDER.map((k) => {
        const c = score.components?.[k] ?? 0
        const w = weights?.[k] ?? 0
        return (
          <div className="score-row" key={k}>
            <span>{SCORE_LABEL[k]}</span>
            <span className="score-bar">
              <i style={{ width: `${Math.round(Math.max(0, Math.min(1, c)) * 100)}%` }} />
            </span>
            <span className="num mono">
              {c.toFixed(2)} × {w.toFixed(2)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function DonorCard({ d, unit, floorDays, weights, nEligible }) {
  const margin = d.cover_after == null ? null : d.cover_after - floorDays
  return (
    <div className="donor">
      <div className="donor-title">
        <b>{d.name}</b>
        <span className="muted">{d.sub_district}</span>
        <span className="muted">{fmtKm(d.distance_km)} away</span>
        <span className="tag">rank {d.rank} of {nEligible} eligible</span>
        {d.order > 1 ? <span className="tag">draws {d.order}nd</span> : null}
      </div>
      <div className="donor-grid">
        <div>
          <span className="k">Sends</span>
          <span className="v">
            <b>
              {fmtQty(d.qty)} {unit}
            </b>
          </span>
        </div>
        <div>
          <span className="k">From lot</span>
          <span className="v small">
            {(d.lots_given || []).map((l) => (
              <span key={l.batch}>
                {l.batch} (exp {l.expiry}, {l.days_to_expiry} d){' '}
              </span>
            ))}
          </span>
        </div>
        <div>
          <span className="k">Donor stock</span>
          <span className="v">
            {fmtQty(d.stock_before)} <span className="arrow">→</span> {fmtQty(d.stock_after)} {unit}
          </span>
        </div>
        <div>
          <span className="k">Donor cover</span>
          <span className="v">
            {fmtDays(d.cover_before)} <span className="arrow">→</span> <b>{fmtDays(d.cover_after)} d</b>
            {margin != null ? <span className="muted"> ({margin >= 0 ? '+' : ''}{fmtDays(margin)} d above the {floorDays}-day floor)</span> : null}
          </span>
        </div>
        <div>
          <span className="k">Donor burn</span>
          <span className="v">
            {fmtRate(d.burn_rate)}/day
            {d.outbreak_surge > 0 ? <span className="muted"> (incl. surge {fmtRate(d.outbreak_surge)})</span> : null}
          </span>
        </div>
        <div>
          <span className="k">Safety check</span>
          <span className="v">
            {d.floor_check?.passes ? <span style={{ color: 'var(--safe)', fontWeight: 600 }}>passes</span> : <span style={{ color: 'var(--critical)', fontWeight: 600 }}>FAILS</span>}
            <span className="muted small">
              {' '}
              {fmtQty(d.floor_check?.stock_after)} ÷ {fmtRate(d.floor_check?.burn_rate)} = {fmtDays(d.floor_check?.cover_after)} d ≥ {floorDays}
            </span>
          </span>
        </div>
      </div>
      <ScoreBreakdown score={d.score} weights={weights} />
    </div>
  )
}

function RejectedCard({ c, other }) {
  return (
    <div className={`rej ${other ? 'rej-other' : ''}`}>
      <div className="rej-title">
        <b>{c.name}</b>
        <span className="muted">{c.sub_district}</span>
        <span className="muted">{fmtKm(c.distance_km)}</span>
        <Band band={c.band} />
        {c.days_of_cover != null ? <span className="muted">{fmtDays(c.days_of_cover)} d cover</span> : null}
        <span className="tag">{reasonLabel(c.reason_code)}</span>
      </div>
      <div className="reason">{c.reason}</div>
      {c.notes?.length ? (
        <div className="small muted">
          {c.notes.map((n, i) => (
            <div key={i}>{n}</div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function Memo({ memo, recipientName }) {
  const [lang, setLang] = useState('en')
  if (!memo) return null
  const text = lang === 'te' ? memo.rationale_te : memo.rationale_en
  const source =
    memo.source === 'template'
      ? 'Templated memo (Gemini unavailable or its response failed numeric validation)'
      : `Written by ${memo.model || 'Gemini'}${memo.cache_hit ? ' · served from cache' : ''} · every number cross-checked against backend values`
  return (
    <div className="memo">
      <div className="memo-bar">
        <b>Transfer memo for {recipientName}</b>
        <div className="btn-group" role="group" aria-label="Memo language">
          <button type="button" className={lang === 'en' ? 'on' : ''} onClick={() => setLang('en')} aria-pressed={lang === 'en'}>
            English
          </button>
          <button type="button" className={lang === 'te' ? 'on' : ''} onClick={() => setLang('te')} aria-pressed={lang === 'te'} lang="te">
            తెలుగు
          </button>
        </div>
        <span className="small muted">{source}</span>
      </div>
      <div className="memo-text" lang={lang}>
        {paragraphs(text).map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
      {memo.risk_notes?.length ? (
        <div>
          <b className="small">Risk notes</b>
          <ul className="small">
            {memo.risk_notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function BeforeAfter({ approvals, unit, floorDays }) {
  const phone = useMediaQuery(PHONE_QUERY)
  // Recipient: before the first movement, after the last. Donors: one row each.
  const first = approvals[0]?.recipient
  const last = approvals[approvals.length - 1]?.recipient
  const r = first?.before && last?.after ? { before: first.before, after: last.after } : null
  const pairs = []
  if (r) pairs.push({ name: r.after.name, role: 'recipient', before: r.before, after: r.after })
  for (const a of approvals) {
    if (a.donor?.before && a.donor?.after) {
      pairs.push({ name: a.donor.after.name, role: 'donor', before: a.donor.before, after: a.donor.after, floorDays })
    }
  }
  return (
    <div className="note note-ok">
      <h4>
        {approvals.length === 1 ? 'Transfer approved' : `${approvals.length} transfers approved`} — ledger updated at both facilities
      </h4>
      {phone ? (
        <div className="pair-cards">
          {pairs.map((p, i) => (
            <PairCard key={i} {...p} unit={unit} />
          ))}
        </div>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl before-after">
            <thead>
              <tr>
                <th>Facility</th>
                <th>Role</th>
                <th className="num">Stock</th>
                <th className="num">Days of cover</th>
                <th>Band</th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((p, i) => (
                <PairRow key={i} {...p} unit={unit} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function PairCard({ name, role, before, after, unit, floorDays }) {
  const margin = role === 'donor' && after.days_of_cover != null && floorDays != null ? after.days_of_cover - floorDays : null
  return (
    <div className="pair-card">
      <div>
        <b>{name}</b> <span className="muted small">{role}</span>
      </div>
      <div className="small">
        Stock {fmtQty(before.stock)} <span className="arrow">→</span>{' '}
        <b>
          {fmtQty(after.stock)} {unit}
        </b>
      </div>
      <div className="small">
        Cover {fmtDays(before.days_of_cover)} <span className="arrow">→</span> <b>{fmtDays(after.days_of_cover)} d</b> <Band band={before.band} />{' '}
        <span className="arrow">→</span> <Band band={after.band} />
        {margin != null ? (
          <div className="muted">
            {fmtDays(margin)} d above the {floorDays}-day floor
          </div>
        ) : null}
      </div>
    </div>
  )
}

function PairRow({ name, role, before, after, unit, floorDays }) {
  const margin = role === 'donor' && after.days_of_cover != null && floorDays != null ? after.days_of_cover - floorDays : null
  return (
    <tr>
      <td>{name}</td>
      <td className="muted">{role}</td>
      <td className="num">
        {fmtQty(before.stock)} <span className="arrow">→</span> <b>{fmtQty(after.stock)}</b> {unit}
      </td>
      <td className="num">
        {fmtDays(before.days_of_cover)} <span className="arrow">→</span> <b>{fmtDays(after.days_of_cover)}</b>
        {margin != null ? <div className="small muted">{fmtDays(margin)} d above the {floorDays}-day floor</div> : null}
      </td>
      <td className="nowrap">
        <Band band={before.band} /> <span className="arrow">→</span> <Band band={after.band} />
      </td>
    </tr>
  )
}

export default function Recommendation({ rec, medicine, recipientName, medNames, remaining, onApprove, onRetry }) {
  const { loading, error, data, approving, approveError, approvals } = rec
  const medName = medicine?.name || data?.recipient?.medicine_name || medicine?.medicine_id
  const unit = medicine?.unit || data?.recipient?.unit || ''

  return (
    <div className="rec" id={rec.domId}>
      <div className="rec-head">
        <h3>Donor recommendation — {medName}</h3>
        {data ? (
          <span className="counts">
            <span>
              <b>{data.counts.considered}</b> candidates considered
            </span>
            <span>
              <b>{data.counts.eligible}</b> eligible
            </span>
            <span>
              <b>{data.counts.rejected}</b> rejected
              {data.counts.rejected_by_safety_check ? (
                <>
                  {' '}
                  (<b>{data.counts.rejected_by_safety_check}</b> by the donor safety check)
                </>
              ) : null}
            </span>
          </span>
        ) : null}
      </div>
      <div className="rec-body">
        {loading ? (
          <Waiting
            label={`Running the donor engine for ${medName} and writing the memo…`}
            hint="Filter → donor safety check → quantity → score, then Gemini writes the bilingual memo. First memo for a pair can take up to a minute; repeats are cached."
          />
        ) : null}
        {error ? <ErrorNote title="Could not fetch a recommendation" error={error} onRetry={onRetry} /> : null}
        {data ? (
          <Body
            data={data}
            unit={unit}
            medName={medName}
            recipientName={recipientName}
            medNames={medNames}
            remaining={remaining}
            approving={approving}
            approveError={approveError}
            approvals={approvals}
            onApprove={onApprove}
          />
        ) : null}
      </div>
    </div>
  )
}

function Body({ data, unit, medName, recipientName, medNames, remaining, approving, approveError, approvals, onApprove }) {
  const R = data.recipient
  const P = data.parameters || {}
  const floorDays = P.donor_safety_floor_days
  const plan = data.plan
  const memo = data.memo
  const rejected = data.rejected || []
  const safety = rejected.filter((c) => c.reason_code === 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  const others = rejected.filter((c) => c.reason_code !== 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  const chosenIds = new Set((plan?.donors || []).map((d) => d.facility_id))
  const runnersUp = (data.eligible || []).filter((c) => !chosenIds.has(c.facility_id))
  const [showAllSafety, setShowAllSafety] = useState(false)
  const safetyShown = showAllSafety ? safety : safety.slice(0, 4)
  const pending = (data.transfers || []).filter((t) => t.status === 'pending')
  const approved = approvals && approvals.length > 0
  const close = memo?.close_call

  return (
    <>
      <div className="note note-info" style={{ marginTop: 0 }}>
        <b>{R.name}</b> holds <b>{fmtQty(R.stock)} {unit}</b> of {medName}, consumed at <b>{fmtRate(R.burn_rate)}/day</b>
        {R.outbreak_surge > 0 ? (
          <>
            {' '}
            ({fmtRate(R.baseline_burn_rate)} baseline + <span className="surge-pos">{fmtRate(R.outbreak_surge)} outbreak surge</span>)
          </>
        ) : null}
        {' '}= <b>{fmtDays(R.days_of_cover)} days</b> of cover (<Band band={R.band} />).{' '}
        {R.need_units > 0 ? (
          <>
            Reaching {R.target_cover_days} days needs <b>{fmtQty(R.need_units)} {unit}</b>.
          </>
        ) : (
          'No transfer is needed.'
        )}
      </div>

      {data.status === 'no_eligible_donors' ? (
        <div className="note note-warn">
          <h4>No eligible donor within {P.max_transfer_radius_km} km</h4>
          Every candidate was rejected — see the reasons below. Nothing is recommended.
        </div>
      ) : null}
      {data.status === 'partial' && plan ? (
        <div className="note note-warn">
          <h4>Need only partly met</h4>
          Eligible donors can spare {fmtQty(plan.total_qty)} of the {fmtQty(plan.need_units)} {unit} needed; shortfall {fmtQty(plan.shortfall)} {unit}.
        </div>
      ) : null}

      {plan?.donors?.length ? (
        <>
          <h4>
            Recommended {plural(plan.donors.length, 'donor')}
            {plan.split ? <span className="muted"> — split across {plan.donors.length} facilities</span> : null}
          </h4>
          {plan.donors.map((d) => (
            <DonorCard key={d.facility_id} d={d} unit={unit} floorDays={floorDays} weights={P.score_weights} nEligible={data.counts.eligible} />
          ))}
          {close?.is_close_call && close.selectable?.length > 1 ? (
            <div className="small muted" style={{ marginBottom: 6 }}>
              Close call (scores within {close.margin}): Gemini adjudicated between{' '}
              {close.selectable.map((s, i) => (
                <span key={s.facility_id}>
                  {i ? ', ' : ''}
                  {s.name} ({fmtScore(s.score_total)})
                </span>
              ))}
              {memo?.deviates_from_backend_plan ? ' and reordered the backend ranking.' : '; it kept the backend ranking.'}
              {' '}Quantities were computed by the backend in both cases.
            </div>
          ) : null}
          {data.post_transfer?.recipient ? (
            <div className="small" style={{ marginBottom: 4 }}>
              Projected for {R.name} after the transfer: stock {fmtQty(data.post_transfer.recipient.stock_before)} →{' '}
              <b>{fmtQty(data.post_transfer.recipient.stock_after)} {unit}</b>, cover {fmtDays(data.post_transfer.recipient.days_of_cover_before)} →{' '}
              <b>{fmtDays(data.post_transfer.recipient.days_of_cover_after)} d</b> (<Band band={data.post_transfer.recipient.band_before} /> →{' '}
              <Band band={data.post_transfer.recipient.band_after} />). Transit assumed {P.transfer_transit_days} days; not modelled as instant.
            </div>
          ) : null}
        </>
      ) : null}

      {runnersUp.length ? (
        <details className="fold">
          <summary>
            {runnersUp.length} other eligible {plural(runnersUp.length, 'donor')} passed the safety check (not chosen)
          </summary>
          <div className="fold-body tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Facility</th>
                  <th className="num">Distance</th>
                  <th className="num">Spare</th>
                  <th className="num">Cover</th>
                  <th className="num">Score</th>
                </tr>
              </thead>
              <tbody>
                {runnersUp.slice(0, 15).map((c) => (
                  <tr key={c.facility_id}>
                    <td className="muted">{c.rank}</td>
                    <td>
                      {c.name} <span className="muted small">{c.sub_district}</span>
                    </td>
                    <td className="num">{fmtKm(c.distance_km)}</td>
                    <td className="num">
                      {fmtQty(c.spare_units)} {unit}
                    </td>
                    <td className="num">{fmtDays(c.days_of_cover)} d</td>
                    <td className="num mono">{fmtScore(c.score?.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runnersUp.length > 15 ? <div className="small muted">Showing the 15 highest-ranked of {runnersUp.length}.</div> : null}
          </div>
        </details>
      ) : null}

      <h4>
        Rejected by the donor safety check — {safety.length} {plural(safety.length, 'facility', 'facilities')}
      </h4>
      {safety.length ? (
        <>
          <p className="small muted">
            spare = stock − burn rate × {floorDays} days, with the burn rate including the outbreak surge. A facility that would fall below {floorDays} days of its own cover cannot give; the same outbreak that created the shortage removes the nearest donors.
          </p>
          {safetyShown.map((c) => (
            <RejectedCard key={c.facility_id} c={c} />
          ))}
          {safety.length > safetyShown.length ? (
            <button type="button" className="btn-link" onClick={() => setShowAllSafety(true)}>
              Show all {safety.length} safety-floor rejections
            </button>
          ) : null}
        </>
      ) : (
        <p className="small muted">No candidate was rejected by the safety check.</p>
      )}

      {others.length ? (
        <details className="fold">
          <summary>
            {others.length} {plural(others.length, 'candidate')} rejected for other reasons (
            {Object.entries(data.rejected_by_reason || {})
              .filter(([k]) => k !== 'donor_safety_floor')
              .map(([k, n]) => `${reasonLabel(k).toLowerCase()} ${n}`)
              .join(', ')}
            )
          </summary>
          <div className="fold-body">
            {others.map((c) => (
              <RejectedCard key={c.facility_id} c={c} other />
            ))}
          </div>
        </details>
      ) : null}

      {memo && plan ? (
        <>
          <h4>Memo</h4>
          <Memo memo={memo} recipientName={recipientName || R.name} />
        </>
      ) : null}

      {plan ? (
        <div className="approve-bar">
          {approved ? null : (
            <button type="button" className="btn btn-approve" disabled={approving || pending.length === 0} onClick={onApprove} aria-busy={approving ? 'true' : 'false'}>
              {approving ? (
                <>
                  <span className="spinner light" /> Approving…
                </>
              ) : (
                <>
                  Approve transfer
                  {pending.length > 1 ? ` (${pending.length} movements)` : ''}
                </>
              )}
            </button>
          )}
          {!approved && pending.length === 0 ? <span className="small muted">No pending transfer row — fetch donors again.</span> : null}
          {!approved ? (
            <span className="small muted">
              Approval re-runs the donor safety check on the live ledger, then writes movements at both facilities.
            </span>
          ) : null}
        </div>
      ) : null}
      {approveError ? <ErrorNote title="Approval refused" error={approveError} /> : null}
      {approved ? (
        <>
          <BeforeAfter approvals={approvals} unit={unit} floorDays={floorDays} />
          <FacilityStatusAfter approvals={approvals} medNames={medNames} medName={medName} remaining={remaining} />
        </>
      ) : null}
      <div className="notice">{data.notice}</div>
    </>
  )
}

// The facility-level consequence of a single-medicine transfer: the medicine
// may be safe while the facility stays critical on another one. `remaining`
// is the live per-medicine picture from the refreshed facility detail; the
// approval response's worst_band_after is the fallback before it arrives.
function FacilityStatusAfter({ approvals, medNames, medName, remaining }) {
  const last = approvals[approvals.length - 1]
  const w = last?.recipient?.worst_band_after
  const after = last?.recipient?.after
  if (!w || !after) return null
  const facility = after.name
  const still = remaining || (w.band === 'safe' ? [] : [{ name: medNames?.[w.worst_medicine_id] || w.worst_medicine_id, band: w.band }])
  if (!still.length) {
    return (
      <div className="note note-ok">
        <b>{facility}</b> is now safe on every medicine — its map marker turns green.
      </div>
    )
  }
  const worstBand = still.some((m) => m.band === 'critical') ? 'critical' : 'warning'
  const names = still.map((m) => m.name)
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
  return (
    <div className={`note ${worstBand === 'critical' ? 'note-error' : 'note-warn'}`}>
      <b>{medName}</b> is now <Band band={after.band} /> at {facility}, but the facility and its map marker stay <Band band={worstBand} />:{' '}
      <b>{list}</b> {still.length === 1 ? 'is' : 'are'} still below the line. Risk is a property of each (facility, medicine) pair, not of
      the facility — use <b>Approve all recommended</b> above to resolve the rest.
    </div>
  )
}
