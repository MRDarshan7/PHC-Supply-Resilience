import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { BANDS, BAND_LABEL, BAND_SEVERITY, fmtDays, shortMed } from '../format.js'
import { Dot, Waiting, ErrorNote } from './common.jsx'

// Marker fill per band. Critical and warning are drawn larger and always on
// top, so a red dot is never hidden behind a healthy neighbour.
const FILL = { critical: '#c62828', warning: '#e08a00', safe: '#2e7d32', unknown: '#9aa0a8' }
const RADIUS = { critical: 9, warning: 8, safe: 6, unknown: 5.5 }

function markerStyle(band, selected) {
  return {
    radius: (RADIUS[band] || RADIUS.unknown) + (selected ? 3 : 0),
    color: selected ? '#111' : '#fff',
    weight: selected ? 3 : 1.5,
    fillColor: FILL[band] || FILL.unknown,
    fillOpacity: 0.95,
    opacity: 1,
  }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

function tooltipHtml(f) {
  const days = f.days_of_cover == null ? '' : ` · <b>${fmtDays(f.days_of_cover)} d</b> of ${esc(shortMed(f.worst_medicine_id, f.worst_medicine_name))}`
  return `<b>${esc(f.name)}</b><br><span>${esc(f.sub_district || '')}</span><br><span>${esc(BAND_LABEL[f.band] || f.band)}${days}</span>`
}

// "Fit district": resets the view to every marker.
const FitControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const div = L.DomUtil.create('div', 'leaflet-bar leaflet-control-fit')
    const a = L.DomUtil.create('a', '', div)
    a.href = '#'
    a.title = 'Fit map to district'
    a.setAttribute('role', 'button')
    a.setAttribute('aria-label', 'Fit map to district')
    a.textContent = 'Fit district'
    L.DomEvent.disableClickPropagation(div)
    L.DomEvent.disableScrollPropagation(div)
    L.DomEvent.on(a, 'click', (e) => {
      L.DomEvent.preventDefault(e)
      this.options.onFit()
    })
    return div
  },
})

function Legend({ facilities, thresholds }) {
  const counts = {}
  for (const b of BANDS) counts[b] = 0
  for (const f of facilities || []) counts[f.band] = (counts[f.band] || 0) + 1
  const t = thresholds
  const thr = {
    critical: t ? `< ${t.critical_days} d` : '',
    warning: t ? `${t.critical_days}–${t.warning_days} d` : '',
    safe: t ? `> ${t.warning_days} d` : '',
    unknown: '',
  }
  return (
    <div className="map-legend" aria-label="Legend">
      <div className="lg-title">Days of cover · worst medicine</div>
      {BANDS.map((b) => (
        <div className="lg-row" key={b}>
          <Dot band={b} />
          <span>
            {BAND_LABEL[b]}
            {thr[b] ? <span className="thr">{thr[b]}</span> : null}
          </span>
          <span className={`n c-${b}`} data-legend-count={b}>
            {counts[b]}
          </span>
        </div>
      ))}
      <div className="lg-foot">{(facilities || []).length} PHCs · real coordinates</div>
    </div>
  )
}

