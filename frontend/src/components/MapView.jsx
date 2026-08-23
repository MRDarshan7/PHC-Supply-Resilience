import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { BANDS, BAND_LABEL, fmtDays, shortMed } from '../format.js'
import { Dot } from './common.jsx'

// Marker fill per band. The stroke turns dark and the dot grows when selected.
const FILL = {
  critical: '#c62828',
  warning: '#e08a00',
  safe: '#2e7d32',
  unknown: '#9aa0a8',
}

function markerStyle(band, selected) {
  return {
    radius: selected ? 10 : 7,
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
  const days = f.days_of_cover == null ? '' : ` · ${fmtDays(f.days_of_cover)} d of ${esc(shortMed(f.worst_medicine_id, f.worst_medicine_name))}`
  return `<b>${esc(f.name)}</b><br><span>${esc(f.sub_district || '')}</span><br><span>${esc(BAND_LABEL[f.band] || f.band)}${days}</span>`
}

export default function MapView({ facilities, selectedId, onSelect }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const markersRef = useRef(new Map())
  const fittedRef = useRef(false)
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  useEffect(() => {
    const map = L.map(containerRef.current, {
      scrollWheelZoom: false, // the page scrolls; the wheel must not hijack it
      zoomControl: true,
      attributionControl: true,
    })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    }).addTo(map)
    map.setView([16.3, 80.3], 9) // Guntur district; replaced by fitBounds once facilities arrive
    const markers = markersRef.current
    mapRef.current = map
    fittedRef.current = false
    return () => {
      map.remove()
      mapRef.current = null
      markers.clear()
      fittedRef.current = false
    }
  }, [])

  // Keep markers in sync with the facility list: create once, restyle in
  // place on every update so a band change recolours without a rebuild.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !facilities) return
    const markers = markersRef.current
    const seen = new Set()
    const points = []
    let selectedMarker = null
    for (const f of facilities) {
      if (typeof f.lat !== 'number' || typeof f.lon !== 'number') continue
      seen.add(f.id)
      const selected = f.id === selectedId
      const style = markerStyle(f.band, selected)
      let m = markers.get(f.id)
      if (!m) {
        m = L.circleMarker([f.lat, f.lon], style).addTo(map)
        m.on('click', () => onSelectRef.current(f.id))
        m.bindTooltip(tooltipHtml(f), { direction: 'top', offset: [0, -8], opacity: 0.95 })
        markers.set(f.id, m)
      } else {
        m.setStyle(style)
        m.setRadius(style.radius)
        m.setTooltipContent(tooltipHtml(f))
      }
      if (selected) selectedMarker = m
      points.push([f.lat, f.lon])
    }
    for (const [id, m] of markers) {
      if (!seen.has(id)) {
        m.remove()
        markers.delete(id)
      }
    }
    if (selectedMarker) selectedMarker.bringToFront()
    if (!fittedRef.current && points.length) {
      map.fitBounds(points, { padding: [18, 18] })
      fittedRef.current = true
    }
  }, [facilities, selectedId])

  // Leaflet measures its container once; re-measure when the layout settles.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return undefined
    const onResize = () => map.invalidateSize()
    window.addEventListener('resize', onResize)
    const t = setTimeout(onResize, 150)
    return () => {
      window.removeEventListener('resize', onResize)
      clearTimeout(t)
    }
  }, [])

  const counts = {}
  for (const b of BANDS) counts[b] = 0
  for (const f of facilities || []) counts[f.band] = (counts[f.band] || 0) + 1

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" role="region" aria-label="Map of primary health centres coloured by worst medicine risk band" />
      <div className="map-legend" aria-label="Legend">
        {BANDS.map((b) => (
          <div key={b}>
            <Dot band={b} />
            <span>{BAND_LABEL[b]}</span>
            <span className="n">{counts[b]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
