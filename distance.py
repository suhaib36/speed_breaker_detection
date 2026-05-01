# ============================================================
# distance.py — Haversine Distance Calculator
# ============================================================

import math
from typing import Tuple

EARTH_RADIUS_M = 6_371_000  # metres


def haversine(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """
    Return the great-circle distance in **metres** between two GPS coordinates.

    Parameters
    ----------
    lat1, lon1 : float  — Point A (decimal degrees)
    lat2, lon2 : float  — Point B (decimal degrees)

    Returns
    -------
    float  — Distance in metres
    """
    # Convert to radians
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_M * c


def nearest_breaker(
    current_lat: float,
    current_lon: float,
    breakers: list,
) -> Tuple[dict, float]:
    """
    Find the closest breaker from a list and return (breaker_dict, distance_m).
    Returns (None, inf) if the list is empty.
    """
    if not breakers:
        return None, float("inf")

    best, best_dist = None, float("inf")
    for b in breakers:
        d = haversine(current_lat, current_lon, b["latitude"], b["longitude"])
        if d < best_dist:
            best, best_dist = b, d
    return best, best_dist


# ─── Quick self-test ───────────────────────────────────────
if __name__ == "__main__":
    # India Gate  →  Rashtrapati Bhavan  (~3 km)
    d = haversine(28.6129, 77.2295, 28.6143, 77.1993)
    print(f"India Gate → Rashtrapati Bhavan: {d:.1f} m  (expect ~2900 m)")