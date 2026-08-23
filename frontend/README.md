# PHC Supply Resilience — frontend

A full-viewport operations console: the page never scrolls, panels do. Map on
the left (dominant), a compact outbreak strip under it, and a right rail that
holds the facility list and the selected facility's detail, recommendation,
memo and approval — so the map is in frame while a transfer is approved.
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

## Screen

```
┌ header: title · Ingest IDSP report · source file · "Inventory simulated" badge ┐
│ map (Leaflet, fills the column)            │ facilities list (scrolls)        │
│   legend: band thresholds + counts         │──────────────────────────────────│
│   selected-facility pill                   │ selected facility (scrolls)      │
│────────────────────────────────────────────│   outbreak context · status      │
│ active outbreaks: the 457-case card,       │   stock by medicine              │
│   ingest result, "43 outside Guntur"       │   recommendation · memo · folds  │
└ footer: decision-support notice · sources ─────────────────────────────────────┘
```

- Rail width `clamp(560px, 40vw, 780px)`; the list takes ~24 % of the rail once a
  facility is selected (collapsible with the chevron); the detail scrolls on its own.
- Under 900 px the map keeps 36 vh at the top and the rail becomes one scrolling
  column; the medicine table drops its burn column under 640 px.

## Boot and keepalive

Render's free tier sleeps after ~15 minutes idle and the first request then
takes up to ~50 s. `src/Boot.jsx` sits in front of the dashboard:

- On load it probes `GET /health` (6 s per probe, 2 s between probes, 90 s
  ceiling). An awake backend answers within the 500 ms grace window and the
  console mounts directly — the warmup screen is never rendered, no flash.
- Past the grace window a centred warmup screen shows the title, a spinner and
  "Connecting to the backend"; elapsed seconds appear after 5 s. The console
  mounts the moment a probe succeeds.
- At the ceiling it shows the last error and a **Retry** button instead of an
  empty dashboard.
- While the console is open it pings `/health` every 10 minutes, silently, so a
  long session never hits a cold start mid-interaction.

`window.__phcBoot` (phase, startedAt, readyAt, warmupShown, probes,
keepalivePings) is a read-only test hook.

## Demo flow (mouse only)

1. Page loads: 104 real Guntur PHCs on the map, coloured by the worst medicine
   (critical red · warning amber · safe green · unknown grey). Critical and warning
   markers are larger and always drawn on top; the wheel zooms, drag pans,
   **Fit district** resets the view. The outbreak strip is empty. The list shows
   facilities at warning or worse (**Attention**) with an **All** toggle.
2. **Ingest IDSP report** reads `idsp_2025_w45.pdf` through the backend (Gemini,
   cached after the first run). The strip shows the Guntur / Acute Diarrhoeal
   Disease / **457 cases** card with band counts before → after; markers whose
   band changed pulse once as the map recolours — no reload.
3. Click a facility row or marker: outbreak context, then stock by medicine with
   baseline + surge burn, days of cover and band. A facility can be critical on
   one medicine and safe on another.
4. **Find donors** on a warning/critical medicine. Always visible: recommended
   donor, quantity and lot, distance, recipient before → after days of cover,
   donor before → after with its margin above the safety floor, and the memo with
   an English / తెలుగు toggle. One click away: "Why not the other N facilities?"
   (every rejection with its reason, safety-floor first), the score breakdown and
   safety check, the full ranking.
5. **Approve transfer** (sticky in the recommendation header) writes the ledger
   at both facilities. The recommendation collapses to one line (**Details**
   re-expands it) and a status banner explains the facility-level result: after
   the ORS transfer at Thulluru the ORS row turns green while the marker stays red —
   zinc and IV fluids are still critical.
6. **Approve all recommended** resolves the remaining medicines: three compact
   lines, the banner turns green, the marker turns green.

To run the demo again from the start, reset the backend state
(`py scratch/reset_demo_state.py` from the repo root) and reload the page.

## Files

- `src/Boot.jsx` — health-probe gate (warmup screen, ceiling + Retry) and the 10-minute keepalive
- `src/App.jsx` — state and data flow (loads, ingest, recommend, approve, approve-all) and the shell layout
- `src/api.js` — fetch wrapper; 120 s timeout so a Render cold start is not reported as failure (`timeoutMs` per call for the boot probes)
- `src/components/Header.jsx`, `MapPanel.jsx`, `OutbreakStrip.jsx` (strip + over-map drawer),
  `FacilityList.jsx`, `FacilityDetail.jsx`, `Recommendation.jsx`, `common.jsx`
- `src/index.css` — the whole stylesheet
- `MapPanel` exposes the Leaflet instance as `containerEl.__leafletMap` for scripted checks
  (zoom, bounds, marker colours); legend counts carry `data-legend-count`
