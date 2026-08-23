import { useEffect, useRef } from 'react'
import { fmtDays, needsDonors, shortMed, BAND_SEVERITY } from '../format.js'
import { Band, Dot, Waiting, ErrorNote } from './common.jsx'

function sortWorstFirst(list) {
  return [...list].sort(
    (a, b) =>
      BAND_SEVERITY[b.band] - BAND_SEVERITY[a.band] ||
      (a.days_of_cover ?? 1e9) - (b.days_of_cover ?? 1e9) ||
      a.name.localeCompare(b.name),
  )
}

export default function FacilityList({ facilities, loading, error, onRetry, selectedId, onSelect, filter, onFilter, collapsed, onToggleCollapsed }) {
  const all = facilities ? sortWorstFirst(facilities) : []
  const attention = all.filter((f) => needsDonors(f.band))
  // Safe facilities are already visible as green dots; the list defaults to
  // the ones that need attention. The selected facility always stays listed,
  // so a row does not vanish the moment its transfers make it safe.
  const rows = filter === 'all' ? all : all.filter((f) => needsDonors(f.band) || f.id === selectedId)
  const hasSelection = Boolean(selectedId)

  // Keep the selected row in view when it was chosen on the map or re-sorted.
  const bodyRef = useRef(null)
  useEffect(() => {
    if (!selectedId || collapsed) return
    const el = bodyRef.current?.querySelector(`[data-fac="${CSS.escape(selectedId)}"]`)
    if (el) el.scrollIntoView({ block: 'nearest' })
  }, [selectedId, collapsed, rows.length])

  const cls = ['rail-list', hasSelection && !collapsed ? 'shrunk' : '', hasSelection && collapsed ? 'collapsed' : ''].filter(Boolean).join(' ')

  return (
    <section className={cls} aria-labelledby="facilities-h">
      <div className="list-head">
        <h2 id="facilities-h">Facilities</h2>
        {facilities ? (
          <span className="meta">
            {filter === 'all' ? `all ${all.length} PHCs, worst first` : attention.length ? `${attention.length} of ${all.length} at warning or worse` : `none of ${all.length} at warning or worse`}
          </span>
        ) : null}
        {facilities ? (
          <div className="seg" role="group" aria-label="Filter">
            <button type="button" className={filter === 'attention' ? 'on' : ''} onClick={() => onFilter('attention')} aria-pressed={filter === 'attention'}>
              Attention <b>{attention.length}</b>
            </button>
            <button type="button" className={filter === 'all' ? 'on' : ''} onClick={() => onFilter('all')} aria-pressed={filter === 'all'}>
              All <b>{all.length}</b>
            </button>
          </div>
        ) : null}
        {hasSelection ? (
          <button type="button" className="btn-icon" onClick={onToggleCollapsed} title={collapsed ? 'Show the list' : 'Collapse the list'} aria-label={collapsed ? 'Show the list' : 'Collapse the list'} aria-expanded={!collapsed}>
            {collapsed ? '▾' : '▴'}
          </button>
        ) : null}
      </div>
      <div className="list-body" ref={bodyRef}>
        {loading && !facilities ? (
          <div style={{ padding: 12 }}>
            <Waiting label="Loading facilities and risk bands…" />
          </div>
        ) : null}
        {error && !facilities ? (
          <div style={{ padding: 12 }}>
            <ErrorNote title="Could not load facilities" error={error} onRetry={onRetry} />
          </div>
        ) : null}
        {facilities && rows.length === 0 ? (
          <p className="list-empty">Every facility is above the warning line. Click a marker on the map, or switch to All, to inspect one.</p>
        ) : null}
        <ul className="fac-list">
          {rows.map((f) => {
            const on = f.id === selectedId
            return (
              <li key={f.id}>
                <button type="button" className={`fac-row ${on ? 'on' : ''}`} data-fac={f.id} onClick={() => onSelect(f.id)} aria-pressed={on}>
                  <Dot band={f.band} />
                  <span className="name">
                    {f.name}
                    <span className="sub">{f.sub_district}</span>
                  </span>
                  <span className={`days c-${f.band}`}>
                    {f.days_of_cover == null ? '—' : fmtDays(f.days_of_cover)}
                    {f.days_of_cover == null ? null : <small>d</small>}
                    <span className="med">{f.worst_medicine_id ? shortMed(f.worst_medicine_id, f.worst_medicine_name) : 'no demand recorded'}</span>
                  </span>
                  <Band band={f.band} />
                </button>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
