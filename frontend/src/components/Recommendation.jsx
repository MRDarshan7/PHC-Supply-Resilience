import { useEffect, useRef, useState } from 'react'
import { fmtDays, fmtKm, fmtQty, fmtRate, fmtScore, paragraphs, reasonLabel, plural, shortMed } from '../format.js'
import { Arrow, Band, Waiting, ErrorNote } from './common.jsx'

const SCORE_ORDER = ['sufficiency', 'proximity', 'expiry_benefit', 'donor_comfort']
const SCORE_LABEL = { sufficiency: 'Sufficiency', proximity: 'Proximity', expiry_benefit: 'Expiry benefit', donor_comfort: 'Donor comfort' }

// --------------------------------------------------------------------------
// Shapes: one "leg" per donor, before → after at both ends. Built from the
// projected plan before approval and from the ledger as written after it.
// --------------------------------------------------------------------------

function planLegs(data, floorDays) {
  const pt = data.post_transfer
  const R = pt?.recipient
  const recipient = R
    ? {
        name: data.recipient.name,
        stock_before: R.stock_before,
        stock_after: R.stock_after,
        days_before: R.days_of_cover_before,
        days_after: R.days_of_cover_after,
        band_before: R.band_before,
        band_after: R.band_after,
      }
    : null
  const legs = (data.plan?.donors || []).map((d) => ({
    facility_id: d.facility_id,
    name: d.name,
    sub_district: d.sub_district,
    distance_km: d.distance_km,
    rank: d.rank,
    score: d.score,
    qty: d.qty,
    lots: d.lots_given || [],
    stock_before: d.stock_before,
    stock_after: d.stock_after,
    days_before: d.cover_before,
    days_after: d.cover_after,
    band_before: d.band_before,
    band_after: d.band_after,
    margin: d.cover_after == null ? null : d.cover_after - floorDays,
    floor_check: d.floor_check,
    outbreak_surge: d.outbreak_surge,
  }))
  return { legs, recipient }
}

function approvedLegs(data, approvals, floorDays) {
  const byId = {}
  for (const d of data.plan?.donors || []) byId[d.facility_id] = d
  const legs = approvals
    .filter((a) => a.donor?.before && a.donor?.after)
    .map((a) => {
      const d = a.donor
      const p = byId[d.after.facility_id] || {}
      return {
        facility_id: d.after.facility_id,
        name: d.after.name,
        sub_district: p.sub_district,
        distance_km: p.distance_km,
        rank: p.rank,
        score: p.score,
        qty: Math.abs(d.before.stock - d.after.stock),
        lots: (a.movements || []).filter((m) => m.delta < 0).map((m) => ({ batch: m.batch, expiry: m.expiry, qty: -m.delta })),
        stock_before: d.before.stock,
        stock_after: d.after.stock,
        days_before: d.before.days_of_cover,
        days_after: d.after.days_of_cover,
        band_before: d.before.band,
        band_after: d.after.band,
        margin: d.after.days_of_cover == null ? null : d.after.days_of_cover - floorDays,
        floor_check: p.floor_check,
        outbreak_surge: p.outbreak_surge,
      }
    })
  const first = approvals[0]?.recipient
  const last = approvals[approvals.length - 1]?.recipient
  const recipient =
    first?.before && last?.after
      ? {
          name: last.after.name,
          stock_before: first.before.stock,
          stock_after: last.after.stock,
          days_before: first.before.days_of_cover,
          days_after: last.after.days_of_cover,
          band_before: first.before.band,
          band_after: last.after.band,
        }
      : null
  return { legs, recipient }
}

// --------------------------------------------------------------------------
// Pieces
// --------------------------------------------------------------------------

