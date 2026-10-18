import asyncio
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from loguru import logger
from pydantic import ValidationError
from techfi_shared.errors import ConfigurationError, ProviderError
from techfi_shared.logging import SafeFormatter
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations import providers
from test_foundations import voice_settings

logger.remove()


def google_settings(**overrides):
    return voice_settings(
        **dict(
            tts_provider="google",
            tts_model="chirp3-hd",
            tts_voice_id="hi-IN-Chirp3-HD-Achird",
            tts_api_key=None,
        )
        | overrides
    )


def google_adapter():
    pytest.importorskip("google.cloud.texttospeech_v1", reason="install worker [google] extra")
    from techfi_voice.integrations import google

    return google


def test_google_adc_needs_no_tts_api_key_and_loads_dotenv_path_safely(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    env = tmp_path / ".env"
    private_path = "/outside-repository/private-service-account.json"
    env.write_text(f"GOOGLE_APPLICATION_CREDENTIALS={private_path}\n")
    values = google_settings().model_dump()
    values.pop("tts_api_key")
    settings = VoiceSettings(_env_file=env, **values)
    providers.validate_provider_settings(settings)
    assert settings.tts_api_key is None
    assert settings.google_application_credentials.get_secret_value() == private_path
    assert private_path not in repr(settings)
    assert "google_application_credentials" not in settings.model_dump()
    # Pydantic must not mutate the process environment.
    import os

    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/secure/environment-override.json")
    settings = VoiceSettings(_env_file=env, **values)
    assert settings.google_application_credentials.get_secret_value().endswith("override.json")


@pytest.mark.parametrize("provider", ["sarvam", "cartesia"])
def test_non_google_still_requires_tts_key(provider):
    with pytest.raises(ValidationError):
        voice_settings(tts_provider=provider, tts_api_key=None)
    with pytest.raises(ValidationError):
        voice_settings(tts_provider=provider, tts_api_key="<placeholder>")


@pytest.mark.parametrize(
    "overrides",
    [
        {"tts_model": "bulbul:v3"},
        {"tts_voice_id": "en-US-Chirp3-HD-Achird"},
        {"tts_voice_id": "hi-IN-Standard-A"},
        {"tts_voice_id": "hi-IN-Chirp3-HD-"},
    ],
)
def test_google_rejects_incompatible_voice_language_and_model(overrides):
    with pytest.raises(ConfigurationError):
        providers.validate_provider_settings(google_settings(**overrides))


def test_google_constructor_uses_streaming_settings_and_does_not_change_stt_llm(monkeypatch):
    google = google_adapter()
    constructor = MagicMock()
    constructor.Settings = google.ConfiguredGoogleTTSService.Settings
    monkeypatch.setattr(google, "ConfiguredGoogleTTSService", constructor)
    stt, llm = MagicMock(), MagicMock()
    monkeypatch.setattr(providers, "_stt", stt)
    monkeypatch.setattr(providers, "_llm", llm)
    settings = google_settings(language="hi")
    services = providers.create_services(settings)
    assert services.tts is constructor.return_value
    options = constructor.call_args.kwargs
    assert options["credentials_path"] is None
    assert "api_key" not in options
    assert options["sample_rate"] == 24000
    assert options["settings"].voice == "hi-IN-Chirp3-HD-Achird"
    assert options["settings"].language.value == "hi-IN"
    stt.assert_called_once_with(settings)
    llm.assert_called_once_with(settings)
    assert voice_settings().tts_provider == "sarvam"


def test_google_adc_streams_pcm_with_context_and_closes_each_session_channel(monkeypatch):
    google = google_adapter()
    native = __import__("pipecat.services.google.tts", fromlist=["default"])
    credentials = object()
    adc = MagicMock(return_value=(credentials, "synthetic-project"))
    monkeypatch.setattr(native, "default", adc)
    requests = []

    async def responses():
        yield SimpleNamespace(audio_content=b"\x00\x00" * 480)

    async def synthesize(request_iterator):
        requests.extend([item async for item in request_iterator])
        return responses()

    clients = [
        SimpleNamespace(
            streaming_synthesize=AsyncMock(side_effect=synthesize),
            transport=SimpleNamespace(close=AsyncMock()),
        )
        for _ in range(2)
    ]
    constructor = MagicMock(side_effect=clients)
    monkeypatch.setattr(google.texttospeech_v1, "TextToSpeechAsyncClient", constructor)

    async def run():
        one, two = providers._tts(google_settings()), providers._tts(google_settings())
        assert one is not two and one._client is not two._client
        one._sample_rate = 24000
        frames = [frame async for frame in one.run_tts("synthetic Hindi text", "opaque-context")]
        assert frames and all(frame.context_id == "opaque-context" for frame in frames)
        assert all(frame.sample_rate == 24000 and frame.num_channels == 1 for frame in frames)
        config = requests[0].streaming_config
        assert config.voice.name == "hi-IN-Chirp3-HD-Achird"
        assert config.voice.language_code == "hi-IN"
        assert (
            config.streaming_audio_config.audio_encoding == google.texttospeech_v1.AudioEncoding.PCM
        )
        assert config.streaming_audio_config.sample_rate_hertz == 24000
        assert requests[1].input.text == "synthetic Hindi text"
        await one.cleanup()
        await two.cleanup()
        for client in clients:
            client.transport.close.assert_awaited_once()
        assert clients[1].streaming_synthesize.await_count == 0

    asyncio.run(run())
    assert adc.call_count == 2
    assert constructor.call_args.kwargs["credentials"] is credentials


def test_google_credentials_file_uses_google_auth_loader_without_environment_mutation(monkeypatch):
    google = google_adapter()
    load = MagicMock(return_value=(object(), "synthetic-project"))
    monkeypatch.setattr(google, "load_credentials_from_file", load)
    client = SimpleNamespace(transport=SimpleNamespace(close=AsyncMock()))
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(google.texttospeech_v1, "TextToSpeechAsyncClient", constructor)

    async def run():
        service = providers._tts(
            google_settings(google_application_credentials="/secure/synthetic-credentials.json")
        )
        load.assert_called_once_with(
            "/secure/synthetic-credentials.json",
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        assert constructor.call_args.kwargs["credentials"] is load.return_value[0]
        await service.cleanup()

    asyncio.run(run())


def test_google_auth_failure_is_typed_and_secret_safe(monkeypatch):
    google = google_adapter()
    from google.auth.exceptions import DefaultCredentialsError

    monkeypatch.setattr(providers, "_stt", MagicMock())
    monkeypatch.setattr(providers, "_llm", MagicMock())
    monkeypatch.setattr(
        google,
        "ConfiguredGoogleTTSService",
        MagicMock(side_effect=DefaultCredentialsError("private-credential-content")),
    )
    with pytest.raises(ProviderError) as exc:
        providers.create_services(google_settings())
    assert "private-credential-content" not in str(exc.value)


def test_google_stream_failure_uses_existing_error_boundary_and_sanitized_logs(monkeypatch):
    google = google_adapter()
    client = SimpleNamespace(
        streaming_synthesize=AsyncMock(side_effect=RuntimeError("private-provider-payload")),
        transport=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(
        google.ConfiguredGoogleTTSService, "_create_client", MagicMock(return_value=client)
    )

    async def run():
        service = providers._tts(google_settings())
        service._sample_rate = 24000
        service.push_error = AsyncMock()
        assert [frame async for frame in service.run_tts("synthetic text", "opaque-context")] == []
        service.push_error.assert_awaited_once()
        # Pipecat error payloads stay internal; the existing formatter never emits them.
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(SafeFormatter())
        safe_logger = logging.Logger("google-test")
        safe_logger.addHandler(handler)
        safe_logger.error(
            service.push_error.call_args.kwargs["error_msg"], extra={"session_id": uuid4()}
        )
        assert "private-provider-payload" not in output.getvalue()
        await service.cleanup()
        client.transport.close.assert_awaited_once()

    asyncio.run(run())
