import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from techfi_voice.native_simulation import (
    FIXTURES,
    NativeSettings,
    build_native_session,
    pipeline_settings,
    require_local_console,
    run_native,
)


def config(**kwargs):
    return NativeSettings(
        _env_file=None, environment="development", SARVAM_API_KEY="offline-test", **kwargs
    )


def test_fixture_and_configuration_are_closed_to_production_environment(monkeypatch):
    monkeypatch.setenv("TECHFI_COLLECTION_CASE", '{"customer_name":"Real Customer"}')
    monkeypatch.setenv("TECHFI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TECHFI_SARVAM_LLM_BASE_URL", "https://untrusted.example")
    settings = pipeline_settings(config(), "female")
    assert settings.collection_case is FIXTURES["fictional-emi-001"]
    assert settings.llm_provider == "sarvam" and settings.tts_voice_id == "ritu"
    assert not settings.collection_demo_enabled
    assert settings.sarvam_llm_base_url == "https://api.sarvam.ai/v1"
    with pytest.raises(TypeError):
        FIXTURES["other"] = settings.collection_case
    with pytest.raises(ValidationError):
        settings.collection_case.customer_name = "Demo Other"
    with pytest.raises(ValueError):
        pipeline_settings(NativeSettings(_env_file=None, SARVAM_API_KEY="test"), "male")


def test_console_rejects_remote_and_ambiguous_execution(monkeypatch):
    import techfi_voice.native_simulation as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(module.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    require_local_console()
    monkeypatch.setenv("SSH_CONNECTION", "remote")
    with pytest.raises(ValueError):
        require_local_console()
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(ValueError):
        require_local_console()


def test_no_production_policy_or_network_server_needed(monkeypatch):
    from techfi_voice import collection
    from techfi_voice.session import _assemble_session

    monkeypatch.setattr(collection, "DemoCallingPolicy", Mock(side_effect=AssertionError))
    monkeypatch.setattr("techfi_voice.session._assemble_session", Mock(return_value="session"))
    settings = pipeline_settings(config(), "male")
    assert build_native_session(settings, object(), object()) == "session"
    # Leave the real assembly function available to subsequent tests.
    assert callable(_assemble_session)
    with pytest.raises(ValueError):
        build_native_session(settings.model_copy(update={"collection_case": None}), None, None)


@pytest.mark.parametrize("persona", ["male", "female"])
def test_native_greeting_to_confirmed_outcome_with_mocked_audio_and_providers(monkeypatch, persona):
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InterimTranscriptionFrame,
        LLMContextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TranscriptionFrame,
        TTSAudioRawFrame,
    )
    from pipecat.processors.aggregators import llm_response_universal
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.services.tts_service import TTSService
    from techfi_voice import collection
    from techfi_voice.integrations.providers import Services
    from techfi_voice.integrations.timing import CorrelatedLLMMixin, TimedTTSMixin

    monkeypatch.setattr(collection, "DemoCallingPolicy", Mock(side_effect=AssertionError))
    users, spoken, contexts, audio, snapshots = [], [], [], [], []
    original = llm_response_universal.LLMContextAggregatorPair

    def capture(*args, **kwargs):
        pair = original(*args, **kwargs)
        users.append(pair.user())
        return pair

    monkeypatch.setattr(llm_response_universal, "LLMContextAggregatorPair", capture)

    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class STT(Forward):
        def __init__(self):
            super().__init__()
            self.script = iter(("neela kamal", "I will pay 26 October 2026", "haan"))
            for event in ("on_connected", "on_disconnected", "on_connection_error"):
                self._register_event_handler(event)

        async def process_frame(self, frame, direction):
            if isinstance(frame, InputAudioRawFrame):
                await self.push_frame(InterimTranscriptionFrame("untrusted partial", "", ""))
                await self.push_frame(TranscriptionFrame(next(self.script), "", "", finalized=True))
            else:
                await super().process_frame(frame, direction)

    class MockLLM(Forward):
        async def process_frame(self, frame, direction):
            if isinstance(frame, LLMContextFrame):
                contexts.append(frame.context.get_messages())
                for response in (
                    LLMFullResponseStartFrame(),
                    LLMTextFrame("UNAUTHORIZED PAYMENT DEMAND"),
                    LLMFullResponseEndFrame(),
                ):
                    await self.push_frame(response)
            else:
                await super().process_frame(frame, direction)

    class LLM(CorrelatedLLMMixin, MockLLM):
        pass

    class MockTTS(TTSService):
        async def run_tts(self, text, context_id):
            spoken.append(text)
            yield TTSAudioRawFrame(b"\x00\x00" * 480, 24000, 1, context_id=context_id)

    class TTS(TimedTTSMixin, MockTTS):
        pass

    async def run():
        settings = pipeline_settings(config(), persona).model_copy(
            update={"max_session_seconds": 5, "turn_silence_seconds": 0.01}
        )
        stt = STT()
        ready = asyncio.Queue()

        class Output(Forward):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, TTSAudioRawFrame):
                    audio.append(frame.audio)
                    ready.put_nowait(True)

        class Transport:
            close = Mock()

            def input(self):
                return Forward()

            def output(self):
                return Output()

        transport = Transport()
        actual_builder = build_native_session
        session_holder = []

        def capture_session(*args):
            session = actual_builder(*args)
            session_holder.append(session)
            return session

        monkeypatch.setattr("techfi_voice.native_simulation.build_native_session", capture_session)
        task = asyncio.create_task(
            run_native(settings, lambda: transport, lambda _: Services(stt, LLM(), TTS(), True))
        )
        try:
            await asyncio.wait_for(ready.get(), 2)  # Actual pipeline greeting, mocked PCM output.
            controller = SimpleNamespace(
                _vad_analyzer=SimpleNamespace(params=SimpleNamespace(start_secs=0.2, stop_secs=0.4))
            )
            for _ in range(3):
                await asyncio.sleep(0.15)  # Drain all TTS sentence chunks before the next turn.
                while not ready.empty():
                    ready.get_nowait()
                snapshots.append(session_holder[0].collection.result())
                await users[0]._on_vad_speech_started(controller)
                await asyncio.sleep(0.02)
                await users[0]._on_vad_speech_stopped(controller)
                await stt.process_frame(
                    InputAudioRawFrame(b"\x00\x00" * 320, 16000, 1),
                    FrameDirection.DOWNSTREAM,
                )
                await asyncio.wait_for(ready.get(), 2)
            await asyncio.sleep(0.15)
            session_holder[0].stop.set()
            result = await asyncio.wait_for(task, 2)
            assert not session_holder[0].failed
            assert session_holder[0].context.get_messages() == []
            assert session_holder[0].calling_window_remaining is None
            assert result.collection.customer_reported_promise_to_pay_date == date(2026, 10, 26)
            assert result.collection.verified_recipient_status == "demo_verified"
            assert not result.collection.financial_records_modified
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        transport.close.assert_called_once()

    asyncio.run(run())
    assert len(audio) >= 4 and len(contexts) == 3
    assert "automated AI" in spoken[0] and "4500" not in spoken[0]
    assert "4500" in " ".join(spoken[1:])
    assert "UNAUTHORIZED" not in " ".join(spoken)
    assert "neela kamal" not in str(contexts) and "Asha Verma" not in str(contexts)
    assert snapshots[0].verified_recipient_status == "not_verified"
    assert snapshots[2].customer_reported_promise_to_pay_date is None


