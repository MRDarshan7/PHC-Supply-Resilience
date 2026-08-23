"""Phase 4 deployment stub.

Minimal FastAPI app to prove the deploy pipeline works: /health, /facilities,
CORS, and a startup hook that builds the ledger from scratch. No risk engine,
no ledger generator, no IDSP pipeline — those are later phases.

    py -m uvicorn backend.main:app --reload
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.db import create_tables, get_connection  # noqa: E402
from backend.queries import active_phcs  # noqa: E402
from backend.run_loaders import main as run_loaders  # noqa: E402

app = FastAPI(title="PHC Supply Resilience — API")

# Allow all origins for now. Deployment failure #1 is CORS discovered after
# the frontend is already live; this is set before that can happen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    # Idempotent: create_tables() and run_loaders() are both safe to call
    # against an existing database, so a fresh deploy builds its own ledger
    # and a warm restart just re-verifies it.
    with get_connection() as conn:
        create_tables(conn)
    run_loaders(verbose=False)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/facilities")
def facilities():
    with get_connection() as conn:
        phcs = active_phcs(conn)
    return [
        {
            "id": f["facility_id"],
            "name": f["name"],
            "sub_district": f["sub_district"],
            "lat": f["lat"],
            "lon": f["lon"],
        }
        for f in phcs
    ]
