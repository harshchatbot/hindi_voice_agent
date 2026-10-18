import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import ValidationError
from techfi_shared.contracts import CallOutcome, SessionScope
from techfi_shared.errors import ConfigurationError, ProviderError
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import create_services, validate_provider_settings
from techfi_voice.prompt import conversational_prompt
from techfi_voice.runtime import run_session
from techfi_voice.session import VoiceSession
from test_foundations import voice_settings

logger.remove()


def test_conventional_keys_are_loaded_by_settings_without_exposure(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=synthetic-openai\nSARVAM_API_KEY=synthetic-sarvam\n")
    values = voice_settings().model_dump()
    for name in ("llm_api_key", "stt_api_key", "tts_api_key"):
        values.pop(name)
    settings = VoiceSettings(_env_file=path, **values)
    assert settings.llm_api_key.get_secret_value() == "synthetic-openai"
    assert settings.stt_api_key.get_secret_value() == "synthetic-sarvam"
    assert settings.tts_api_key.get_secret_value() == "synthetic-sarvam"
    assert "synthetic-openai" not in repr(settings)
    assert "synthetic-sarvam" not in repr(settings)


@pytest.mark.parametrize(
    "override",
    [
        {"host": "0.0.0.0"},
        {"language": "unsupported"},
        {"provider_timeout_seconds": 0},
        {"stt_api_key": "<placeholder>"},
        {"opt_out_phrases": ()},
    ],
)
def test_config_rejects_unsafe_or_invalid_values(override):
    with pytest.raises(ValidationError):
        voice_settings(**override)


@pytest.mark.parametrize(
    "override",
    [
        {"stt_model": "invalid"},
        {"tts_model": "bulbul:v2"},
        {"tts_voice_id": "invalid"},
        {"stt_mode": "translate"},
    ],
)
def test_provider_capability_validation(override):
    with pytest.raises(ConfigurationError):
        validate_provider_settings(voice_settings(**override))


def test_supported_provider_services_are_injected_with_settings(monkeypatch):
    from pipecat.services.sarvam.stt import SarvamSTTService
    from pipecat.services.sarvam.tts import SarvamTTSService
    from techfi_voice.integrations.openai import ConfiguredOpenAILLMService

    constructors = []
    for cls in (SarvamSTTService, ConfiguredOpenAILLMService, SarvamTTSService):
        mock = MagicMock()
        mock.Settings = cls.Settings
        constructors.append(mock)
    monkeypatch.setattr("pipecat.services.sarvam.stt.SarvamSTTService", constructors[0])
    monkeypatch.setattr(
        "techfi_voice.integrations.openai.ConfiguredOpenAILLMService", constructors[1]
    )
    monkeypatch.setattr("pipecat.services.sarvam.tts.SarvamTTSService", constructors[2])
    settings = voice_settings()
    services = create_services(settings)
    assert services.stt is constructors[0].return_value
    assert constructors[0].call_args.kwargs["settings"].vad_signals is False
    assert constructors[0].call_args.kwargs["settings"].model == settings.stt_model
    assert constructors[1].call_args.kwargs["timeout_seconds"] == settings.provider_timeout_seconds
    assert constructors[2].call_args.kwargs["settings"].voice == settings.tts_voice_id
    assert constructors[2].call_args.kwargs["reconnect_on_error"] is False
    constructors[0].side_effect = RuntimeError("sensitive-provider-body")
    with pytest.raises(ProviderError) as exc:
        create_services(settings)
    assert "sensitive" not in str(exc.value)


def test_openai_client_timeout_retry_and_cleanup(monkeypatch):
    from techfi_voice.integrations.openai import ConfiguredOpenAILLMService

    client = SimpleNamespace(close=AsyncMock())
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("techfi_voice.integrations.openai.AsyncOpenAI", constructor)

    async def run():
        service = ConfiguredOpenAILLMService(
            api_key="synthetic",
            timeout_seconds=7,
            settings=ConfiguredOpenAILLMService.Settings(model="test"),
        )
        assert constructor.call_args.kwargs["timeout"] == 7
        assert constructor.call_args.kwargs["max_retries"] == 0
        await service.cleanup()
        client.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    ["STOP!", "Please do not call me again.", "band karo", "कृपया कॉल मत करो", "बात नहीं करनी"],
)
def test_opt_out_detection(text):
    from techfi_voice.policy import requests_opt_out

    assert requests_opt_out(text, voice_settings().opt_out_phrases)


