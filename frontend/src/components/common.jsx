import { BAND_LABEL } from '../format.js'
import { useElapsed } from '../hooks.js'

export function Band({ band }) {
  const b = band || 'unknown'
  return <span className={`band band-${b}`}>{BAND_LABEL[b] || b}</span>
}

export function Dot({ band }) {
  return <span className={`dot dot-${band || 'unknown'}`} aria-hidden="true" />
}

export function Arrow() {
  return <span className="arrow">→</span>
}

// A fetch in progress. After a few seconds it says what is probably
// happening, so a long wait (Render cold start, live Gemini call) reads as
// expected rather than broken. `hint` is the per-call explanation; `slow`
// is the line added once the wait is clearly long (default: cold start).
const COLD_START = 'The backend runs on a free Render instance and may be waking from sleep — that alone can take up to 50 seconds.'
export function Waiting({ label, hint, slow = COLD_START, inline = false }) {
  const sec = useElapsed(true)
  if (inline) {
    return (
      <span className="wait-inline" role="status">
        <span className="spinner" />
        {label}
        {sec >= 2 ? ` · ${sec} s` : ''}
      </span>
    )
  }
  const extra = slow && sec >= 5 ? slow : null
  return (
    <div className="note note-wait" role="status" aria-live="polite">
      <span className="spinner" />
      <div>
        <div>
          <b>{label}</b>
          {sec >= 2 ? <span className="muted"> · {sec} s</span> : null}
        </div>
        {hint ? <div className="small muted">{hint}</div> : null}
        {extra ? <div className="small muted">{extra}</div> : null}
      </div>
    </div>
  )
}

export function ErrorNote({ title = 'Request failed', error, onRetry, children }) {
  const message = typeof error === 'string' ? error : error?.message
  const detail = error && typeof error === 'object' && error.detail && typeof error.detail === 'object' ? error.detail : null
  return (
    <div className="note note-error" role="alert">
      <h4>{title}</h4>
      {message ? <div>{message}</div> : null}
      {detail ? (
        <div className="small mono" style={{ marginTop: 4 }}>
          {Object.entries(detail)
            .filter(([k]) => k !== 'error')
            .map(([k, v]) => (
              <div key={k}>
                {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </div>
            ))}
        </div>
      ) : null}
      {children}
      {onRetry ? (
        <div style={{ marginTop: 6 }}>
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : null}
    </div>
  )
}
