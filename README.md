🧠 Speed Breaker Detection System

A real-time intelligent system that detects speed breakers using vehicle speed patterns, GPS data, computer vision (YOLO), and human feedback.

🚗 Features
⚡ Detects sudden speed drops
📍 Stores GPS-based breaker locations
🧠 Confidence-based learning system
📷 Camera + YOLO verification
👤 Human-in-the-loop feedback (YES / NO)
🗺️ Live dashboard with map visualization
🔊 Voice alerts for driver safety
🏗️ System Architecture
Vehicle / Phone GPS
        ↓
Speed Drop Detection
        ↓
Candidate Creation (DB)
        ↓
Camera + YOLO Verification
        ↓
User Feedback (YES / NO)
        ↓
Confidence Update
        ↓
Confirmed Speed Breaker
        ↓
Alert System
🛠️ Tech Stack
Python (Core logic)
OpenCV (Camera)
Ultralytics YOLOv8 (Detection)
Flask (REST API)
SQLite (Database)
Leaflet.js (Map UI)
HTML/CSS/JavaScript (Frontend)
📂 Project Structure
speed-breaker-detection/
│
├── main.py
├── gps_server.py
├── detection.py
├── db.py
├── config.py
├── dashboard.html
├── requirements.txt
▶️ How to Run
1. Install dependencies
pip install -r requirements.txt
2. Run backend
python main.py
3. Open dashboard
http://127.0.0.1:5500/dashboard.html

(or open file directly)

📊 API Endpoints
GET  /data       → live GPS + speed
GET  /breakers   → all stored breakers
POST /feedback   → user YES/NO
POST /action     → manual confirm/reject
POST /reset      → reset database
📦 Dataset & Model

⚠️ Dataset and trained YOLO model are not included due to size.

You can:

Train your own model
Or use sample datasets
🎯 Use Cases
ADAS systems (driver assistance)
Smart navigation systems
Road safety analytics
Autonomous vehicle preprocessing
🚧 Challenges Solved
Differentiating traffic vs speed breaker
GPS noise handling
Real-time processing
Reducing false positives
🚀 Future Scope
Cloud-based database (PostGIS)
Mobile app integration
Automatic braking system
Integration with smart city infrastructure
