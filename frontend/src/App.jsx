// Narrative shell: four numbered routes — 01 District, 02 Facility,
// 03 Transfer, 04 Approved — with a top tab bar and a side nav kept in
// sync. The sequence is part of the explanation, so later routes unlock
// only when earned: Transfer after a report has been ingested, Approved
// after a transfer has been approved. Disabled routes stay visible.
//
// All data comes from the live API (VITE_BACKEND_URL); this file owns the
// fetches and hands each view one response.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'
import { needsDonors, BAND_SEVERITY, shortMed } from './format.js'
import District from './views/District.jsx'
import Facility from './views/Facility.jsx'
import Transfer from './views/Transfer.jsx'
import Approved from './views/Approved.jsx'
import IngestModal from './IngestModal.jsx'

const ROUTES = [
  { k: '01', id: 'district', t: 'District' },
  { k: '02', id: 'facility', t: 'Facility' },
  { k: '03', id: 'transfer', t: 'Transfer' },
  { k: '04', id: 'approved', t: 'Approved' },
]
const LOCK_HINT = {
  transfer: 'Unlocks after an IDSP report is ingested',
  approved: 'Unlocks after a transfer is approved',
}
const APPROVED_BY = 'District Medical Officer'

export default function App() {
  const [route, setRoute] = useState('district')
  const [facilityId, setFacilityId] = useState(null)

  const [facilities, setFacilities] = useState(null)
  const [facState, setFacState] = useState({ loading: true, error: null })
  const [outbreaks, setOutbreaks] = useState(null)
  const [obState, setObState] = useState({ loading: true, error: null })
  const [details, setDetails] = useState({})
  const [ingest, setIngest] = useState({ phase: 'closed', filename: null, result: null, error: null })
  const [lastIngest, setLastIngest] = useState(null)
  const [rec, setRec] = useState(null) // {facilityId, medicineId, loading, error, data}
  const [approveState, setApproveState] = useState({ running: false, error: null })
  const [approvals, setApprovals] = useState([])
  const [remainState, setRemainState] = useState({ running: false, step: '', error: null, summary: null })

  const stateRef = useRef({})
  useEffect(() => {
    stateRef.current = { facilities, rec, facilityId }
  }, [facilities, rec, facilityId])

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

  // One detail fetch up front: it carries the thresholds the census cites.
  const primedRef = useRef(false)
  const loadFacilities = useCallback(async () => {
    setFacState((s) => ({ ...s, error: null }))
    try {
      const data = await api.facilities()
      setFacilities(data)
      setFacState({ loading: false, error: null })
      if (data?.length && !primedRef.current) {
        primedRef.current = true
        loadDetail(data[0].id)
      }
      return data
    } catch (err) {
      setFacState({ loading: false, error: err })
      return null
    }
  }, [loadDetail])

  const loadOutbreaks = useCallback(async () => {
    setObState((s) => ({ ...s, error: null }))
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
    loadFacilities()
    loadOutbreaks()
  }, [loadFacilities, loadOutbreaks])

  const retryLoads = useCallback(() => {
    loadFacilities()
    loadOutbreaks()
  }, [loadFacilities, loadOutbreaks])

  // --------------------------------------------------------------- derived
  const ingested = (outbreaks?.driving_surge || 0) > 0
  const approved = approvals.length > 0
  const available = useMemo(
    () => ({ district: true, facility: true, transfer: ingested, approved }),
    [ingested, approved],
  )
  const thresholds = useMemo(() => {
    for (const d of Object.values(details)) if (d?.data?.thresholds) return d.data.thresholds
    return null
  }, [details])

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [route])

  // Test hook (read-only): lets a browser script assert the narrative state.
  useEffect(() => {
    window.__phcApp = { route, ingested, approved, facilityId }
  }, [route, ingested, approved, facilityId])

  // ------------------------------------------------------------ navigation
  const openFacility = useCallback(
    (id) => {
      setFacilityId(id)
      loadDetail(id)
      setRoute('facility')
      setIngest((g) => (g.phase === 'closed' ? g : { ...g, phase: 'closed' }))
    },
    [loadDetail],
  )

  const findDonors = useCallback(async (fid, mid) => {
    setRec({ facilityId: fid, medicineId: mid, loading: true, error: null, data: null })
    setApproveState({ running: false, error: null })
    setRoute('transfer')
    try {
      const data = await api.recommend(fid, mid)
      setRec((r) => (r && r.facilityId === fid && r.medicineId === mid ? { ...r, loading: false, data } : r))
    } catch (err) {
      setRec((r) => (r && r.facilityId === fid && r.medicineId === mid ? { ...r, loading: false, error: err } : r))
    }
  }, [])

  const goRoute = useCallback(
    (id) => {
      if (!available[id]) return
      const { facilities: facs, rec: curRec, facilityId: curFac } = stateRef.current
      if (id === 'facility') {
        // Landing on Facility without picking one opens the facility the
        // district is currently about: the worst-first sort's first row.
        const fid = curFac || facs?.[0]?.id
        if (!fid) return
        openFacility(fid)
        return
      }
      if (id === 'transfer' && !curRec) {
        const f = (facs || []).find((x) => x.id === curFac) || facs?.[0]
        if (f && f.worst_medicine_id) {
          findDonors(f.id, f.worst_medicine_id)
          return
        }
      }
      setRoute(id)
    },
    [available, openFacility, findDonors],
  )

  // --------------------------------------------------------------- ingest
  const runIngest = useCallback(
    async (filename) => {
      setIngest({ phase: 'running', filename, result: null, error: null })
      try {
        const result = await api.ingest(filename)
        setIngest({ phase: 'done', filename, result, error: null })
        setLastIngest(result)
        // The district recolours from this refresh — no page reload.
        await Promise.all([loadFacilities(), loadOutbreaks()])
        const cur = stateRef.current.facilityId
        if (cur) loadDetail(cur)
      } catch (err) {
        setIngest({ phase: 'error', filename, result: null, error: err })
      }
    },
    [loadFacilities, loadOutbreaks, loadDetail],
  )

  // -------------------------------------------------------------- approve
  const stashApproval = useCallback((res, recData) => {
    const a = {
      ...res,
      medicine_id: res.transfer.medicine_id,
      medicine_name: recData?.recipient?.medicine_name,
      unit: recData?.recipient?.unit || '',
      parameters: recData?.parameters || null,
    }
    setApprovals((list) => [...list, a])
    return a
  }, [])

  const approveCurrent = useCallback(async () => {
    const curRec = stateRef.current.rec
    const data = curRec?.data
    const pending = (data?.transfers || []).filter((t) => t.status === 'pending')
    if (!pending.length) return
    setApproveState({ running: true, error: null })
    try {
      for (const t of pending) {
        const res = await api.approve(t.id, APPROVED_BY)
        stashApproval(res, data)
      }
      setApproveState({ running: false, error: null })
      setRoute('approved')
      await Promise.all([loadFacilities(), loadDetail(data.recipient.facility_id)])
    } catch (err) {
      setApproveState({ running: false, error: err })
      await Promise.all([loadFacilities(), loadDetail(data.recipient.facility_id)])
    }
  }, [loadFacilities, loadDetail, stashApproval])

  // Approve-all for the medicines still below the line at the recipient:
  // one recommendation per medicine, each approved with the safety check
  // re-run — exactly the single-transfer path, in sequence.
  const approveRemaining = useCallback(
    async (fid) => {
      setRemainState({ running: true, step: 'Checking medicines…', error: null, summary: null })
      try {
        let detail = await api.facility(fid)
        const targets = (detail.medicines || [])
          .filter((m) => needsDonors(m.band))
          .sort((a, b) => BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] || (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9))
        let nApproved = 0
        const skipped = []
        for (const m of targets) {
          const short = shortMed(m.medicine_id, m.name)
          setRemainState((s) => ({ ...s, step: `Finding donors for ${short}…` }))
          const data = await api.recommend(fid, m.medicine_id)
          const pending = (data.transfers || []).filter((t) => t.status === 'pending')
          if (!pending.length) {
            skipped.push(short)
            continue
          }
          setRemainState((s) => ({ ...s, step: `Approving ${short}…` }))
          for (const t of pending) {
            const res = await api.approve(t.id, APPROVED_BY)
            stashApproval(res, data)
            nApproved += 1
          }
        }
        detail = await loadDetail(fid)
        await loadFacilities()
        const still = detail ? detail.medicines.filter((m) => needsDonors(m.band)).map((m) => shortMed(m.medicine_id, m.name)) : []
        let summary = `${nApproved} transfer${nApproved === 1 ? '' : 's'} approved.`
        if (!still.length) summary += ' Every medicine is above the line.'
        else summary += ` Still below the line: ${still.join(', ')}${skipped.length ? ' (no eligible donor)' : ''}.`
        setRemainState({ running: false, step: '', error: null, summary })
      } catch (err) {
        setRemainState({ running: false, step: '', error: err, summary: null })
        loadDetail(fid)
        loadFacilities()
      }
    },
    [loadDetail, loadFacilities, stashApproval],
  )

  // ---------------------------------------------------------------- render
  const navButton = (r, cls, inner) => {
    const ok = available[r.id]
    return (
      <button
        key={r.id}
        type="button"
        className={cls}
        aria-current={route === r.id ? 'page' : undefined}
        disabled={!ok}
        title={ok ? undefined : LOCK_HINT[r.id]}
        data-nav={r.id}
        onClick={() => goRoute(r.id)}
      >
        {inner}
      </button>
    )
  }

  const lastApproval = approvals[approvals.length - 1]
  const recipientId = lastApproval?.recipient?.before?.facility_id

  return (
    <div>
      <header className="top">
        <div className="wordmark">
          <b>SUPPLY RESILIENCE</b>
          <span>Guntur · A.P.</span>
        </div>
        <nav className="tabs" aria-label="Primary">
          {ROUTES.map((r) =>
            navButton(
              r,
              'tab',
              <>
                <span className="k">{r.k}</span>
                <span className="t">{r.t}</span>
              </>,
            ),
          )}
        </nav>
        <div className="topright">
          <div
            className="simbadge"
            title="Facility stock is derived from real HMIS caseloads — India publishes no facility-level stock. Facility and outbreak data are real."
          >
            <i /> Inventory simulated · facility &amp; outbreak data real
          </div>
          <div className="dsel">Guntur</div>
        </div>
      </header>

      <div className="shell">
        <aside className="side">
          <div className="sidegroup">
            <span className="lbl">Sections</span>
            <div>
              {ROUTES.map((r) =>
                navButton(
                  r,
                  'sidelink',
                  <>
                    <span className="ix">{r.k}</span>
                    {r.t}
                  </>,
                ),
              )}
            </div>
          </div>
          <div className="sidenote">
            <span className="lbl">Decision support</span>
            <p>
              Recommendations require approval by the authorised District Medical Officer. The system executes nothing
              autonomously.
            </p>
          </div>
        </aside>

        <main className="stage">
          {route === 'district' ? (
            <District
              facilities={facilities}
              facState={facState}
              outbreaks={outbreaks}
              obState={obState}
              thresholds={thresholds}
              lastIngest={lastIngest}
              ingested={ingested}
              onRetry={retryLoads}
              onOpenIngest={() => setIngest((g) => ({ ...g, phase: 'pick', error: null }))}
              onOpenFacility={openFacility}
            />
          ) : null}
          {route === 'facility' ? (
            <Facility
              facilityId={facilityId}
              detail={facilityId ? details[facilityId] : null}
              ingested={ingested}
              onBack={() => setRoute('district')}
              onFindDonors={findDonors}
              onRetry={() => facilityId && loadDetail(facilityId)}
            />
          ) : null}
          {route === 'transfer' ? (
            <Transfer
              rec={rec}
              onBack={() => (rec?.facilityId ? openFacility(rec.facilityId) : setRoute('district'))}
              onRetry={() => rec && findDonors(rec.facilityId, rec.medicineId)}
              onApprove={approveCurrent}
              approveState={approveState}
            />
          ) : null}
          {route === 'approved' && approvals.length ? (
            <Approved
              approvals={approvals}
              facilities={facilities}
              recipientDetail={recipientId ? details[recipientId] : null}
              remainState={remainState}
              onApproveRemaining={approveRemaining}
              onOpenFacility={openFacility}
              onReturnDistrict={() => setRoute('district')}
            />
          ) : null}
        </main>
      </div>

      {ingest.phase !== 'closed' ? (
        <IngestModal
          ingest={ingest}
          onRun={runIngest}
          onClose={() => setIngest((g) => ({ ...g, phase: 'closed' }))}
          onOpenFacility={openFacility}
        />
      ) : null}
    </div>
  )
}