def test_native_audio_optional_adapter_has_no_signaling_and_owns_portaudio(monkeypatch):
    import sys

    from techfi_voice.native_simulation import native_transport

    audio = Mock()
    monkeypatch.setitem(sys.modules, "pyaudio", SimpleNamespace(PyAudio=lambda: audio))
    transport = native_transport()
    assert transport._params.audio_in_sample_rate == 16000
    assert transport._params.audio_out_sample_rate == 24000
    assert transport._params.audio_in_enabled and transport._params.audio_out_enabled
    assert type(transport).__mro__[1].__name__ == "LocalAudioTransport"
    transport.close()
    audio.terminate.assert_called_once()
    audio.open.assert_not_called()  # Construction alone cannot capture audio.


def test_native_initialization_failure_closes_audio_without_production_policy():
    transport = SimpleNamespace(close=Mock(), input=Mock(), output=Mock())
    services = Mock(side_effect=RuntimeError("offline injected provider construction failure"))
    with pytest.raises(RuntimeError):
        asyncio.run(run_native(pipeline_settings(config(), "male"), lambda: transport, services))
    transport.close.assert_called_once()
    transport.input.assert_not_called()


def test_new_sessions_do_not_share_workflow_flags_or_outcomes(monkeypatch):
    workflows = []

    def capture(*args, **kwargs):
        workflows.append(kwargs["workflow"])

    monkeypatch.setattr("techfi_voice.session._assemble_session", capture)
    for persona in ("male", "female"):
        build_native_session(pipeline_settings(config(), persona), object(), object())
    workflows[0].opt_out()
    assert workflows[0].result().do_not_call_request
    assert not workflows[1].result().do_not_call_request
    assert not workflows[1].verified


def test_production_entry_point_rejects_native_startup_flags(monkeypatch):
    from techfi_voice.main import main

    monkeypatch.setattr("sys.argv", ["techfi-voice", "--development", "--fictional-data-only"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