def test_opt_out_does_not_match_substrings_and_preserves_hindi():
    from techfi_voice.policy import normalize, requests_opt_out

    assert not requests_opt_out("I use a stopwatch", ("stop",))
    assert normalize("कॉल मत करो!") == "कॉल मत करो"


def test_opt_out_is_consumed_before_llm_and_interrupts():
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection
    from techfi_voice.policy import OptOutProcessor

    async def run():
        end = AsyncMock()
        processor = OptOutProcessor(("band karo",), end)
        processor.push_frame = AsyncMock()
        processor.broadcast_interruption = AsyncMock()
        for text in ("band karo", "loan details please"):
            await processor.process_frame(
                TranscriptionFrame(text, "", ""), FrameDirection.DOWNSTREAM
            )
        processor.broadcast_interruption.assert_awaited_once()
        end.assert_awaited_once()
        processor.push_frame.assert_not_awaited()

    asyncio.run(run())


def test_prompt_keeps_identity_unverified_and_prohibits_detail_disclosure():
    prompt = conversational_prompt("Use conversational Hinglish.")
    for rule in ("AI assistant", "UNVERIFIED", "OTP", "Never reveal", "opt out", "DND"):
        assert rule in prompt


class FakeTask:
    def __init__(self):
        self.finished = asyncio.Event()
        self.cancel = AsyncMock(side_effect=self.finished.set)
        self.queue_frame = AsyncMock(side_effect=lambda frame: self.finished.set())


class FakeRunner:
    def __init__(self, task, fail=False):
        self.task = task
        self.fail = fail

    async def add_workers(self, task):
        pass

    async def run(self):
        if self.fail:
            raise RuntimeError("secret-request-body")
        await self.task.finished.wait()


def make_session():
    return VoiceSession(
        SessionScope(tenant_id=uuid4()), FakeTask(), SimpleNamespace(set_messages=MagicMock())
    )


@pytest.mark.parametrize("reason", ["stop", "timeout", "failure", "opt_out"])
def test_runtime_terminates_cleans_history_and_classifies_outcomes(reason):
    async def run():
        settings = voice_settings().model_copy(
            update={
                "max_session_seconds": 0.02,
                "cleanup_timeout_seconds": 0.1,
                "opt_out_grace_seconds": 0.01,
            }
        )
        session = make_session()
        if reason == "stop":
            session.outcome = CallOutcome.CANCELLED
            session.stop.set()
        if reason == "opt_out":
            session.opted_out = True
            session.outcome = CallOutcome.OPTED_OUT
            session.opt_out_requested.set()
        result = await run_session(session, settings, FakeRunner(session.task, reason == "failure"))
        assert (
            result.outcome
            == {
                "stop": CallOutcome.CANCELLED,
                "timeout": CallOutcome.TIMED_OUT,
                "failure": CallOutcome.FAILED,
                "opt_out": CallOutcome.OPTED_OUT,
            }[reason]
        )
        session.context.set_messages.assert_called_once_with([])
        if reason == "timeout":
            session.task.cancel.assert_awaited()
        if reason in ("stop", "opt_out"):
            session.task.queue_frame.assert_awaited_once()

    asyncio.run(run())


def test_monitor_times_out_and_cancels_on_interruption():
    from pipecat.frames.frames import InterruptionFrame, TTSStartedFrame
    from techfi_voice.monitor import SessionMonitor

    async def run():
        fail = AsyncMock()
        monitor = SessionMonitor(0.01, fail, AsyncMock())
        await monitor.on_push_frame(SimpleNamespace(frame=TTSStartedFrame()))
        await asyncio.sleep(0.03)
        fail.assert_awaited_once()
        fail.reset_mock()
        await monitor.on_push_frame(SimpleNamespace(frame=TTSStartedFrame()))
        await monitor.on_push_frame(SimpleNamespace(frame=InterruptionFrame()))
        await asyncio.sleep(0.03)
        fail.assert_not_awaited()
        await monitor.cleanup()
        assert not monitor._timers

    asyncio.run(run())


