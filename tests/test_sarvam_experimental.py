"""No provider calls: constructors, HTTP/SSE and sockets are in-memory mocks."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pipecat.frames.frames import LLMTextFrame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection
from pydantic import ValidationError
from techfi_shared.errors import ConfigurationError
from techfi_voice.integrations.providers import (
    _llm,
    _tts,
    playground_settings,
    session_profile,
    validate_provider_settings,
)
from techfi_voice.integrations.sarvam_experimental import (
    SarvamConversationLLMService,
    SarvamV4TTSService,
)
from test_foundations import voice_settings
from websockets.protocol import State


def test_experimental_presets_are_isolated_and_credentials_never_cross_providers(monkeypatch):
    monkeypatch.setenv("TECHFI_LLM_API_KEY", "ambient-wrong-provider")
    base = voice_settings(
        stt_provider="sarvam-realtime",
        stt_model="saaras:v4",
        SARVAM_API_KEY="synthetic-sarvam",
        OPENAI_API_KEY="synthetic-openai",
    )
    alternate = playground_settings(base, "sarvam-v4-flash", "sarvam")
    default = playground_settings(base, "sarvam", "openai")
    assert alternate.llm_api_key.get_secret_value() == "synthetic-sarvam"
    assert alternate.tts_api_key == base.tts_api_key  # Same Sarvam account/explicit role key.
    assert (alternate.llm_model, alternate.tts_model, alternate.tts_voice_id) == (
        "sarvam-105b-conversations",
        "bulbul:v4-flash",
        "shubh_hi_customer",
    )
    assert default.llm_api_key == base.llm_api_key
    assert (default.llm_model, default.tts_model) == ("gpt-4.1-mini", "bulbul:v3")
    for key in ("stt_provider", "stt_model", "stt_api_key", "system_prompt", "vad_stop_seconds"):
        assert getattr(alternate, key) == getattr(default, key) == getattr(base, key)
    restored = playground_settings(alternate, "sarvam", "openai")
    assert restored.llm_api_key.get_secret_value() == "synthetic-openai"
    assert session_profile(alternate)["llm_profile"] == "sarvam_105b_conversations"
    assert session_profile(alternate)["tts_profile"] == "sarvam_bulbul_v4_flash"
    with pytest.raises(ConfigurationError):
        playground_settings(voice_settings(), "sarvam", "sarvam")


@pytest.mark.parametrize(
    "changes",
    [
        {"tts_voice_id": "shubh"},
        {"tts_model": "bulbul:v3"},
        {"tts_voice_id": "client-controlled"},
        {"language": "en-IN"},
        {"llm_provider": "sarvam", "llm_model": "sarvam-105b"},
    ],
)
def test_invalid_experimental_capabilities_fail_before_clients(changes):
    settings = voice_settings(
        tts_provider="sarvam-v4-flash",
        tts_model="bulbul:v4-flash",
        tts_voice_id="shubh_hi_customer",
    )
    with pytest.raises(ConfigurationError):
        validate_provider_settings(settings.model_copy(update=changes))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1",
        "https://user:secret@api.sarvam.ai/v1",
        "wss://api.sarvam.ai/text-to-speech/ws",
    ],
)
def test_llm_credentials_cannot_be_redirected(endpoint):
    with pytest.raises(ValidationError):
        voice_settings(sarvam_llm_base_url=endpoint)


def test_llm_v1_http_contract_streams_content_without_reasoning_or_openai_fields(monkeypatch):
    from openai import AsyncOpenAI
    from pipecat.processors.aggregators.llm_context import LLMContext

    async def run():
        requests = []

        def respond(request):
            requests.append(request)
            assert str(request.url) == "https://api.sarvam.ai/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer synthetic-sarvam"
            body = json.loads(request.content)
            assert set(body) == {"model", "messages", "stream", "reasoning_effort"}
            assert body["reasoning_effort"] is None and body["stream"] is True
            assert body["model"] == "sarvam-105b-conversations"
            assert body["messages"][0]["role"] == "system"
            chunks = [
                {"choices": [{"index": 0, "delta": {"reasoning_content": "private-reasoning"}}]},
                {"choices": [{"index": 0, "delta": {"content": "Namaste."}}]},
                {"choices": [{"index": 0, "delta": {"content": " Ji."}}]},
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                },
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join(
                    "data: "
                    + json.dumps(
                        {
                            "id": "synthetic",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": body["model"],
                            **c,
                        }
                    )
                    + "\n\n"
                    for c in chunks
                )
                + "data: [DONE]\n\n",
            )

        client = AsyncOpenAI(
            api_key="synthetic-sarvam",
            base_url="https://api.sarvam.ai/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            max_retries=0,
        )
        monkeypatch.setattr(
            "techfi_voice.integrations.openai.AsyncOpenAI", MagicMock(return_value=client)
        )
        service = _llm(voice_settings(llm_provider="sarvam", llm_model="sarvam-105b-conversations"))
        assert isinstance(service, SarvamConversationLLMService)
        received = []

        async def receive(frame, *args):
            if isinstance(frame, LLMTextFrame):
                received.append(frame.text)

        service.push_frame = receive
        await service._process_context(LLMContext([{"role": "user", "content": "synthetic"}]))
        assert received == ["Namaste.", " Ji."]
        assert len(requests) == 1
        await service.cleanup()
        assert client.is_closed()

    asyncio.run(run())


def test_v4_socket_contract_audio_completion_and_timing_do_not_use_v3_registry():
    async def run():
        service = _tts(
            voice_settings(
                tts_provider="sarvam-v4-flash",
                tts_model="bulbul:v4-flash",
                tts_voice_id="shubh_hi_customer",
            )
        )
        assert isinstance(service, SarvamV4TTSService)
        socket = SimpleNamespace(state=State.OPEN, send=AsyncMock(), close=AsyncMock())
        service._websocket_connect = AsyncMock(return_value=socket)
        service._call_event_handler = AsyncMock()
        await service._connect_websocket()
        args = service._websocket_connect.call_args
        assert args.args[0].endswith("?model=bulbul:v4-flash&send_completion_event=true")
        config = json.loads(socket.send.call_args.args[0])
        assert config == {
            "type": "config",
            "data": {
                "model": "bulbul:v4-flash",
                "language_code": "hi-IN",
                "speaker": "shubh_hi_customer",
                "speech_sample_rate": 24000,
                "output_audio_codec": "linear16",
                "enable_preprocessing": True,
            },
        }
        service.turn_timing = SimpleNamespace(tts_request=MagicMock())
        token = service._timing_turn.set(3)
        assert [f async for f in service.run_tts("Synthetic.", "context-3")] == [None]
        service._timing_turn.reset(token)
        service.turn_timing.tts_request.assert_called_once_with("context-3", 3)
        await service.flush_audio("context-3")
        assert [json.loads(call.args[0])["type"] for call in socket.send.call_args_list] == [
            "config",
            "text",
            "flush",
        ]
        service.get_active_audio_context_id = lambda: "context-3"
        service.audio_context_available = lambda ctx: ctx == "context-3"
        service.append_to_audio_context = AsyncMock()
        service.remove_audio_context = AsyncMock()
        service.stop_ttfb_metrics = AsyncMock()

        async def messages():
            yield json.dumps(
                {"type": "audio", "data": {"audio": base64.b64encode(b"\x00\x00").decode()}}
            )
            yield json.dumps({"type": "event", "data": {"event_type": "final"}})

        # Pipeline setup normally resolves sample_rate before opening the socket.
        # This socket-only fixture does not start a pipeline or its background tasks.
        service._sample_rate = service._init_sample_rate
        service._get_websocket = lambda: messages()
        await service._receive_messages()
        frames = [c.args[1] for c in service.append_to_audio_context.call_args_list]
        assert isinstance(frames[0], TTSAudioRawFrame)
        assert frames[0].context_id == "context-3" and frames[0].sample_rate == 24000
        assert isinstance(frames[1], TTSStoppedFrame)
        service.remove_audio_context.assert_awaited_once_with("context-3")
        service.stop_all_metrics = AsyncMock()
        await service._disconnect_websocket()
        socket.close.assert_awaited_once()
        assert service._websocket is None

    asyncio.run(run())


def test_v4_interruption_closes_and_reopens_socket_even_before_first_audio(monkeypatch):
    from pipecat.frames.frames import InterruptionFrame
    from pipecat.services.tts_service import WebsocketTTSService

    monkeypatch.setattr(WebsocketTTSService, "_handle_interruption", AsyncMock())

    async def run():
        service = SarvamV4TTSService(
            api_key="synthetic",
            url="wss://api.sarvam.ai/text-to-speech/ws",
            voice="shubh_hi_customer",
            language="hi-IN",
        )
        service._tts_started = True
        service._disconnect = AsyncMock()
        service._connect = AsyncMock()
        await service._handle_interruption(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        service._disconnect.assert_awaited_once()
        service._connect.assert_awaited_once()
        assert service._tts_started is False

    asyncio.run(run())


def test_v4_config_failure_closes_socket_before_native_connect_clears_reference():
    async def run():
        service = SarvamV4TTSService(
            api_key="synthetic",
            url="wss://api.sarvam.ai/text-to-speech/ws",
            voice="shubh_hi_customer",
            language="hi-IN",
        )
        socket = SimpleNamespace(
            state=State.OPEN, send=AsyncMock(side_effect=OSError("synthetic")), close=AsyncMock()
        )
        service._websocket_connect = AsyncMock(return_value=socket)
        service.push_error = AsyncMock()
        service._call_event_handler = AsyncMock()
        await service._connect_websocket()
        socket.close.assert_awaited_once()
        assert service._websocket is None
        service.push_error.assert_awaited_once()

    asyncio.run(run())
