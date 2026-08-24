import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { BAND_SEVERITY, fmtDays, shortMed } from './format.js'

// Band encoding on the basemap, matching the census swatches: critical is
// the only red thing on the map; warning is solid black; safe is white with
// a black ring; unknown is grey. Critical and warning are drawn larger and
// always on top, so a red dot is never hidden behind a healthy neighbour.
const STYLE = {
  critical: { fillColor: '#FF3000', color: '#000000', weight: 1.5, radius: 9 },
  warning: { fillColor: '#000000', color: '#000000', weight: 1.5, radius: 7.5 },
  safe: { fillColor: '#FFFFFF', color: '#000000', weight: 1.5, radius: 5.5 },
  unknown: { fillColor: '#B8B8B8', color: '#B8B8B8', weight: 1, radius: 4.5 },
}

function markerStyle(band) {
  const s = STYLE[band] || STYLE.unknown
  return { ...s, fillOpacity: 1, opacity: 1 }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

function tooltipHtml(f) {
  const days = f.days_of_cover == null ? '' : ` · <b>${fmtDays(f.days_of_cover)} d</b> of ${esc(shortMed(f.worst_medicine_id, f.worst_medicine_name))}`
  return `<b>${esc(f.name)}</b><br><span>${esc(f.sub_district || '')}</span><br><span style="text-transform:uppercase;font-size:10px;font-weight:900;letter-spacing:.08em">${esc(f.band)}</span>${days}`
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

// labelId: facility given a permanent red label (the facility the report
// pushed under the critical line — the one the district view is about).
export default function MapPanel({ facilities, onSelect, labelId }) {
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
    // OpenStreetMap data on CARTO's monochrome Positron rendering — the
    // basemap the mock specifies. Free, keyless, no billing account.
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map)
    map.setView([16.3, 80.3], 9) // Guntur district; replaced by fitBounds once facilities arrive
    const fit = () => {
      if (boundsRef.current) map.fitBounds(boundsRef.current, { padding: [24, 24] })
    }
    new FitControl({ onFit: fit }).addTo(map)
    mapRef.current = map
    el.__leafletMap = map // test hook: lets a script read zoom / bounds / marker count
    fittedRef.current = false
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

  // Keep markers in sync: create once, restyle in place on every update so a
  // band change recolours without a rebuild. Draw order is severity —
  // unknown, safe, warning, critical — so red sits on top. A marker whose
  // band changed pulses so the change is visible in frame.
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
    const changed = []
    for (const f of ordered) {
      seen.add(f.id)
      const style = markerStyle(f.band)
      const labelled = f.id === labelId
      let m = markers.get(f.id)
      if (!m) {
        m = L.circleMarker([f.lat, f.lon], style).addTo(map)
        m.on('click', () => onSelectRef.current(f.id))
        markers.set(f.id, m)
      } else {
        m.setStyle(style)
        m.setRadius(style.radius)
        if (bands.get(f.id) !== f.band) changed.push(m)
        m.unbindTooltip()
      }
      if (labelled) {
        m.bindTooltip(`${esc(f.name)} ${f.days_of_cover == null ? '' : fmtDays(f.days_of_cover) + 'D'}`, {
          permanent: true,
          direction: 'right',
          offset: [12, 0],
          className: 'mk-label',
        })
      } else {
        m.bindTooltip(tooltipHtml(f), { direction: 'top', offset: [0, -8], opacity: 1 })
      }
      bands.set(f.id, f.band)
      m.bringToFront()
      points.push([f.lat, f.lon])
    }
    for (const [id, m] of markers) {
      if (!seen.has(id)) {
        m.remove()
        markers.delete(id)
        bands.delete(id)
      }
    }
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
  }, [facilities, labelId])

  return <div id="map" ref={containerRef} />
}
