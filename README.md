# 🚗 Real-Time Speed Breaker Detection System

A modular Python backend that combines GPS tracking, YOLOv8 computer vision,
and an SQLite confidence system to detect, validate, and alert drivers about
speed breakers in real time.

---

## 📁 Project Structure

```
speed_breaker_detection/
├── main.py          # Entry point & main loop
├── config.py        # All tunable parameters
├── db.py            # SQLite CRUD layer
├── gps.py           # Real NMEA / Simulated GPS
├── distance.py      # Haversine formula
├── detection.py     # OpenCV + YOLOv8 + confidence engine
├── voice.py         # pyttsx3 TTS alerts
├── requirements.txt
└── README.md
```

---

## ⚡ Quick Start

### 1 — Install dependencies
```bash
pip install -r requirements.txt
```

### 2 — (Optional) Prepare a custom YOLOv8 model
Train or download a YOLOv8 model with a `speed_breaker` class and place
the `.pt` file in the project root.  Update `YOLO_MODEL_PATH` in `config.py`.

The system defaults to `yolov8n.pt` (COCO classes); swap `YOLO_TARGET_CLASS`
to an appropriate class if testing with a generic model.

### 3 — Run
```bash
python main.py
```

### CLI flags
```bash
python main.py --stats          # Print DB summary
python main.py --list           # List all known breakers
python main.py --confirm <id>   # Manually confirm a candidate
python main.py --reject  <id>   # Manually reject a candidate
```

---

## ⚙️ Configuration (`config.py`)

| Key | Default | Description |
|-----|---------|-------------|
| `ALERT_DISTANCE_M` | 100 | Voice/terminal alert radius (m) |
| `CAMERA_ON_DISTANCE_M` | 30 | Camera activation radius (m) |
| `MAX_ALERTS_PER_BREAKER` | 2 | Max alerts per breaker per session |
| `ALERT_COOLDOWN_SEC` | 30 | Minimum seconds between repeat alerts |
| `CONFIDENCE_CONFIRM_THRESHOLD` | 0.8 | Auto-confirm at this confidence |
| `CONFIDENCE_INCREMENT` | 0.15 | Confidence boost per positive detection |
| `CONFIDENCE_DECREMENT` | 0.10 | Confidence drop per missed detection |
| `CONFIDENCE_REJECT_THRESHOLD` | 0.1 | Reject candidate below this |
| `GPS_SIMULATE` | `True` | `False` = real GPS hardware |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | Path to YOLOv8 weights |
| `FRAME_SKIP` | 3 | Process every Nth frame |

---

## 🔁 System Flow

```
BOOT
 ├─ Init DB (SQLite)
 ├─ Load known breakers
 ├─ Start GPS thread
 ├─ Load YOLOv8 model
 └─ Start TTS engine

LOOP (every 0.5 s)
 ├─ Get GPS position
 ├─ For each DB breaker:
 │   ├─ [≤100 m]  voice + terminal alert (max 2×, cooldown)
 │   └─ [≤30 m]   turn ON camera
 │       ├─ Capture frame (OpenCV)
 │       ├─ Run YOLOv8
 │       ├─ Detected?
 │       │   ├─ Known breaker → confidence ↑ → confirm at ≥0.8
 │       │   └─ New → add candidate (conf=0.3) → validate
 │       └─ Not detected → confidence ↓ → reject if ≤0.1
 └─ [>30 m]  turn OFF camera
```

---

## 🗄️ Database Schema

```sql
CREATE TABLE speed_breakers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude        REAL    NOT NULL,
    longitude       REAL    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 0.3,
    status          TEXT    NOT NULL DEFAULT 'candidate',  -- candidate|confirmed|rejected
    detection_count INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
```

---

## 🛰️ GPS Modes

| Mode | Setting | Notes |
|------|---------|-------|
| Simulated | `GPS_SIMULATE = True` | Replays `SIMULATED_GPS_PATH` waypoints |
| Real | `GPS_SIMULATE = False` | Reads NMEA from serial port (`GPS_PORT`) |

For real GPS: `pip install pyserial pynmea2`

---

## 🔊 Voice Alerts (pyttsx3)

- Offline / no internet required
- Alert fires at ≤100 m (max 2 times per breaker, 30 s cooldown)
- Announces distance, confirmation events, new candidates
- If pyttsx3 is unavailable, system continues with terminal-only alerts

---

## 📷 Camera Notes

- Requires a webcam or USB camera on `CAMERA_INDEX = 0`
- Camera turns ON only when within 30 m of a known or candidate breaker
- OpenCV overlay shows breaker ID, confidence %, bounding boxes
- Every 3rd frame is sent to YOLO (configurable via `FRAME_SKIP`)

---

## 🧩 Extending the System

- **New alert type**: Add methods to `VoiceEngine` in `voice.py`
- **REST API**: Wrap `SpeedBreakerSystem` with FastAPI in a new `api.py`
- **Map UI**: Query the DB from a frontend that reads `speed_breakers.db`
- **Custom YOLO**: Train on your own speed-breaker dataset with Ultralytics