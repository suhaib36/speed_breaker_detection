# ============================================================
# gps.py — GPS Provider (Real NMEA or Simulated Waypoints)
# ============================================================

import time
import logging
import threading
from typing import Optional, Tuple
from config import (
    GPS_SIMULATE, GPS_PORT, GPS_BAUD,
    SIMULATED_GPS_PATH, GPS_STEP_INTERVAL_SEC,
)

logger = logging.getLogger(__name__)

# ── OLD type alias (unchanged) ────────────────────────────
# Coord = Tuple[float, float]   # (latitude, longitude)
#
# ── NEW type alias ────────────────────────────────────────
# CHANGED: Extended from (lat, lon) to (lat, lon, speed_kmph) so that
#          callers always get speed together with position in one call.
#          speed_kmph = 0.0 when a real GPS sentence has no speed field.
Coord = Tuple[float, float, float]   # (latitude, longitude, speed_kmph)  ← NEW


# ─────────────────────────────────────────────────────────
# Simulated GPS
# ─────────────────────────────────────────────────────────

class SimulatedGPS:
    """
    Steps through a list of (lat, lon, speed_kmph) waypoints at a fixed interval.
    After the last waypoint the path reverses, so the simulation loops
    indefinitely without restart.

    CHANGED: Waypoints now include speed_kmph (third element).
             get_location() returns (lat, lon, speed_kmph).
    """

    def __init__(self) -> None:
        self._path  = list(SIMULATED_GPS_PATH)
        self._index = 0
        self._step  = 1                          # +1 forward, -1 reverse
        self._lock  = threading.Lock()
        self._current: Coord = self._path[0]     # (lat, lon, speed)  ← NEW tuple shape
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._walk, daemon=True)
        self._thread.start()
        logger.info(
            f"[GPS] Simulated GPS started — {len(self._path)} waypoints, "
            f"{GPS_STEP_INTERVAL_SEC}s/step"
        )

    def stop(self) -> None:
        self._running = False

    def get_location(self) -> Coord:
        # CHANGED: Now returns (lat, lon, speed_kmph) instead of (lat, lon)
        with self._lock:
            return self._current

    def _walk(self) -> None:
        while self._running:
            time.sleep(GPS_STEP_INTERVAL_SEC)
            with self._lock:
                self._current = self._path[self._index]   # (lat, lon, speed)  ← NEW
                lat, lon, spd = self._current             # ← NEW: unpack speed too
                logger.debug(
                    f"[GPS-SIM] step={self._index:>3}  "
                    f"lat={lat:.6f}  lon={lon:.6f}  speed={spd} km/h"  # ← NEW: log speed
                )
                # Advance index; reverse at ends (unchanged logic)
                self._index += self._step
                if self._index >= len(self._path):
                    self._step  = -1
                    self._index = len(self._path) - 2
                elif self._index < 0:
                    self._step  = 1
                    self._index = 1


# ─────────────────────────────────────────────────────────
# Real GPS (pynmea2 via serial)
# ─────────────────────────────────────────────────────────

class RealGPS:
    """
    Reads NMEA sentences from a serial GPS dongle.
    Requires:  pip install pyserial pynmea2

    CHANGED: get_location() now returns (lat, lon, speed_kmph).
             Speed is parsed from $GPRMC sentences (sog field = knots → km/h).
             Falls back to 0.0 if speed is unavailable in the sentence.
    """

    def __init__(self) -> None:
        # OLD: self._current: Coord = (0.0, 0.0)
        self._current: Coord = (0.0, 0.0, 0.0)   # ← NEW: added speed placeholder
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        try:
            import serial           # type: ignore
            import pynmea2          # type: ignore
        except ImportError:
            logger.error("[GPS] pyserial / pynmea2 not installed. "
                         "Run: pip install pyserial pynmea2")
            raise

        self._running = True
        self._thread = threading.Thread(
            target=self._read_serial, daemon=True,
            kwargs={"serial_mod": serial, "nmea_mod": pynmea2}
        )
        self._thread.start()
        logger.info(f"[GPS] Real GPS started on {GPS_PORT} @ {GPS_BAUD} baud")

    def stop(self) -> None:
        self._running = False

    def get_location(self) -> Coord:
        # CHANGED: Returns (lat, lon, speed_kmph)
        with self._lock:
            return self._current

    def _read_serial(self, serial_mod, nmea_mod) -> None:
        try:
            with serial_mod.Serial(GPS_PORT, GPS_BAUD, timeout=1) as ser:
                while self._running:
                    line = ser.readline().decode("ascii", errors="replace").strip()
                    if line.startswith("$GPGGA") or line.startswith("$GPRMC"):
                        try:
                            msg = nmea_mod.parse(line)
                            if hasattr(msg, "latitude") and msg.latitude:
                                # ── NEW: extract speed from $GPRMC (knots → km/h) ──
                                # OLD code had no speed extraction at all.
                                speed_kmph = 0.0
                                if hasattr(msg, "spd_over_grnd") and msg.spd_over_grnd:
                                    try:
                                        speed_kmph = float(msg.spd_over_grnd) * 1.852
                                    except (ValueError, TypeError):
                                        speed_kmph = 0.0
                                # ──────────────────────────────────────────────────
                                with self._lock:
                                    self._current = (
                                        float(msg.latitude),
                                        float(msg.longitude),
                                        speed_kmph,     # ← NEW: store speed
                                    )
                                logger.debug(
                                    f"[GPS-REAL] lat={msg.latitude:.6f}  "
                                    f"lon={msg.longitude:.6f}  "
                                    f"speed={speed_kmph:.1f} km/h"  # ← NEW: log speed
                                )
                        except nmea_mod.ParseError:
                            pass
        except Exception as exc:
            logger.error(f"[GPS] Serial read error: {exc}")

#fhruuhfuhuhuhurhurhfuhuhfuhfuhfuhfuhfuhfuhfuhuhuhfurhfurfh
class PhoneGPS:
    def start(self):
        from gps_server import start_server
        start_server()

    def stop(self):
        pass

    def get_location(self):
        from gps_server import get_latest
        return get_latest()
    
    #hiuhuvhuvovhtubhjtiojiotjotntvhtbhthothbothb
# ─────────────────────────────────────────────────────────
# Factory (unchanged)
# ─────────────────────────────────────────────────────────

def create_gps():
    """Return the appropriate GPS provider based on config."""
    if GPS_SIMULATE:
        logger.info("[GPS] Mode: SIMULATED")
        return SimulatedGPS()
    else:
        logger.info("[GPS] Mode: PHONE GPS")
        return PhoneGPS()