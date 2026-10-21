import asyncio
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from loguru import logger
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
from pydantic import ValidationError
from techfi_shared.contracts import CallOutcome
from techfi_shared.errors import ConfigurationError
from techfi_shared.logging import SafeFormatter
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import (
    Services,
    _stt,
    create_services,
    validate_provider_settings,
)
from techfi_voice.latency import LatencyObserver
from techfi_voice.monitor import SessionMonitor
from techfi_voice.realtime_stt import FinalTranscriptGate
from techfi_voice.runtime import run_session
from techfi_voice.session import build_session
from test_foundations import voice_settings
from test_voice_pipeline import Forward, Transport
from websockets.protocol import State

logger.remove()


def realtime_settings(**overrides):
    return voice_settings(
        **({"stt_provider": "sarvam-realtime", "stt_model": "saaras:v4"} | overrides)
    )


def test_realtime_constructor_uses_supported_settings(monkeypatch):
    constructor = MagicMock()
    constructor.Settings = SarvamRealtimeSTTService.Settings
    monkeypatch.setattr(
        "techfi_voice.integrations.sarvam.ConfiguredSarvamRealtimeSTTService", constructor
    )
    monkeypatch.setattr("techfi_voice.integrations.providers._llm", MagicMock())
    monkeypatch.setattr("techfi_voice.integrations.providers._tts", MagicMock())
    services = create_services(realtime_settings(stt_mode="codemix"))
    options = constructor.call_args.kwargs
    assert services.require_finalized_transcripts
    assert options["sample_rate"] == 16000
    assert options["endpointing"] == "manual"
    assert options["settings"].model == "saaras:v4"
    assert options["settings"].language_code == "hi-IN"
    assert options["settings"].stream_type == "balanced"
    assert options["settings"].mode == "codemix"
    assert options["keepalive_timeout"] == 10
    assert "reconnect_on_error" not in options  # explicitly rejected by pinned Pipecat API


def test_sarvam_credentials_are_used_only_for_sarvam_roles(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=synthetic-openai\nSARVAM_API_KEY=synthetic-sarvam\n")
    values = realtime_settings().model_dump()
    for role in ("stt", "llm", "tts"):
        values.pop(f"{role}_api_key")
    settings = VoiceSettings(_env_file=env, **values)
    assert settings.stt_api_key.get_secret_value() == "synthetic-sarvam"
    assert settings.llm_api_key.get_secret_value() == "synthetic-openai"
    assert settings.tts_api_key.get_secret_value() == "synthetic-sarvam"
    assert settings.llm_provider == "openai" and settings.tts_provider == "sarvam"
    assert voice_settings().stt_provider == "sarvam"  # existing adapter still available
    assert VoiceSettings.model_fields["stt_provider"].default == "sarvam-realtime"


@pytest.mark.parametrize(
    "override",
    [
        {"stt_model": "whisper-1"},
        {"stt_model": "saaras:v4-realtime"},
        {"stt_model": "saaras:v3-realtime"},
        {"stt_mode": "translate"},
    ],
)
def test_unsupported_realtime_model_or_mode_rejected(override):
    with pytest.raises(ConfigurationError):
        validate_provider_settings(realtime_settings(**override))


@pytest.mark.parametrize("override", [{"stt_stream_type": "invalid"}, {"stt_keepalive_seconds": 0}])
def test_realtime_tuning_is_validated(override):
    with pytest.raises(ValidationError):
        realtime_settings(**override)


def test_streams_pcm_chunks_on_one_socket_and_flushes_tail_before_boundary(monkeypatch):
    async def run():
        service = _stt(realtime_settings())
        service._sample_rate = 16000
        service._websocket = SimpleNamespace(state=State.OPEN)
        assert service._query_params()["encoding"] == "linear16"
        assert service._query_params()["sample_rate"] == 16000
        assert service._reconnect_on_error is False
        calls = []
        service._connect = AsyncMock(side_effect=AssertionError("must not reconnect per chunk"))

        async def send_audio(audio):
            calls.append(("audio", len(audio)))

        async def send_json(message):
            calls.append(("event", message["event"]))
            return True

        monkeypatch.setattr(service, "_send_audio_chunk", send_audio)
        monkeypatch.setattr(service, "_send_json", send_json)
        monkeypatch.setattr("pipecat.services.stt_service.STTService.process_frame", AsyncMock())
        await service.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        for audio in (bytes(640), bytes(960), bytes(320)):
            async for _ in service.run_stt(audio):
                pass
        assert calls == [("event", "speech_start"), ("audio", 1600)]
        await service.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert calls[-2:] == [("audio", 320), ("event", "speech_end")]
        assert not service._audio_buffer
        service._connect.assert_not_awaited()
        assert not any(call == ("event", "flush") for call in calls)

    asyncio.run(run())


def test_native_partial_and_final_events_are_gated_without_logging_content(monkeypatch):
    stream = io.StringIO()
    safe_logger = logging.Logger("test")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter())
    safe_logger.addHandler(handler)
    monkeypatch.setattr(
        "techfi_voice.realtime_stt.logging", SimpleNamespace(getLogger=lambda name: safe_logger)
    )

    async def run():
        service = _stt(realtime_settings())
        gate = FinalTranscriptGate(uuid4())
        gate.push_frame = AsyncMock()
        emitted = []

        async def receive(frame, *args):
            emitted.append(frame)
            await gate.process_frame(frame, FrameDirection.DOWNSTREAM)

        service.push_frame = receive
        service._trace_transcription = AsyncMock()
        await service._handle_partial_transcript({"text": "private partial"})
        await gate.process_frame(
            TranscriptionFrame("private uncommitted", "", ""), FrameDirection.DOWNSTREAM
        )
        assert gate.push_frame.await_count == 0
        await service._handle_final_transcript(
            {"text": "private final", "request_id": "private-id"}
        )
        assert isinstance(emitted[0], InterimTranscriptionFrame)
        assert emitted[1].finalized
        assert gate.push_frame.await_count == 1
        assert gate.push_frame.call_args.args[0].text == "private final"
        record = json.loads(stream.getvalue())
        assert record["event"] == "stt_finalized" and record["partial_count"] == 1
        assert "private" not in stream.getvalue()

    asyncio.run(run())


