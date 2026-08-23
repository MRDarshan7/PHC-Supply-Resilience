import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, backendConfigured, backendUrl } from './api.js'
import { needsDonors, shortMed, BAND_SEVERITY } from './format.js'
import { Waiting, ErrorNote } from './components/common.jsx'
import MapView from './components/MapView.jsx'
import OutbreaksPanel from './components/OutbreaksPanel.jsx'
import FacilityList from './components/FacilityList.jsx'

// The one report the demo is built around: MoHFW IDSP weekly outbreak
// report, week 45 of 2025 (3–9 November). It lives in data/idsp_pdfs/ on the
// backend; the frontend only names it.
const DEMO_PDF = 'idsp_2025_w45.pdf'
const APPROVED_BY = 'District Medical Officer'

const recKey = (fid, mid) => `${fid}/${mid}`

export default function App() {
  const [facilities, setFacilities] = useState(null)
  const [facState, setFacState] = useState({ loading: true, refreshing: false, error: null })
  const [outbreaks, setOutbreaks] = useState(null)
  const [obState, setObState] = useState({ loading: true, error: null })
  const [selectedId, setSelectedId] = useState(null)
  const [details, setDetails] = useState({})
  const [recs, setRecs] = useState({})
  const [ingest, setIngest] = useState({ loading: false, error: null, result: null, filename: DEMO_PDF })
  const [approveAll, setApproveAll] = useState({ running: false, facilityId: null, step: '', error: null, summary: null })
  const scrollToRef = useRef(null)
  const recsRef = useRef(recs)
  const selectedRef = useRef(selectedId)
  useEffect(() => {
    recsRef.current = recs
    selectedRef.current = selectedId
  }, [recs, selectedId])

  // ---------------------------------------------------------------- loads
  const loadFacilities = useCallback(async (mode = 'initial') => {
    setFacState((s) => ({ ...s, loading: mode === 'initial', refreshing: mode !== 'initial', error: null }))
    try {
      const data = await api.facilities()
      setFacilities(data)
      setFacState({ loading: false, refreshing: false, error: null })
      return data
    } catch (err) {
      setFacState({ loading: false, refreshing: false, error: err })
      return null
    }
  }, [])

  const loadOutbreaks = useCallback(async () => {
    setObState((s) => ({ ...s, loading: true, error: null }))
    try {
      const data = await api.outbreaks()
      setOutbreaks(data)
      setObState({ loading: false, error: null })
      return data
    } catch (err) {
      setObState({ loading: false, error: err })
      return null
    }
  }, [])

  const loadDetail = useCallback(async (id) => {
    setDetails((d) => ({ ...d, [id]: { ...(d[id] || {}), loading: true, error: null } }))
    try {
      const data = await api.facility(id)
      setDetails((d) => ({ ...d, [id]: { loading: false, error: null, data } }))
      return data
    } catch (err) {
      setDetails((d) => ({ ...d, [id]: { ...(d[id] || {}), loading: false, error: err } }))
      return null
    }
  }, [])

  useEffect(() => {
    if (!backendConfigured) return
    loadFacilities('initial')
    loadOutbreaks()
  }, [loadFacilities, loadOutbreaks])

  // One detail fetch up front: it carries the thresholds the footer legend cites.
  const primedRef = useRef(false)
  useEffect(() => {
    if (facilities?.length && !primedRef.current) {
      primedRef.current = true
      loadDetail(facilities[0].id)
    }
  }, [facilities, loadDetail])

  // ------------------------------------------------------------ selection
  const toggleFacility = useCallback(
    (id) => {
      setSelectedId((prev) => (prev === id ? null : id))
      if (selectedId !== id) loadDetail(id)
    },
    [selectedId, loadDetail],
  )

  const selectFromMap = useCallback(
    (id) => {
      scrollToRef.current = id
      setSelectedId(id)
      loadDetail(id)
    },
    [loadDetail],
  )

  // Scroll the selected row into view when a map click selected it, or when
  // a bulk approval has just re-sorted it down the worst-first list.
  const [scrollTick, setScrollTick] = useState(0)
  useEffect(() => {
    if (selectedId && scrollToRef.current === selectedId) {
      scrollToRef.current = null
      const el = document.getElementById(`fac-${selectedId}`)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [selectedId, scrollTick])

  // --------------------------------------------------------------- ingest
  const runIngest = useCallback(async () => {
    setIngest({ loading: true, error: null, result: null, filename: DEMO_PDF })
    try {
      const result = await api.ingest(DEMO_PDF)
      setIngest({ loading: false, error: null, result, filename: DEMO_PDF })
      // The map recolours from this refresh — no page reload.
      await Promise.all([loadFacilities('refresh'), loadOutbreaks()])
      if (selectedRef.current) loadDetail(selectedRef.current)
    } catch (err) {
      setIngest({ loading: false, error: err, result: null, filename: DEMO_PDF })
    }
  }, [loadFacilities, loadOutbreaks, loadDetail])

  // ---------------------------------------------------------- recommend
  const findDonors = useCallback(async (fid, mid) => {
    const key = recKey(fid, mid)
    setRecs((r) => ({
      ...r,
      [key]: { loading: true, error: null, data: null, approvals: [], approving: false, approveError: null, domId: `rec-${key.replace(/[^a-z0-9]/gi, '-')}` },
    }))
    try {
      const data = await api.recommend(fid, mid)
      setRecs((r) => ({ ...r, [key]: { ...r[key], loading: false, data } }))
      return data
    } catch (err) {
      setRecs((r) => ({ ...r, [key]: { ...r[key], loading: false, error: err } }))
      return null
    }
  }, [])

  // ------------------------------------------------------------- approve
  const approveRec = useCallback(
    async (fid, mid, dataArg) => {
      const key = recKey(fid, mid)
      const data = dataArg || recsRef.current[key]?.data
      const transfers = (data?.transfers || []).filter((t) => t.status === 'pending')
      if (!transfers.length) return { ok: false, approvals: [] }
      setRecs((r) => ({ ...r, [key]: { ...r[key], approving: true, approveError: null } }))
      const approvals = []
      try {
        for (const t of transfers) {
          const res = await api.approve(t.id, APPROVED_BY)
          approvals.push(res)
          setRecs((r) => ({ ...r, [key]: { ...r[key], approvals: [...approvals] } }))
        }
        setRecs((r) => ({ ...r, [key]: { ...r[key], approving: false } }))
      } catch (err) {
        setRecs((r) => ({ ...r, [key]: { ...r[key], approving: false, approveError: err, approvals: [...approvals] } }))
        await Promise.all([loadFacilities('refresh'), loadDetail(fid)])
        throw err
      }
      await Promise.all([loadFacilities('refresh'), loadDetail(fid)])
      return { ok: true, approvals }
    },
    [loadFacilities, loadDetail],
  )

  const onApprove = useCallback(
    (fid, mid) => {
      approveRec(fid, mid).catch(() => {
        /* shown inline on the recommendation */
      })
    },
    [approveRec],
  )

  const approveAllRecommended = useCallback(
    async (fid) => {
      setApproveAll({ running: true, facilityId: fid, step: 'Checking medicines…', error: null, summary: null })
      try {
        let detail = await loadDetail(fid)
        if (!detail) throw new Error('Could not load the facility detail')
        const targets = detail.medicines
          .filter((m) => needsDonors(m.band))
          .sort((a, b) => BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] || (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9))
        let nApproved = 0
        const skipped = []
        for (const m of targets) {
          const short = shortMed(m.medicine_id, m.name)
          setApproveAll((s) => ({ ...s, step: `Finding donors for ${short}…` }))
          const data = await findDonors(fid, m.medicine_id)
          if (!data) throw new Error(`The recommendation for ${m.name} failed`)
          const pending = (data.transfers || []).filter((t) => t.status === 'pending')
          if (!pending.length) {
            skipped.push(m.name)
            continue
          }
          setApproveAll((s) => ({ ...s, step: `Approving ${short}…` }))
          const r = await approveRec(fid, m.medicine_id, data)
          nApproved += r.approvals.length
        }
        detail = await loadDetail(fid)
        const still = detail ? detail.medicines.filter((m) => needsDonors(m.band)).map((m) => m.name) : []
        const name = detail?.name || 'The facility'
        let summary = `${nApproved} transfer${nApproved === 1 ? '' : 's'} approved.`
        if (!still.length) summary += ` ${name} is now safe on every medicine — its map marker has turned green.`
        else summary += ` ${name} is still below the line on ${still.join(', ')}${skipped.length ? ' (no eligible donor)' : ''}.`
        setApproveAll({ running: false, facilityId: fid, step: '', error: null, summary })
        if (selectedRef.current === fid) {
          scrollToRef.current = fid
          setScrollTick((t) => t + 1)
        }
      } catch (err) {
        setApproveAll((s) => ({ ...s, running: false, step: '', error: err }))
      }
    },
    [loadDetail, findDonors, approveRec],
  )

  // --------------------------------------------------------------- derived
  const medNames = useMemo(() => {
    const out = {}
    for (const f of facilities || []) {
      for (const [mid, m] of Object.entries(f.medicines || {})) if (!out[mid]) out[mid] = m.name
      if (Object.keys(out).length >= 5) break
    }
    return out
  }, [facilities])

  const thresholds = useMemo(() => {
    for (const d of Object.values(details)) if (d?.data?.thresholds) return d.data.thresholds
    return null
  }, [details])

  const ingestView = useMemo(() => ({ ...ingest, retry: runIngest }), [ingest, runIngest])

  // ---------------------------------------------------------------- render
  return (
    <div className="page">
      <header className="hdr">
        <div className="hdr-inner">
          <h1>
            PHC Supply Resilience <span className="hdr-dist">— Guntur District, Andhra Pradesh</span>
          </h1>
          <div className="hdr-actions">
            <button type="button" className="btn btn-primary" onClick={runIngest} disabled={!backendConfigured || ingest.loading || !facilities} aria-busy={ingest.loading ? 'true' : 'false'}>
              {ingest.loading ? (
                <>
                  <span className="spinner light" /> Reading IDSP report…
                </>
              ) : (
                'Ingest IDSP report'
              )}
            </button>
            <span className="hdr-file">
              {DEMO_PDF} · MoHFW IDSP weekly outbreak report, week 45 of 2025
            </span>
          </div>
          <span className="badge-sim">Inventory data simulated · facility and outbreak data are real</span>
        </div>
      </header>

      {!backendConfigured ? (
        <ErrorNote title="Backend URL not configured">
          Set <span className="mono">VITE_BACKEND_URL</span> (see frontend/.env.example) and rebuild. The frontend has no fallback address.
        </ErrorNote>
      ) : null}

      <section className="panel" aria-label="Map">
        {facilities ? (
          <MapView facilities={facilities} selectedId={selectedId} onSelect={selectFromMap} />
        ) : (
          <div className="map-loading">
            {facState.error ? (
              <ErrorNote title="Could not load facilities" error={facState.error} onRetry={() => loadFacilities('initial')} />
            ) : backendConfigured ? (
              <Waiting label="Connecting to the backend and loading facilities…" hint={`Backend: ${backendUrl}`} />
            ) : null}
          </div>
        )}
      </section>

      <OutbreaksPanel outbreaks={outbreaks} loading={obState.loading} error={obState.error} onRetry={loadOutbreaks} ingest={ingestView} />

      <FacilityList
        facilities={facilities}
        loading={facState.loading}
        refreshing={facState.refreshing}
        error={facState.error}
        onRetry={() => loadFacilities('initial')}
        selectedId={selectedId}
        onToggle={toggleFacility}
        details={details}
        recs={recs}
        medNames={medNames}
        approveAll={approveAll}
        onFindDonors={findDonors}
        onApprove={onApprove}
        onApproveAll={approveAllRecommended}
        onRetryDetail={loadDetail}
      />

      <footer className="foot">
        <p>
          <b>Decision-support only.</b> Recommendations require approval by the authorised District Medical Officer. The system executes nothing
          autonomously.
        </p>
        <p>
          Days of cover = stock ÷ daily consumption. Consumption = HMIS caseload × clinical rule table, plus any IDSP outbreak surge allocated by
          caseload share — an outbreak changes how fast stock is used, not how much there is.
          {thresholds
            ? ` Critical below ${thresholds.critical_days} days · warning ${thresholds.critical_days}–${thresholds.warning_days} · safe above ${thresholds.warning_days} · donor safety floor ${thresholds.donor_safety_floor_days} days.`
            : ''}
        </p>
        <p>
          Sources: facility directory (data.gov.in, 2016) · caseloads (MoHFW HMIS 2019-20, Andhra Pradesh) · outbreaks (MoHFW IDSP weekly reports) ·
          medicines (NLEM 2022). The inventory ledger is simulated from real caseloads with a fixed seed. Distances are straight-line. Transfers are
          shown as projected, not instant.
          {backendConfigured ? (
            <>
              {' '}
              Backend: <span className="mono">{backendUrl}</span>
            </>
          ) : null}
        </p>
      </footer>
    </div>
  )
}
