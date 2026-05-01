# ============================================================
# config.py — Central configuration for Speed Breaker Detection
# ============================================================

# --- Database ---
DB_PATH = "speed_breakers.db"

# --- Detection Thresholds ---
ALERT_DISTANCE_M = 100          # Distance (m) to trigger voice/terminal alert for CONFIRMED breakers
MAX_ALERTS_PER_BREAKER = 2      # Max voice alerts per breaker per session
ALERT_COOLDOWN_SEC = 30         # Seconds between repeated alerts for same breaker

# MAY202601 change — OLD camera-on distance (single fixed 30m radius) replaced
# with two separate radii below (validation scan vs old proximity trigger).
# OLD: CAMERA_ON_DISTANCE_M = 30
# MAY202601 change — NEW: camera activates at 150m from a known candidate/breaker
#   for the validation scan pass, which is much more practical for real driving.
CAMERA_SCAN_DISTANCE_M = 150    # MAY202601 change — Camera ON when ≤ 150m from existing candidate/confirmed

# --- Confidence Thresholds ---
INITIAL_CANDIDATE_CONFIDENCE = 0.25  # MAY202601 change — weak candidate confidence on speed-drop (was 0.3)
# OLD: INITIAL_CANDIDATE_CONFIDENCE = 0.3
CONFIDENCE_CONFIRM_THRESHOLD = 0.80  # Confirm breaker at this confidence
CONFIDENCE_INCREMENT_YOLO    = 0.15  # MAY202601 change — renamed from CONFIDENCE_INCREMENT for clarity
# OLD: CONFIDENCE_INCREMENT = 0.15
CONFIDENCE_INCREMENT_YOLO_STRONG = 0.25  # MAY202601 change — stronger YOLO boost when repeated detection
# (used when detection_count > 1 for same location)
CONFIDENCE_DECREMENT = 0.10           # Confidence decrease on no-detection
CONFIDENCE_REJECT_THRESHOLD = 0.10   # Reject candidate below this confidence

# User-feedback confidence delta constants
CONFIDENCE_USER_CONFIRM_BOOST = 0.40  # +0.40 when user answers "yes, there is a breaker"
CONFIDENCE_USER_REJECT_DROP   = 0.40  # −0.40 when user answers "no, there is no breaker"

# --- Camera / YOLO ---
CAMERA_INDEX = 0                     # OpenCV camera index
YOLO_MODEL_PATH = "models/speed_breaker.pt"       # YOLOv8 model (nano for speed)
YOLO_CONFIDENCE = 0.4                # Minimum YOLO detection confidence
YOLO_TARGET_CLASS = "speed_breaker"  # Target class name (custom model)
# MAY202601 change — FRAME_SKIP removed from camera read loop (snapshot mode now used).
# OLD: FRAME_SKIP = 3
# MAY202601 change — NEW: camera warmup frames — discard first N frames after opening
#   so the sensor can auto-expose before we send a frame to YOLO.
CAMERA_WARMUP_FRAMES = 5    # MAY202601 change — discard these frames on camera open for smooth startup
CAMERA_FRAME_TIMEOUT_SEC = 3.0  # MAY202601 change — max seconds to wait for a valid frame

# --- GPS ---
GPS_SIMULATE = True                  # True = use simulated GPS path
GPS_PORT = "/dev/ttyUSB0"            # Serial port for real GPS
GPS_BAUD = 9600

# --- Speed-drop detection parameters ---
SPEED_WINDOW_SEC         = 5    # Rolling window length (seconds) for speed history
MIN_SPEED_FOR_DROP_KMPH  = 60   # max speed in window must be >= this to count
MAX_CURRENT_SPEED_KMPH   = 45   # current speed must be <= this to count
MIN_SPEED_DROP_KMPH      = 25   # (max_in_window − current_speed) must be >= this
SPEED_DROP_CHECK_RADIUS_M = 100 # radius (m) to search DB after speed drop detected

# MAY202601 change — speed-drop cooldown: after one drop fires, suppress further
#   drop-triggers for this many seconds so we don't save duplicate weak candidates.
# OLD: no such cooldown existed
SPEED_DROP_COOLDOWN_SEC = 8    # MAY202601 change — min seconds between consecutive drop events

