import { fmtQty } from '../format.js'
import { Waiting, ErrorNote } from './common.jsx'

function OutbreakRow({ o, dim }) {
  return (
    <tr className={o.in_scope ? 'row-inscope' : dim ? 'row-dim' : ''}>
      <td>
        {o.disease}
        {o.in_scope ? (
          <>
            {' '}
            <span className={`tag ${o.drives_surge ? 'tag-surge' : ''}`}>{o.drives_surge ? 'drives surge' : 'in scope'}</span>
          </>
        ) : null}
        {!o.in_scope && o.review_flags?.includes('unknown_disease') ? (
          <>
            {' '}
            <span className="tag" title="Disease is not in the clinical rule table; recorded, no surge applied">no rule</span>
          </>
        ) : null}
      </td>
      <td>
        {o.district}
        {o.state && o.state !== 'Andhra Pradesh' ? <span className="muted small"> · {o.state}</span> : null}
      </td>
      <td>{o.sub_district || <span className="muted">—</span>}</td>
      <td className="num cases">{fmtQty(o.cases)}</td>
      <td className="num">{o.deaths ?? 0}</td>
      <td className="nowrap">
        wk {o.week} · {o.year}
      </td>
      <td className="small">{o.status || ''}</td>
    </tr>
  )
}

function Table({ rows, dim }) {
  return (
    <div className="tbl-scroll">
      <table className="tbl">
        <thead>
          <tr>
            <th>Disease</th>
            <th>District</th>
            <th>Sub-district</th>
            <th className="num">Cases</th>
            <th className="num">Deaths</th>
            <th>Week</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <OutbreakRow key={o.outbreak_id} o={o} dim={dim} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function OutbreaksPanel({ outbreaks, loading, error, onRetry, ingest }) {
  const all = outbreaks?.outbreaks || []
  const inScope = all.filter((o) => o.in_scope)
  const outScope = all.filter((o) => !o.in_scope)
  const sourceFiles = [...new Set(all.map((o) => o.source_file).filter(Boolean))]

  return (
    <section className="panel" aria-labelledby="outbreaks-h">
      <div className="panel-head">
        <h2 id="outbreaks-h">Active outbreaks</h2>
        {outbreaks ? (
          <span className="meta">
            {all.length === 0
              ? 'none ingested'
              : `${all.length} extracted · ${outbreaks.in_scope} in scope for Guntur · ${outbreaks.driving_surge} driving surge · ${outbreaks.unknown_disease} not in rule table`}
          </span>
        ) : null}
        {sourceFiles.length ? <span className="right mono">{sourceFiles.join(', ')}</span> : null}
      </div>
      <div className={`panel-body ${all.length ? 'tight' : ''}`}>
        {ingest?.loading ? (
          <div style={{ padding: all.length ? '8px 12px 0' : 0 }}>
            <Waiting
              label={`Reading ${ingest.filename} with Gemini…`}
              hint="A real MoHFW IDSP weekly report. First extraction of a PDF can take up to a minute; repeat runs are served from the on-disk cache with zero model calls."
            />
          </div>
        ) : null}
        {ingest?.error ? (
          <div style={{ padding: all.length ? '8px 12px 0' : 0 }}>
            <ErrorNote title="Ingest failed" error={ingest.error} onRetry={ingest.retry} />
          </div>
        ) : null}
        {ingest?.result ? (
          <div className="note note-ok" style={{ margin: all.length ? '8px 12px' : '0 0 8px' }}>
            <h4>
              {ingest.result.source_file}: {ingest.result.extracted} outbreaks extracted, {ingest.result.stored} stored
              {ingest.result.gemini_calls === 0 ? ' — served from cache, 0 Gemini calls' : ` — ${ingest.result.gemini_calls} Gemini call${ingest.result.gemini_calls === 1 ? '' : 's'}`}
            </h4>
            <div className="small">
              {ingest.result.out_of_scope} outside Guntur · {ingest.result.unknown_disease} diseases not in the rule table (recorded, no surge applied)
              {ingest.result.rejected?.length ? ` · ${ingest.result.rejected.length} rows rejected` : ''}
            </div>
            <div className="small">
              Facilities changed band: <b>{ingest.result.facilities_changed_band?.length ?? 0}</b> · critical{' '}
              {ingest.result.band_counts_before.critical} → <b>{ingest.result.band_counts_after.critical}</b>, warning{' '}
              {ingest.result.band_counts_before.warning} → <b>{ingest.result.band_counts_after.warning}</b>, safe{' '}
              {ingest.result.band_counts_before.safe} → <b>{ingest.result.band_counts_after.safe}</b>. Stock did not change — consumption
              rates did.
            </div>
          </div>
        ) : null}

        {loading && !outbreaks ? <Waiting label="Loading outbreak records…" /> : null}
        {error && !outbreaks ? <ErrorNote title="Could not load outbreaks" error={error} onRetry={onRetry} /> : null}

        {outbreaks && all.length === 0 && !ingest?.loading ? (
          <p className="muted" style={{ margin: 0 }}>
            No outbreak report has been ingested. Use <b>Ingest IDSP report</b> above to read the MoHFW IDSP weekly report for week 45, 2025.
          </p>
        ) : null}

        {inScope.length ? <Table rows={inScope} /> : null}
        {all.length && !inScope.length ? (
          <p className="muted" style={{ margin: '8px 12px' }}>
            None of the {all.length} extracted outbreaks is in Guntur district.
          </p>
        ) : null}
        {outScope.length ? (
          <details className="fold" style={{ margin: '6px 12px 8px' }}>
            <summary>
              Show the other {outScope.length} outbreaks extracted from the same report (outside Guntur — recorded, no surge applied)
            </summary>
            <div className="fold-body">
              <Table rows={outScope} dim />
            </div>
          </details>
        ) : null}
      </div>
    </section>
  )
}