def test_partial_and_unfinalized_results_do_not_cancel_finalization_timeout():
    async def run():
        fail, timeout = AsyncMock(), AsyncMock()
        monitor = SessionMonitor(
            0.01, fail, AsyncMock(), require_finalized_transcripts=True, on_stt_timeout=timeout
        )
        for frame in (
            VADUserStoppedSpeakingFrame(),
            InterimTranscriptionFrame("partial", "", ""),
            TranscriptionFrame("not-final", "", ""),
        ):
            await monitor.on_push_frame(SimpleNamespace(frame=frame))
        await asyncio.sleep(0.03)
        fail.assert_awaited_once()
        timeout.assert_awaited_once()
        fail.reset_mock()
        await monitor.on_push_frame(SimpleNamespace(frame=VADUserStoppedSpeakingFrame()))
        await monitor.on_push_frame(
            SimpleNamespace(frame=TranscriptionFrame("final", "", "", finalized=True))
        )
        await asyncio.sleep(0.03)
        fail.assert_not_awaited()
        await monitor.cleanup()

    asyncio.run(run())


def test_latency_uses_finalized_result_only():
    async def run():
        stt, llm = object(), object()
        observer = LatencyObserver(
            uuid4(), stt, llm, object(), object(), lambda: 0, require_finalized_transcripts=True
        )
        observer.emit = MagicMock()
        for frame, source, timestamp in (
            (VADUserStoppedSpeakingFrame(), stt, 100),
            (TranscriptionFrame("uncommitted", "", ""), stt, 200),
            (InterimTranscriptionFrame("partial", "", ""), stt, 300),
        ):
            await observer.on_push_frame(
                SimpleNamespace(frame=frame, source=source, timestamp=timestamp)
            )
        assert observer.transcript_end is None
        await observer.on_push_frame(
            SimpleNamespace(
                frame=TranscriptionFrame("final", "", "", finalized=True), source=stt, timestamp=400
            )
        )
        await observer.on_push_frame(
            SimpleNamespace(frame=LLMFullResponseStartFrame(), source=llm, timestamp=500)
        )
        assert observer.transcript_end == 400
        await observer.cleanup()

    asyncio.run(run())


def test_real_pipeline_does_not_invoke_llm_until_finalized_transcript():
    class FakeRealtimeSTT(Forward):
        def __init__(self):
            super().__init__()
            for event in ("on_connected", "on_disconnected", "on_connection_error"):
                self._register_event_handler(event)

    async def run():
        received = []

        class MockLLM(Forward):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, LLMContextFrame):
                    received.append([dict(message) for message in frame.context.messages])
                    session.stop.set()

        settings = realtime_settings(turn_silence_seconds=0.1, max_session_seconds=3)
        stt = FakeRealtimeSTT()
        session = build_session(
            settings, Transport(), lambda _: Services(stt, MockLLM(), Forward(), True)
        )

        @session.task.event_handler("on_pipeline_started")
        async def started(worker, frame):
            await worker.queue_frames(
                [
                    VADUserStartedSpeakingFrame(),
                    VADUserStoppedSpeakingFrame(stop_secs=0.1),
                    InterimTranscriptionFrame("synthetic partial", "", ""),
                    TranscriptionFrame("synthetic uncommitted", "", ""),
                ]
            )
            await asyncio.sleep(0.25)
            assert received == []
            await worker.queue_frame(TranscriptionFrame("synthetic final", "", "", finalized=True))

        result = await run_session(session, settings)
        assert result.outcome == CallOutcome.COMPLETED
        assert received == [[{"role": "user", "content": "synthetic final"}]]
        assert session.context.messages == []

    asyncio.run(run())


def test_connection_failure_and_unusable_state_fail_session_without_reconnect(monkeypatch):
    async def run():
        for fail_connect in (True, False):
            settings = realtime_settings()
            stt = _stt(settings)
            factory = MagicMock(return_value=Services(stt, Forward(), Forward(), True))
            session = build_session(settings, Transport(), factory)
            if fail_connect:
                stt._websocket_connect = AsyncMock(
                    side_effect=ConnectionError("private credential")
                )
                stt.push_error = AsyncMock()
                await stt._connect_websocket()
            else:
                await stt.set_usable(False)  # Covers a clean-but-unexpected receive loop exit.
            await asyncio.wait_for(session.stop.wait(), 0.2)
            assert session.failed and session.outcome == CallOutcome.FAILED
            assert stt._reconnect_on_error is False
            factory.assert_called_once()

    asyncio.run(run())


def test_socket_cleanup_closes_and_clears_reference():
    async def run():
        stt = _stt(realtime_settings())
        socket = SimpleNamespace(close=AsyncMock())
        stt._websocket = socket
        await stt._disconnect_websocket()
        socket.close.assert_awaited_once()
        assert stt._websocket is None

    asyncio.run(run())
