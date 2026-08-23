import { useElapsed } from '../hooks.js'

function IngestButton({ ingest, onIngest, disabled }) {
  const sec = useElapsed(ingest.loading)
  return (
    <button type="button" className="btn btn-light" onClick={onIngest} disabled={disabled || ingest.loading} aria-busy={ingest.loading ? 'true' : 'false'} data-action="ingest">
      {ingest.loading ? (
        <>
          <span className="spinner" /> Reading IDSP report{sec >= 2 ? ` · ${sec} s` : '…'}
        </>
      ) : ingest.result ? (
        'Re-ingest IDSP report'
      ) : (
        'Ingest IDSP report'
      )}
    </button>
  )
}

export default function Header({ facilityCount, ingest, onIngest, disabled, configError }) {
  return (
    <header className="hdr">
      <div className="hdr-title">
        <h1>PHC Supply Resilience</h1>
        <span className="sub">
          Guntur district · Andhra Pradesh{facilityCount ? ` · ${facilityCount} primary health centres` : ''} · district medical officer console
        </span>
      </div>
      <div className="hdr-ingest">
        <IngestButton ingest={ingest} onIngest={onIngest} disabled={disabled} />
        <div className="file">
          <span className="name">{ingest.filename}</span>
          {ingest.result ? (
            <span className="done">
              ✓ {ingest.result.extracted} outbreaks extracted{ingest.result.gemini_calls === 0 ? ' · cached' : ''} · {ingest.result.extracted - ingest.result.out_of_scope} in Guntur
            </span>
          ) : (
            <span className="desc">MoHFW IDSP weekly outbreak report · week 45 · 3–9 November 2025</span>
          )}
        </div>
      </div>
      <div className="hdr-right">
        {configError ? <span className="hdr-err">{configError}</span> : null}
        <span className="badge-sim">
          <b>Inventory simulated</b> · facility and outbreak data real
        </span>
      </div>
    </header>
  )
}
