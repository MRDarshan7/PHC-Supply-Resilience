import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, backendConfigured, backendUrl } from './api.js'
import { needsDonors, shortMed, BAND_SEVERITY } from './format.js'
import Header from './components/Header.jsx'
import MapPanel from './components/MapPanel.jsx'
import OutbreakStrip, { OutbreakDrawer } from './components/OutbreakStrip.jsx'
import FacilityList from './components/FacilityList.jsx'
import FacilityDetail from './components/FacilityDetail.jsx'

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
  const [listFilter, setListFilter] = useState('attention')
  const [listCollapsed, setListCollapsed] = useState(false)
  const [othersOpen, setOthersOpen] = useState(false)
  const recsRef = useRef(recs)
  const selectedRef = useRef(selectedId)
  useEffect(() => {
    recsRef.current = recs
    selectedRef.current = selectedId
  }, [recs, selectedId])

  // ---------------------------------------------------------------- loads
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

  // One detail fetch up front: it carries the thresholds the legend cites.
  const primedRef = useRef(false)
  const loadFacilities = useCallback(async (mode = 'initial') => {
    setFacState((s) => ({ ...s, loading: mode === 'initial', refreshing: mode !== 'initial', error: null }))
    try {
      const data = await api.facilities()
      setFacilities(data)
      setFacState({ loading: false, refreshing: false, error: null })
      if (data?.length && !primedRef.current) {
        primedRef.current = true
        loadDetail(data[0].id)
      }
      return data
    } catch (err) {
      setFacState({ loading: false, refreshing: false, error: err })
      return null
    }
  }, [loadDetail])

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

  useEffect(() => {
    if (!backendConfigured) return
    loadFacilities('initial')
    loadOutbreaks()
  }, [loadFacilities, loadOutbreaks])

  // ------------------------------------------------------------ selection
  const selectFacility = useCallback(
    (id) => {
      setSelectedId(id)
      loadDetail(id)
    },
    [loadDetail],
  )
  const closeFacility = useCallback(() => setSelectedId(null), [])

  // Retry from the map or the list: everything that failed, not just one call.
  const retryLoads = useCallback(() => {
    loadFacilities('initial')
    loadOutbreaks()
  }, [loadFacilities, loadOutbreaks])

  // --------------------------------------------------------------- ingest
  const runIngest = useCallback(async () => {
    setIngest({ loading: true, error: null, result: null, filename: DEMO_PDF })
    setOthersOpen(false)
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
        if (!still.length) summary += ` ${name} is now safe on every medicine.`
        else summary += ` ${name} is still below the line on ${still.join(', ')}${skipped.length ? ' (no eligible donor)' : ''}.`
        setApproveAll({ running: false, facilityId: fid, step: '', error: null, summary })
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
  const selected = selectedId ? (facilities || []).find((f) => f.id === selectedId) : null

  // ---------------------------------------------------------------- render
  return (
    <div className="app">
      <Header
        facilityCount={facilities?.length}
        ingest={ingest}
        onIngest={runIngest}
        disabled={!backendConfigured || !facilities}
        configError={backendConfigured ? null : 'VITE_BACKEND_URL is not set — see frontend/.env.example'}
      />

      <div className="main">
        <div className="left">
          <MapPanel
            facilities={facilities}
            selectedId={selectedId}
            onSelect={selectFacility}
            thresholds={thresholds}
            refreshing={facState.refreshing}
            loadError={facState.error}
            onRetry={retryLoads}
            backendUrl={backendConfigured ? backendUrl : '(not configured)'}
          >
            {othersOpen ? <OutbreakDrawer outbreaks={outbreaks} onClose={() => setOthersOpen(false)} /> : null}
          </MapPanel>
          <OutbreakStrip
            outbreaks={outbreaks}
            loading={obState.loading}
            error={obState.error}
            onRetry={loadOutbreaks}
            ingest={ingestView}
            othersOpen={othersOpen}
            onToggleOthers={() => setOthersOpen((v) => !v)}
          />
        </div>

        <aside className="rail" aria-label="Facilities and selected facility">
          <FacilityList
            facilities={facilities}
            loading={facState.loading}
            error={facState.error}
            onRetry={retryLoads}
            selectedId={selectedId}
            onSelect={selectFacility}
            filter={listFilter}
            onFilter={setListFilter}
            collapsed={listCollapsed}
            onToggleCollapsed={() => setListCollapsed((v) => !v)}
          />
          <section className="rail-detail" aria-label="Selected facility">
            {selected ? (
              <FacilityDetail
                key={selected.id}
                facility={selected}
                detail={details[selected.id]}
                recs={recs}
                medNames={medNames}
                approveAll={approveAll}
                onFindDonors={(mid) => findDonors(selected.id, mid)}
                onApprove={onApprove}
                onApproveAll={approveAllRecommended}
                onRetryDetail={() => loadDetail(selected.id)}
                onClose={closeFacility}
              />
            ) : facilities ? (
              <div className="detail-empty">
                <h3>Select a facility</h3>
                Click a marker on the map or a row in the list to see its stock by medicine, find a donor that can safely spare stock, read the memo and approve
                the transfer — the map stays in view the whole time.
                <ol>
                  <li>
                    <b>Ingest IDSP report</b> reads a real MoHFW outbreak PDF; burn rates recompute and the map recolours.
                  </li>
                  <li>
                    Open a <b>critical</b> facility: one medicine can be critical while another is safe.
                  </li>
                  <li>
                    <b>Find donors</b> runs the safety-checked donor engine and writes a bilingual memo; <b>Approve</b> moves the stock.
                  </li>
                </ol>
              </div>
            ) : null}
          </section>
        </aside>
      </div>

      <footer className="foot">
        <span className="notice">
          <b>Decision-support only.</b> Recommendations require approval by the authorised District Medical Officer. The system executes nothing autonomously.
        </span>
        <span className="src">
          Days of cover = stock ÷ daily consumption (HMIS caseload × rule table + IDSP surge by caseload share)
          {thresholds ? ` · donor safety floor ${thresholds.donor_safety_floor_days} d` : ''} · data.gov.in facility directory 2016 · MoHFW HMIS 2019-20 · MoHFW IDSP
          weekly reports · NLEM 2022 · ledger simulated from real caseloads, fixed seed · distances straight-line · transfers shown as projected
          {backendConfigured ? ` · backend ${backendUrl}` : ''}
        </span>
      </footer>
    </div>
  )
}
