import io
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from techfi_voice.config import StartupSettings, VoiceSettings
from techfi_voice.diagnostics import development_traceback
from techfi_voice.integrations.providers import create_services
from test_foundations import voice_settings


def test_intended_stack_and_conventional_credentials(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=synthetic-openai\nSARVAM_API_KEY=synthetic-sarvam\n")
    settings = VoiceSettings(
        _env_file=path,
        tenant_id=uuid4(),
        stt_provider="openai",
        stt_model="whisper-1",
        llm_model="gpt-4.1-mini",
        tts_model="bulbul:v3",
        tts_voice_id="shubh",
    )
    assert (settings.stt_provider, settings.llm_provider, settings.tts_provider) == (
        "openai",
        "openai",
        "sarvam",
    )
    assert settings.stt_api_key.get_secret_value() == "synthetic-openai"
    assert settings.llm_api_key.get_secret_value() == "synthetic-openai"
    assert settings.tts_api_key.get_secret_value() == "synthetic-sarvam"
    assert StartupSettings(_env_file=None).environment == "production"


def test_whisper_is_selected_with_model_language_and_timeout(monkeypatch):
    from techfi_voice.integrations.openai import ConfiguredOpenAISTTService

    constructor = MagicMock()
    constructor.Settings = ConfiguredOpenAISTTService.Settings
    monkeypatch.setattr("techfi_voice.integrations.openai.ConfiguredOpenAISTTService", constructor)
    monkeypatch.setattr("techfi_voice.integrations.providers._llm", MagicMock())
    monkeypatch.setattr("techfi_voice.integrations.providers._tts", MagicMock())
    settings = voice_settings(stt_provider="openai", stt_model="whisper-1")
    services = create_services(settings)
    assert services.stt is constructor.return_value
    options = constructor.call_args.kwargs
    assert options["settings"].model == "whisper-1"
    assert options["settings"].language.value == "hi-IN"
    assert options["timeout_seconds"] == settings.provider_timeout_seconds


def test_whisper_http_timeout_and_cleanup_without_requests(monkeypatch):
    import asyncio

    from techfi_voice.integrations.openai import ConfiguredOpenAISTTService

    client = SimpleNamespace(close=AsyncMock())
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("pipecat.services.whisper.base_stt.AsyncOpenAI", constructor)

    async def run():
        service = ConfiguredOpenAISTTService(
            api_key="synthetic-key",
            timeout_seconds=7,
            settings=ConfiguredOpenAISTTService.Settings(model="whisper-1"),
        )
        http_client = constructor.call_args.kwargs["http_client"]
        assert http_client.timeout.read == 7
        await service.cleanup()
        client.close.assert_awaited_once()
        await http_client.aclose()

    asyncio.run(run())


def test_development_traceback_omits_inputs_context_locals_and_arbitrary_messages():
    output = io.StringIO()
    secret = "sensitive-test-value"
    try:
        voice_settings(tenant_id=secret)
    except ValidationError as exc:
        development_traceback(exc, output)
    text = output.getvalue()
    assert "ValidationError" in text
    assert "TECHFI_TENANT_ID" in text
    assert "test_voice_startup.py" in text
    assert secret not in text
    assert "input_value" not in text
    output = io.StringIO()
    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        development_traceback(exc, output)
    assert "RuntimeError" in output.getvalue()
    assert secret not in output.getvalue()


@pytest.mark.parametrize("environment", ["production", "development"])
def test_entry_point_reports_configuration_errors_safely(monkeypatch, capsys, environment):
    import techfi_voice.main as entry

    monkeypatch.setattr(sys, "argv", ["techfi-voice", "--check-config"])
    monkeypatch.setattr(
        "techfi_voice.config.StartupSettings", lambda: SimpleNamespace(environment=environment)
    )

    def fail():
        return VoiceSettings(_env_file=None, OPENAI_API_KEY="synthetic-secret")

    monkeypatch.setattr("techfi_voice.config.VoiceSettings", fail)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1
    text = capsys.readouterr().err
    assert "worker_failed" in text
    assert "synthetic-secret" not in text
    if environment == "development":
        assert "ValidationError" in text
        assert "TECHFI_TENANT_ID" in text
        assert "required setting missing" in text
    else:
        assert "ValidationError" not in text
        assert "traceback" not in text.lower()


def test_offline_startup_preflight_does_not_listen_or_create_provider_clients(monkeypatch):
    import techfi_voice.main as entry

    monkeypatch.setattr(sys, "argv", ["techfi-voice", "--check-config"])
    monkeypatch.setattr(
        "techfi_voice.config.StartupSettings", lambda: SimpleNamespace(environment="development")
    )
    monkeypatch.setattr(
        "techfi_voice.config.VoiceSettings",
        lambda: voice_settings(stt_provider="openai", stt_model="whisper-1"),
    )
    run_server = MagicMock(side_effect=AssertionError("must not start server"))
    create_clients = MagicMock(side_effect=AssertionError("must not create provider clients"))
    monkeypatch.setattr("uvicorn.run", run_server)
    monkeypatch.setattr("techfi_voice.server.create_services", create_clients)
    entry.main()
    run_server.assert_not_called()
    create_clients.assert_not_called()
