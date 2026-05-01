from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

_latest = {"lat": 0.0, "lon": 0.0, "speed": 0.0}
_lock = threading.Lock()

_runtime = {
    "lat": 0.0,
    "lon": 0.0,
    "speed": 0.0,
    "max_speed": 0.0,
    "camera_on": False,
    "alert": None,
    "pending_feedback": None,
    "event_log": []
}

def update_runtime(lat, lon, speed, max_speed=0.0):
    _runtime["lat"] = lat
    _runtime["lon"] = lon
    _runtime["speed"] = speed
    _runtime["max_speed"] = max_speed

@app.route("/data", methods=["GET"])
def get_data():
    return jsonify(_runtime)

@app.route("/breakers", methods=["GET"])
def breakers():
    import db
    db.init_db()
    return jsonify(db.load_all_breakers())

@app.route("/gps", methods=["POST"])
def gps():
    data = request.json or {}
    with _lock:
        _latest["lat"] = float(data.get("lat", 0.0))
        _latest["lon"] = float(data.get("lon", 0.0))
        _latest["speed"] = float(data.get("speed", 0.0))
    return jsonify({"ok": True})

def get_latest():
    with _lock:
        return _latest["lat"], _latest["lon"], _latest["speed"]

def start_server():
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5050, debug=False),
        daemon=True
    ).start()

    