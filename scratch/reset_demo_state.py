"""Throwaway: put the ledger back to the 'before ingest' demo state.

    py scratch/reset_demo_state.py

Removes every transfer movement and transfer row (backend.transfers.reset_demo)
and clears the outbreaks table, exactly as backend/verify_phase9.py does
before it starts its server. Seed rows are untouched. Run it with the API
server stopped, or restart the server afterwards - the server reads the
database on every request, so no restart is strictly needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import get_connection  # noqa: E402
from backend.transfers import reset_demo  # noqa: E402

conn = get_connection()
r = reset_demo(conn)
with conn:
    n_ob = conn.execute("DELETE FROM outbreaks").rowcount
conn.close()
print(f"removed {r['movements_removed']} transfer movements, {r['transfers_removed']} transfer rows, {n_ob} outbreak rows")
