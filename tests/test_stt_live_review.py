"""Regression cases inferred from timing-only live traces; all providers mocked."""

import asyncio
import json
import logging
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from techfi_shared.contracts import CallOutcome
from techfi_shared.logging import Event, SafeFormatter, TurnStage
from techfi_voice.integrations.providers import Services, _stt, session_profile
from techfi_voice.latency import LatencyObserver
from techfi_voice.runtime import run_session
from techfi_voice.session import build_session
from test_foundations import voice_settings
from test_sarvam_realtime import realtime_settings
from test_turn_timing import capture
from test_voice_pipeline import Forward
from websockets.protocol import State


def test_profile_identifies_whisper_instead_of_mislabeling_it_realtime():
    profile = session_profile(voice_settings(stt_provider="openai", stt_model="whisper-1"))
    assert profile["stt_profile"] == "openai_whisper"
    assert profile["endpointing"] == "not_applicable"
    assert profile["stream_type"] == "not_applicable"
    record = logging.LogRecord("test", 20, "", 1, Event.SESSION_CONFIGURATION, (), None)
    record.__dict__.update(
        profile,
        session_id=uuid4(),
        transcript_fence=True,
        credentials="private",
        llm_profile="private-model",
    )
    output = SafeFormatter().format(record)
    assert "private" not in output
    assert json.loads(output)["stt_profile"] == "openai_whisper"


@pytest.mark.parametrize("stream_type", ["balanced", "fast"])
def test_stream_profile_does_not_change_endpointing_or_client_audio_chunks(stream_type):
    service = _stt(realtime_settings(stt_stream_type=stream_type))
    service._sample_rate = 16000
    params = service._query_params()
    assert params["stream_type"] == stream_type
    assert params["endpointing"] == "manual"
    assert params["encoding"] == "linear16"
    assert service._chunk_size_bytes() == 1600
    assert not {"silence_duration_ms", "threshold", "min_speech_duration_ms"} & params.keys()


@pytest.mark.parametrize("connected", [True, False])
def test_endpoint_sent_means_successful_native_socket_send(monkeypatch, connected):
    async def run():
        service = _stt(realtime_settings())
        service._sample_rate = 16000
        service._websocket = SimpleNamespace(
            state=State.OPEN if connected else State.CLOSED, send=AsyncMock()
        )
        timing = SimpleNamespace(mark=AsyncMock())
        # mark is synchronous; retain only numeric correlation in this fake sink.
        calls = []
        timing.mark = lambda stage, owner: calls.append((stage, owner))
        service.turn_timing = timing
        service.push_error = AsyncMock()
        monkeypatch.setattr("pipecat.services.stt_service.STTService.process_frame", AsyncMock())
        stop = VADUserStoppedSpeakingFrame()
        stop.metadata["techfi_turn_index"] = 9
        await service.process_frame(stop, FrameDirection.UPSTREAM)
        if connected:
            assert calls == [(TurnStage.ENDPOINT_SENT, 9)]
            sent = json.loads(service._websocket.send.call_args.args[0])
            assert sent == {"event": "speech_end"}
            service.push_error.assert_not_awaited()
        else:
            assert calls == []
            service.push_error.assert_awaited_once_with("STT endpoint delivery failed")

    asyncio.run(run())


