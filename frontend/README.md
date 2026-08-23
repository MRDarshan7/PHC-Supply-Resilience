# PHC Supply Resilience — frontend

One screen, no navigation: map → active outbreaks → facility list with inline
expansion (per-medicine table → donor recommendation → memo → approve).
React 19 + Vite, Leaflet with OpenStreetMap tiles, plain CSS.

## Configuration

The backend address comes **only** from `VITE_BACKEND_URL` — there is no
fallback. Copy `.env.example` to `.env.local` for development:

```
VITE_BACKEND_URL=http://127.0.0.1:8000
```

`.env.production` points at the Render deployment and is used by `npm run build`.
Vite reads the variable at build time, so change it and rebuild.

## Run

```
npm install
npm run dev          # http://127.0.0.1:5173, against the backend in VITE_BACKEND_URL
npm run build        # dist/ for Firebase Hosting (firebase.json already points at dist)
npm run lint
```

Start the backend first (`py -m uvicorn backend.main:app` from the repo root).

## Demo flow (mouse only)

1. Page loads: 104 real Guntur PHCs on the map, coloured by the worst medicine
   (critical red · warning amber · safe green · unknown grey). Critical and warning
   markers are larger and always drawn on top; the wheel zooms, drag pans, **Fit district**
   resets the view. The outbreaks panel is empty. The facility list shows only
   warning-or-worse facilities (safe ones are the green dots) with a **Show all** toggle.
2. **Ingest IDSP report** reads `idsp_2025_w45.pdf` through the backend (Gemini,
   cached after the first run). The outbreaks panel fills, the map recolours,
   and a two-line summary gives band counts before → after.
3. Click a facility row (or its marker) to expand it inline: one line of outbreak
   context, then per-medicine stock, baseline and outbreak-surge burn shown
   separately, days of cover and band. A facility can be critical on one
   medicine and safe on another.
4. **Find donors** on a warning/critical medicine. Always visible: the donor,
   quantity, lot, distance, recipient and donor before → after with the safety
   margin, and the memo with an English / తెలుగు toggle. One click away: the score
   breakdown, "Why not the other N facilities?" (every rejection with its reason,
   safety-floor first) and the full ranking.
5. **Approve transfer** writes the ledger at both facilities. The recommendation
   collapses to one line (donor → recipient, quantity, before → after; **Details**
   re-expands it) and a status line explains the facility-level result: after the
   ORS transfer at Thulluru the ORS row turns green while the marker stays red —
   zinc and IV fluids are still critical.
6. **Approve all recommended** resolves the remaining medicines: three compact
   lines, the status line turns green, the marker turns green.

To run the demo again from the start, reset the backend state
(`py scratch/reset_demo_state.py` from the repo root) and reload the page.

## Layout

- `src/App.jsx` — state and data flow (loads, ingest, recommend, approve, approve-all)
- `src/api.js` — fetch wrapper; 120 s timeout so a Render cold start is not reported as failure
- `src/components/MapView.jsx` — Leaflet circle markers, restyled in place on every refresh
- `src/components/OutbreaksPanel.jsx`, `FacilityList.jsx`, `FacilityDetail.jsx`, `Recommendation.jsx`
- `src/index.css` — the whole stylesheet; tables become stacked cards under 640 px
- `MapView` exposes the Leaflet instance as `containerEl.__leafletMap` for scripted checks (zoom, bounds)
