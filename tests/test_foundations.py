import io
import json
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from techfi_api.main import create_app
from techfi_shared.contracts import SessionScope
from techfi_shared.errors import ProviderError
from techfi_shared.logging import Event, SafeFormatter
from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import create_services


def voice_settings(**overrides):
    values = dict(
        tenant_id=uuid4(),
        stt_provider="sarvam",
        llm_provider="openai",
        tts_provider="sarvam",
        stt_api_key="test-stt-secret",
        llm_api_key="test-llm-secret",
        tts_api_key="test-tts-secret",
        stt_model="saaras:v4",
        llm_model="test-llm",
        tts_model="bulbul:v3",
        tts_voice_id="shubh",
        language="hi-IN",
        system_prompt="test prompt",
    )
    return VoiceSettings(_env_file=None, **(values | overrides))


def test_config_validation_and_secret_repr():
    settings = voice_settings()
    assert "test-stt-secret" not in repr(settings)
    with pytest.raises(ValidationError) as exc:
        voice_settings(tenant_id="sensitive-invalid-id")
    assert "sensitive-invalid-id" not in str(exc.value)
    with pytest.raises(ValidationError):
        voice_settings(stt_api_key="")
    with pytest.raises(ValidationError):
        voice_settings(max_session_seconds=0)


def test_environment_config(monkeypatch):
    monkeypatch.setenv("TECHFI_LANGUAGE", "en")
    values = voice_settings().model_dump()
    values.pop("language")
    assert VoiceSettings(_env_file=None, **values).language == "en"


def test_scope_is_unique_and_immutable():
    tenant = uuid4()
    one, two = SessionScope(tenant_id=tenant), SessionScope(tenant_id=tenant)
    assert one.session_id != two.session_id
    with pytest.raises(ValidationError):
        one.tenant_id = uuid4()


def test_log_output_drops_pii_exceptions_and_extras():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter())
    logger = logging.Logger("test")
    logger.addHandler(handler)
    try:
        raise RuntimeError("customer-secret")
    except RuntimeError:
        logger.exception("customer-secret", extra={"phone": "customer-secret"})
    logger.info(Event.WORKER_STARTED)
    lines = stream.getvalue().splitlines()
    assert "customer-secret" not in stream.getvalue()
    assert json.loads(lines[0])["event"] == "suppressed"
    assert json.loads(lines[1])["event"] == "worker_started"


def test_api_health_and_safe_errors():
    app = create_app()

    @app.get("/typed-error")
    def typed_error():
        raise ProviderError("secret-provider-payload")

    @app.get("/unexpected-error")
    def unexpected_error():
        raise ValueError("secret-customer-payload")

    @app.get("/validate")
    def validate(number: int):
        return number

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/typed-error")
        assert response.status_code == 503
        assert response.json() == {"error": "provider_unavailable"}
        assert client.get("/unexpected-error").json() == {"error": "internal_error"}
        response = client.get("/validate?number=secret")
        assert response.status_code == 422
        assert "secret" not in response.text


def test_unsupported_provider_fails_before_imports():
    from techfi_shared.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        create_services(voice_settings(stt_provider="unsupported"))