@pytest.mark.parametrize("bot_active", [False, True])
def test_completed_window_closure_is_not_automatically_a_cancellation(monkeypatch, bot_active):
    output = capture(monkeypatch)

    async def run():
        user, stt, llm, tts, transport = (FrameProcessor() for _ in range(5))
        llm.timing_turn = ContextVar("synthetic-owner", default=1)
        timing = LatencyObserver(uuid4(), stt, llm, tts, transport, lambda: 2_000_000_000)
        timing.bind(user, SpeechTimeoutUserTurnStopStrategy())
        await user._call_event_handler("on_before_push_frame", VADUserStartedSpeakingFrame())
        await user._call_event_handler("on_before_push_frame", VADUserStoppedSpeakingFrame())
        timing.stt_frame(TranscriptionFrame("synthetic", "", "", finalized=True))
        await llm._call_event_handler("on_before_push_frame", LLMFullResponseStartFrame())
        await llm._call_event_handler("on_before_push_frame", LLMFullResponseEndFrame())
        timing.tts_request("opaque", 1)
        timing.audio(TTSAudioRawFrame(b"\x00\x00", 24000, 1, context_id="opaque"), 2_000_000_000)
        if bot_active:
            await transport._call_event_handler("on_before_push_frame", BotStartedSpeakingFrame())
        await user._call_event_handler("on_before_push_frame", VADUserStartedSpeakingFrame())
        await timing.cleanup()

    asyncio.run(run())
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    terminal = [
        row for row in rows if row.get("stage") in {"turn_cancelled", "timing_window_closed"}
    ]
    assert len(terminal) == 1
    assert terminal[0]["stage"] == ("turn_cancelled" if bot_active else "timing_window_closed")
    assert terminal[0]["reason"] == (
        "speech_while_server_bot_active" if bot_active else "new_speech_after_response"
    )


@pytest.mark.parametrize("segmented", [True, False])
@pytest.mark.parametrize("overlap", [True, False])
def test_late_or_overlapping_results_cannot_reach_llm_history_or_opt_out(
    monkeypatch, segmented, overlap
):
    from pipecat.processors.aggregators import llm_response_universal

    users = []
    original = llm_response_universal.LLMContextAggregatorPair

    def capture_user(*args, **kwargs):
        pair = original(*args, **kwargs)
        users.append(pair.user())
        return pair

    monkeypatch.setattr(llm_response_universal, "LLMContextAggregatorPair", capture_user)

    class Segmented(SegmentedSTTService):
        async def run_stt(self, audio):
            yield None  # Native segment queue executes; no external request.

    class Realtime(Forward):
        def __init__(self):
            super().__init__()
            for name in ("on_connected", "on_disconnected", "on_connection_error"):
                self._register_event_handler(name)

    async def run():
        calls, dispatched, first_audio = [], asyncio.Event(), asyncio.Event()

        class LLM(Forward):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, LLMContextFrame):
                    calls.append([dict(message) for message in frame.context.messages])
                    dispatched.set()
                    await self.push_frame(TTSAudioRawFrame(b"\x01\x00" * 10, 24000, 1))

        class Output(Forward):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TTSAudioRawFrame):
                    first_audio.set()

        stt = Segmented() if segmented else Realtime()
        settings = voice_settings(
            stt_provider="openai" if segmented else "sarvam-realtime",
            stt_model="whisper-1" if segmented else "saaras:v4",
            turn_silence_seconds=0.01,
            max_session_seconds=2,
        )
        transport = SimpleNamespace(input=lambda: Forward(), output=lambda: Output())
        session = build_session(
            settings, transport, lambda _: Services(stt, LLM(), Forward(), not segmented)
        )

        @session.task.event_handler("on_pipeline_started")
        async def started(worker, frame):
            controller = SimpleNamespace(
                _vad_analyzer=SimpleNamespace(params=SimpleNamespace(start_secs=0.2, stop_secs=0.4))
            )
            await users[0]._on_vad_speech_started(controller)
            await asyncio.sleep(0.02)
            await users[0]._on_vad_speech_stopped(controller)
            if overlap:
                # The live trace's 4→5 and 7→8 pattern, before the older final arrives.
                await users[0]._on_vad_speech_started(controller)
            await stt.push_frame(
                TranscriptionFrame("synthetic accepted input", "", "", finalized=True)
            )
            if not overlap:
                await asyncio.wait_for(dispatched.wait(), 1)
                await asyncio.wait_for(first_audio.wait(), 1)
            # An extra final must never cause an opt-out or another LLM call.
            await stt.push_frame(TranscriptionFrame("band karo", "", "", finalized=True))
            await asyncio.sleep(0.03)
            assert len(calls) == (0 if overlap else 1)
            assert all("band karo" not in str(message) for message in session.context.messages)
            assert not session.opted_out

        result = await run_session(session, settings)
        assert result.outcome == CallOutcome.FAILED
        assert len(calls) == (0 if overlap else 1)
        assert not session.opted_out
        assert session.context.messages == []

    asyncio.run(run())
