import { useEffect, useState } from 'react'
import { api, backendConfigured, backendUrl } from './api.js'
import App from './App.jsx'

// Gate in front of the dashboard. Render's free tier sleeps after ~15 min
// idle and the first request then takes up to ~50 s; rendering the console
// during that wait shows an empty, broken-looking page. So: probe /health
// until it answers, and only then mount the app.
//
// An awake backend answers a probe in well under GRACE_MS, in which case
// nothing but the page background is ever shown — no warmup flash.
const POLL_MS = 2_000 // pause between probes
const PROBE_TIMEOUT_MS = 6_000 // one probe gives up after this; the loop tries again
const CEILING_MS = 90_000 // total patience before the error screen
const GRACE_MS = 500 // a backend that answers within this never shows the warmup screen
const KEEPALIVE_MS = 10 * 60 * 1000 // silent /health ping so a long session never hits a cold start

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Test hook (read-only, like MapPanel's __leafletMap): lets a script confirm
// whether the warmup screen was shown and when the app became ready.
const trace = typeof window !== 'undefined' ? (window.__phcBoot = { phase: 'init', startedAt: null, readyAt: null, warmupShown: false, probes: 0, keepaliveMs: KEEPALIVE_MS, keepalivePings: 0 }) : {}

function Elapsed({ since }) {
  const [sec, setSec] = useState(() => Math.floor((Date.now() - since) / 1000))
  useEffect(() => {
    const id = setInterval(() => setSec(Math.floor((Date.now() - since) / 1000)), 500)
    return () => clearInterval(id)
  }, [since])
  // Quiet for the first few seconds; after that the count shows it is alive.
  return sec >= 5 ? <span className="sec">· {sec} s</span> : null
}

function Card({ error, children }) {
  return (
    <div className="boot" data-boot={error ? 'error' : 'warming'}>
      <div className={`boot-card ${error ? 'err' : ''}`} role="status" aria-live="polite">
        <h1>PHC Supply Resilience</h1>
        <div className="sub">Guntur district · Andhra Pradesh · district medical officer console</div>
        {children}
        <div className="boot-url">backend {backendConfigured ? backendUrl : 'not configured'}</div>
      </div>
    </div>
  )
}

export default function Boot() {
  const [phase, setPhase] = useState(backendConfigured ? 'probing' : 'unconfigured') // probing | warming | ready | failed | unconfigured
  const [startedAt, setStartedAt] = useState(() => Date.now())
  const [failure, setFailure] = useState(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    trace.phase = phase
  }, [phase])

  // Probe loop. Re-runs on Retry (attempt changes).
  useEffect(() => {
    if (!backendConfigured) return undefined
    let cancelled = false
    const t0 = Date.now()
    trace.startedAt = t0
    const grace = setTimeout(() => {
      if (cancelled) return
      trace.warmupShown = true
      setPhase((p) => (p === 'probing' ? 'warming' : p))
    }, GRACE_MS)
    ;(async () => {
      let lastError = null
      while (!cancelled) {
        const left = CEILING_MS - (Date.now() - t0)
        if (left <= 0) break
        trace.probes += 1
        try {
          await api.health({ timeoutMs: Math.min(PROBE_TIMEOUT_MS, left) })
          if (cancelled) return
          clearTimeout(grace)
          trace.readyAt = Date.now()
          setPhase('ready')
          return
        } catch (err) {
          lastError = err
        }
        await sleep(Math.max(0, Math.min(POLL_MS, CEILING_MS - (Date.now() - t0))))
      }
      if (cancelled) return
      clearTimeout(grace)
      setFailure(lastError)
      setPhase('failed')
    })()
    return () => {
      cancelled = true
      clearTimeout(grace)
    }
  }, [attempt])

  // Keepalive: one quiet /health every ten minutes while the app is open.
  // Browsers throttle or freeze timers in hidden tabs, so a tab that comes
  // back after a long absence also pings at once — the instance is then
  // already waking by the time the first click lands. Failures are ignored;
  // the next real request shows its own waiting state.
  useEffect(() => {
    if (phase !== 'ready') return undefined
    let lastPing = Date.now()
    const ping = () => {
      lastPing = Date.now()
      trace.keepalivePings += 1
      api.health({ timeoutMs: 60_000 }).catch(() => {})
    }
    const id = setInterval(ping, KEEPALIVE_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastPing > KEEPALIVE_MS / 2) ping()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [phase])

  const retry = () => {
    setFailure(null)
    setStartedAt(Date.now())
    setPhase('probing')
    setAttempt((a) => a + 1)
  }

  if (phase === 'ready') return <App />
  if (phase === 'probing') return <div className="boot" data-boot="probing" />
  if (phase === 'unconfigured') {
    return (
      <Card error>
        <div className="boot-status">Backend address not configured</div>
        <p className="boot-msg">
          Set <span className="mono">VITE_BACKEND_URL</span> (see frontend/.env.example) and rebuild. The frontend has no fallback address.
        </p>
      </Card>
    )
  }
  if (phase === 'failed') {
    return (
      <Card error>
        <div className="boot-status">The backend did not respond within {CEILING_MS / 1000} seconds</div>
        {failure?.message ? <p className="boot-err">Last attempt: {failure.message}</p> : null}
        <p className="boot-msg">If this is the free Render instance it may still be starting — retrying usually succeeds. Otherwise check that the backend is running.</p>
        <div style={{ marginTop: 14 }}>
          <button type="button" className="btn btn-primary" onClick={retry} data-action="boot-retry">
            Retry
          </button>
        </div>
      </Card>
    )
  }
  return (
    <Card>
      <div className="boot-status">
        <span className="spinner" />
        <span>Connecting to the backend</span>
        <Elapsed since={startedAt} />
      </div>
      <p className="boot-msg">The free-tier server sleeps when idle and may take up to a minute to wake. This page checks every few seconds and opens the console as soon as it answers.</p>
    </Card>
  )
}
