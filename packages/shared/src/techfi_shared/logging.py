import json
import logging
import math
import sys
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Event(StrEnum):
    API_ERROR = "api_error"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    WORKER_FAILED = "worker_failed"
    STT_CONNECTED = "stt_connected"
    STT_DISCONNECTED = "stt_disconnected"
    STT_CONNECTION_FAILED = "stt_connection_failed"
    STT_FINALIZED = "stt_finalized"
    STT_TIMEOUT = "stt_timeout"


class LatencyName(StrEnum):
    STT_COMPLETION = "stt_completion_ms"
    LLM_FIRST_TOKEN = "llm_first_token_ms"
    TTS_FIRST_AUDIO = "tts_first_audio_ms"
    SPEECH_TO_AUDIO = "speech_end_to_first_audio_ms"
    SPEECH_TO_OUTPUT = "speech_end_to_output_start_ms"


@dataclass(frozen=True)
class LatencySample:
    session_id: UUID
    turn_index: int
    metric: LatencyName
    duration_ms: float


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Fail closed: arbitrary messages, extras and exception text are never serialized.
        message = record.msg
        if (
            isinstance(message, LatencySample)
            and isinstance(message.session_id, UUID)
            and type(message.turn_index) is int
            and message.turn_index > 0
            and isinstance(message.metric, LatencyName)
            and type(message.duration_ms) in (int, float)
            and math.isfinite(message.duration_ms)
            and message.duration_ms >= 0
        ):
            return json.dumps(
                {
                    "event": "voice_latency",
                    "level": record.levelname,
                    "session_id": str(message.session_id),
                    "turn_index": message.turn_index,
                    "metric": message.metric.value,
                    "duration_ms": round(message.duration_ms, 3),
                }
            )
        event = message.value if isinstance(message, Event) else "suppressed"
        payload = {"event": event, "level": record.levelname}
        session_id = getattr(record, "session_id", None)
        if isinstance(message, Event) and isinstance(session_id, UUID):
            payload["session_id"] = str(session_id)
        partial_count = getattr(record, "partial_count", None)
        if message == Event.STT_FINALIZED and type(partial_count) is int and partial_count >= 0:
            payload["partial_count"] = partial_count
        return json.dumps(payload)


def configure_logging() -> logging.Logger:
    # Suppress dependency logs: provider exceptions can include transcripts and credentials.
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.NullHandler())
    logger = logging.getLogger("techfi")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SafeFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
