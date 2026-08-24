// 03 · Transfer — one GET /recommend/{facility}/{medicine} response, whole.
// The engine's five stages, the ranked donor with its score breakdown, every
// rejected candidate with its reason, the bilingual memo, and the approve
// action. Every figure on screen — thresholds included — is read from the
// response; the deterministic engine ran before Gemini was called.
import { useState } from 'react'
import { fmtDays, fmtRate, fmtQty, fmtScore, paragraphs, shortMed } from '../format.js'
import { SecHead, Chip, BackLink, ErrBox, Steps, Elapsed } from '../ui.jsx'

const COMP_LABEL = {
  sufficiency: 'Sufficiency',
  proximity: 'Proximity',
  expiry_benefit: 'Expiry benefit',
  donor_comfort: 'Donor comfort',
}

const GROUPS = [
  { key: 'safety', title: 'Failed the donor safety check', codes: ['donor_safety_floor', 'spare_below_one_unit', 'expiry_guard'], show: 2 },
  { key: 'expiry', title: 'Stock cannot survive transit', codes: ['stock_expired', 'stock_expiring_in_window', 'stock_undated'], show: 1 },
  { key: 'radius', title: 'Beyond the transfer radius', codes: ['out_of_radius'], show: 1 },
  { key: 'stock', title: 'Does not hold the medicine', codes: ['no_stock'], show: 1 },
]

// Pull the arithmetic out of the backend's rejection sentence (or rebuild it
// from fields the response carries) so it can sit in its own box.
function splitReason(c, params) {
  if (c.reason_code === 'donor_safety_floor' || c.reason_code === 'spare_below_one_unit') {
    const m = (c.reason || '').match(/\((spare = [^)]+)\)/)
    if (m) {
      const why = c.reason.replace(m[0], '').replace(/\s{2,}/g, ' ').replace(/ \./g, '.')
      const arith = m[1].replace(/ x /g, ' × ')
      const i = arith.lastIndexOf('=')
      return { why, pre: arith.slice(0, i + 1), val: arith.slice(i + 1).trim() }
    }
  }
  if (c.reason_code === 'out_of_radius' && params) {
    return { why: c.reason, pre: `${c.distance_km} km >`, val: `${params.max_transfer_radius_km} km` }
  }
  if (c.reason_code === 'stock_expiring_in_window' && params) {
    const dated = (c.lots || []).filter((l) => l.days_to_expiry != null)
    if (dated.length) {
      const maxD = Math.max(...dated.map((l) => l.days_to_expiry))
      return { why: c.reason, pre: `days to expiry ${maxD} ≤ window`, val: `${params.transit_and_use_window_days} d` }
    }
  }
  return { why: c.reason, pre: null, val: null }
}