export default function MapPanel({ facilities, selectedId, onSelect, thresholds, refreshing, loadError, onRetry, backendUrl, children }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const markersRef = useRef(new Map())
  const bandsRef = useRef(new Map())
  const boundsRef = useRef(null)
  const fittedRef = useRef(false)
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  useEffect(() => {
    const el = containerRef.current
    const map = L.map(el, { scrollWheelZoom: true, zoomControl: true, attributionControl: true })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    }).addTo(map)
    map.setView([16.3, 80.3], 9) // Guntur district; replaced by fitBounds once facilities arrive
    const fit = () => {
      if (boundsRef.current) map.fitBounds(boundsRef.current, { padding: [24, 24] })
    }
    new FitControl({ onFit: fit }).addTo(map)
    mapRef.current = map
    el.__leafletMap = map // test hook: lets a script read zoom / bounds / marker count
    fittedRef.current = false
    // The map fills a flex cell whose size depends on the rail and the
    // outbreak strip; re-measure whenever that changes.
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(el)
    const markers = markersRef.current
    const bands = bandsRef.current
    return () => {
      ro.disconnect()
      map.remove()
      mapRef.current = null
      markers.clear()
      bands.clear()
      fittedRef.current = false
    }
  }, [])

  // Keep markers in sync: create once, restyle in place on every update so
  // a band change recolours without a rebuild. Draw order is severity:
  // unknown, safe, warning, critical, then the selected marker on top.
  // A marker whose band changed pulses so the change is visible in frame.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !facilities) return
    const markers = markersRef.current
    const bands = bandsRef.current
    const seen = new Set()
    const points = []
    const ordered = [...facilities]
      .filter((f) => typeof f.lat === 'number' && typeof f.lon === 'number')
      .sort((a, b) => BAND_SEVERITY[a.band] - BAND_SEVERITY[b.band])
    let selectedMarker = null
    const changed = []
    for (const f of ordered) {
      seen.add(f.id)
      const selected = f.id === selectedId
      const style = markerStyle(f.band, selected)
      let m = markers.get(f.id)
      if (!m) {
        m = L.circleMarker([f.lat, f.lon], style).addTo(map)
        m.on('click', () => onSelectRef.current(f.id))
        m.bindTooltip(tooltipHtml(f), { direction: 'top', offset: [0, -8], opacity: 0.96 })
        markers.set(f.id, m)
      } else {
        m.setStyle(style)
        m.setRadius(style.radius)
        m.setTooltipContent(tooltipHtml(f))
        if (bands.get(f.id) !== f.band) changed.push(m)
      }
      bands.set(f.id, f.band)
      m.bringToFront()
      if (selected) selectedMarker = m
      points.push([f.lat, f.lon])
    }
    for (const [id, m] of markers) {
      if (!seen.has(id)) {
        m.remove()
        markers.delete(id)
        bands.delete(id)
      }
    }
    if (selectedMarker) selectedMarker.bringToFront()
    for (const m of changed) {
      const p = m.getElement()
      if (!p) continue
      p.classList.remove('mk-pulse')
      void p.getBoundingClientRect()
      p.classList.add('mk-pulse')
    }
    if (points.length) {
      boundsRef.current = L.latLngBounds(points)
      if (!fittedRef.current) {
        map.fitBounds(boundsRef.current, { padding: [24, 24] })
        fittedRef.current = true
      }
    }
  }, [facilities, selectedId])

  // A facility picked from the list may be off-screen: pan just enough to
  // show it (never zoom, never move if it is already visible).
  useEffect(() => {
    const map = mapRef.current
    const m = selectedId ? markersRef.current.get(selectedId) : null
    if (!map || !m) return
    map.panInside(m.getLatLng(), { padding: [60, 60] })
  }, [selectedId])

  const selected = selectedId ? (facilities || []).find((f) => f.id === selectedId) : null

  return (
    <section className="map-panel" aria-label="Map of primary health centres coloured by worst medicine risk band">
      <div ref={containerRef} className="map" />
      {!facilities ? (
        <div className="map-cover">
          {loadError ? (
            <ErrorNote title="Could not load facilities" error={loadError} onRetry={onRetry} />
          ) : (
            <Waiting label="Connecting to the backend and loading facilities…" hint={`Backend: ${backendUrl}`} />
          )}
        </div>
      ) : null}
      {facilities ? <Legend facilities={facilities} thresholds={thresholds} /> : null}
      {refreshing ? (
        <div className="map-pill" role="status">
          <span className="spinner" /> Recomputing days of cover…
        </div>
      ) : null}
      {selected ? (
        <div className="map-sel" aria-live="polite">
          <Dot band={selected.band} />
          <b>{selected.name}</b>
          <span className="muted">
            {selected.days_of_cover == null ? '' : `${fmtDays(selected.days_of_cover)} d · ${shortMed(selected.worst_medicine_id, selected.worst_medicine_name)}`}
          </span>
        </div>
      ) : null}
      {children}
    </section>
  )
}
