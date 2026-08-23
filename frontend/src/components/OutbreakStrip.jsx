import { fmtQty } from '../format.js'
import { Arrow, Waiting, ErrorNote } from './common.jsx'

// One prominent card per in-scope outbreak: the 457-case Guntur row is the
// thing the whole demo turns on, so it gets the big number.
function InScopeCard({ o }) {
  return (
    <div className="ob-card" data-outbreak={o.outbreak_id}>
      <div className="cases">
        {fmtQty(o.cases)}
        <small>cases</small>
      </div>
      <div className="what">
        <div className="disease">
          {o.disease} {o.drives_surge ? <span className="tag tag-surge">drives surge</span> : <span className="tag">in scope · no rule</span>}
        </div>
        <div className="where">
          {o.district}
          {o.sub_district ? ` · ${o.sub_district}` : ''} · {o.state || 'Andhra Pradesh'} · <span className="mono">{o.outbreak_id}</span>
        </div>
      </div>
      <div className="facts">
        <span>
          deaths <b>{o.deaths ?? 0}</b>
        </span>
        <span>
          week <b>{o.week}</b> · {o.year}
        </span>
        {o.status ? <span>{o.status}</span> : null}
      </div>
    </div>
  )
}

function IngestResult({ r }) {
  const b = r.band_counts_before
  const a = r.band_counts_after
  return (
    <div className="ob-result" data-ingest-result="">
      <div className="counts">
        <span>
          critical <b className="c-critical">{b.critical}</b>
          <Arrow />
          <b className="c-critical">{a.critical}</b>
        </span>
        <span>
          warning <b className="c-warning">{b.warning}</b>
          <Arrow />
          <b className="c-warning">{a.warning}</b>
        </span>
        <span>
          safe <b className="c-safe">{b.safe}</b>
          <Arrow />
          <b className="c-safe">{a.safe}</b>
        </span>
      </div>
      <div className="expl">
        {r.pairs_changed_band} facility-medicine pairs changed band · stock unchanged, consumption rose
      </div>
    </div>
  )
}

export default function OutbreakStrip({ outbreaks, loading, error, onRetry, ingest, othersOpen, onToggleOthers }) {
  const all = outbreaks?.outbreaks || []
  const inScope = all.filter((o) => o.in_scope)
  const others = all.length - inScope.length

  return (
    <section className="ob-strip" aria-labelledby="outbreaks-h">
      <div className="ob-head">
        <h2 id="outbreaks-h">Active outbreaks</h2>
        {outbreaks && all.length ? (
          <span className="meta">
            {all.length} extracted · <b>{outbreaks.in_scope}</b> in scope for Guntur · {outbreaks.driving_surge} drives surge · {outbreaks.unknown_disease} not in rule table
          </span>
        ) : null}
        {others > 0 ? (
          <span className="ob-more">
            {others} outside Guntur · recorded, no surge applied ·{' '}
            <button type="button" className="btn-link" onClick={onToggleOthers} aria-expanded={othersOpen}>
              {othersOpen ? 'hide' : 'show'}
            </button>
          </span>
        ) : null}
      </div>

      {ingest?.loading ? (
        <Waiting
          label={`Reading ${ingest.filename} with Gemini…`}
          hint="A real MoHFW IDSP weekly report: Gemini extracts every outbreak row from both tables, then burn rates are recomputed."
          slow="Still working — the first extraction of a PDF is a live Gemini call and can take up to a minute; repeats come from the on-disk cache."
        />
      ) : null}
      {ingest?.error ? <ErrorNote title="Ingest failed" error={ingest.error} onRetry={ingest.retry} /> : null}
      {loading && !outbreaks ? <Waiting label="Loading outbreak records…" /> : null}
      {error && !outbreaks ? <ErrorNote title="Could not load outbreaks" error={error} onRetry={onRetry} /> : null}

      {outbreaks && all.length === 0 && !ingest?.loading && !ingest?.error ? (
        <div className="ob-empty">
          No outbreak records yet. <b>Ingest IDSP report</b> reads the MoHFW weekly outbreak report for week 45 of 2025 — a real government PDF — and
          recomputes every facility's burn rate from what it finds.
        </div>
      ) : null}

      {inScope.length ? (
        <div className="ob-row">
          {inScope.map((o) => (
            <InScopeCard key={o.outbreak_id} o={o} />
          ))}
          {ingest?.result ? <IngestResult r={ingest.result} /> : null}
        </div>
      ) : null}
      {all.length && !inScope.length ? <div className="ob-empty">None of the {all.length} extracted outbreaks is in Guntur district.</div> : null}
    </section>
  )
}

// Floats over the map so the layout does not grow.
export function OutbreakDrawer({ outbreaks, onClose }) {
  const rows = (outbreaks?.outbreaks || []).filter((o) => !o.in_scope)
  return (
    <div className="map-drawer" role="dialog" aria-label="Outbreaks outside Guntur">
      <div className="drawer-head">
        <h3>
          {rows.length} outbreaks outside Guntur <span className="muted small">— recorded for completeness, no surge applied to any facility</span>
        </h3>
        <button type="button" className="btn-icon" style={{ marginLeft: 'auto' }} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      <div className="drawer-body">
        <table className="tbl">
          <thead>
            <tr>
              <th>Disease</th>
              <th>State</th>
              <th>District</th>
              <th>Sub-district</th>
              <th className="num">Cases</th>
              <th className="num">Deaths</th>
              <th>Week</th>
              <th>Status</th>
              <th>Rule table</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.outbreak_id} className="row-dim">
                <td>{o.disease}</td>
                <td>{o.state}</td>
                <td>{o.district}</td>
                <td>{o.sub_district || '—'}</td>
                <td className="num">{fmtQty(o.cases)}</td>
                <td className="num">{o.deaths ?? 0}</td>
                <td className="nowrap">
                  wk {o.week} · {o.year}
                </td>
                <td>{o.status || ''}</td>
                <td className="xs">{o.disease_key ? `matched · ${o.disease_key}` : 'not in rule table'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
