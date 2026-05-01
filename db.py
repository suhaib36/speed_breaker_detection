# ============================================================
# db.py — SQLite Database Layer for Speed Breaker Detection
# ============================================================

import sqlite3
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS speed_breakers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude        REAL    NOT NULL,
    longitude       REAL    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 0.25,
    status          TEXT    NOT NULL DEFAULT 'candidate',
    detection_count INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
"""

# Status values
STATUS_CANDIDATE  = "candidate"
STATUS_CONFIRMED  = "confirmed"
STATUS_REJECTED   = "rejected"

# ─────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ─────────────────────────────────────────────────────────
# Initialise DB
# ─────────────────────────────────────────────────────────

def init_db() -> None:
    """Create the speed_breakers table if it doesn't exist."""
    with _get_connection() as conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    logger.info(f"[DB] Database initialised at '{DB_PATH}'")

# MAY202601 change — NEW: seed_confirmed_breaker inserts the pre-loaded demo breaker
#   only if no breaker exists within 50m of that location. This guarantees the
#   practical simulation always has a known confirmed breaker to alert against
#   without duplicating if the DB already has it.
# OLD: no such seed function existed; DB was always empty on first run.
def seed_confirmed_breaker(lat: float, lon: float) -> None:
    """
    MAY202601 change — Insert a confirmed breaker at (lat, lon) for simulation demo
    if none already exists within 50m. Safe to call on every startup.
    """
    from distance import haversine
    existing = load_all_breakers()
    for b in existing:
        if haversine(lat, lon, b["latitude"], b["longitude"]) <= 50:
            logger.info(
                f"[DB] Seed breaker already exists at ({lat}, {lon}) — skipping"
            )
            return
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        conn.execute(
            """INSERT INTO speed_breakers
               (latitude, longitude, confidence, status, detection_count, created_at, updated_at)
               VALUES (?, ?, 0.8, 'confirmed', 2, ?, ?)""",
            (lat, lon, now, now),
        )
        conn.commit()
    logger.info(
        f"[DB] 🌱 Seeded confirmed breaker at ({lat:.6f}, {lon:.6f}) for simulation"
    )

# ─────────────────────────────────────────────────────────
# CRUD helpers  (UNCHANGED except docstring)
# ─────────────────────────────────────────────────────────

def load_all_breakers() -> List[Dict[str, Any]]:
    """Return all speed breakers (excluding rejected ones)."""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM speed_breakers WHERE status != 'rejected' ORDER BY id"
        ).fetchall()
    breakers = [dict(r) for r in rows]
    logger.info(f"[DB] Loaded {len(breakers)} breaker(s) from database")
    return breakers


def insert_breaker(lat: float, lon: float, confidence: float) -> int:
    """Insert a new candidate breaker; return its new ID."""
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO speed_breakers
               (latitude, longitude, confidence, status, detection_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (lat, lon, confidence, STATUS_CANDIDATE, now, now),
        )
        conn.commit()
        new_id = cur.lastrowid
    logger.info(f"[DB] Inserted new candidate breaker id={new_id} at ({lat:.6f}, {lon:.6f})")
    return new_id


def update_breaker(
    breaker_id: int,
    confidence: float,
    status: str,
    detection_count: int,
) -> None:
    """Update confidence, status and detection_count for an existing breaker."""
    now = datetime.utcnow().isoformat()
    with _get_connection() as conn:
        conn.execute(
            """UPDATE speed_breakers
               SET confidence=?, status=?, detection_count=?, updated_at=?
               WHERE id=?""",
            (confidence, status, detection_count, now, breaker_id),
        )
        conn.commit()
    logger.debug(
        f"[DB] Updated breaker id={breaker_id} → confidence={confidence:.2f}, "
        f"status={status}, detections={detection_count}"
    )


def get_breaker_by_id(breaker_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single breaker by primary key."""
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM speed_breakers WHERE id=?", (breaker_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_breaker(breaker_id: int) -> None:
    """Hard-delete a breaker record."""
    with _get_connection() as conn:
        conn.execute("DELETE FROM speed_breakers WHERE id=?", (breaker_id,))
        conn.commit()
    logger.warning(f"[DB] Deleted breaker id={breaker_id}")


def summary_stats() -> Dict[str, int]:
    """Return count by status for logging/display."""
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM speed_breakers GROUP BY status"
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}