function DonorCard({ d, post, weights, unit }) {
  const after = (post?.donors || []).find((x) => x.facility_id === d.facility_id)
  const lots = d.lots_given || []
  return (
    <div className="donor" data-donor={d.facility_id}>
      <div className="donor-hd">
        <div className="donor-rank">{d.order}</div>
        <div className="donor-id">
          <h4>{d.name} PHC</h4>
          <div className="m">
            {d.sub_district} · {d.distance_km} km · score {fmtScore(d.score?.total)}
          </div>
        </div>
        <div className="donor-qty">
          <div className="v">{fmtQty(d.qty)}</div>
          <div className="u">{unit}</div>
        </div>
      </div>
      {d.summary ? <p className="donor-why">{d.summary}</p> : null}
      <div className="kv">
        <div className="k">
          <span className="lbl">{lots.length === 1 ? 'Lot drawn' : 'Lots drawn'}</span>
          {lots.length ? (
            lots.map((l) => (
              <div key={l.batch || l.expiry}>
                <div className="v">{l.batch || '—'}</div>
                <div className="lbl" style={{ color: 'var(--red-ink)', marginTop: 4 }}>
                  {l.expiry ? `exp ${l.expiry}${l.days_to_expiry != null ? ` · ${l.days_to_expiry}d` : ''}` : 'undated'}
                </div>
              </div>
            ))
          ) : (
            <div className="v">—</div>
          )}
        </div>
        <div className="k">
          <span className="lbl">Its stock</span>
          <div className="v">
            {fmtQty(d.stock_before)}
            <span className="ar">→</span>
            {fmtQty(d.stock_after)}
          </div>
        </div>
        <div className="k">
          <span className="lbl">Its cover</span>
          <div className="v">
            {fmtDays(d.cover_before)}
            <span className="ar">→</span>
            {fmtDays(d.cover_after)}
            <span style={{ fontSize: '.7em' }}>D</span>
          </div>
        </div>
        <div className="k">
          <span className="lbl">Above floor by</span>
          <div className="v">{after?.above_floor_by_days != null ? `+${fmtDays(after.above_floor_by_days)} days` : '—'}</div>
        </div>
      </div>
      {d.score?.components ? (
        <div className="scores">
          {Object.entries(d.score.components).map(([k, v]) => (
            <div className="sc" key={k}>
              <div className="lbl">
                <span>
                  {COMP_LABEL[k] || k}
                  {weights?.[k] != null ? ` ×${weights[k]}` : ''}
                </span>
                <span>{Number(v).toFixed(2)}</span>
              </div>
              <div className="bar">
                <i style={{ width: `${Math.min(100, Number(v) * 100)}%` }} />
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export default function Transfer({ rec, onBack, onRetry, onApprove, approveState }) {
  const [lang, setLang] = useState('en')
  const data = rec?.data
  const backName = data?.recipient?.name || 'Facility'

  if (!data) {
    return (
      <div className="waitstage">
        <div className="crumb">
          <BackLink onClick={onBack}>{backName}</BackLink>
        </div>
        <span className="lbl lbl-red">03 — Transfer Recommendation</span>
        {rec?.error ? (
          <div className="mt5">
            <ErrBox title="The recommendation failed" error={rec.error} onRetry={onRetry} />
          </div>
        ) : (
          <>
            <Steps
              items={[
                { state: 'done', label: 'Request sent', rs: `GET /recommend/${rec?.facilityId || ''}/${rec?.medicineId || ''}` },
                { state: 'on', label: 'Engine stages 01–05 · Gemini memo (Job 02)', rs: <Elapsed /> },
              ]}
            />
            <p className="stephint">
              The donor engine is deterministic and returns at once; the wait is Gemini writing and validating the
              bilingual memo. A live call can take around 45 seconds — a cached memo, or the deterministic template
              fallback, returns immediately.
            </p>
          </>
        )}
      </div>
    )
  }

  const R = data.recipient
  const params = data.parameters
  const counts = data.counts || {}
  const plan = data.plan
  const post = data.post_transfer
  const memo = data.memo || {}
  const med = shortMed(R.medicine_id, R.medicine_name)
  const closeCall = memo.close_call?.is_close_call
  const pending = (data.transfers || []).filter((t) => t.status === 'pending')

  const donors = plan?.donors || []
  const donorIds = new Set(donors.map((d) => d.facility_id))
  const runner = (data.eligible || []).find((c) => !donorIds.has(c.facility_id))
  const topScore = donors[0]?.score?.total
  const gap = topScore != null && runner?.score?.total != null ? topScore - runner.score.total : null

  // The nearest facility that actually holds stock — if it was rejected,
  // that rejection is the strongest trust signal on the page.
  const holders = [...(data.eligible || []), ...(data.rejected || [])].filter((c) => (c.stock || 0) > 0)
  const nearestHolder = holders.length ? holders.reduce((a, b) => (a.distance_km <= b.distance_km ? a : b)) : null
  const nearestRejectedId = nearestHolder && !nearestHolder.eligible ? nearestHolder.facility_id : null

  const numerals = memo.validation?.numerals?.length || 0
  const memoText = lang === 'te' ? memo.rationale_te : memo.rationale_en
  const noNeed = data.status === 'no_need' || data.status === 'no_burn_rate'

  return (
    <div>
      <div className="crumb">
        <BackLink onClick={onBack}>{backName} PHC</BackLink>
      </div>

      <div className="hero">
        <div>
          <span className="lbl lbl-red">03 — Transfer Recommendation</span>
          <h1 className="h1" style={{ marginTop: 14 }}>
            {med}
            <span style={{ color: 'var(--grey)' }}> → {R.name} PHC</span>
          </h1>
          <p className="lede" style={{ marginTop: 16 }}>
            {closeCall
              ? 'Every figure below was computed before Gemini was called. The top donors score within the close-call margin, so the model selected between backend-certified options — and it originated no number.'
              : 'Every figure below was computed before Gemini was called. The model chose nothing here — there was no close call to adjudicate — and it originated no number.'}
          </p>
        </div>
      </div>

      {noNeed ? (
        <div className="gapbar quiet">
          <span className="lbl">No transfer needed</span>
          <span className="t">
            {R.days_of_cover != null ? `${fmtDays(R.days_of_cover)} days of cover` : 'No consumption recorded'} against the{' '}
            {R.target_cover_days}-day target
          </span>
        </div>
      ) : (
        <div className="gapbar">
          <span className="lbl lbl-red">Supply gap</span>
          <span className="t">
            <em>
              {fmtQty(R.need_units)} {R.unit}
            </em>{' '}
            short of {R.target_cover_days}-day cover
          </span>
          <span className="lbl grey">
            need = {fmtRate(R.burn_rate)} × {R.target_cover_days} − {fmtQty(R.stock)}
          </span>
        </div>
      )}

      <div>
        <SecHead n="00" title="Recipient state" aside="The division, both halves" />
        <div className="cells" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))' }}>
          <div className="cell">
            <span className="lbl">On hand</span>
            <div className="v">{fmtQty(R.stock)}</div>
            <div className="sub">{R.unit}</div>
          </div>
          <div className="cell">
            <span className="lbl">Baseline burn</span>
            <div className="v">{fmtRate(R.baseline_burn_rate)}</div>
            <div className="sub">per day · HMIS</div>
          </div>
          <div className="cell">
            <span className="lbl">Outbreak surge</span>
            <div className="v" style={R.outbreak_surge > 0 ? { color: 'var(--red)' } : undefined}>
              {R.outbreak_surge > 0 ? `+${fmtRate(R.outbreak_surge)}` : '—'}
            </div>
            <div className="sub">per day · IDSP</div>
          </div>
          <div className="cell">
            <span className="lbl">Total burn</span>
            <div className="v">{fmtRate(R.burn_rate)}</div>
            <div className="sub">per day</div>
          </div>
          <div className="cell">
            <span className="lbl">Days of cover</span>
            <div className="v" style={R.band === 'critical' ? { color: 'var(--red)' } : undefined}>{fmtDays(R.days_of_cover)}</div>
            <div className="sub">
              <Chip band={R.band} />
            </div>
          </div>
        </div>
      </div>

      {!noNeed ? (
        <>
          <div className="mt7">
            <SecHead n="01–05" title="Engine stages" aside="Fully deterministic · no AI in this path" />
            <div className="cells" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))' }}>
              {[
                ['01', 'Candidate screen', counts.considered, 'assessed', `Holds the medicine, inside ${params.max_transfer_radius_km} km, stock survives transit`],
                ['02', 'Donor safety check', counts.rejected_by_safety_check, 'excluded here', `spare = stock − burn × ${params.donor_safety_floor_days}, surge included`],
                ['03', 'Quantity', plan ? fmtQty(plan.total_qty) : 0, R.unit, 'min(need, donor spare), rounded to whole units'],
                ['04', 'Scoring', counts.eligible, 'eligible', 'Sufficiency · proximity · expiry · comfort'],
                ['05', 'Rejections', counts.rejected, 'with reasons', 'Returned as part of the recommendation'],
              ].map(([n, t, v, u, note]) => (
                <div className="cell" key={n}>
                  <span className="lbl lbl-red">{n}</span>
                  <div className="h3" style={{ margin: '6px 0 10px' }}>{t}</div>
                  <div className="v" style={{ fontSize: '1.9rem' }}>{v}</div>
                  <div className="sub">{u}</div>
                  <p className="note">{note}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="split split-wide mt7">
            <div className="stackv">
              <div>
                <SecHead
                  n="E"
                  title={donors.length ? (donors.length === 1 ? 'Recommended donor' : 'Recommended donors') : 'No eligible donor'}
                  aside={`${counts.eligible} of ${counts.considered} can safely spare stock`}
                />
                {donors.map((d) => (
                  <DonorCard key={d.facility_id} d={d} post={post} weights={params.score_weights} unit={R.unit} />
                ))}
                {!donors.length ? (
                  <div className="excl excl-safety">
                    <div className="excl-hd">
                      <span className="nm">No facility can safely give</span>
                    </div>
                    <p className="why">
                      Every candidate inside the {params.max_transfer_radius_km} km radius was excluded — most by the donor
                      safety check. A transfer that starves the donor is the one failure this engine exists to prevent, so
                      no recommendation is made.
                    </p>
                  </div>
                ) : null}
                {plan && plan.shortfall > 0 ? (
                  <p className="footnote" style={{ marginTop: 12 }}>
                    Partial cover: the eligible donors can safely give {fmtQty(plan.total_qty)} of the {fmtQty(plan.need_units)}{' '}
                    {R.unit} needed — a shortfall of {fmtQty(plan.shortfall)}. No donor is pushed below the floor to close it.
                  </p>
                ) : null}

                {runner ? (
                  <div className="box" style={{ marginTop: 16 }}>
                    <div className="box-hd">
                      <span className="h3">Runner-up · {runner.name} PHC</span>
                      <span className="lbl grey">
                        {runner.distance_km} km · score {fmtScore(runner.score?.total)}
                      </span>
                    </div>
                    <div className="box-bd">
                      <p style={{ fontSize: '12.5px', lineHeight: 1.6 }}>
                        Eligible, and passes the same safety check — it can spare {fmtQty(runner.spare_units)} {R.unit}{' '}
                        against a need of {fmtQty(R.need_units)}, {runner.distance_km} km away, holding{' '}
                        {fmtDays(runner.days_of_cover)} days of its own cover. It would be drawn on next if the ranked{' '}
                        {donors.length === 1 ? 'donor' : 'donors'} could not cover the full quantity.
                      </p>
                      {gap != null ? (
                        <p className="footnote" style={{ marginTop: 14 }}>
                          {closeCall ? (
                            <>
                              The score gap is <strong>{gap.toFixed(3)}</strong> — inside the{' '}
                              <strong>{memo.close_call.margin}</strong> close-call margin, so Gemini adjudicated between the
                              closely-scored candidates{memo.deviates_from_backend_plan ? ' and reordered the backend plan' : ''}.
                            </>
                          ) : (
                            <>
                              The gap between the two scores is <strong>{gap.toFixed(3)}</strong> — wider than the{' '}
                              <strong>{memo.close_call?.margin}</strong> close-call margin, so there is nothing for the model
                              to adjudicate. The backend's ranking stands. Close calls are not manufactured to give the model
                              something to do.
                            </>
                          )}
                        </p>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>

              <div>
                <SecHead n="F" title="Excluded, and why" aside={`${counts.rejected} facilities`} />
                <p className="lede" style={{ marginBottom: 24 }}>
                  {counts.rejected_by_safety_check > 0 ? (
                    <>
                      {counts.rejected_by_safety_check} facilities hold {med}, sit inside the radius, and still cannot give
                      any — the donor safety check excluded them.{' '}
                    </>
                  ) : null}
                  An officer who can see why the <em>nearest</em> facility was excluded has reason to trust the one that was
                  chosen. This list is part of the recommendation, not diagnostics.
                </p>
                {GROUPS.map((g) => {
                  const items = (data.rejected || [])
                    .filter((c) => g.codes.includes(c.reason_code))
                    .sort((a, b) => a.distance_km - b.distance_km)
                  if (!items.length) return null
                  const shown = items.slice(0, g.show)
                  return (
                    <div className="exclgroup" key={g.key}>
                      <div className="exclgroup-hd">
                        <span className="lbl">{g.title}</span>
                        <span className="lbl">{items.length}</span>
                      </div>
                      {shown.map((c) => {
                        const s = splitReason(c, params)
                        return (
                          <div className={`excl${g.key === 'safety' ? ' excl-safety' : ''}`} key={c.facility_id}>
                            <div className="excl-hd">
                              <span className="nm">{c.name} PHC</span>
                              <Chip band={c.band} />
                              <span className="km">{c.distance_km} km</span>
                            </div>
                            <p className="why">{s.why}</p>
                            {s.pre ? (
                              <div className="arith">
                                {s.pre} {s.val ? <b>{s.val}</b> : null}
                              </div>
                            ) : null}
                            {c.facility_id === nearestRejectedId ? (
                              <div>
                                <span className="tagline">Nearest facility holding {med}</span>
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                      {items.length > shown.length ? (
                        <span className="lbl grey" style={{ display: 'block', marginTop: 12 }}>
                          + {items.length - shown.length} more with the same verdict
                        </span>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="stackv">
              <div className="box">
                <div className="box-hd">
                  <span className="h3">Transfer memo</span>
                  <span className="lbl grey">Gemini · Job 02</span>
                </div>
                <div className="memo-tabs" role="tablist">
                  <button type="button" role="tab" aria-selected={lang === 'en'} onClick={() => setLang('en')}>
                    English
                  </button>
                  <button type="button" role="tab" aria-selected={lang === 'te'} onClick={() => setLang('te')} data-action="memo-te">
                    తెలుగు
                  </button>
                </div>
                <div className="memo-body" lang={lang}>
                  {paragraphs(memoText).map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </div>
                <div className="prov">
                  <span className="ptag">{memo.model}</span>
                  <span className="ptag">
                    {memo.source === 'gemini' ? 'live response' : memo.source === 'cache' ? 'cached response' : 'deterministic template'}
                  </span>
                  {numerals ? <span className="ptag ptag-ok">{numerals} numerals verified</span> : null}
                  <span className="ptag">{closeCall ? 'close call — adjudicated' : 'no close call'}</span>
                  <p>
                    Every numeral in both languages is cross-checked against the backend-computed input; one unmatched
                    number discards the whole response in favour of the deterministic template — enforced in code, not by
                    prompting.{memo.source === 'template' ? ' This memo is that template: the model response was unavailable or failed validation.' : ''}
                  </p>
                </div>
              </div>

              {(memo.risk_notes || []).length ? (
                <div className="box">
                  <div className="box-hd">
                    <span className="h3">Risk notes</span>
                  </div>
                  <div className="box-bd">
                    <ul className="notes">
                      {memo.risk_notes.map((r, i) => (
                        <li key={i}>
                          <span className="n">{String(i + 1).padStart(2, '0')}</span>
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}

              <div className="box">
                <div className="box-hd">
                  <span className="h3">Parameters</span>
                </div>
                <table className="tbl">
                  <tbody>
                    {[
                      ['Donor safety floor', `${params.donor_safety_floor_days} d`],
                      ['Target cover', `${params.target_cover_days} d`],
                      ['Transfer radius', `${params.max_transfer_radius_km} km`],
                      ['Transit + min use', `${params.transfer_transit_days} + ${params.transfer_min_use_days} d`],
                      ['Near-expiry', `${params.near_expiry_days} d`],
                      ...Object.entries(params.score_weights || {}).map(([k, v]) => [`${COMP_LABEL[k] || k} weight`, String(v)]),
                    ].map(([k, v]) => (
                      <tr key={k}>
                        <td className="lbl grey">{k}</td>
                        <td className="r num" style={{ fontWeight: 900 }}>{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="approve mt7">
            <div className="notice">
              <span className="lbl">Decision support only</span>
              <p>
                {data.notice} Approval re-runs the donor safety check against the ledger as it stands now — if the donor can
                no longer spare the quantity, the approval is refused rather than applied partially.
              </p>
              {approveState.error ? (
                <p style={{ color: 'var(--red-ink)', fontWeight: 700, marginTop: 8 }}>
                  Refused: {approveState.error.message}
                </p>
              ) : null}
            </div>
            <button type="button" className="btn btn-lg" onClick={onBack}>
              Back to facility
            </button>
            <button
              type="button"
              className="btn btn-primary btn-lg"
              data-action="approve"
              disabled={!pending.length || approveState.running}
              onClick={onApprove}
            >
              {approveState.running
                ? 'Applying — safety check re-running…'
                : pending.length
                  ? `Approve transfer · ${fmtQty(plan?.total_qty)} ${R.unit}`
                  : 'Nothing pending to approve'}
            </button>
          </div>
        </>
      ) : null}
    </div>
  )
}
