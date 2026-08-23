import { useState } from 'react'
import { fmtDays, fmtKm, fmtQty, fmtRate, fmtScore, paragraphs, reasonLabel, plural, shortMed } from '../format.js'
import { Band, Waiting, ErrorNote } from './common.jsx'
import { useMediaQuery, PHONE_QUERY } from '../hooks.js'

const SCORE_ORDER = ['sufficiency', 'proximity', 'expiry_benefit', 'donor_comfort']
const SCORE_LABEL = {
  sufficiency: 'Sufficiency',
  proximity: 'Proximity',
  expiry_benefit: 'Expiry benefit',
  donor_comfort: 'Donor comfort',
}

// --------------------------------------------------------------------------
// Pieces
// --------------------------------------------------------------------------

function Arrow() {
  return <span className="arrow">→</span>
}

// Stock / cover / band, before → after, one row per facility. Used for the
// projected plan and, after approval, for the ledger as written.
function FlowTable({ rows, unit, phone }) {
  if (phone) {
    return (
      <div className="flow-cards">
        {rows.map((r, i) => (
          <div className="flow-card" key={i}>
            <div>
              <b>{r.name}</b> <span className="muted">{r.role}</span>
            </div>
            <div>
              {fmtQty(r.before.stock)} <Arrow /> <b>{fmtQty(r.after.stock)}</b> {unit} · {fmtDays(r.before.days)} <Arrow /> <b>{fmtDays(r.after.days)} d</b>
            </div>
            <div>
              <Band band={r.before.band} /> <Arrow /> <Band band={r.after.band} />
              {r.margin != null ? (
                <span className="muted">
                  {' '}
                  · {r.margin >= 0 ? '+' : ''}
                  {fmtDays(r.margin)} d above floor
                </span>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    )
  }
  return (
    <table className="flow">
      <thead>
        <tr>
          <th />
          <th className="num">Stock ({unit})</th>
          <th className="num">Days of cover</th>
          <th>Band</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td>
              <b>{r.name}</b> <span className="muted">{r.role}</span>
            </td>
            <td className="num">
              {fmtQty(r.before.stock)} <Arrow /> <b>{fmtQty(r.after.stock)}</b>
            </td>
            <td className="num">
              {fmtDays(r.before.days)} <Arrow /> <b>{fmtDays(r.after.days)}</b>
            </td>
            <td className="nowrap">
              <Band band={r.before.band} /> <Arrow /> <Band band={r.after.band} />
              {r.margin != null ? (
                <span className="muted small">
                  {' '}
                  · {r.margin >= 0 ? '+' : ''}
                  {fmtDays(r.margin)} d above the {r.floorDays}-day floor
                </span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function planRows(data, floorDays) {
  const pt = data.post_transfer
  const rows = []
  for (const d of pt?.donors || []) {
    rows.push({
      name: d.name,
      role: `sends ${fmtQty(d.qty)}`,
      before: { stock: d.stock_before, days: d.days_of_cover_before, band: d.band_before },
      after: { stock: d.stock_after, days: d.days_of_cover_after, band: d.band_after },
      margin: d.above_floor_by_days,
      floorDays,
    })
  }
  if (pt?.recipient) {
    rows.push({
      name: data.recipient.name,
      role: 'receives',
      before: { stock: pt.recipient.stock_before, days: pt.recipient.days_of_cover_before, band: pt.recipient.band_before },
      after: { stock: pt.recipient.stock_after, days: pt.recipient.days_of_cover_after, band: pt.recipient.band_after },
    })
  }
  return rows
}

function approvalRows(approvals, floorDays) {
  const rows = []
  for (const a of approvals) {
    const d = a.donor
    if (d?.before && d?.after) {
      rows.push({
        name: d.after.name,
        role: `sent ${fmtQty(Math.abs(d.before.stock - d.after.stock))}`,
        before: { stock: d.before.stock, days: d.before.days_of_cover, band: d.before.band },
        after: { stock: d.after.stock, days: d.after.days_of_cover, band: d.after.band },
        margin: d.after.days_of_cover == null ? null : d.after.days_of_cover - floorDays,
        floorDays,
      })
    }
  }
  const first = approvals[0]?.recipient
  const last = approvals[approvals.length - 1]?.recipient
  if (first?.before && last?.after) {
    rows.push({
      name: last.after.name,
      role: 'received',
      before: { stock: first.before.stock, days: first.before.days_of_cover, band: first.before.band },
      after: { stock: last.after.stock, days: last.after.days_of_cover, band: last.after.band },
    })
  }
  return rows
}

function ScoreBreakdown({ d, weights, floorDays, unit }) {
  const s = d.score
  if (!s) return null
  return (
    <details className="fold">
      <summary>
        Score breakdown and safety check{' '}
        <span className="muted">
          — {d.name}, {fmtScore(s.total)}
        </span>
      </summary>
      <div className="fold-body score">
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
                {c.toFixed(2)} × {w.toFixed(2)}
              </span>
            </div>
          )
        })}
        <div className="small" style={{ marginTop: 6 }}>
          Safety check: {fmtQty(d.floor_check?.stock_after)} {unit} left ÷ {fmtRate(d.floor_check?.burn_rate)}/day
          {d.outbreak_surge > 0 ? ` (incl. ${fmtRate(d.outbreak_surge)} surge)` : ''} = {fmtDays(d.floor_check?.cover_after)} d ≥ {floorDays} —{' '}
          {d.floor_check?.passes ? <b style={{ color: 'var(--safe)' }}>passes</b> : <b style={{ color: 'var(--critical)' }}>fails</b>}
          {s.rescued_units > 0 ? ` · ${fmtQty(s.rescued_units)} ${unit} would otherwise have expired unused at the donor.` : ''}
        </div>
      </div>
    </details>
  )
}

function DonorHeader({ d, nEligible, unit }) {
  return (
    <div className="donor-head">
      <span className="donor-name">{d.name}</span>
      <span className="muted">
        {d.sub_district} · {fmtKm(d.distance_km)}
      </span>
      <span className="tag">
        rank {d.rank} of {nEligible}
      </span>
      <span className="tag">score {fmtScore(d.score?.total)}</span>
      <span className="donor-sends">
        sends{' '}
        <b>
          {fmtQty(d.qty)} {unit}
        </b>
        <span className="muted small"> from {(d.lots_given || []).map((l) => `lot ${l.batch} (exp ${l.expiry})`).join(', ')}</span>
      </span>
    </div>
  )
}

function RejectedCard({ c }) {
  return (
    <div className="rej">
      <div className="rej-title">
        <b>{c.name}</b>
        <span className="muted">
          {c.sub_district} · {fmtKm(c.distance_km)}
        </span>
        <Band band={c.band} />
        {c.days_of_cover != null ? <span className="muted">{fmtDays(c.days_of_cover)} d cover</span> : null}
      </div>
      <div className="reason">{c.reason}</div>
    </div>
  )
}

function Rejections({ data, floorDays }) {
  const rejected = data.rejected || []
  const [showAllSafety, setShowAllSafety] = useState(false)
  if (!rejected.length) return null
  const safety = rejected.filter((c) => c.reason_code === 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  const others = rejected.filter((c) => c.reason_code !== 'donor_safety_floor').sort((a, b) => a.distance_km - b.distance_km)
  // The safety-check count leads: it is the rejection that makes the
  // recommendation trustworthy. The rest follow by size.
  const byReason = Object.entries(data.rejected_by_reason || {})
    .sort((a, b) => (b[0] === 'donor_safety_floor') - (a[0] === 'donor_safety_floor') || b[1] - a[1])
    .map(([k, n]) => `${n} ${k === 'donor_safety_floor' ? 'failed the donor safety check' : reasonLabel(k).toLowerCase()}`)
    .join(' · ')
  const shown = showAllSafety ? safety : safety.slice(0, 4)
  return (
    <details className="fold">
      <summary>
        Why not the other {rejected.length} {plural(rejected.length, 'facility', 'facilities')}? <span className="muted">{byReason}</span>
      </summary>
      <div className="fold-body">
        {safety.length ? (
          <>
            <p className="small muted">
              Donor safety check: spare = stock − burn rate × {floorDays} days, burn rate including the outbreak surge. A facility that would drop below{' '}
              {floorDays} days of its own cover cannot give — the same outbreak that created the shortage rules out the nearest donors.
            </p>
            {shown.map((c) => (
              <RejectedCard key={c.facility_id} c={c} />
            ))}
            {safety.length > shown.length ? (
              <button type="button" className="btn-link" onClick={() => setShowAllSafety(true)}>
                Show all {safety.length} safety-check rejections
              </button>
            ) : null}
          </>
        ) : null}
        {others.length ? (
          <table className="tbl tbl-compact" style={{ marginTop: 8 }}>
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
                    {c.name} <span className="muted small">{c.sub_district}</span>
                  </td>
                  <td className="num">{fmtKm(c.distance_km)}</td>
                  <td className="small">{reasonLabel(c.reason_code)}</td>
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
  if (eligible.length <= 1) return null
  return (
    <details className="fold">
      <summary>Full ranking of the {eligible.length} eligible donors</summary>
      <div className="fold-body tbl-scroll">
        <table className="tbl tbl-compact">
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
    <div className="memo">
      <div className="memo-bar">
        <span className="memo-title">Memo for the District Medical Officer</span>
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
function ApprovedLine({ data, medicine, approvals, unit, onExpand }) {
  const first = approvals[0]?.recipient
  const last = approvals[approvals.length - 1]?.recipient
  const donors = approvals.map((a) => a.donor?.after?.name).filter(Boolean)
  const qty = approvals.reduce((n, a) => n + Math.abs((a.donor?.before?.stock ?? 0) - (a.donor?.after?.stock ?? 0)), 0)
  const km = data.plan?.donors?.length === 1 ? fmtKm(data.plan.donors[0].distance_km) : null
  const keep = approvals.length === 1 ? approvals[0].donor?.after?.days_of_cover : null
  return (
    <div className="rec rec-done">
      <span className="tag tag-ok">✓ Approved</span>
      <span className="rec-done-text">
        <b>{shortMed(medicine.medicine_id, medicine.name)}</b> · {donors.join(' + ')} <Arrow /> {last?.after?.name} ·{' '}
        <b>
          {fmtQty(qty)} {unit}
        </b>
        {km ? ` · ${km}` : ''}
        {first?.before && last?.after ? (
          <>
            {' '}
            · {last.after.name} {fmtDays(first.before.days_of_cover)} <Arrow /> <b>{fmtDays(last.after.days_of_cover)} d</b> (<Band band={first.before.band} />{' '}
            <Arrow /> <Band band={last.after.band} />)
          </>
        ) : null}
        {keep != null ? <span className="muted"> · donor keeps {fmtDays(keep)} d</span> : null}
      </span>
      <button type="button" className="btn-link" onClick={onExpand}>
        Details
      </button>
    </div>
  )
}

// --------------------------------------------------------------------------
// Panel
// --------------------------------------------------------------------------

export default function Recommendation({ rec, medicine, onApprove, onRetry }) {
  const { loading, error, data, approving, approveError, approvals } = rec
  const [expanded, setExpanded] = useState(false)
  const phone = useMediaQuery(PHONE_QUERY)
  const medName = medicine?.name || data?.recipient?.medicine_name || medicine?.medicine_id
  const unit = medicine?.unit || data?.recipient?.unit || ''
  const approved = approvals && approvals.length > 0

  if (data && approved && !expanded) {
    return <ApprovedLine data={data} medicine={medicine} approvals={approvals} unit={unit} onExpand={() => setExpanded(true)} />
  }

  return (
    <div className="rec" id={rec.domId}>
      <div className="rec-head">
        <h3>{medName} — transfer recommendation</h3>
        {data?.recipient?.need_units > 0 ? (
          <span className="muted">
            needs{' '}
            <b>
              {fmtQty(data.recipient.need_units)} {unit}
            </b>{' '}
            to reach {data.recipient.target_cover_days} days
          </span>
        ) : null}
        {approved ? (
          <button type="button" className="btn-link" style={{ marginLeft: 'auto' }} onClick={() => setExpanded(false)}>
            Collapse
          </button>
        ) : null}
      </div>
      <div className="rec-body">
        {loading ? (
          <Waiting
            label={`Running the donor engine for ${medName} and writing the memo…`}
            hint="Filter → donor safety check → quantity → score; then Gemini writes the bilingual memo. The first memo for a pair can take up to a minute; repeats are cached."
          />
        ) : null}
        {error ? <ErrorNote title="Could not fetch a recommendation" error={error} onRetry={onRetry} /> : null}
        {data ? (
          <Body data={data} unit={unit} phone={phone} approving={approving} approveError={approveError} approvals={approvals} onApprove={onApprove} />
        ) : null}
      </div>
    </div>
  )
}

function Body({ data, unit, phone, approving, approveError, approvals, onApprove }) {
  const R = data.recipient
  const P = data.parameters || {}
  const floorDays = P.donor_safety_floor_days
  const plan = data.plan
  const memo = data.memo
  const chosenIds = new Set((plan?.donors || []).map((d) => d.facility_id))
  const pending = (data.transfers || []).filter((t) => t.status === 'pending')
  const approved = approvals && approvals.length > 0
  const close = memo?.close_call
  const rows = approved ? approvalRows(approvals, floorDays) : planRows(data, floorDays)

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
            ({fmtRate(R.baseline_burn_rate)} baseline + <span className="surge-pos">{fmtRate(R.outbreak_surge)} surge</span>)
          </span>
        ) : null}{' '}
        = <b>{fmtDays(R.days_of_cover)} days</b> of cover <Band band={R.band} />
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

      {plan?.donors?.length ? (
        <div className="donor">
          {plan.donors.map((d) => (
            <DonorHeader key={d.facility_id} d={d} nEligible={data.counts.eligible} unit={unit} />
          ))}
          {approved ? (
            <div className="flow-caption ok">Approved — ledger updated at both facilities</div>
          ) : (
            <div className="flow-caption">Projected after transfer · transit assumed {P.transfer_transit_days} days, not instant</div>
          )}
          <FlowTable rows={rows} unit={unit} phone={phone} />
          {plan.donors.map((d) => (
            <ScoreBreakdown key={d.facility_id} d={d} weights={P.score_weights} floorDays={floorDays} unit={unit} />
          ))}
        </div>
      ) : null}

      {close?.is_close_call && close.selectable?.length > 1 ? (
        <p className="small muted rec-close">
          Close call — scores within {close.margin}: Gemini adjudicated between{' '}
          {close.selectable.map((s, i) => (
            <span key={s.facility_id}>
              {i ? ', ' : ''}
              {s.name} ({fmtScore(s.score_total)})
            </span>
          ))}
          {memo?.deviates_from_backend_plan ? ' and reordered the backend ranking' : ' and kept the backend ranking'}; quantities are the backend's either way.
        </p>
      ) : null}

      <Rejections data={data} floorDays={floorDays} />
      <Ranking data={data} unit={unit} chosenIds={chosenIds} />

      {memo && plan ? <Memo memo={memo} /> : null}

      {plan && !approved ? (
        <div className="approve-bar">
          <button type="button" className="btn btn-approve" disabled={approving || pending.length === 0} onClick={onApprove} aria-busy={approving ? 'true' : 'false'}>
            {approving ? (
              <>
                <span className="spinner light" /> Approving…
              </>
            ) : (
              <>Approve transfer{pending.length > 1 ? ` (${pending.length} movements)` : ''}</>
            )}
          </button>
          <span className="small muted">
            {pending.length === 0
              ? 'No pending transfer row — fetch donors again.'
              : 'Decision-support only — the transfer requires approval by the District Medical Officer.'}
          </span>
        </div>
      ) : null}
      {approveError ? <ErrorNote title="Approval refused" error={approveError} /> : null}
    </>
  )
}