# --- Simulation path ---
# MAY202601 change — Completely redesigned simulation path for practical showcase.
#
# Scenario:
#   Phase 1 : Start ~800m before the pre-loaded confirmed breaker at (28.6165, 77.2065).
#             Vehicle cruises at 65-70 km/h → alert fires at 100m from it.
#   Phase 2 : Vehicle passes the confirmed breaker and continues.
#   Phase 3 : Vehicle encounters an UNKNOWN location (XYZ) and brakes hard.
#             Speed drop fires → weak candidate saved (no camera, no YOLO).
#             User is asked yes/no → if YES, confidence boosted to strong candidate.
#   Phase 4 : Vehicle loops back and approaches the same XYZ from 500m away.
#             Camera opens at 150m → YOLO runs → confidence updated → may confirm.
#
# Each waypoint: (latitude, longitude, speed_kmph)
#
# OLD simulation path (kept for reference):
# SIMULATED_GPS_PATH = [
#     (28.6139, 77.2090, 65),
#     (28.6145, 77.2085, 67),
#     (28.6150, 77.2080, 68),
#     (28.6155, 77.2075, 70),
#     (28.6160, 77.2070, 66),
#     (28.6165, 77.2065, 62),
#     (28.6170, 77.2060, 55),
#     (28.6175, 77.2055, 42),
#     (28.6180, 77.2050, 38),
#     (28.6185, 77.2045, 35),
#     (28.6190, 77.2040, 30),
#     (28.6195, 77.2035, 38),
#     (28.6200, 77.2030, 50),
#     (28.6205, 77.2025, 60),
#     (28.6210, 77.2020, 65),
# ]

# MAY202601 change — NEW practical simulation path
# Pre-loaded confirmed breaker in DB: (28.6165, 77.2065)
# XYZ unknown location: (28.6230, 77.2010)
SIMULATED_GPS_PATH = [
    # ── PHASE 1: Approach the known confirmed breaker ──────────────────────────
    # Start ~800m before confirmed breaker at (28.6165, 77.2065)
    (28.6090, 77.2130, 65),   # P01 — 800m+ away, cruising 65 km/h
    (28.6100, 77.2120, 67),   # P02
    (28.6110, 77.2110, 68),   # P03
    (28.6120, 77.2105, 70),   # P04 — peak speed 70 km/h
    (28.6130, 77.2095, 70),   # P05
    (28.6140, 77.2085, 69),   # P06 — ~300m from known breaker
    (28.6150, 77.2078, 66),   # P07 — ~180m, entering alert zone soon
    (28.6157, 77.2073, 63),   # P08 — ~120m, alert zone entered (≤100m fires next step)
    (28.6163, 77.2068, 58),   # P09 — ~60m from breaker, ALERT fires (confirmed breaker)
    # ── PHASE 2: Pass the known breaker, accelerate away ──────────────────────
    (28.6165, 77.2065, 35),   # P10 — AT confirmed breaker, driver slows naturally
    (28.6168, 77.2062, 40),   # P11 — past breaker, speeding up
    (28.6172, 77.2058, 50),   # P12
    (28.6177, 77.2053, 58),   # P13
    (28.6183, 77.2047, 65),   # P14 — back to cruise speed
    (28.6190, 77.2040, 68),   # P15
    (28.6197, 77.2033, 70),   # P16 — peak speed again
    # ── PHASE 3: Sudden brake at XYZ (unknown location, no DB entry) ──────────
    (28.6205, 77.2025, 69),   # P17
    (28.6213, 77.2018, 66),   # P18
    (28.6220, 77.2014, 62),   # P19 — driver starts braking
    (28.6225, 77.2012, 52),   # P20 — braking harder
    (28.6230, 77.2010, 40),   # P21 — SPEED DROP: max=70, cur=40, drop=30 → fires!
                               #        No DB entry within 100m → weak candidate saved
                               #        User prompt: yes/no
    (28.6233, 77.2009, 38),   # P22 — still slow at XYZ
    (28.6235, 77.2008, 42),   # P23
    # ── PHASE 4: Vehicle drives away then loops back toward XYZ ───────────────
    (28.6238, 77.2006, 50),   # P24 — accelerating away from XYZ
    (28.6242, 77.2003, 60),   # P25
    (28.6246, 77.2000, 68),   # P26
    (28.6248, 77.1998, 70),   # P27 — peak speed
    # Reverse back toward XYZ (simulation will reverse path direction)
    # The reversing logic in SimulatedGPS will retrace waypoints in reverse,
    # so vehicle heads back toward XYZ — camera opens at 150m from candidate.
]

GPS_STEP_INTERVAL_SEC = 2.0   # Seconds between GPS updates in simulation

# --- Voice ---
VOICE_RATE = 160                     # Words per minute for TTS
VOICE_VOLUME = 1.0                   # TTS volume (0.0 – 1.0)

# --- Logging ---
LOG_LEVEL = "INFO"                   # DEBUG / INFO / WARNING / ERROR