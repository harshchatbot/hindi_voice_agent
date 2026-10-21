import asyncio
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    STTMetadataFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from techfi_shared.logging import SafeFormatter, TurnStage, TurnTiming
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.timing import TURN_KEY, CorrelatedLLMMixin
from techfi_voice.latency import LatencyObserver
from techfi_voice.realtime_stt import FinalTranscriptGate


def capture(monkeypatch):
    output = io.StringIO()
    logger = logging.Logger("timing-test")
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeFormatter())
    logger.addHandler(handler)
    monkeypatch.setattr("techfi_voice.latency.logging", SimpleNamespace(getLogger=lambda _: logger))
    return output


def test_current_stack_defaults_and_configurable_deadline():
    for name, value in {
        "stt_provider": "sarvam-realtime",
        "stt_model": "saaras:v4",
        "llm_model": "gpt-4.1-mini",
        "tts_provider": "sarvam",
        "tts_model": "bulbul:v3",
    }.items():
        assert VoiceSettings.model_fields[name].default == value
    assert VoiceSettings.model_fields["stt_final_timeout_seconds"].default == 5


@pytest.mark.parametrize("final_before_floor", [True, False])
def test_native_turn_strategy_overlaps_policy_and_stt_waits(final_before_floor):
    async def run():
        async def cancel(task):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        manager = SimpleNamespace(
            create_task=lambda coro, name: asyncio.create_task(coro), cancel_task=cancel
        )
        strategy = SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.04, task_manager=manager)
        done = asyncio.Event()

        @strategy.event_handler("on_user_turn_inference_triggered")
        async def completed(strategy, *args):
            done.set()

        await strategy.process_frame(
            STTMetadataFrame(service_name="synthetic", ttfs_p99_latency=10)
        )
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.4))
        if final_before_floor:
            await strategy.process_frame(TranscriptionFrame("synthetic", "", "", finalized=True))
            await asyncio.sleep(0.01)
            assert not done.is_set()  # The silence policy is deliberate, not an STT wait.
            await asyncio.wait_for(done.wait(), 0.15)
        else:
            await asyncio.sleep(0.06)
            assert not done.is_set()
            await strategy.process_frame(TranscriptionFrame("synthetic", "", "", finalized=True))
            await asyncio.wait_for(done.wait(), 0.02)  # No new silence timer after final.
        await strategy.cleanup()

    asyncio.run(run())


def test_final_deadline_is_not_extended_by_partials_and_late_final_is_fenced():
    async def run():
        fail = AsyncMock()
        gate = FinalTranscriptGate(uuid4(), final_timeout=0.015, fail=fail)
        gate.push_frame = AsyncMock()
        for frame in (VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame()):
            await gate.process_frame(frame, FrameDirection.UPSTREAM)
        for _ in range(3):
            await gate.process_frame(
                InterimTranscriptionFrame("uncertain amount", "", ""), FrameDirection.DOWNSTREAM
            )
            await asyncio.sleep(0.01)
        fail.assert_awaited_once()
        gate.push_frame.reset_mock()
        await gate.process_frame(
            TranscriptionFrame("late old amount", "", "", finalized=True), FrameDirection.DOWNSTREAM
        )
        gate.push_frame.assert_not_awaited()
        await gate.cleanup()

    asyncio.run(run())


