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
   (critical red · warning amber · safe green · unknown grey). Outbreaks panel is empty.
2. **Ingest IDSP report** reads `idsp_2025_w45.pdf` through the backend (Gemini,
   cached after the first run). The outbreaks panel fills, the map recolours,
   and the ingest summary shows band counts before → after.
3. Click a facility row (or its marker) to expand it inline: per-medicine
   stock, baseline and outbreak-surge burn rates shown separately, days of
   cover and band. A facility can be critical on one medicine and safe on another.
4. **Find donors** on a warning/critical medicine: recommended donor(s) with
   quantity, lot, distance, score breakdown and the safety check arithmetic;
   every rejected candidate with its reason (safety-floor rejections first);
   the memo with an English / తెలుగు toggle.
5. **Approve transfer** writes the ledger at both facilities and shows the
   before/after figures. After the ORS transfer at Thulluru the ORS row turns
   green while the marker stays red — zinc and IV fluids are still critical.
6. **Approve all recommended** resolves the remaining medicines; the marker turns green.

To run the demo again from the start, reset the backend state
(`py scratch/reset_demo_state.py` from the repo root) and reload the page.

## Layout

- `src/App.jsx` — state and data flow (loads, ingest, recommend, approve, approve-all)
- `src/api.js` — fetch wrapper; 120 s timeout so a Render cold start is not reported as failure
- `src/components/MapView.jsx` — Leaflet circle markers, restyled in place on every refresh
- `src/components/OutbreaksPanel.jsx`, `FacilityList.jsx`, `FacilityDetail.jsx`, `Recommendation.jsx`
- `src/index.css` — the whole stylesheet; tables become stacked cards under 640 px
