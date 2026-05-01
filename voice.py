# ============================================================
# voice.py — Text-to-Speech Alert Engine (pyttsx3)
# ============================================================

import logging
import threading
import queue
from typing import Optional
from config import VOICE_RATE, VOICE_VOLUME

logger = logging.getLogger(__name__)


class VoiceEngine:
    """
    Thread-safe TTS engine wrapper around pyttsx3.

    pyttsx3 is NOT thread-safe, so we run it in a dedicated
    background thread that drains an internal message queue.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[Optional[str]] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._engine = None
        self._available = False

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> None:
        """Initialise pyttsx3 and start the worker thread."""
        try:
            import pyttsx3  # type: ignore
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", VOICE_RATE)
            self._engine.setProperty("volume", VOICE_VOLUME)
            # Try to pick a clear voice if available
            voices = self._engine.getProperty("voices")
            if voices:
                self._engine.setProperty("voice", voices[0].id)
            self._available = True
            logger.info("[VOICE] pyttsx3 engine initialised ✓")
        except Exception as exc:
            logger.warning(f"[VOICE] pyttsx3 unavailable ({exc}) — voice alerts disabled")
            self._available = False
            return

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker to exit."""
        if self._available:
            self._q.put(None)           # sentinel
            if self._thread:
                self._thread.join(timeout=3)
        logger.info("[VOICE] Engine stopped")

    # ── Public API ─────────────────────────────────────────

    def speak(self, text: str) -> None:
        """Queue a message for speech (non-blocking)."""
        if not self._available:
            logger.debug(f"[VOICE] (disabled) would say: {text!r}")
            return
        logger.info(f"[VOICE] 🔊 Speaking: {text!r}")
        self._q.put(text)

    def alert_speed_breaker(self, distance_m: float) -> None:
        """Pre-formatted speed-breaker proximity alert."""
        dist = int(distance_m)
        self.speak(
            f"Attention! Speed breaker ahead in {dist} metres. Please slow down."
        )

    def alert_confirmed(self, breaker_id: int) -> None:
        self.speak(f"Speed breaker {breaker_id} has been confirmed and saved.")

    def alert_new_candidate(self) -> None:
        self.speak("Possible new speed breaker detected. Monitoring for confirmation.")

    # ── Worker ─────────────────────────────────────────────

    def _worker(self) -> None:
        while True:
            msg = self._q.get()
            if msg is None:             # sentinel → shutdown
                break
            try:
                self._engine.say(msg)
                self._engine.runAndWait()
            except Exception as exc:
                logger.error(f"[VOICE] TTS error: {exc}")