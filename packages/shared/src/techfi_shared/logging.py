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
    STT_AMBIGUOUS = "stt_ambiguous_boundary"
    SESSION_CONFIGURATION = "session_configuration"
    WORKER_CONFIGURATION = "worker_configuration"
    STT_FINAL_RECEIVED = "stt_final_received"


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


class TurnStage(StrEnum):
    SPEECH_END = "estimated_speech_end"
    VAD_STOP = "vad_stop"
    ENDPOINT = "stt_endpoint_handoff"
    ENDPOINT_SENT = "stt_endpoint_sent"
    TURN_COMPLETE = "turn_strategy_complete"
    INTERIM = "stt_first_interim"
    FINAL = "stt_final"
    LLM_REQUEST = "llm_request"
    LLM_START = "llm_start"
    LLM_TEXT = "llm_first_text"
    LLM_END = "llm_response_end"
    TTS_INPUT = "tts_first_text_delivered"
    TTS_READY = "tts_first_text_chunk"
    TTS_REQUEST = "tts_request"
    TTS_AUDIO = "tts_first_audio"
    TRANSPORT = "audio_transport_handoff"
    CANCELLED = "turn_cancelled"
    WINDOW_CLOSED = "timing_window_closed"
    FINAL_TIMEOUT = "stt_final_timeout"


@dataclass(frozen=True)
class TurnTiming:
    session_id: UUID
    turn_index: int
    stage: TurnStage
    at_ms: float
    since_speech_end_ms: float | None = None
    reason: "TurnReason | None" = None


class TurnReason(StrEnum):
    NEXT_INPUT = "new_speech_after_response"
    RESUMED_BEFORE_FINAL = "speech_resumed_before_final"
    SERVER_BARGE_IN = "speech_while_server_bot_active"
    INFERENCE_INTERRUPTED = "speech_during_llm_generation"
    NEW_INPUT = "new_speech_before_response"
    INTERRUPTION = "interruption_frame"
    SESSION_END = "session_end"
    PIPELINE_CANCEL = "pipeline_cancel"


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Fail closed: arbitrary messages, extras and exception text are never serialized.
        message = record.msg
        if (
            isinstance(message, TurnTiming)
            and isinstance(message.session_id, UUID)
            and type(message.turn_index) is int
            and message.turn_index > 0
            and isinstance(message.stage, TurnStage)
            and type(message.at_ms) in (int, float)
            and math.isfinite(message.at_ms)
            and message.at_ms >= 0
            and (message.reason is None or isinstance(message.reason, TurnReason))
            and (
                message.since_speech_end_ms is None
                or (
                    type(message.since_speech_end_ms) in (int, float)
                    and math.isfinite(message.since_speech_end_ms)
                )
            )
        ):
            return json.dumps(
                {
                    "event": "voice_turn_timing",
                    "level": record.levelname,
                    "session_id": str(message.session_id),
                    "turn_index": message.turn_index,
                    "stage": message.stage.value,
                    "at_ms": round(message.at_ms, 3),
                    **({"reason": message.reason.value} if message.reason is not None else {}),
                    **(
                        {"since_speech_end_ms": round(message.since_speech_end_ms, 3)}
                        if message.since_speech_end_ms is not None
                        else {}
                    ),
                }
            )
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
        if isinstance(message, Event) and message == Event.STT_FINAL_RECEIVED:
            count = getattr(record, "final_event_index", None)
            if type(count) is int and count > 0:
                payload["final_event_index"] = count
        if isinstance(message, Event) and message == Event.STT_AMBIGUOUS:
            reason = getattr(record, "boundary_reason", None)
            if type(reason) is str and reason in {
                "speech_resumed_before_final",
                "final_without_pending_endpoint",
            }:
                payload["boundary_reason"] = reason
        if isinstance(message, Event) and message in (
            Event.SESSION_CONFIGURATION,
            Event.WORKER_CONFIGURATION,
        ):
            # Fixed profile names only: never serialize arbitrary model names or settings.
            allowed = {
                "stt_profile": {"sarvam_realtime_v4", "sarvam_legacy", "openai_whisper", "other"},
                "llm_profile": {"openai_gpt_4_1_mini", "sarvam_105b_conversations", "other"},
                "tts_profile": {
                    "sarvam_bulbul_v3",
                    "sarvam_bulbul_v4_flash",
                    "google_chirp3_hd",
                    "other",
                },
                "stream_type": {"balanced", "fast", "not_applicable"},
                "endpointing": {"manual", "not_applicable"},
            }
            for key, values in allowed.items():
                value = getattr(record, key, None)
                if type(value) is str and value in values:
                    payload[key] = value
            fenced = getattr(record, "transcript_fence", None)
            if type(fenced) is bool:
                payload["transcript_fence"] = fenced
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
