// Ingest sheet — Gemini Job 01. The four PDFs are the real MoHFW IDSP
// weekly reports committed under data/idsp_pdfs/ on the backend; the
// frontend only names them. Every figure in the result panel is read from
// the POST /ingest-idsp response.
import { useEffect, useState } from 'react'
import { Steps, Elapsed, ErrBox, Chip } from './ui.jsx'
import { fmtDays, shortMed } from './format.js'

const PDFS = [
  { f: 'idsp_2025_w45.pdf', m: 'Week 45, 2025 · 3–9 November', demo: true },
  { f: 'idsp_2023_w21.pdf', m: 'Week 21, 2023' },
  { f: 'idsp_2022_w42.pdf', m: 'Week 42, 2022' },
  { f: 'idsp_2022_w30.pdf', m: 'Week 30, 2022' },
]

export default function IngestModal({ ingest, onRun, onClose, onOpenFacility }) {
  const [picked, setPicked] = useState(PDFS[0].f)
  const { phase, result, error } = ingest // pick | running | done | error
  const busy = phase === 'running'

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const driving = (result?.records || []).filter((r) => r.drives_surge)
  const changed = result?.facilities_changed_band || []
  const first = changed[0]

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="ingest-title" onClick={(e) => {
      if (e.target === e.currentTarget && !busy) onClose()
    }}>
      <div className="sheet">
        <div className="sheet-hd">
          <span className="lbl">Gemini · Job 01</span>
          <h3 className="h2" id="ingest-title">Ingest IDSP weekly report</h3>
          <p>
            Gemini reads the PDF and extracts every outbreak row from both tables — the weekly table and the late-reported one.
            Responses are cached by file hash: a second ingest of the same file makes zero model calls.
          </p>
        </div>

        <div className="sheet-bd">
          {phase === 'pick' ? (
            <div>
              {PDFS.map((p) => (
                <button
                  key={p.f}
                  type="button"
                  className="pdf"
                  aria-pressed={p.f === picked}
                  onClick={() => setPicked(p.f)}
                >
                  <span style={{ minWidth: 0 }}>
                    <span className="fn">{p.f}</span>
                    <span className="fm" style={{ display: 'block' }}>{p.m}</span>
                  </span>
                  {p.demo ? <span className="dm">Demo</span> : null}
                </button>
              ))}
            </div>
          ) : null}

          {phase === 'running' ? (
            <div>
              <Steps
                items={[
                  { state: 'done', label: 'POST /ingest-idsp', rs: ingest.filename },
                  { state: 'on', label: 'Gemini reading the document', rs: <Elapsed /> },
                  { state: 'wait', label: 'Validate · allocate · recompute days of cover' },
                ]}
              />
              <p className="stephint">
                A live extraction takes up to a minute. A repeat ingest of the same file is served from the on-disk
                cache and makes zero model calls.
              </p>
            </div>
          ) : null}

          {phase === 'error' ? (
            <ErrBox title="The ingest failed" error={error} onRetry={() => onRun(ingest.filename)} />
          ) : null}

          {phase === 'done' && result ? (
            <div>
              <div className="cells" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(110px,1fr))' }}>
                {[
                  ['Extracted', result.extracted],
                  ['Stored', result.stored],
                  ['Drive surge', driving.length],
                  ['Gemini calls', result.gemini_calls],
                ].map(([k, v]) => (
                  <div className="cell" key={k}>
                    <span className="lbl">{k}</span>
                    <div className="v">{v}</div>
                  </div>
                ))}
              </div>
              <p style={{ fontSize: '12.5px', lineHeight: 1.6, margin: '20px 0' }}>
                {result.cache_hit
                  ? 'Served entirely from the on-disk cache — zero Gemini calls, and no duplicate rows. Stored records were replaced in place, keyed by source file. '
                  : ''}
                {driving.length > 0 ? (
                  <>
                    {driving.length === 1 ? 'One outbreak falls' : `${driving.length} outbreaks fall`} inside the district{' '}
                    <em>and</em> match a disease in the curated rule table:{' '}
                    {driving.map((r, i) => (
                      <span key={r.outbreak_id}>
                        {i > 0 ? '; ' : ''}
                        <strong>{r.outbreak_id}</strong>, {r.disease}
                        {r.sub_district ? `, ${r.sub_district} sub-district` : ''}, {r.cases} cases
                      </span>
                    ))}
                    . The remaining {result.stored - driving.length} are on record, flagged, and apply no surge — never guessed at.
                  </>
                ) : (
                  <>No stored outbreak both falls inside the district and matches the curated rule table, so no surge was applied.</>
                )}
              </p>
              <div className="box">
                <div className="box-hd">
                  <span className="h3">
                    {changed.length} {changed.length === 1 ? 'facility' : 'facilities'} changed band
                  </span>
                </div>
                {changed.length ? (
                  <table className="tbl">
                    <tbody>
                      {changed.slice(0, 6).map((c) => (
                        <tr key={c.id}>
                          <td className="nm">{c.name}</td>
                          <td className="grey">{shortMed(c.worst_medicine_id)}</td>
                          <td>
                            <Chip band={c.band_after} />
                          </td>
                          <td className="r num">
                            {fmtDays(c.days_of_cover_before)}D <span className="grey">→</span>{' '}
                            <strong style={c.band_after === 'critical' ? { color: 'var(--red-ink)' } : undefined}>
                              {fmtDays(c.days_of_cover_after)}D
                            </strong>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="box-bd-tight">
                    <p className="footnote">Days of cover were recomputed; no facility crossed a band line.</p>
                  </div>
                )}
                {changed.length > 6 ? (
                  <div className="box-bd-tight">
                    <span className="lbl grey">+ {changed.length - 6} more</span>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        <div className="sheet-ft">
          {phase === 'pick' ? (
            <>
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={() => onRun(picked)}>
                Ingest
              </button>
            </>
          ) : null}
          {phase === 'error' ? (
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
          ) : null}
          {phase === 'done' ? (
            <>
              {first ? (
                <button type="button" className="btn" onClick={() => onOpenFacility(first.id)}>
                  Open {first.name}
                </button>
              ) : null}
              <button type="button" className="btn btn-primary" onClick={onClose}>
                View district
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