HEADERS = {"Origin": "http://testserver", "X-TechFi-Client": "local-browser", "Host": "localhost"}
# Tests use a real loopback Host and Origin, just as the browser does.
HEADERS["Origin"] = "http://localhost"


def test_browser_routes_reject_cross_origin_and_sanitize_validation():
    from techfi_voice.server import create_app

    with TestClient(create_app(voice_settings()), raise_server_exceptions=False) as client:
        response = client.get("/", headers={"Host": "localhost"})
        assert response.status_code == 200
        assert "AI assistant" in response.text
        assert "test-llm-secret" not in response.text
        assert client.get("/client.js", headers={"Host": "localhost"}).status_code == 200
        assert (
            client.post(
                "/api/offer", json={"sdp": "private"}, headers={"Host": "localhost"}
            ).status_code
            == 403
        )
        response = client.post(
            "/api/offer", json={"sdp": "private", "type": "bad"}, headers=HEADERS
        )
        assert response.status_code == 422
        assert "private" not in response.text
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_signaling_failure_closes_connection_and_sanitizes_error():
    from techfi_voice.server import create_app

    connection = SimpleNamespace(
        pc_id="test-id",
        initialize=AsyncMock(side_effect=RuntimeError("secret-SDP-or-key")),
        disconnect=AsyncMock(),
    )
    app = create_app(voice_settings(), connection_factory=lambda **kw: connection)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/offer", json={"sdp": "synthetic", "type": "offer"}, headers=HEADERS
        )
        assert response.status_code == 503
        assert response.json() == {"error": "provider_unavailable"}
    connection.disconnect.assert_awaited_once()


def test_session_limit_stop_and_server_shutdown_own_connections(monkeypatch):
    from techfi_voice.server import create_app

    class Transport:
        def __init__(self, *args):
            pass

        def event_handler(self, name):
            return lambda callback: callback

    monkeypatch.setattr("pipecat.transports.smallwebrtc.transport.SmallWebRTCTransport", Transport)
    connection = SimpleNamespace(
        pc_id="synthetic-id",
        initialize=AsyncMock(),
        disconnect=AsyncMock(),
        get_answer=lambda: {"sdp": "synthetic-answer", "type": "answer", "pc_id": "synthetic-id"},
    )
    created = []

    def builder(settings, transport, factory):
        session = make_session()
        created.append(session)
        return session

    async def run(session, settings):
        await session.stop.wait()

    app = create_app(
        voice_settings(max_sessions=1),
        connection_factory=lambda **kw: connection,
        session_builder=builder,
        session_runner=run,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        request = {"sdp": "synthetic-offer", "type": "offer"}
        response = client.post("/api/offer", json=request, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["pc_id"] == "synthetic-id"
        assert client.post("/api/offer", json=request, headers=HEADERS).status_code == 429
        assert client.post("/api/sessions/synthetic-id/stop", headers=HEADERS).status_code == 204
        assert created[0].outcome == CallOutcome.CANCELLED
    connection.disconnect.assert_awaited()


def test_external_cancellation_cleans_session_and_propagates():
    async def run():
        session = make_session()
        job = asyncio.create_task(run_session(session, voice_settings(), FakeRunner(session.task)))
        await asyncio.sleep(0.01)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert session.outcome == CallOutcome.CANCELLED
        session.task.cancel.assert_awaited()
        session.context.set_messages.assert_called_once_with([])

    asyncio.run(run())


def test_runtime_failure_event_cancels_a_session_without_exposing_details():
    async def run():
        session = make_session()
        session.failed = True
        session.outcome = CallOutcome.FAILED
        session.stop.set()
        result = await run_session(session, voice_settings(), FakeRunner(session.task))
        assert result.outcome == CallOutcome.FAILED
        session.task.cancel.assert_awaited_once()

    asyncio.run(run())


def test_provider_keys_are_not_sent_to_a_different_provider(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=synthetic-openai\nSARVAM_API_KEY=synthetic-sarvam\n")
    values = voice_settings(llm_provider="groq").model_dump()
    values.pop("llm_api_key")
    with pytest.raises(ValidationError):
        VoiceSettings(_env_file=path, **values)
    values["llm_api_key"] = "synthetic-groq"
    settings = VoiceSettings(_env_file=path, **values)
    assert settings.llm_api_key.get_secret_value() == "synthetic-groq"
