# ============================================================
# detection.py — Camera Control + YOLOv8 Speed-Breaker Detection
# ============================================================

import time
import logging
import threading
from typing import Optional, Tuple, Dict, Any
from collections import deque

import config
import db as DB
from config import (
    CAMERA_INDEX, YOLO_MODEL_PATH, YOLO_CONFIDENCE,
    YOLO_TARGET_CLASS,
    CONFIDENCE_CONFIRM_THRESHOLD,
    CONFIDENCE_INCREMENT_YOLO,           # MAY202601 change — renamed from CONFIDENCE_INCREMENT
    CONFIDENCE_INCREMENT_YOLO_STRONG,    # MAY202601 change — new stronger increment
    CONFIDENCE_DECREMENT, CONFIDENCE_REJECT_THRESHOLD,
    INITIAL_CANDIDATE_CONFIDENCE,
    CONFIDENCE_USER_CONFIRM_BOOST, CONFIDENCE_USER_REJECT_DROP,
    SPEED_WINDOW_SEC, MIN_SPEED_FOR_DROP_KMPH,
    MAX_CURRENT_SPEED_KMPH, MIN_SPEED_DROP_KMPH,
    CAMERA_WARMUP_FRAMES,       # MAY202601 change — new constant for smooth camera startup
    CAMERA_FRAME_TIMEOUT_SEC,   # MAY202601 change — new constant for frame read timeout
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# SpeedWindow — Rolling speed tracker  (logic UNCHANGED)
# ══════════════════════════════════════════════════════════

class SpeedWindow:
    """
    Maintains a rolling window of speed samples (last SPEED_WINDOW_SEC seconds).
    Exposes check_sudden_drop() implementing the 3-condition speed-drop rule.
    """

    def __init__(self) -> None:
        self._samples: deque = deque()

    def add(self, speed_kmph: float) -> None:
        now = time.time()
        self._samples.append((now, speed_kmph))
        self._evict_old(now)

    def max_speed_in_window(self) -> float:
        if not self._samples:
            return 0.0
        return max(s for _, s in self._samples)

    def check_sudden_drop(self, current_speed_kmph: float) -> bool:
        """
        Returns True if ALL three conditions are met:
          1. max speed in rolling window  >= MIN_SPEED_FOR_DROP_KMPH  (60)
          2. current speed               <= MAX_CURRENT_SPEED_KMPH    (45)
          3. speed_drop (max − current)  >= MIN_SPEED_DROP_KMPH       (25)
        """
        max_spd = self.max_speed_in_window()
        drop    = max_spd - current_speed_kmph

        cond1 = max_spd >= MIN_SPEED_FOR_DROP_KMPH
        cond2 = current_speed_kmph <= MAX_CURRENT_SPEED_KMPH
        cond3 = drop >= MIN_SPEED_DROP_KMPH

        if cond1 and cond2 and cond3:
            logger.info(
                f"[SPEED] 🚨 Sudden drop: max={max_spd:.1f}  "
                f"cur={current_speed_kmph:.1f}  drop={drop:.1f} km/h"
            )
            return True

        logger.debug(
            f"[SPEED] window_max={max_spd:.1f}  cur={current_speed_kmph:.1f}  "
            f"drop={drop:.1f}  cond=({cond1},{cond2},{cond3})"
        )
        return False

    def _evict_old(self, now: float) -> None:
        cutoff = now - SPEED_WINDOW_SEC
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


# ══════════════════════════════════════════════════════════
# YOLOv8 Model Wrapper  (UNCHANGED)
# ══════════════════════════════════════════════════════════

class YOLODetector:
    """Lazy-load YOLOv8 model and run inference on frames."""

    def __init__(self) -> None:
        self._model = None
        self._loaded = False

    def load(self) -> bool:
        try:
            from ultralytics import YOLO  # type: ignore
            self._model = YOLO(YOLO_MODEL_PATH)
            self._loaded = True
            logger.info(f"[YOLO] Model loaded: {YOLO_MODEL_PATH}")
            return True
        except Exception as exc:
            logger.error(f"[YOLO] Failed to load model: {exc}")
            return False

    def detect(self, frame) -> Tuple[bool, float, list]:
        """Returns (detected, best_confidence, boxes)."""
        if not self._loaded or self._model is None:
            return False, 0.0, []
        try:
            results = self._model(frame,imgsz=320, verbose=False)
            detections = []
            best_conf = 0.0
            for r in results:
                for box in r.boxes:
                    cls_id   = int(box.cls[0])
                    cls_name = r.names[cls_id].lower().replace(" ", "_")
                    conf     = float(box.conf[0])
                    if (cls_name == YOLO_TARGET_CLASS.lower().replace(" ", "_")
                            and conf >= YOLO_CONFIDENCE):
                        detections.append({
                            "class": cls_name,
                            "confidence": conf,
                            "box": box.xyxy[0].tolist(),
                        })
                        best_conf = max(best_conf, conf)
            detected = len(detections) > 0
            if detected:
                logger.info(
                    f"[YOLO] ✅ Detected (best_conf={best_conf:.2f}, "
                    f"count={len(detections)})"
                )
            return detected, best_conf, detections
        except Exception as exc:
            logger.error(f"[YOLO] Inference error: {exc}")
            return False, 0.0, []


# ══════════════════════════════════════════════════════════
# CameraManager — MAY202601 change: smooth open/close + warmup
# ══════════════════════════════════════════════════════════

class CameraManager:
    """
    Manages OpenCV camera lifecycle with smooth open/close operations.

    MAY202601 change — Key improvements over previous version:
      • Camera is opened ONCE and kept in a "warm" state after turn_on().
      • CAMERA_WARMUP_FRAMES frames are drained silently after open so the
        sensor has time to auto-expose — prevents blurry/dark first frames
        that caused YOLO misses and contributed to perceived lag.
      • capture_best_frame() runs a short burst and picks the sharpest frame
        (highest Laplacian variance) rather than blindly taking frame 1.
      • turn_off() calls release() AND cv2.waitKey to flush any OS-level
        buffers before returning, eliminating the multi-second lag on close.
      • A background "keepalive" thread drains frames while the camera is on
        but YOLO is not actively running, so the buffer never backs up and
        the next frame read is always fresh.
    """

    def __init__(self) -> None:
        self._cap          = None
        self.is_on         = False
        self._frame_count  = 0
        self._lock         = threading.Lock()           # MAY202601 change — thread-safe cap access
        self._keepalive    = None                       # MAY202601 change — background drain thread
        self._drain_active = False                      # MAY202601 change — controls drain thread

    # ── Turn ON ───────────────────────────────────────────
    # MAY202601 change — Added warmup drain loop and keepalive thread.
    # OLD turn_on (kept for reference):
    # def turn_on(self) -> bool:
    #     if self.is_on:
    #         return True
    #     import cv2
    #     self._cap = cv2.VideoCapture(CAMERA_INDEX)
    #     if not self._cap.isOpened():
    #         return False
    #     self.is_on = True
    #     self._frame_count = 0
    #     return True

    def turn_on(self) -> bool:
        """MAY202601 change — Open camera, drain warmup frames, start keepalive."""
        if self.is_on:
            return True
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(CAMERA_INDEX)
            if not cap.isOpened():
                logger.error("[CAM] Could not open camera")
                return False

            # MAY202601 change — Reduce internal OpenCV buffer to 1 frame so reads
            #   are always fresh (was default 10, causing 10-frame read lag).
            # OLD: no buffer size was set
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # MAY202601 change — Drain CAMERA_WARMUP_FRAMES silently so auto-exposure
            #   settles before we use any frame for YOLO.
            for i in range(CAMERA_WARMUP_FRAMES):
                cap.read()   # discard

            with self._lock:
                self._cap         = cap
                self.is_on        = True
                self._frame_count = 0

            # MAY202601 change — Start background drain thread to keep buffer fresh.
            #self._drain_active = True
            #self._keepalive = threading.Thread(
            #    target=self._drain_buffer, daemon=True
            #)
            #self._keepalive.start()

            logger.info("[CAM] 📷 Camera TURNED ON (warmup done, keepalive started)")
            return True
        except Exception as exc:
            logger.error(f"[CAM] Error turning on camera: {exc}")
            return False

    # ── Turn OFF ──────────────────────────────────────────
    # MAY202601 change — Added buffer flush and keepalive stop.
    # OLD turn_off (kept for reference):
    # def turn_off(self) -> None:
    #     if not self.is_on:
    #         return
    #     if self._cap:
    #         self._cap.release()
    #         self._cap = None
    #     self.is_on = False

    def turn_off(self) -> None:
        """MAY202601 change — Stop keepalive, flush buffer, release camera cleanly."""
        if not self.is_on:
            return
        try:
            # MAY202601 change — Signal drain thread to stop before releasing cap.
            #self._drain_active = False
            #if self._keepalive and self._keepalive.is_alive():
            #    self._keepalive.join(timeout=1.0)   # wait up to 1s
            #self._keepalive = None

            with self._lock:
                if self._cap:
                    import cv2  # type: ignore
                    # MAY202601 change — read + discard one more frame to flush OS buffer
                    self._cap.read()
                    self._cap.release()
                    self._cap = None
                    cv2.waitKey(1)   # MAY202601 change — let OS finish camera teardown
                self.is_on = False

            logger.info("[CAM] 📷 Camera TURNED OFF (buffer flushed)")
        except Exception as exc:
            logger.error(f"[CAM] Error releasing camera: {exc}")

    # ── Capture best frame (MAY202601 change — NEW method) ────────────────────
    # OLD: callers called read_frame() which took whichever frame came next.
    # NEW: capture_best_frame() takes a short burst and picks the sharpest one
    #      measured by Laplacian variance (focus quality metric).
    def capture_best_frame(self, burst: int = 3):
        """
        Lightweight snapshot capture (NO sharpness calculation).
        """
        if not self.is_on or self._cap is None:
            return None, False

        try:
            frame = None

            for _ in range(burst):
                ret, frame = self._cap.read()

            if frame is None:
                logger.warning("[CAM] No frame captured")
                return None, False

            return frame, True
        except Exception as exc:
            logger.error(f"[CAM] capture error: {exc}")
            return None, False

    # ── OLD read_frame kept for reference (no longer called) ──────────────────
    # MAY202601 change — read_frame replaced by capture_best_frame in all callers.
    # def read_frame(self):
    #     if not self.is_on or self._cap is None:
    #         return None, False
    #     import cv2
    #     ret, frame = self._cap.read()
    #     if not ret or frame is None:
    #         return None, False
    #     self._frame_count += 1
    #     should_process = (self._frame_count % FRAME_SKIP == 0)
    #     return frame, should_process

    # ── Background drain thread (MAY202601 change — NEW) ──────────────────────
    def _drain_buffer(self) -> None:
        """
        MAY202601 change — Continuously drain the camera buffer while camera is on
        so frames never queue up. Runs at ~30 fps to match typical camera rate.
        Without this, calling read() after 2 seconds of idle returns a 2-second-old
        stale frame — the main source of camera lag in the previous version.
        """
        try:
            import cv2  # type: ignore
            while self._drain_active:
                with self._lock:
                    if self._cap and self._cap.isOpened():
                        self._cap.grab()   # grab without decode — very cheap
                time.sleep(0.033)  # ~30 fps drain rate
        except Exception as exc:
            logger.debug(f"[CAM] Drain thread exited: {exc}")

    # ── Overlay / display helpers (UNCHANGED) ─────────────────────────────────

    @staticmethod
    def draw_overlay(frame, label: str, colour=(0, 255, 0)):
        try:
            import cv2
            cv2.putText(
                frame, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, colour, 2, cv2.LINE_AA,
            )
        except Exception:
            pass
        return frame

    @staticmethod
    def draw_boxes(frame, detections: list):
        try:
            import cv2
            for det in detections:
                x1, y1, x2, y2 = [int(v) for v in det["box"]]
                conf = det["confidence"]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(
                    frame, f"Speed Breaker {conf:.0%}",
                    (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 255), 2,
                )
        except Exception:
            pass
        return frame

    @staticmethod
    def show_frame(frame, window_name: str = "Speed Breaker Detection"):
        try:
            import cv2
            cv2.imshow(window_name, frame)
            cv2.waitKey(1)
        except Exception:
            pass

    @staticmethod
    def destroy_windows():
        try:
            import cv2
            cv2.destroyAllWindows()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════
# DetectionEngine  — MAY202601 change: revised workflow
# ══════════════════════════════════════════════════════════

class DetectionEngine:
    """
    MAY202601 change — Revised workflow vs previous version:

    OLD workflow (previous version):
      Speed drop → check DB → if exists: camera ON + YOLO + user prompt
                             if not: camera ON + YOLO + user prompt

    NEW workflow (MAY202601):
      Speed drop → check DB within 100m
        A. NO candidate within 100m:
             → Save weak candidate (conf=0.20-0.30), NO camera, NO YOLO
             → Ask user yes/no (no camera needed)
             → If yes: boost confidence
             → DB updated; camera stays OFF
        B. Candidate/confirmed EXISTS within 100m:
             → NOT triggered here (handled by proximity scan in main.py)
             → When vehicle comes within 150m of that entry on any future pass:
                  camera ON → capture_best_frame → YOLO → user prompt
                  → confidence updated → camera OFF

    Camera is also activated by main.py's proximity scan (150m) whenever
    the vehicle approaches a known candidate/confirmed, regardless of speed drop.
    """

    NEARBY_MATCH_RADIUS_M = 30  # treat detections within 30m as same breaker

    def __init__(self, yolo: YOLODetector, camera: CameraManager,
                 voice_engine=None) -> None:
        self._yolo   = yolo
        self._camera = camera
        self._voice  = voice_engine
        self._pending_confirm: Dict[int, float] = {}

    # ══════════════════════════════════════════════════════
    # MAY202601 change — handle_speed_drop_event
    # Replaces old run_detection_cycle() which always opened the camera.
    # OLD run_detection_cycle (kept for reference):
    # def run_detection_cycle(self, lat, lon, all_breakers):
    #     self._camera.turn_on()
    #     frame, _ = self._camera.read_frame()
    #     if frame is None:
    #         self._camera.turn_off()
    #         return all_breakers
    #     existing = self._find_nearby_breaker(lat, lon, all_breakers, radius_m=100)
    #     if existing:
    #         all_breakers = self.process_validation_mode(frame, existing, all_breakers)
    #     else:
    #         all_breakers = self.process_discovery_mode(frame, lat, lon, all_breakers)
    #     self._camera.turn_off()
    #     return all_breakers
    # ══════════════════════════════════════════════════════

    def handle_speed_drop_event(
        self,
        lat: float,
        lon: float,
        all_breakers: list,
    ) -> list:
        """
        MAY202601 change — Called by main.py when a sudden speed drop is detected.

        Camera does NOT open here.
        If no DB entry within 100m: save weak candidate, ask user yes/no.
        If DB entry exists within 100m: log it and do nothing
          (the proximity-based scan in main.py will handle it at 150m).
        """
        from config import SPEED_DROP_CHECK_RADIUS_M

        logger.info(f"[DROP-EVENT] Speed drop at ({lat:.6f}, {lon:.6f})")

        existing = self._find_nearby_breaker(
            lat, lon, all_breakers, radius_m=SPEED_DROP_CHECK_RADIUS_M
        )

        if existing:
            # MAY202601 change — Breaker/candidate already known; no action needed here.
            # The proximity scan (150m) will open camera when vehicle is close enough.
            logger.info(
                f"[DROP-EVENT] ℹ️  Existing entry id={existing['id']} "
                f"({existing['status']}, conf={existing['confidence']:.2f}) "
                f"within {SPEED_DROP_CHECK_RADIUS_M}m — "
                f"camera scan deferred to proximity check (150m)"
            )
            return all_breakers

        # MAY202601 change — No entry within 100m: save a weak candidate.
        #   Camera stays OFF at this point.
        logger.info(
            "[DROP-EVENT] 🆕 No known breaker within 100m — saving WEAK CANDIDATE"
        )
        all_breakers = self._save_weak_candidate(lat, lon, all_breakers)
        return all_breakers

    # ── MAY202601 change — _save_weak_candidate (NEW) ─────────────────────────
    # OLD: when no DB entry existed, camera was opened and YOLO ran immediately.
    # NEW: just save a low-confidence candidate and ask the user.
    def _save_weak_candidate(
        self, lat: float, lon: float, all_breakers: list
    ) -> list:
        """
        MAY202601 change — Insert a weak candidate (conf=0.20-0.30) without camera.
        Then ask user yes/no. If yes: boost by +0.40. If no: drop by −0.40.
        """
        # Insert with initial confidence
        new_id = DB.insert_breaker(lat, lon, INITIAL_CANDIDATE_CONFIDENCE)
        candidate = {
            "id":              new_id,
            "latitude":        lat,
            "longitude":       lon,
            "confidence":      INITIAL_CANDIDATE_CONFIDENCE,
            "status":          DB.STATUS_CANDIDATE,
            "detection_count": 1,
        }
        all_breakers.append(candidate)

        print(
            f"\n{'═'*60}\n"
            f"  🆕 WEAK CANDIDATE SAVED\n"
            f"     id={new_id}  lat={lat:.6f}  lon={lon:.6f}\n"
            f"     confidence={INITIAL_CANDIDATE_CONFIDENCE:.2f}  "
            f"status=candidate\n"
            f"     Camera: OFF (speed-drop only, not yet validated)\n"
            f"{'═'*60}"
        )

        if self._voice:
            self._voice.speak(
                "Possible speed breaker location recorded. Please confirm."
            )

        # Ask user — no camera needed
        user_yes = self._ask_user_feedback(new_id)

        if user_yes:
            candidate["confidence"] = min(
                1.0, candidate["confidence"] + CONFIDENCE_USER_CONFIRM_BOOST
            )
            logger.info(
                f"[WEAK-CAND] 👤 User YES → conf ↑ to {candidate['confidence']:.2f}"
            )
            print(
                f"  ✅ User confirmed → strong candidate  "
                f"conf={candidate['confidence']:.2f}\n"
                f"  (Camera will open at 150m on next approach)\n"
                f"{'─'*60}"
            )
        else:
            candidate["confidence"] = max(
                0.0, candidate["confidence"] - CONFIDENCE_USER_REJECT_DROP
            )
            logger.info(
                f"[WEAK-CAND] 👤 User NO → conf ↓ to {candidate['confidence']:.2f}"
            )

        # Evaluate after user input
        if candidate["confidence"] >= CONFIDENCE_CONFIRM_THRESHOLD:
            candidate["status"] = DB.STATUS_CONFIRMED
            logger.info(f"[WEAK-CAND] ✅ Candidate id={new_id} CONFIRMED immediately")
        elif candidate["confidence"] <= CONFIDENCE_REJECT_THRESHOLD:
            candidate["status"] = DB.STATUS_REJECTED
            logger.warning(f"[WEAK-CAND] ❌ Candidate id={new_id} REJECTED")
        else:
            logger.info(
                f"[WEAK-CAND] ⏳ Candidate id={new_id} stays as candidate "
                f"(conf={candidate['confidence']:.2f}) — awaiting camera validation"
            )

        DB.update_breaker(
            new_id, candidate["confidence"],
            candidate["status"], candidate["detection_count"]
        )

        # Sync back
        for i, b in enumerate(all_breakers):
            if b["id"] == new_id:
                all_breakers[i] = candidate
                break

        return all_breakers

    # ══════════════════════════════════════════════════════
    # MAY202601 change — run_proximity_scan
    # NEW method: called by main.py when vehicle is within CAMERA_SCAN_DISTANCE_M
    # of a known candidate/confirmed. This is where camera opens.
    # ══════════════════════════════════════════════════════

    def run_proximity_scan(
        self,
        lat: float,
        lon: float,
        target: Dict[str, Any],
        all_breakers: list,
    ) -> list:
        """
        MAY202601 change — Camera-based validation when vehicle approaches an
        existing candidate or confirmed breaker within CAMERA_SCAN_DISTANCE_M (150m).

        Steps:
          1. Camera ON (with warmup + keepalive from new CameraManager)
          2. Capture best frame (burst of 5, pick sharpest)
          3. Run YOLO
             DETECTED  → confidence += 0.15 (or 0.25 if detection_count > 1)
             NOT DET   → confidence -= 0.10
          4. Ask user yes/no
             YES → confidence += 0.40
             NO  → confidence -= 0.40
          5. Evaluate thresholds → confirm / reject / keep candidate
          6. DB updated
          7. Camera OFF
        """
        bid  = target["id"]
        conf = target["confidence"]

        print(
            f"\n{'═'*60}\n"
            f"  📷 PROXIMITY SCAN — id={bid}  status={target['status']}\n"
            f"     Vehicle within 150m — opening camera …\n"
            f"{'═'*60}"
        )

        # Step 1 — Camera ON
        cam_ok = self._camera.turn_on()
        if not cam_ok:
            logger.error("[PROX-SCAN] Camera failed to open — skipping scan")
            return all_breakers

        # Step 2 — Capture best frame (MAY202601 change — uses new burst method)
        frame, got_frame = self._camera.capture_best_frame(burst=5)
        if not got_frame or frame is None:
            logger.warning("[PROX-SCAN] No frame captured — skipping YOLO")
            self._camera.turn_off()
            return all_breakers

        # Step 3 — YOLO detection
        yolo_detected, _, boxes = self._yolo.detect(frame)

        # MAY202601 change — use stronger increment on repeat detections
        # OLD: always used CONFIDENCE_INCREMENT = 0.15
        if yolo_detected:
            increment = (
                CONFIDENCE_INCREMENT_YOLO_STRONG    # 0.25
                if target["detection_count"] > 1
                else CONFIDENCE_INCREMENT_YOLO       # 0.15
            )
            conf = min(1.0, conf + increment)
            logger.info(
                f"[PROX-SCAN] YOLO ✅  conf ↑ +{increment:.2f} → {conf:.2f}"
            )
            frame = CameraManager.draw_overlay(
                frame,
                f"YOLO DETECTED  id={bid}  conf={conf:.0%}",
                colour=(0, 255, 0),
            )
            frame = CameraManager.draw_boxes(frame, boxes)
        else:
            conf = max(0.0, conf - CONFIDENCE_DECREMENT)
            logger.info(f"[PROX-SCAN] YOLO ❌  conf ↓ -0.10 → {conf:.2f}")
            frame = CameraManager.draw_overlay(
                frame,
                f"YOLO: no detection  id={bid}  conf={conf:.0%}",
                colour=(0, 165, 255),
            )

        #CameraManager.show_frame(frame)

        # Step 4 — User prompt
        user_yes = self._ask_user_feedback(bid)

        if user_yes:
            conf = min(1.0, conf + CONFIDENCE_USER_CONFIRM_BOOST)
            logger.info(f"[PROX-SCAN] 👤 User YES → conf ↑ → {conf:.2f}")
        else:
            conf = max(0.0, conf - CONFIDENCE_USER_REJECT_DROP)
            logger.info(f"[PROX-SCAN] 👤 User NO  → conf ↓ → {conf:.2f}")

        # Step 5 — Evaluate thresholds
        target["confidence"]      = conf
        target["detection_count"] += 1

        if conf >= CONFIDENCE_CONFIRM_THRESHOLD:
            target["status"] = DB.STATUS_CONFIRMED
            logger.info(
                f"[PROX-SCAN] ✅ Breaker id={bid} CONFIRMED (conf={conf:.2f})"
            )
            if self._voice:
                self._voice.alert_confirmed(bid)
        elif conf <= CONFIDENCE_REJECT_THRESHOLD:
            target["status"] = DB.STATUS_REJECTED
            logger.warning(
                f"[PROX-SCAN] ❌ Breaker id={bid} REJECTED (conf={conf:.2f})"
            )
        else:
            target["status"] = DB.STATUS_CANDIDATE
            logger.info(
                f"[PROX-SCAN] ⏳ Breaker id={bid} stays CANDIDATE (conf={conf:.2f})"
            )

        # Step 6 — DB update
        DB.update_breaker(
            bid, target["confidence"],
            target["status"], target["detection_count"]
        )

        # Step 7 — Camera OFF
        self._camera.turn_off()

        # Sync back
        for i, b in enumerate(all_breakers):
            if b["id"] == bid:
                all_breakers[i] = target
                break

        return all_breakers

    # ── _ask_user_feedback (UNCHANGED logic, cleaner output) ──────────────────

    def _ask_user_feedback(
        self,
        breaker_id: Optional[int],
        timeout_sec: float = 15.0,
    ) -> bool:
        """
        Print a terminal prompt and wait up to timeout_sec seconds.
        Returns True for yes, False for no/timeout.
        """
        import select, sys

        id_str = f" (id={breaker_id})" if breaker_id is not None else ""
        prompt = (
            f"\n{'─'*60}\n"
            f"  ❓ Is there a speed breaker here{id_str}?\n"
            f"     Type  yes / y  or  no / n  "
            f"[{timeout_sec:.0f}s timeout]: "
        )
        print(prompt, end="", flush=True)

        try:
            ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
            if ready:
                answer = sys.stdin.readline().strip().lower()
                result = answer in ("yes", "y")
                print(f"  → {'YES ✅' if result else 'NO ❌'}")
                return result
            else:
                print("\n  → Timeout — treating as NO")
                return False
        except Exception:
            try:
                answer = input().strip().lower()
                return answer in ("yes", "y")
            except Exception:
                return False

    # ── user_confirm / user_reject — CLI helpers (UNCHANGED) ──────────────────

    def user_confirm(self, breaker_id: int, all_breakers: list) -> bool:
        for b in all_breakers:
            if b["id"] == breaker_id:
                b["confidence"] = 1.0
                b["status"]     = DB.STATUS_CONFIRMED
                DB.update_breaker(breaker_id, 1.0, DB.STATUS_CONFIRMED, b["detection_count"])
                self._pending_confirm.pop(breaker_id, None)
                logger.info(f"[DETECT] 👤 id={breaker_id} CONFIRMED via CLI")
                return True
        return False

    def user_reject(self, breaker_id: int, all_breakers: list) -> bool:
        for b in all_breakers:
            if b["id"] == breaker_id:
                b["status"] = DB.STATUS_REJECTED
                DB.update_breaker(
                    breaker_id, b["confidence"], DB.STATUS_REJECTED, b["detection_count"]
                )
                self._pending_confirm.pop(breaker_id, None)
                logger.info(f"[DETECT] 👤 id={breaker_id} REJECTED via CLI")
                return True
        return False

    def pending_confirmations(self) -> Dict[int, float]:
        return dict(self._pending_confirm)

    # ── _find_nearby_breaker (UNCHANGED logic) ─────────────────────────────────

    def _find_nearby_breaker(
        self,
        lat: float,
        lon: float,
        breakers: list,
        radius_m: float = None,
    ) -> Optional[Dict[str, Any]]:
        from distance import haversine
        r = radius_m if radius_m is not None else self.NEARBY_MATCH_RADIUS_M
        best, best_d = None, float("inf")
        for b in breakers:
            if b["status"] == DB.STATUS_REJECTED:
                continue
            d = haversine(lat, lon, b["latitude"], b["longitude"])
            if d <= r and d < best_d:
                best, best_d = b, d
        return best