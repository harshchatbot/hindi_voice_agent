from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from techfi_voice.integrations.providers import playground_tts_settings
from techfi_voice.server import create_app
from test_foundations import voice_settings
from test_voice_milestone2 import HEADERS, make_session


def test_voice_presets_revalidate_fresh_settings_and_isolate_provider_credentials(monkeypatch):
    # An ambient role key must not be picked up again when switching providers.
    monkeypatch.setenv("TECHFI_TTS_API_KEY", "unrelated-ambient-role-key")
    base = voice_settings(
        SARVAM_API_KEY="synthetic-sarvam-key",
        google_application_credentials="/secure/synthetic-adc.json",
    )
    google = playground_tts_settings(base, "google")
    assert google is not base
    assert (google.tts_provider, google.tts_model, google.tts_voice_id) == (
        "google",
        "chirp3-hd",
        "hi-IN-Chirp3-HD-Achird",
    )
    assert google.tts_api_key is None
    assert google.google_application_credentials.get_secret_value() == "/secure/synthetic-adc.json"
    original = base.model_dump()
    selected = google.model_dump()
    for key in original:
        if key not in {"tts_provider", "tts_model", "tts_voice_id", "tts_api_key"}:
            assert selected[key] == original[key]
    assert base.tts_provider == "sarvam"
    restored = playground_tts_settings(google, "sarvam")
    assert restored.tts_api_key.get_secret_value() == "synthetic-sarvam-key"
    assert restored.tts_model == "bulbul:v3" and restored.tts_voice_id == "shubh"
    assert playground_tts_settings(base, "sarvam").tts_api_key == base.tts_api_key


def test_preset_model_and_voice_come_only_from_server_configuration():
    selected = playground_tts_settings(
        voice_settings(playground_google_voice_id="hi-IN-Chirp3-HD-Kore"), "google"
    )
    assert selected.tts_voice_id == "hi-IN-Chirp3-HD-Kore"
    with pytest.raises(ValidationError):
        playground_tts_settings(
            voice_settings(
                tts_provider="google",
                tts_model="chirp3-hd",
                tts_voice_id="hi-IN-Chirp3-HD-Achird",
                tts_api_key=None,
            ),
            "sarvam",
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"voice_provider": "unknown"},
        {"llm_provider": "unknown"},
        {"llm_provider": "sarvam", "llm_model": "client-controlled"},
        {"voice_provider": "google", "tts_voice_id": "client-controlled"},
        {"voice_provider": "sarvam", "tts_model": "client-controlled"},
        {"voice_provider": "google", "credentials": "private"},
    ],
)
def test_offer_rejects_unknown_choices_and_client_configuration_before_connecting(extra):
    connection = MagicMock(side_effect=AssertionError("must not open a peer"))
    app = create_app(voice_settings(), connection_factory=connection)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/offer", json={"sdp": "synthetic", "type": "offer"} | extra, headers=HEADERS
        )
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request"}
    connection.assert_not_called()


def test_offer_routes_each_voice_to_existing_factory_with_independent_settings(monkeypatch):
    class Transport:
        def __init__(self, *args):
            pass

        def event_handler(self, name):
            return lambda callback: callback

    monkeypatch.setattr("pipecat.transports.smallwebrtc.transport.SmallWebRTCTransport", Transport)
    connections, selected, sessions, executed = [], [], [], []

    def connection_factory(**kwargs):
        pc_id = f"opaque-peer-{len(connections)}"
        connection = SimpleNamespace(
            pc_id=pc_id,
            initialize=AsyncMock(),
            disconnect=AsyncMock(),
            get_answer=lambda: {"sdp": "synthetic-answer", "type": "answer", "pc_id": pc_id},
        )
        connections.append(connection)
        return connection

    service_factory = MagicMock()

    def builder(settings, transport, factory):
        assert factory is service_factory
        selected.append(settings)
        session = make_session()
        sessions.append(session)
        return session

    async def run(session, settings):
        executed.append(settings)
        await session.stop.wait()

    base = voice_settings(SARVAM_API_KEY="synthetic-sarvam", max_sessions=6)
    with TestClient(
        create_app(
            base,
            service_factory=service_factory,
            connection_factory=connection_factory,
            session_builder=builder,
            session_runner=run,
        )
    ) as client:
        choices = [
            (tts, llm)
            for tts in ("sarvam", "google", "sarvam-v4-flash")
            for llm in ("openai", "sarvam")
        ]
        for tts, llm in choices:
            extra = {"voice_provider": tts, "llm_provider": llm}
            response = client.post(
                "/api/offer",
                json={"sdp": "synthetic", "type": "offer"} | extra,
                headers=HEADERS,
            )
            assert response.status_code == 200
        assert [(s.tts_provider, s.llm_provider) for s in selected] == choices
        assert [s.llm_model for s in selected] == ["gpt-4.1-mini", "sarvam-105b-conversations"] * 3
        assert len({s.scope.session_id for s in sessions}) == 6
        assert selected[0] is not selected[1] and selected[0] is not base
        assert sessions[0].scope.session_id != sessions[1].scope.session_id
        for index in range(6):
            assert (
                client.post(f"/api/sessions/opaque-peer-{index}/stop", headers=HEADERS).status_code
                == 204
            )
    assert len(executed) == 6
    assert executed[0] is selected[0] and executed[1] is selected[1]
    for connection in connections:
        connection.disconnect.assert_awaited_once()
    service_factory.assert_not_called()  # Builder is mocked: no SDK clients or API requests.


def test_missing_selected_credentials_fail_without_peer_or_sensitive_response():
    settings = voice_settings(
        tts_provider="google",
        tts_model="chirp3-hd",
        tts_voice_id="hi-IN-Chirp3-HD-Achird",
        tts_api_key=None,
    )
    factory = MagicMock()
    with TestClient(
        create_app(settings, connection_factory=factory), raise_server_exceptions=False
    ) as client:
        response = client.post(
            "/api/offer", json={"sdp": "synthetic", "type": "offer"}, headers=HEADERS
        )
        assert response.status_code == 503
        assert response.json() == {"error": "provider_unavailable"}
    factory.assert_not_called()


def test_playground_assets_have_security_headers_and_no_configuration_secrets():
    with TestClient(create_app(voice_settings())) as client:
        html = client.get("/", headers={"Host": "localhost"})
        assert "TechFi Labs — Voice AI Playground" in html.text
        assert 'value="sarvam" selected' in html.text
        assert 'value="google"' in html.text
        assert 'role="status"' in html.text and 'for="voice-provider"' in html.text
        for path in ("/", "/styles.css", "/client.js"):
            response = client.get(path, headers={"Host": "localhost"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert "test-tts-secret" not in response.text
            assert "test-llm-secret" not in response.text
        assert (
            "text/css"
            in client.get("/styles.css", headers={"Host": "localhost"}).headers["content-type"]
        )