def test_natural_pause_resumption_waits_for_every_final_before_release():
    async def run():
        fail = AsyncMock()
        gate = FinalTranscriptGate(uuid4(), final_timeout=1, fail=fail)
        gate.push_frame = AsyncMock()
        for frame in (
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
        ):
            await gate.process_frame(frame, FrameDirection.UPSTREAM)
        gate.push_frame.reset_mock()
        await gate.process_frame(
            TranscriptionFrame("mera naam", "", "", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        gate.push_frame.assert_not_awaited()
        fail.assert_not_awaited()
        await gate.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        gate.push_frame.reset_mock()
        await gate.process_frame(
            TranscriptionFrame("Asha hai", "", "", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        transcription = [
            call.args[0]
            for call in gate.push_frame.await_args_list
            if isinstance(call.args[0], TranscriptionFrame)
        ]
        assert len(transcription) == 1
        assert transcription[0].text == "mera naam Asha hai"
        assert transcription[0].finalized
        fail.assert_not_awaited()
        await gate.cleanup()

    asyncio.run(run())


def test_duplicate_final_fails_closed_before_quarantined_text_is_released():
    async def run():
        fail = AsyncMock()
        gate = FinalTranscriptGate(uuid4(), final_timeout=1, fail=fail)
        gate.push_frame = AsyncMock()
        for frame in (
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
        ):
            await gate.process_frame(frame, FrameDirection.UPSTREAM)
        gate.push_frame.reset_mock()
        final = TranscriptionFrame("same provider final", "", "", finalized=True)
        await gate.process_frame(final, FrameDirection.DOWNSTREAM)
        await gate.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        gate.push_frame.reset_mock()
        await gate.process_frame(final, FrameDirection.DOWNSTREAM)
        fail.assert_awaited_once()
        gate.push_frame.assert_not_awaited()
        await gate.cleanup()

    asyncio.run(run())


def test_cancellation_discards_quarantined_final_and_cancels_timer():
    async def run():
        fail = AsyncMock()
        gate = FinalTranscriptGate(uuid4(), final_timeout=0.02, fail=fail)
        gate.push_frame = AsyncMock()
        for frame in (
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(),
            VADUserStartedSpeakingFrame(),
        ):
            await gate.process_frame(frame, FrameDirection.UPSTREAM)
        await gate.process_frame(
            TranscriptionFrame("quarantined", "", "", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await gate.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        gate.push_frame.reset_mock()
        await gate.process_frame(
            TranscriptionFrame("late after cancel", "", "", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.03)
        fail.assert_not_awaited()
        gate.push_frame.assert_not_awaited()
        await gate.cleanup()

    asyncio.run(run())


def test_cleanup_cancels_pending_final_timer():
    async def run():
        fail = AsyncMock()
        clean = FinalTranscriptGate(uuid4(), final_timeout=0.02, fail=fail)
        clean.push_frame = AsyncMock()
        for frame in (VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame()):
            await clean.process_frame(frame, FrameDirection.UPSTREAM)
        await clean.cleanup()
        await asyncio.sleep(0.03)
        fail.assert_not_awaited()

    asyncio.run(run())


def test_bound_timing_deduplicates_vad_and_rejects_stale_response(
    monkeypatch,
):
    output = capture(monkeypatch)

    class NativeLLM(FrameProcessor):
        async def process_frame(self, frame, direction):
            if isinstance(frame, LLMContextFrame):
                await self._call_event_handler("on_before_push_frame", LLMFullResponseStartFrame())
                await asyncio.sleep(0)
                await self._call_event_handler(
                    "on_before_push_frame", LLMTextFrame("private token")
                )

    class LLM(CorrelatedLLMMixin, NativeLLM):
        pass

    async def run():
        user, stt, llm, tts, transport = (
            FrameProcessor(),
            FrameProcessor(),
            LLM(),
            FrameProcessor(),
            FrameProcessor(),
        )
        strategy = SpeechTimeoutUserTurnStopStrategy()
        now = [2_000_000_000]
        timing = LatencyObserver(
            uuid4(), stt, llm, tts, transport, lambda: now[0], require_finalized_transcripts=True
        )
        timing.bind(user, strategy)

        async def user_push(frame):
            await user._call_event_handler("on_before_push_frame", frame)

        # Independent upstream/downstream VAD instances must be one boundary.
        await user_push(VADUserStartedSpeakingFrame())
        await user_push(VADUserStartedSpeakingFrame())
        timing.stt_frame(InterimTranscriptionFrame("private partial", "", ""))
        stop = VADUserStoppedSpeakingFrame(stop_secs=0.4)
        await user_push(stop)
        await user_push(VADUserStoppedSpeakingFrame(stop_secs=0.4))
        assert timing.turn_index == 1
        await timing.on_process_frame(SimpleNamespace(processor=stt, frame=stop, timestamp=now[0]))
        timing.stt_frame(TranscriptionFrame("private final", "", "", finalized=True))
        await strategy._call_event_handler("on_user_turn_inference_triggered", None)
        context = LLMContextFrame(LLMContext())
        await user_push(context)
        assert context.metadata[TURN_KEY] == 1
        await llm.process_frame(context, FrameDirection.DOWNSTREAM)
        timing.mark(TurnStage.TTS_INPUT, 1)
        timing.mark(TurnStage.TTS_READY, 1)
        timing.tts_request("private-tts-context", 1)
        now[0] += 100_000_000
        audio = TTSAudioRawFrame(b"private audio", 24000, 1, context_id="private-tts-context")
        await tts._call_event_handler("on_before_push_frame", audio)
        await timing.on_process_frame(
            SimpleNamespace(processor=transport, frame=audio, timestamp=now[0])
        )
        # A late synthesis callback keeps its old owner after the next input starts.
        now[0] += 1_000_000_000
        await user_push(VADUserStartedSpeakingFrame())
        old_count = len(output.getvalue().splitlines())
        timing.tts_request("unattributed-callback")
        assert "unattributed-callback" not in timing._requests
        timing.tts_request("late-old-context", 1)
        timing.audio(TTSAudioRawFrame(b"old", 24000, 1, context_id="late-old-context"), now[0])
        assert len(output.getvalue().splitlines()) == old_count
        assert timing.turn_index == 2
        await timing.cleanup()

    asyncio.run(run())
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    stages = {record["stage"] for record in records if record["event"] == "voice_turn_timing"}
    assert {
        stage.value
        for stage in (
            TurnStage.SPEECH_END,
            TurnStage.VAD_STOP,
            TurnStage.ENDPOINT,
            TurnStage.INTERIM,
            TurnStage.FINAL,
            TurnStage.TURN_COMPLETE,
            TurnStage.LLM_REQUEST,
            TurnStage.LLM_START,
            TurnStage.LLM_TEXT,
            TurnStage.TTS_INPUT,
            TurnStage.TTS_READY,
            TurnStage.TTS_REQUEST,
            TurnStage.TTS_AUDIO,
            TurnStage.TRANSPORT,
        )
    } <= stages
    assert all(row["turn_index"] == 1 for row in records)
    assert "private" not in output.getvalue()


def test_formatter_rejects_unsafe_timing_values_and_extras():
    for timestamp in ("private", -1, float("nan"), float("inf")):
        record = logging.LogRecord(
            "test", 20, "", 1, TurnTiming(uuid4(), 1, TurnStage.FINAL, timestamp), (), None
        )
        record.transcript = "private transcript"
        assert json.loads(SafeFormatter().format(record))["event"] == "suppressed"


def test_openai_native_completion_emits_tokens_before_stream_finishes(monkeypatch):
    from techfi_voice.integrations.openai import ConfiguredOpenAILLMService

    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        "techfi_voice.integrations.openai.AsyncOpenAI", MagicMock(return_value=client)
    )

    async def run():
        service = ConfiguredOpenAILLMService(api_key="synthetic-key", timeout_seconds=1)
        release, first_token = asyncio.Event(), asyncio.Event()
        received = []

        async def chunks():
            for token in ("Namaste.", " Aap kaise hain?"):
                yield SimpleNamespace(
                    usage=None,
                    model=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=token, tool_calls=None),
                            finish_reason=None,
                        )
                    ],
                )
                await release.wait()

        service.get_chat_completions = AsyncMock(return_value=chunks())

        async def receive(frame, *args):
            if isinstance(frame, LLMTextFrame):
                received.append((frame.text, service.timing_turn.get()))
                first_token.set()

        service.push_frame = receive
        context = LLMContextFrame(LLMContext())
        context.metadata[TURN_KEY] = 7
        task = asyncio.create_task(service.process_frame(context, FrameDirection.DOWNSTREAM))
        await asyncio.wait_for(first_token.wait(), 1)
        assert received == [("Namaste.", 7)]
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 1)
        assert received == [("Namaste.", 7), (" Aap kaise hain?", 7)]
        assert service.timing_turn.get() is None
        await service.cleanup()

    asyncio.run(run())


def test_native_sentence_tts_starts_before_complete_llm_reply_and_preserves_owner(monkeypatch):
    from pipecat.services.sarvam.tts import SarvamTTSService
    from techfi_voice.integrations.sarvam import ConfiguredSarvamTTSService

    async def synthesize(self, text, context_id):
        yield TTSAudioRawFrame(b"\x00\x00" * 10, 24000, 1, context_id=context_id)

    monkeypatch.setattr(SarvamTTSService, "run_tts", synthesize)

    async def run():
        service = ConfiguredSarvamTTSService(api_key="synthetic-key")
        timing = LatencyObserver(
            uuid4(), object(), object(), service, object(), lambda: 1_000_000_000
        )
        timing.speech_started(VADUserStartedSpeakingFrame(), 1_000_000_000)
        service.turn_timing = timing
        service.push_frame = AsyncMock()
        synthesized = []

        async def boundary(frame, *args, **kwargs):
            # Exercise the native sentence aggregator without socket/audio-context setup.
            for aggregation_type, transform in service._text_transforms:
                await transform(frame.text, aggregation_type)
            synthesized.append(frame.text)
            async for audio in service.run_tts(frame.text, "synthetic-context"):
                assert audio.context_id == "synthetic-context"

        service._push_tts_frames = boundary
        start = LLMFullResponseStartFrame()
        start.metadata[TURN_KEY] = 1
        await service.process_frame(start, FrameDirection.DOWNSTREAM)
        for text in ("Namaste", ". Aap"):
            frame = LLMTextFrame(text)
            frame.metadata[TURN_KEY] = 1
            await service.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert synthesized and "Namaste" in synthesized[0]
        assert timing._requests["synthetic-context"][0] == 1
        assert TurnStage.TTS_INPUT in timing.current.times
        assert TurnStage.TTS_READY in timing.current.times
        assert TurnStage.TTS_REQUEST in timing.current.times
        # No LLMFullResponseEndFrame was supplied and no SDK request was made.
        await service.cleanup()
        await timing.cleanup()

    asyncio.run(run())


def test_running_pipeline_with_native_vad_broadcasts_records_one_correlated_turn(monkeypatch):
    from pipecat.frames.frames import LLMFullResponseEndFrame
    from pipecat.processors.aggregators import llm_response_universal
    from pipecat.services.tts_service import TTSService
    from techfi_voice.integrations.providers import Services
    from techfi_voice.integrations.timing import TimedTTSMixin
    from techfi_voice.runtime import run_session
    from techfi_voice.session import build_session
    from test_foundations import voice_settings

    output = capture(monkeypatch)
    # Session startup installs its own logger only in the HTTP app, which is not used here.
    users = []
    original = llm_response_universal.LLMContextAggregatorPair

    def capture_user(*args, **kwargs):
        pair = original(*args, **kwargs)
        users.append(pair.user())
        return pair

    monkeypatch.setattr(llm_response_universal, "LLMContextAggregatorPair", capture_user)

    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class STT(Forward):
        def __init__(self):
            super().__init__()
            for name in ("on_connected", "on_disconnected", "on_connection_error"):
                self._register_event_handler(name)

    class NativeLLM(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame("Namaste. Aap kaise hain?"))
                await self.push_frame(LLMFullResponseEndFrame())
            else:
                await self.push_frame(frame, direction)

    class LLM(CorrelatedLLMMixin, NativeLLM):
        pass

    class NativeTTS(TTSService):
        async def run_tts(self, text, context_id):
            yield TTSAudioRawFrame(b"\x01\x00" * 480, 24000, 1, context_id=context_id)

    class TTS(TimedTTSMixin, NativeTTS):
        pass

    async def run():
        class Output(Forward):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TTSAudioRawFrame):
                    session.stop.set()

        transport = SimpleNamespace(input=lambda: Forward(), output=lambda: Output())
        settings = voice_settings(
            stt_provider="sarvam-realtime",
            stt_model="saaras:v4",
            turn_silence_seconds=0.01,
            max_session_seconds=2,
        )
        stt = STT()
        session = build_session(settings, transport, lambda _: Services(stt, LLM(), TTS(), True))

        @session.task.event_handler("on_pipeline_started")
        async def started(worker, frame):
            controller = SimpleNamespace(
                _vad_analyzer=SimpleNamespace(params=SimpleNamespace(start_secs=0.2, stop_secs=0.4))
            )
            await users[0]._on_vad_speech_started(controller)
            await asyncio.sleep(0.02)
            await users[0]._on_vad_speech_stopped(controller)
            await stt.push_frame(InterimTranscriptionFrame("synthetic", "", ""))
            await stt.push_frame(TranscriptionFrame("synthetic final", "", "", finalized=True))

        result = await run_session(session, settings)
        assert not session.failed, result
        assert session.stop.is_set()

    asyncio.run(run())
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    stages = {row["stage"] for row in records if row["event"] == "voice_turn_timing"}
    assert {
        "stt_final",
        "turn_strategy_complete",
        "llm_request",
        "llm_start",
        "llm_first_text",
        "tts_first_text_delivered",
        "tts_first_text_chunk",
        "tts_request",
        "tts_first_audio",
        "audio_transport_handoff",
    } <= stages
    assert {row["turn_index"] for row in records if "turn_index" in row} == {1}


def test_bound_whisper_fallback_retains_completed_segment_timing(monkeypatch):
    output = capture(monkeypatch)

    async def run():
        user, stt, llm, tts, transport = (FrameProcessor() for _ in range(5))
        timing = LatencyObserver(uuid4(), stt, llm, tts, transport, lambda: 2_000_000_000)
        timing.bind(user, SpeechTimeoutUserTurnStopStrategy())
        await user._call_event_handler("on_before_push_frame", VADUserStartedSpeakingFrame())
        await user._call_event_handler(
            "on_before_push_frame", VADUserStoppedSpeakingFrame(stop_secs=0.4)
        )
        await stt._call_event_handler(
            "on_before_push_frame", TranscriptionFrame("synthetic", "", "")
        )
        assert timing.transcript_end == 2_000_000_000
        timing.emit_stt()
        await timing.cleanup()

    asyncio.run(run())
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert any(
        row.get("metric") == "stt_completion_ms" and row["duration_ms"] == 400 for row in records
    )


def test_delayed_llm_output_keeps_cancelled_request_owner(monkeypatch):
    output = capture(monkeypatch)

    async def run():
        release = {1: asyncio.Event(), 2: asyncio.Event()}
        started = {1: asyncio.Event(), 2: asyncio.Event()}

        class Native(FrameProcessor):
            async def process_frame(self, frame, direction):
                owner = self.timing_turn.get()
                await self._call_event_handler("on_before_push_frame", LLMFullResponseStartFrame())
                started[owner].set()
                await release[owner].wait()
                token = LLMTextFrame("synthetic delayed text")
                await self._call_event_handler("on_before_push_frame", token)
                assert token.metadata[TURN_KEY] == owner

        class LLM(CorrelatedLLMMixin, Native):
            pass

        user, stt, tts, transport = (FrameProcessor() for _ in range(4))
        llm = LLM()
        timing = LatencyObserver(uuid4(), stt, llm, tts, transport, lambda: 2_000_000_000)
        timing.bind(user, SpeechTimeoutUserTurnStopStrategy())
        tasks = []
        for owner in (1, 2):
            await user._call_event_handler("on_before_push_frame", VADUserStartedSpeakingFrame())
            await user._call_event_handler("on_before_push_frame", VADUserStoppedSpeakingFrame())
            context = LLMContextFrame(LLMContext())
            await user._call_event_handler("on_before_push_frame", context)
            tasks.append(asyncio.create_task(llm.process_frame(context, FrameDirection.DOWNSTREAM)))
            await started[owner].wait()
        # The first request still emits after cancellation; it must not become turn 2 text.
        release[1].set()
        await tasks[0]
        release[2].set()
        await tasks[1]
        assert llm.timing_turn.get() is None
        await timing.cleanup()

    asyncio.run(run())
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    first_tokens = [row for row in records if row.get("stage") == "llm_first_text"]
    assert [row["turn_index"] for row in first_tokens] == [2]