function DonorCard({ leg, recipient, unit, nEligible, floorDays, approved, transitDays, split }) {
  return (
    <div className="donor" data-donor={leg.facility_id}>
      <div className="donor-head">
        <span className="role">{approved ? 'Donor' : 'Recommended donor'}</span>
        <span className="dname">{leg.name}</span>
        <span className="dsub">{leg.sub_district}</span>
        <span className="tags">
          {leg.rank ? (
            <span className="tag">
              rank {leg.rank} of {nEligible}
            </span>
          ) : null}
          {leg.score ? <span className="tag">score {fmtScore(leg.score.total)}</span> : null}
        </span>
      </div>
      <div className="tiles">
        <div className="tile">
          <div className="k">{approved ? 'Sent' : 'Quantity'}</div>
          <div className="v">
            {fmtQty(leg.qty)} <small>{unit}</small>
          </div>
          <div className="s">{leg.lots.length ? leg.lots.map((l) => `lot ${l.batch} · exp ${l.expiry}`).join(', ') : ''}</div>
        </div>
        <div className="tile">
          <div className="k">Distance</div>
          <div className="v">{fmtKm(leg.distance_km)}</div>
          <div className="s">straight-line · {transitDays}-day transit</div>
        </div>
        <div className="tile">
          <div className="k">{split ? 'Recipient · after all transfers' : 'Recipient cover'}</div>
          <div className="v">
            {fmtDays(recipient?.days_before)}
            <Arrow />
            {fmtDays(recipient?.days_after)} <small>d</small>
          </div>
          <div className="s">
            <b>{fmtQty(recipient?.stock_before)}</b>
            <Arrow />
            <b>{fmtQty(recipient?.stock_after)}</b> {unit}
          </div>
          <div className="bands">
            <Band band={recipient?.band_before} />
            <Arrow />
            <Band band={recipient?.band_after} />
          </div>
        </div>
        <div className="tile">
          <div className="k">Donor cover</div>
          <div className="v">
            {fmtDays(leg.days_before)}
            <Arrow />
            {fmtDays(leg.days_after)} <small>d</small>
          </div>
          <div className="s">
            <b>{fmtQty(leg.stock_before)}</b>
            <Arrow />
            <b>{fmtQty(leg.stock_after)}</b> {unit}
          </div>
          {leg.margin != null ? (
            <div className="s">
              <b className={leg.margin >= 0 ? 'c-safe' : 'c-critical'}>
                {leg.margin >= 0 ? '+' : ''}
                {fmtDays(leg.margin)} d
              </b>{' '}
              above the {floorDays}-day safety floor
            </div>
          ) : null}
          <div className="bands">
            <Band band={leg.band_before} />
            <Arrow />
            <Band band={leg.band_after} />
          </div>
        </div>
      </div>
      <div className={`donor-foot ${approved ? 'ok' : ''}`}>
        {approved ? (
          <span>✓ Approved — ledger updated at both facilities; days of cover above are recomputed from the ledger</span>
        ) : (
          <span>Projected after transfer · transit assumed {transitDays} days, not instant · donor safety check re-run at approval</span>
        )}
      </div>
    </div>
  )
}

function ScoreBreakdown({ leg, weights, floorDays, unit }) {
  const s = leg.score
  if (!s) return null
  return (
    <details className="fold">
      <summary>
        <span>Score breakdown and safety check</span>
        <span className="muted">
          {leg.name} · total {fmtScore(s.total)}
        </span>
      </summary>
      <div className="fold-body">
        {SCORE_ORDER.map((k) => {
          const c = s.components?.[k] ?? 0
          const w = weights?.[k] ?? 0
          return (
            <div className="score-row" key={k}>
              <span>{SCORE_LABEL[k]}</span>
              <span className="score-bar">
                <i style={{ width: `${Math.round(Math.max(0, Math.min(1, c)) * 100)}%` }} />
              </span>
              <span className="num mono">
                {c.toFixed(2)} × {w.toFixed(2)} = {(c * w).toFixed(3)}
              </span>
            </div>
          )
        })}
        <div className="score-total">
          <span>Weighted total</span>
          <span className="mono">{fmtScore(s.total)}</span>
        </div>
        {leg.floor_check ? (
          <div className="check">
            <b>Donor safety check:</b> {fmtQty(leg.floor_check.stock_after)} {unit} left ÷ {fmtRate(leg.floor_check.burn_rate)}/day
            {leg.outbreak_surge > 0 ? ` (incl. ${fmtRate(leg.outbreak_surge)} outbreak surge)` : ''} = <b>{fmtDays(leg.floor_check.cover_after)} d</b> ≥ {floorDays} d floor —{' '}
            {leg.floor_check.passes ? <b className="c-safe">passes</b> : <b className="c-critical">fails</b>}
            {s.rescued_units > 0 ? ` · ${fmtQty(s.rescued_units)} ${unit} would otherwise have expired unused at the donor.` : ''}
          </div>
        ) : null}
      </div>
    </details>
  )
}

