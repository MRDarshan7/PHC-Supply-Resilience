// Narrative shell: four numbered routes — 01 District, 02 Facility,
// 03 Transfer, 04 Approved — with a top tab bar and a side nav kept in
// sync. The sequence is part of the explanation, so later routes unlock
// only when earned: Transfer after a report has been ingested IN THIS
// SESSION, Approved after a transfer is approved. Disabled routes stay
// visible. A page load always starts pre-ingest: until the user clicks
// Ingest, the district is shown at its routine HMIS baseline (see
// baseline.js) even when the database already stores outbreak rows, so
// the demo can always be run from the start without resetting anything.
//
// All data comes from the live API (VITE_BACKEND_URL); this file owns the
// fetches and hands each view one response. Route 03 holds a session-long
// list of recommendations keyed by (facility, medicine) — finding donors
// for a second medicine adds a row, never replaces one.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'
import { needsDonors, BAND_SEVERITY, shortMed } from './format.js'
import { stripSurge, stripSurgeDetail, censusOf, bandSnapshot } from './baseline.js'
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
const recKey = (fid, mid) => `${fid}/${mid}`

export default function App() {
  const [route, setRoute] = useState('district')
  const [facilityId, setFacilityId] = useState(null)

  const [facilities, setFacilities] = useState(null)
  const [facState, setFacState] = useState({ loading: true, error: null })
  const [outbreaks, setOutbreaks] = useState(null)
  const [obState, setObState] = useState({ loading: true, error: null })
  const [details, setDetails] = useState({})
  const [sessionIngested, setSessionIngested] = useState(false)
  const [ingest, setIngest] = useState({ phase: 'closed', filename: null, result: null, error: null })
  const [ingestSnapshot, setIngestSnapshot] = useState(null) // {census, bands} at the moment Ingest ran
  const [recs, setRecs] = useState([]) // session list; one entry per (facility, medicine)
  const [expandedRec, setExpandedRec] = useState(null)
  const [approvals, setApprovals] = useState([])
  const [remainState, setRemainState] = useState({ running: false, step: '', error: null, summary: null })

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

  // One detail fetch up front: it carries the thresholds the census cites
  // (and the pre-ingest baseline view needs them to band anything at all).
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
  const approved = approvals.length > 0
  const available = useMemo(
    () => ({ district: true, facility: true, transfer: sessionIngested, approved }),
    [sessionIngested, approved],
  )
  const thresholds = useMemo(() => {
    for (const d of Object.values(details)) if (d?.data?.thresholds) return d.data.thresholds
    return null
  }, [details])

  // What the session sees: live risk after an ingest, the surge-free
  // baseline before one.
  const viewFacilities = useMemo(() => {
    if (!facilities) return null
    if (sessionIngested) return facilities
    if (!thresholds) return null
    return stripSurge(facilities, thresholds)
  }, [facilities, sessionIngested, thresholds])

  const rawDetail = facilityId ? details[facilityId] : null
  const viewDetail = useMemo(() => {
    if (!rawDetail?.data || sessionIngested) return rawDetail
    return { ...rawDetail, data: stripSurgeDetail(rawDetail.data) }
  }, [rawDetail, sessionIngested])

  const approvedMeds = useMemo(() => {
    const map = {}
    for (const a of approvals) {
      if (a.recipient?.before?.facility_id !== facilityId) continue
      const mid = a.medicine_id
      map[mid] = { qty: (map[mid]?.qty || 0) + (a.transfer?.qty || 0), unit: a.unit }
    }
    return map
  }, [approvals, facilityId])

  const stateRef = useRef({})
  useEffect(() => {
    stateRef.current = { viewFacilities, recs, facilityId, details }
  }, [viewFacilities, recs, facilityId, details])

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [route])

  // Test hook (read-only): lets a browser script assert the narrative state.
  useEffect(() => {
    window.__phcApp = { route, ingested: sessionIngested, approved, facilityId, recs: recs.map((e) => ({ key: e.key, loading: e.loading, approved: e.approved, hasData: !!e.data })), expandedRec }
  }, [route, sessionIngested, approved, facilityId, recs, expandedRec])

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

  // ------------------------------------------------------- recommendations
  const upsertRec = useCallback((fid, mid, patch) => {
    const key = recKey(fid, mid)
    setRecs((list) => {
      const i = list.findIndex((e) => e.key === key)
      if (i < 0) {
        return [
          ...list,
          { key, facilityId: fid, medicineId: mid, loading: false, error: null, data: null, approved: false, approving: false, approveError: null, approvals: [], ...patch },
        ]
      }
      const copy = [...list]
      copy[i] = { ...copy[i], ...patch }
      return copy
    })
  }, [])

  // Adds to the session list (or re-opens the existing entry) — an earlier
  // recommendation is never discarded by asking for another one.
  const findDonors = useCallback(
    async (fid, mid) => {
      const key = recKey(fid, mid)
      setRoute('transfer')
      setExpandedRec(key)
      if (!stateRef.current.details?.[fid]) loadDetail(fid)
      const existing = stateRef.current.recs.find((e) => e.key === key)
      if (existing && (existing.loading || existing.data)) return
      upsertRec(fid, mid, { loading: true, error: null })
      try {
        const data = await api.recommend(fid, mid)
        upsertRec(fid, mid, { loading: false, data })
      } catch (err) {
        upsertRec(fid, mid, { loading: false, error: err })
      }
    },
    [loadDetail, upsertRec],
  )

  const goRoute = useCallback(
    (id) => {
      if (!available[id]) return
      const { viewFacilities: facs, recs: curRecs, facilityId: curFac } = stateRef.current
      if (id === 'facility') {
        // Landing on Facility without picking one opens the facility the
        // district is currently about: the worst-first sort's first row.
        const fid = curFac || facs?.[0]?.id
        if (!fid) return
        openFacility(fid)
        return
      }
      if (id === 'transfer' && !curRecs.length) {
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
      const before = stateRef.current.viewFacilities
      setIngest({ phase: 'running', filename, result: null, error: null })
      try {
        const result = await api.ingest(filename)
        // Snapshot the session's pre-ingest picture BEFORE revealing the
        // surge, so "what changed" compares this session's before/after even
        // when the database already stored the outbreak rows.
        if (before) setIngestSnapshot({ census: censusOf(before), bands: bandSnapshot(before) })
        setSessionIngested(true)
        setIngest({ phase: 'done', filename, result, error: null })
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

  // Approve one recommendation from the session list. On success the row
  // collapses and stays, marked approved with its before -> after figures.
  const approveRec = useCallback(
    async (key) => {
      const entry = stateRef.current.recs.find((e) => e.key === key)
      const data = entry?.data
      const pending = (data?.transfers || []).filter((t) => t.status === 'pending')
      if (!pending.length || entry.approving || entry.approved) return
      upsertRec(entry.facilityId, entry.medicineId, { approving: true, approveError: null })
      try {
        const got = []
        for (const t of pending) {
          const res = await api.approve(t.id, APPROVED_BY)
          got.push(stashApproval(res, data))
        }
        upsertRec(entry.facilityId, entry.medicineId, { approving: false, approved: true, approvals: got })
        setExpandedRec((k) => (k === key ? null : k))
        await Promise.all([loadFacilities(), loadDetail(entry.facilityId)])
      } catch (err) {
        upsertRec(entry.facilityId, entry.medicineId, { approving: false, approveError: err })
        await Promise.all([loadFacilities(), loadDetail(entry.facilityId)])
      }
    },
    [loadFacilities, loadDetail, stashApproval, upsertRec],
  )

  // Approve-all for the medicines still below the line at the recipient:
  // one recommendation per medicine, each approved with the safety check
  // re-run — the single-transfer path in sequence. Every step lands in the
  // route-03 list too, so the session history stays in one place.
  const approveRemaining = useCallback(
    async (fid) => {
      setRemainState({ running: true, step: 'Checking medicines…', error: null, summary: null })
      try {
        let detail = await api.facility(fid)
        const done = new Set(stateRef.current.recs.filter((e) => e.approved).map((e) => e.key))
        const targets = (detail.medicines || [])
          .filter((m) => needsDonors(m.band) && !done.has(recKey(fid, m.medicine_id)))
          .sort((a, b) => BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] || (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9))
        let nApproved = 0
        const skipped = []
        for (const m of targets) {
          const short = shortMed(m.medicine_id, m.name)
          setRemainState((s) => ({ ...s, step: `Finding donors for ${short}…` }))
          const data = await api.recommend(fid, m.medicine_id)
          upsertRec(fid, m.medicine_id, { loading: false, error: null, data })
          const pending = (data.transfers || []).filter((t) => t.status === 'pending')
          if (!pending.length) {
            skipped.push(short)
            continue
          }
          setRemainState((s) => ({ ...s, step: `Approving ${short}…` }))
          const got = []
          for (const t of pending) {
            const res = await api.approve(t.id, APPROVED_BY)
            got.push(stashApproval(res, data))
            nApproved += 1
          }
          upsertRec(fid, m.medicine_id, { approved: true, approvals: got })
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
    [loadDetail, loadFacilities, stashApproval, upsertRec],
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
              facilities={viewFacilities}
              facState={facState}
              outbreaks={outbreaks}
              obState={obState}
              thresholds={thresholds}
              snapshot={ingestSnapshot}
              ingested={sessionIngested}
              onRetry={retryLoads}
              onOpenIngest={() => setIngest((g) => ({ ...g, phase: 'pick', error: null }))}
              onOpenFacility={openFacility}
            />
          ) : null}
          {route === 'facility' ? (
            <Facility
              facilityId={facilityId}
              detail={viewDetail}
              ingested={sessionIngested}
              approvedMeds={approvedMeds}
              onBack={() => setRoute('district')}
              onFindDonors={findDonors}
              onRetry={() => facilityId && loadDetail(facilityId)}
            />
          ) : null}
          {route === 'transfer' ? (
            <Transfer
              recs={recs}
              expandedKey={expandedRec}
              details={details}
              facilities={facilities}
              onExpand={(key) => setExpandedRec((k) => (k === key ? null : key))}
              onApprove={approveRec}
              onRetry={(e) => {
                upsertRec(e.facilityId, e.medicineId, { loading: true, error: null, data: null })
                api
                  .recommend(e.facilityId, e.medicineId)
                  .then((data) => upsertRec(e.facilityId, e.medicineId, { loading: false, data }))
                  .catch((err) => upsertRec(e.facilityId, e.medicineId, { loading: false, error: err }))
              }}
              onOpenFacility={openFacility}
              onBackDistrict={() => setRoute('district')}
              onGoApproved={() => goRoute('approved')}
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
          facilities={viewFacilities}
          snapshot={ingestSnapshot}
          onRun={runIngest}
          onClose={() => setIngest((g) => ({ ...g, phase: 'closed' }))}
          onOpenFacility={openFacility}
        />
      ) : null}
    </div>
  )
}
