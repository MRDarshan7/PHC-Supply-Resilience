// Shared primitives of the mock's component vocabulary: swatches, chips,
// day-count figures, gauges, section heads, step sequences. Presentation
// only — every figure they show is passed in from an API response.
import { useEffect, useState } from 'react'
import { fmtDays } from './format.js'

export function Swatch({ band }) {
  return <span className={`sw sw-${band}`} aria-hidden="true" />
}

export function Chip({ band }) {
  return (
    <span className={`chip chip-${band}`} data-band={band}>
      <span className={`sw sw-${band}`} />
      {band}
    </span>
  )
}

// Days-of-cover figure. Undefined cover prints an em dash with no unit —
// "— D" would imply a measurement that does not exist.
export function Dc({ days, band, size }) {
  if (days == null || !Number.isFinite(days)) {
    return (
      <span className="dc grey" style={size ? { fontSize: size } : undefined}>
        —
      </span>
    )
  }
  return (
    <span className={`dc ${band === 'critical' ? 'dc-critical' : ''}`} style={size ? { fontSize: size } : undefined}>
      {fmtDays(days)}
      <span className="u">D</span>
    </span>
  )
}

// Threshold gauge: fill vs the two band lines. Ticks come from the API's
// thresholds object; nothing here knows a default.
export function Gauge({ days, band, thresholds, max }) {
  if (!thresholds) return null
  const m = max || Math.max(36, Math.ceil(((days || 0) + 4) / 12) * 12)
  const pct = days == null ? 0 : Math.min(100, (days / m) * 100)
  return (
    <div>
      <div className={`gauge is-${band}`}>
        <i style={{ width: `${pct}%` }} />
        <u style={{ left: `${(thresholds.critical_days / m) * 100}%` }} />
        <u style={{ left: `${(thresholds.warning_days / m) * 100}%` }} />
      </div>
      <div className="gauge-sc">
        <span>0</span>
        <span>{thresholds.critical_days}D</span>
        <span>{thresholds.warning_days}D</span>
        <span>{m}D</span>
      </div>
    </div>
  )
}

export function SecHead({ n, title, aside }) {
  return (
    <div className="sechead">
      <span className="n">{n}</span>
      <h2 className="h2">{title}</h2>
      {aside ? <span className="aside">{aside}</span> : null}
    </div>
  )
}

export function BackLink({ onClick, children }) {
  return (
    <button type="button" className="backlink" onClick={onClick}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
        <path d="m15 18-6-6 6-6" />
      </svg>
      {children}
    </button>
  )
}

// Step sequence. states: done | on | wait. `rs` is the right-hand result slug.
export function Steps({ items }) {
  return (
    <div className="steps">
      {items.map((s, i) => (
        <div key={i} className={`step ${s.state}`}>
          <span className="ic" />
          <span>{s.label}</span>
          <span className="rs">{s.rs || ''}</span>
        </div>
      ))}
    </div>
  )
}

// Seconds since mount — drives "elapsed" copy on long Gemini calls.
export function Elapsed({ active = true }) {
  const [sec, setSec] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    const t0 = Date.now()
    const id = setInterval(() => setSec(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(id)
  }, [active])
  return <span>{sec} s</span>
}

export function ErrBox({ title, error, onRetry, retryLabel = 'Retry' }) {
  return (
    <div className="errbox" role="alert">
      <div className="t">{title}</div>
      <p>{error?.message || String(error || 'Unknown error')}</p>
      {onRetry ? (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  )
}

export function ArrowRight() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  )
}

export function CheckMark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}
