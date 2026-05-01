#!/usr/bin/env python3
# ============================================================
# main.py — Real-Time Speed Breaker Detection System  v3.0
# ============================================================
#
#  Usage:
#    python main.py                  # normal run (simulation)
#    python main.py --confirm <id>   # manually confirm a breaker by ID
#    python main.py --reject  <id>   # manually reject a breaker by ID
#    python main.py --stats          # show DB summary and exit
#    python main.py --list           # list all breakers and exit
#    python main.py --reset-db       # MAY202601 change — wipe DB and re-seed
# ============================================================

import sys
import time
import signal
import logging
import argparse
import threading
from collections import defaultdict
from typing import Dict, Optional, Set

import config
import db as DB
from gps       import create_gps
from distance  import haversine
from voice     import VoiceEngine
from detection import YOLODetector, CameraManager, DetectionEngine, SpeedWindow


# ─────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# AlertTracker  (UNCHANGED)
# ─────────────────────────────────────────────────────────

class AlertTracker:
    def __init__(self) -> None:
        self._count:  Dict[int, int]   = defaultdict(int)
        self._last_t: Dict[int, float] = defaultdict(float)

    def should_alert(self, breaker_id: int) -> bool:
        return (
            self._count[breaker_id]  < config.MAX_ALERTS_PER_BREAKER
            and (time.time() - self._last_t[breaker_id]) >= config.ALERT_COOLDOWN_SEC
        )

    def record_alert(self, breaker_id: int) -> None:
        self._count[breaker_id]  += 1
        self._last_t[breaker_id]  = time.time()

    def reset(self, breaker_id: int) -> None:
        self._count[breaker_id]  = 0
        self._last_t[breaker_id] = 0.0


# ─────────────────────────────────────────────────────────
# Terminal helpers
# ─────────────────────────────────────────────────────────

SEP  = "─" * 60
SEP2 = "═" * 60

def _banner() -> None:
    print(f"\n{SEP2}")
    print("  🚗  SPEED BREAKER DETECTION SYSTEM  |  v3.0")
    print(f"{SEP2}\n")

def _log_location(lat: float, lon: float, speed: float) -> None:
    logger.info(f"[LOC] lat={lat:.6f}  lon={lon:.6f}  spd={speed:.1f} km/h")

def _log_speed_drop(max_spd: float, cur_spd: float, drop: float) -> None:
    print(
        f"\n{SEP}\n"
        f"  ⚡  SPEED DROP DETECTED\n"
        f"     max={max_spd:.1f}  cur={cur_spd:.1f}  drop={drop:.1f} km/h\n"
        f"{SEP}\n"
    )

def _log_alert(bid: int, dist: float, count: int, status: str) -> None:
    icon = "🔴" if status == DB.STATUS_CONFIRMED else "🟡"
    print(
        f"\n{SEP}\n"
        f"  🚨 {icon} ALERT #{count}  —  Breaker id={bid} [{status}]  "
        f"in {dist:.0f} m\n"
        f"{SEP}\n"
    )

# MAY202601 change — new log for proximity-scan trigger
def _log_proximity_scan_trigger(bid: int, dist: float, status: str) -> None:
    print(
        f"\n{SEP}\n"
        f"  📷 PROXIMITY SCAN TRIGGER\n"
        f"     Breaker id={bid} [{status}]  distance={dist:.0f}m\n"
        f"     (≤ {config.CAMERA_SCAN_DISTANCE_M}m threshold)\n"
        f"{SEP}\n"
    )


# ─────────────────────────────────────────────────────────
# SpeedBreakerSystem  — MAY202601 change: revised main loop
# ─────────────────────────────────────────────────────────