function RejectedCard({ c }) {
  return (
    <div className="rej" data-rejected={c.facility_id}>
      <div className="rej-title">
        <b>{c.name}</b>
        <span className="km">
          {c.sub_district} · {fmtKm(c.distance_km)}
        </span>
        <Band band={c.band} />
        {c.days_of_cover != null ? <span className="muted tnum">{fmtDays(c.days_of_cover)} d cover</span> : null}
      </div>
      <div className="reason">{c.reason}</div>
    </div>
  )
}

function Rejections({ data, floorDays }) {
  const rejected = data.rejected || []
  const [showAll, setShowAll] = useState(false)
  if (!rejected.length) return null
  const safety = rejected.filter((c) => c.reason_code === 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  const others = rejected.filter((c) => c.reason_code !== 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  // The safety-check count leads: it is the rejection that makes the
  // recommendation trustworthy. The rest follow by size.
  const byReason = Object.entries(data.rejected_by_reason || {})
    .sort((a, b) => (b[0] === 'donor_safety_floor') - (a[0] === 'donor_safety_floor') || b[1] - a[1])
    .map(([k, n]) => `${n} ${k === 'donor_safety_floor' ? 'failed the donor safety check' : reasonLabel(k).toLowerCase()}`)
    .join(' · ')
  const shown = showAll ? safety : safety.slice(0, 4)
  return (
    <details className="fold" data-fold="rejections">
      <summary>
        <span>
          Why not the other <span className="count">{rejected.length}</span> {plural(rejected.length, 'facility', 'facilities')}?
        </span>
        <span className="muted">{byReason}</span>
      </summary>
      <div className="fold-body">
        {safety.length ? (
          <>
            <p className="small muted">
              Donor safety check: spare = stock − burn rate × {floorDays} days, with the burn rate including the outbreak surge. A facility that would drop
              below {floorDays} days of its own cover cannot give — the same outbreak that created the shortage rules out the nearest donors.
            </p>
            {shown.map((c) => (
              <RejectedCard key={c.facility_id} c={c} />
            ))}
            {safety.length > shown.length ? (
              <button type="button" className="btn-link" onClick={() => setShowAll(true)}>
                Show all {safety.length} safety-check rejections
              </button>
            ) : null}
          </>
        ) : null}
        {others.length ? (
          <table className="tbl" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Facility</th>
                <th className="num">Distance</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {others.map((c) => (
                <tr key={c.facility_id}>
                  <td>
                    {c.name} <span className="muted xs">{c.sub_district}</span>
                  </td>
                  <td className="num">{fmtKm(c.distance_km)}</td>
                  <td>{reasonLabel(c.reason_code)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </details>
  )
}

function Ranking({ data, unit, chosenIds }) {
  const eligible = data.eligible || []
  if (!eligible.length) return null
  return (
    <details className="fold" data-fold="ranking">
      <summary>
        <span>
          Full ranking of the <span className="count">{eligible.length}</span> eligible donors
        </span>
        <span className="muted">score = sufficiency · proximity · expiry benefit · donor comfort</span>
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
            {eligible.map((c) => (
              <tr key={c.facility_id} className={chosenIds.has(c.facility_id) ? 'row-chosen' : ''}>
                <td className="muted">{c.rank}</td>
                <td>
                  {c.name} <span className="muted xs">{c.sub_district}</span>
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
      </div>
    </details>
  )
}

function Memo({ memo }) {
  const [lang, setLang] = useState('en')
  if (!memo) return null
  const text = lang === 'te' ? memo.rationale_te : memo.rationale_en
  const source =
    memo.source === 'template'
      ? 'templated — Gemini unavailable or its response failed numeric validation'
      : `${memo.model || 'Gemini'}${memo.cache_hit ? ' · cached' : ''} · every number checked against backend values`
  return (
    <div className="memo" data-memo-lang={lang}>
      <div className="memo-bar">
        <span className="memo-title">Memo for the District Medical Officer</span>
        <div className="seg" role="group" aria-label="Memo language">
          <button type="button" className={lang === 'en' ? 'on' : ''} onClick={() => setLang('en')} aria-pressed={lang === 'en'}>
            English
          </button>
          <button type="button" className={lang === 'te' ? 'on' : ''} onClick={() => setLang('te')} aria-pressed={lang === 'te'} lang="te">
            తెలుగు
          </button>
        </div>
        <span className="src">{source}</span>
      </div>
      <div className="memo-text" lang={lang}>
        {paragraphs(text).map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
      {memo.risk_notes?.length ? (
        <ul className="memo-notes">
          {memo.risk_notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

// One line per approved recommendation; click to see the full detail again.
function ApprovedLine({ data, medicine, approvals, unit, floorDays, onExpand }) {
  const { legs, recipient } = approvedLegs(data, approvals, floorDays)
  const qty = legs.reduce((n, l) => n + l.qty, 0)
  const one = legs.length === 1 ? legs[0] : null
  return (
    <button type="button" className="rec-done" onClick={onExpand} data-rec-done={medicine.medicine_id}>
      <span className="tag tag-ok">✓ Approved</span>
      <span className="txt">
        <b>{shortMed(medicine.medicine_id, medicine.name)}</b> · {legs.map((l) => l.name).join(' + ')}
        <Arrow />
        {recipient?.name} ·{' '}
        <b>
          {fmtQty(qty)} {unit}
        </b>
        {one ? ` · ${fmtKm(one.distance_km)}` : ''}
        {recipient ? (
          <>
            {' '}
            · {fmtDays(recipient.days_before)}
            <Arrow />
            <b>{fmtDays(recipient.days_after)} d</b> (<Band band={recipient.band_before} />
            <Arrow />
            <Band band={recipient.band_after} />)
          </>
        ) : null}
        {one ? <span className="muted"> · donor keeps {fmtDays(one.days_after)} d</span> : null}
      </span>
      <span className="more">Details</span>
    </button>
  )
}

// --------------------------------------------------------------------------
// Block
// --------------------------------------------------------------------------

export default function Recommendation({ rec, medicine, onApprove, onRetry }) {
  const { loading, error, data, approving, approveError, approvals } = rec
  const [expanded, setExpanded] = useState(false)
  const medName = medicine?.name || data?.recipient?.medicine_name || medicine?.medicine_id
  const unit = medicine?.unit || data?.recipient?.unit || ''
  const approved = approvals && approvals.length > 0
  const floorDays = data?.parameters?.donor_safety_floor_days
  const pending = (data?.transfers || []).filter((t) => t.status === 'pending')

  // A new recommendation opens below the medicine table; bring it into view
  // once so the donor engine's progress and then its answer are on screen.
  // Runs on mount (the short "running the donor engine" block), again when
  // the answer lands (the block is now tall enough to reach the top), and
  // when an approved line is re-expanded.
  const boxRef = useRef(null)
  useEffect(() => {
    if (!approved || expanded) boxRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [data, expanded, approved])

  if (data && approved && !expanded) {
    return <ApprovedLine data={data} medicine={medicine} approvals={approvals} unit={unit} floorDays={floorDays} onExpand={() => setExpanded(true)} />
  }

  return (
    <div className="rec" id={rec.domId} data-rec={medicine?.medicine_id} ref={boxRef}>
      <div className="rec-head">
        <h3 title={medName}>
          {shortMed(medicine?.medicine_id, medName)} <span className="full">· transfer recommendation</span>
        </h3>
        {data?.recipient?.need_units > 0 && !approved ? (
          <span className="need">
            needs{' '}
            <b>
              {fmtQty(data.recipient.need_units)} {unit}
            </b>{' '}
            to reach {data.recipient.target_cover_days} d
          </span>
        ) : null}
        <span className="spacer" />
        {approved ? (
          <>
            <span className="tag tag-ok">✓ Approved</span>
            <button type="button" className="btn-link" onClick={() => setExpanded(false)}>
              Collapse
            </button>
          </>
        ) : data?.plan ? (
          <button type="button" className="btn btn-primary" disabled={approving || pending.length === 0} onClick={onApprove} aria-busy={approving ? 'true' : 'false'} data-action="approve">
            {approving ? (
              <>
                <span className="spinner light" /> Approving…
              </>
            ) : (
              `Approve transfer${pending.length > 1 ? ` (${pending.length} movements)` : ''}`
            )}
          </button>
        ) : null}
      </div>
      <div className="rec-body">
        {loading ? (
          <Waiting
            label={`Running the donor engine for ${medName} and writing the memo…`}
            hint="Filter → donor safety check → quantity → score; then Gemini writes the bilingual memo."
            slow="Still working — the first memo for a pair is a live Gemini call and typically takes 30–60 seconds; repeats come from the cache."
          />
        ) : null}
        {error ? <ErrorNote title="Could not fetch a recommendation" error={error} onRetry={onRetry} /> : null}
        {data ? <Body data={data} unit={unit} approved={approved} approvals={approvals} approveError={approveError} floorDays={floorDays} pending={pending} /> : null}
      </div>
    </div>
  )
}

function Body({ data, unit, approved, approvals, approveError, floorDays, pending }) {
  const R = data.recipient
  const P = data.parameters || {}
  const plan = data.plan
  const memo = data.memo
  const chosenIds = new Set((plan?.donors || []).map((d) => d.facility_id))
  const close = memo?.close_call
  const { legs, recipient } = approved ? approvedLegs(data, approvals, floorDays) : planLegs(data, floorDays)
  const split = legs.length > 1

  return (
    <>
      <p className="rec-situation">
        <b>{R.name}</b> holds{' '}
        <b>
          {fmtQty(R.stock)} {unit}
        </b>
        , consumed at <b>{fmtRate(R.burn_rate)}/day</b>
        {R.outbreak_surge > 0 ? (
          <span className="muted">
            {' '}
            ({fmtRate(R.baseline_burn_rate)} baseline + <span className="surge">{fmtRate(R.outbreak_surge)} outbreak surge</span>)
          </span>
        ) : null}{' '}
        = <b>{fmtDays(R.days_of_cover)} days</b> of cover <Band band={R.band} />
        {R.need_units > 0 ? (
          <>
            {' '}
            · needs <b>{fmtQty(R.need_units)} {unit}</b> to reach {R.target_cover_days} days.
          </>
        ) : null}
      </p>

      {data.status === 'no_eligible_donors' ? (
        <div className="note note-warn">
          <b>No eligible donor within {P.max_transfer_radius_km} km.</b> Every candidate was rejected — see why below. Nothing is recommended.
        </div>
      ) : null}
      {data.status === 'partial' && plan ? (
        <div className="note note-warn">
          <b>Need only partly met:</b> eligible donors can spare {fmtQty(plan.total_qty)} of the {fmtQty(plan.need_units)} {unit} needed.
        </div>
      ) : null}

      {legs.map((leg) => (
        <DonorCard
          key={leg.facility_id}
          leg={leg}
          recipient={recipient}
          unit={unit}
          nEligible={data.counts?.eligible}
          floorDays={floorDays}
          approved={approved}
          transitDays={P.transfer_transit_days}
          split={split}
        />
      ))}

      {close?.is_close_call && close.selectable?.length > 1 ? (
        <p className="close-call">
          Close call — scores within {close.margin}: Gemini adjudicated between{' '}
          {close.selectable.map((s, i) => (
            <span key={s.facility_id}>
              {i ? ', ' : ''}
              {s.name} ({fmtScore(s.score_total)})
            </span>
          ))}
          {memo?.deviates_from_backend_plan ? ' and reordered the backend ranking' : ' and kept the backend ranking'}; the quantities are the backend's either way.
        </p>
      ) : null}

      {memo && plan ? <Memo memo={memo} /> : null}

      <div className="folds">
        <Rejections data={data} floorDays={floorDays} />
        {legs.map((leg) => (
          <ScoreBreakdown key={leg.facility_id} leg={leg} weights={P.score_weights} floorDays={floorDays} unit={unit} />
        ))}
        <Ranking data={data} unit={unit} chosenIds={chosenIds} />
      </div>

      {plan && !approved ? (
        <p className="approve-note">
          {pending.length === 0 ? 'No pending transfer row — fetch donors again.' : data.notice}
        </p>
      ) : null}
      {approveError ? <ErrorNote title="Approval refused" error={approveError} /> : null}
    </>
  )
}