class SpeedBreakerSystem:

    def __init__(self) -> None:
        self._running      = False
        self.all_breakers  = []
        self.alert_tracker = AlertTracker()
        self.speed_window  = SpeedWindow()

        # ── Guard flags ─────────────────────────────────
        # Prevents overlapping speed-drop cycles
        self._drop_cycle_active  = False
        # MAY202601 change — tracks which breaker IDs currently have a
        #   proximity scan running so we don't launch duplicate scans
        self._scan_active_ids: Set[int] = set()   # MAY202601 change
        # MAY202601 change — speed-drop cooldown: timestamp of last drop event
        self._last_drop_time: float = 0.0          # MAY202601 change

        # Subsystems
        self.gps    = create_gps()
        self.voice  = VoiceEngine()
        self.yolo   = YOLODetector()
        self.camera = CameraManager()
        self.detect = DetectionEngine(self.yolo, self.camera, self.voice)

        signal.signal(signal.SIGINT,  self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> None:
        _banner()
        logger.info("[MAIN] Initialising subsystems …")

        try:
            from gps_server import start_server
            start_server()
        except Exception as e:
            logger.warning(f"[API] Could not start dashboard server: {e}")

        DB.init_db()

        # MAY202601 change — Seed the pre-loaded confirmed breaker for the demo.
        # OLD: no seeding; DB started empty every run.
        DB.seed_confirmed_breaker(
            lat=28.6165, lon=77.2065   # MAY202601 change — demo confirmed breaker location
        )

        self.all_breakers = DB.load_all_breakers()
        logger.info(f"[MAIN] {len(self.all_breakers)} breaker(s) loaded from DB")

        self.voice.start()
        self.gps.start()
        yolo_ok = self.yolo.load()
        if not yolo_ok:
            logger.warning("[MAIN] YOLOv8 unavailable — YOLO step will be skipped")

        # Print startup config summary
        print(f"\n{SEP}")
        print(f"  Config summary:")
        print(f"    Alert radius       : {config.ALERT_DISTANCE_M} m (confirmed breakers)")
        print(f"    Camera-scan radius : {config.CAMERA_SCAN_DISTANCE_M} m (candidates + confirmed)")  # MAY202601 change
        print(f"    Speed window       : {config.SPEED_WINDOW_SEC} s")
        print(f"    Drop thresholds    : max≥{config.MIN_SPEED_FOR_DROP_KMPH}  "
              f"cur≤{config.MAX_CURRENT_SPEED_KMPH}  drop≥{config.MIN_SPEED_DROP_KMPH} km/h")
        print(f"    Drop cooldown      : {config.SPEED_DROP_COOLDOWN_SEC} s")   # MAY202601 change
        print(f"    GPS simulate       : {config.GPS_SIMULATE}")
        print(f"{SEP}\n")

        self._running = True
        self._loop()

    def stop(self) -> None:
        logger.info("[MAIN] Shutting down …")
        self._running = False
        self.camera.turn_off()
        self.camera.destroy_windows()
        self.gps.stop()
        self.voice.stop()
        stats = DB.summary_stats()
        logger.info(f"[MAIN] Final DB stats: {stats}")
        logger.info("[MAIN] 🔴 Stopped")

    def _shutdown_handler(self, sig, frame) -> None:
        print("\n[MAIN] Interrupt received — stopping …")
        self.stop()
        sys.exit(0)

    # ── Main loop — MAY202601 change ───────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                lat, lon, speed_kmph = self.gps.get_location()
                try:
                    import gps_server
                    gps_server.update_runtime(
                        lat,
                        lon,
                        speed_kmph,
                        self.speed_window.max_speed_in_window()
                    )
                except Exception:
                    pass
                #hbjbkjbjbkjbkjb
                _log_location(lat, lon, speed_kmph)

                # Feed speed into rolling window
                self.speed_window.add(speed_kmph)

                # ── 1. SPEED-DROP CHECK ────────────────────────────────────────
                # MAY202601 change — Added SPEED_DROP_COOLDOWN_SEC guard so multiple
                #   consecutive drop-ticks don't create multiple weak candidates.
                # OLD: only checked self._cycle_active, no time-based cooldown.
                now = time.time()
                drop_cooldown_ok = (now - self._last_drop_time) >= config.SPEED_DROP_COOLDOWN_SEC
                # MAY202601 change ^

                if (not self._drop_cycle_active
                        and drop_cooldown_ok                         # MAY202601 change
                        and self.speed_window.check_sudden_drop(speed_kmph)):

                    max_spd = self.speed_window.max_speed_in_window()
                    drop    = max_spd - speed_kmph
                    _log_speed_drop(max_spd, speed_kmph, drop)
                    self._last_drop_time = now  # MAY202601 change — record drop timestamp

                    self.voice.speak(
                        "Speed drop detected. Checking for speed breaker."
                    )

                    # Run in background thread (drop handler has a user-prompt
                    # which blocks up to 15s — we don't want to freeze GPS)
                    self._drop_cycle_active = True
                    threading.Thread(
                        target=self._run_drop_thread,
                        args=(lat, lon),
                        daemon=True,
                    ).start()

                # ── 2. PROXIMITY SCAN CHECK (MAY202601 change) ────────────────
                # OLD: camera was opened when distance ≤ 30m (CAMERA_ON_DISTANCE_M).
                # NEW: camera opens at 150m (CAMERA_SCAN_DISTANCE_M) for any
                #      candidate or confirmed breaker that hasn't been scanned yet
                #      in this approach.
                for breaker in list(self.all_breakers):
                    if breaker["status"] == DB.STATUS_REJECTED:
                        continue

                    dist = haversine(
                        lat, lon, breaker["latitude"], breaker["longitude"]
                    )
                    bid = breaker["id"]

                    # MAY202601 change — proximity scan window: ≤ 150m
                    if (dist <= config.CAMERA_SCAN_DISTANCE_M     # MAY202601 change
                            and bid not in self._scan_active_ids):  # MAY202601 change

                        logger.info(
                            f"[PROX] Breaker id={bid} [{breaker['status']}] "
                            f"is {dist:.0f}m away — triggering camera scan"
                        )
                        _log_proximity_scan_trigger(bid, dist, breaker["status"])

                        self._scan_active_ids.add(bid)             # MAY202601 change
                        threading.Thread(
                            target=self._run_proximity_scan_thread,
                            args=(lat, lon, breaker),
                            daemon=True,
                        ).start()

                    # MAY202601 change — reset scan flag when vehicle moves > 300m away
                    #   so a second approach also triggers a scan.
                    # OLD: no such reset existed.
                    elif dist > config.CAMERA_SCAN_DISTANCE_M * 2:  # MAY202601 change
                        self._scan_active_ids.discard(bid)           # MAY202601 change

                # ── 3. CONFIRMED BREAKER VOICE ALERTS ─────────────────────────
                # Only alert for CONFIRMED breakers within ALERT_DISTANCE_M (100m).
                # MAY202601 change — previously alerted for ALL non-rejected breakers.
                # OLD: alerted for both candidate and confirmed
                for breaker in list(self.all_breakers):
                    if breaker["status"] != DB.STATUS_CONFIRMED:  # MAY202601 change — confirmed only
                        continue

                    dist = haversine(
                        lat, lon, breaker["latitude"], breaker["longitude"]
                    )
                    bid = breaker["id"]

                    if dist <= config.ALERT_DISTANCE_M:
                        logger.info(
                            f"🔴 Confirmed breaker id={bid} at {dist:.0f}m — "
                            f"checking alert"
                        )
                        if self.alert_tracker.should_alert(bid):
                            count = self.alert_tracker._count[bid] + 1
                            _log_alert(bid, dist, count, breaker["status"])
                            self.voice.alert_speed_breaker(dist)
                            self.alert_tracker.record_alert(bid)

                    elif dist > config.ALERT_DISTANCE_M * 2:
                        self.alert_tracker.reset(bid)

                time.sleep(0.5)  # main loop cadence

            except Exception as exc:
                logger.error(f"[MAIN] Loop error: {exc}", exc_info=True)
                time.sleep(1)

    # ── Background thread helpers ──────────────────────────

    def _run_drop_thread(self, lat: float, lon: float) -> None:
        """
        MAY202601 change — Runs handle_speed_drop_event() in background.
        Replaces _run_cycle_thread() which called run_detection_cycle().
        OLD: self.all_breakers = self.detect.run_detection_cycle(lat, lon, self.all_breakers)
        """
        try:
            self.all_breakers = self.detect.handle_speed_drop_event(
                lat, lon, self.all_breakers
            )
        except Exception as exc:
            logger.error(f"[MAIN] Drop event error: {exc}", exc_info=True)
        finally:
            self._drop_cycle_active = False

    def _run_proximity_scan_thread(
        self, lat: float, lon: float, breaker: dict
    ) -> None:
        """
        MAY202601 change — NEW: Runs run_proximity_scan() for a specific breaker.
        This is the camera-on path; triggered at 150m, not speed-drop.
        """
        bid = breaker["id"]
        try:
            self.all_breakers = self.detect.run_proximity_scan(
                lat, lon, breaker, self.all_breakers
            )
        except Exception as exc:
            logger.error(
                f"[MAIN] Proximity scan error for id={bid}: {exc}", exc_info=True
            )
        finally:
            # MAY202601 change — don't remove from _scan_active_ids here;
            #   removal happens when vehicle drives > 300m away (in main loop)
            #   so we don't re-trigger scan while still within 150m.
            pass


# ─────────────────────────────────────────────────────────
# CLI  (minor additions for --reset-db)
# ─────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="Speed Breaker Detection System v3.0")
    p.add_argument("--confirm", type=int, metavar="ID")
    p.add_argument("--reject",  type=int, metavar="ID")
    p.add_argument("--stats",   action="store_true")
    p.add_argument("--list",    action="store_true")
    # MAY202601 change — new --reset-db flag for demo resets
    p.add_argument(
        "--reset-db", action="store_true",
        help="MAY202601 change — Delete DB and re-seed the demo confirmed breaker"
    )
    return p.parse_args()


def _handle_cli(args) -> bool:
    DB.init_db()

    # MAY202601 change — handle --reset-db
    if args.reset_db:
        import os
        if os.path.exists(config.DB_PATH):
            os.remove(config.DB_PATH)
            print(f"[CLI] 🗑️  Deleted {config.DB_PATH}")
        DB.init_db()
        DB.seed_confirmed_breaker(lat=28.6165, lon=77.2065)
        print("[CLI] ✅ DB reset and demo breaker seeded")
        return True

    if args.stats:
        stats = DB.summary_stats()
        print(f"\n📊 Database Summary\n{SEP}")
        for status, count in stats.items():
            print(f"  {status:<12} : {count}")
        print()
        return True

    if args.list:
        breakers = DB.load_all_breakers()
        print(f"\n📍 All Breakers ({len(breakers)})\n{SEP}")
        fmt = "{:>4}  {:>10}  {:>10}  {:>6}  {:>12}  {:>5}"
        print(fmt.format("ID", "Latitude", "Longitude", "Conf", "Status", "Dets"))
        print(SEP)
        for b in breakers:
            print(fmt.format(
                b["id"],
                f"{b['latitude']:.6f}",
                f"{b['longitude']:.6f}",
                f"{b['confidence']:.2f}",
                b["status"],
                b["detection_count"],
            ))
        print()
        return True

    if args.confirm:
        breakers = DB.load_all_breakers()
        engine = DetectionEngine(YOLODetector(), CameraManager())
        ok = engine.user_confirm(args.confirm, breakers)
        print(f"{'✅ Confirmed' if ok else '❌ Not found'} breaker id={args.confirm}")
        return True

    if args.reject:
        breakers = DB.load_all_breakers()
        engine = DetectionEngine(YOLODetector(), CameraManager())
        ok = engine.user_reject(args.reject, breakers)
        print(f"{'❌ Rejected' if ok else '❌ Not found'} breaker id={args.reject}")
        return True

    return False


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = _parse_args()
    if not _handle_cli(args):
        system = SpeedBreakerSystem()
        system.start()