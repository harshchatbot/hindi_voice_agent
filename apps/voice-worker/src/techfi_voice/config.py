from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from techfi_shared.config import Settings

from techfi_voice.collection import DemoCase
from techfi_voice.prompt import DEFAULT_STYLE


class StartupSettings(Settings):
    environment: Literal["development", "production"] = "production"


class VoiceSettings(StartupSettings):
    agent_type: Literal["general", "soft-collection"] = "general"
    persona: Literal["male", "female"] = "male"
    collection_case: DemoCase | None = Field(default=None, repr=False)
    collection_demo_enabled: bool = False
    collection_start_hour: int = Field(default=8, ge=8, le=18)
    collection_end_hour: int = Field(default=19, ge=9, le=19)
    collection_max_attempts_per_day: int = Field(default=1, ge=1, le=3)
    collection_retry_gap_seconds: float = Field(default=3600, ge=60, le=86400)
    assistant_male_name: str = Field(default="Aarav", min_length=1, max_length=24)
    assistant_female_name: str = Field(default="Asha", min_length=1, max_length=24)
    playground_sarvam_female_voice_id: str = "ritu"
    playground_sarvam_v4_female_voice_id: str = "ritu_hi_customer"
    playground_google_female_voice_id: str = "hi-IN-Chirp3-HD-Kore"
    tenant_id: UUID
    stt_provider: str = "sarvam-realtime"
    llm_provider: str = "openai"
    tts_provider: str = "sarvam"
    stt_api_key: SecretStr = Field(min_length=1)
    llm_api_key: SecretStr = Field(min_length=1)
    tts_api_key: SecretStr | None = Field(default=None, min_length=1)
    google_application_credentials: SecretStr | None = Field(
        default=None, validation_alias="GOOGLE_APPLICATION_CREDENTIALS", exclude=True, repr=False
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENAI_API_KEY", exclude=True, repr=False
    )
    sarvam_api_key: SecretStr | None = Field(
        default=None, validation_alias="SARVAM_API_KEY", exclude=True, repr=False
    )
    stt_model: str = Field(default="saaras:v4", min_length=1)
    llm_model: str = Field(default="gpt-4.1-mini", min_length=1)
    tts_model: str = Field(default="bulbul:v3", min_length=1)
    tts_voice_id: str = Field(default="shubh", min_length=1)
    playground_sarvam_model: str = Field(default="bulbul:v3", min_length=1)
    playground_sarvam_voice_id: str = Field(default="shubh", min_length=1)
    playground_google_model: str = Field(default="chirp3-hd", min_length=1)
    playground_google_voice_id: str = Field(default="hi-IN-Chirp3-HD-Achird", min_length=1)
    playground_openai_model: str = Field(default="gpt-4.1-mini", min_length=1)
    playground_sarvam_llm_model: str = Field(default="sarvam-105b-conversations", min_length=1)
    playground_sarvam_v4_model: str = Field(default="bulbul:v4-flash", min_length=1)
    playground_sarvam_v4_voice_id: str = Field(default="shubh_hi_customer", min_length=1)
    sarvam_llm_base_url: str = "https://api.sarvam.ai/v1"
    sarvam_tts_websocket_url: str = "wss://api.sarvam.ai/text-to-speech/ws"
    language: str = "hi-IN"
    stt_mode: str = "transcribe"
    stt_stream_type: Literal["balanced", "fast"] = "balanced"
    stt_keepalive_seconds: float = Field(default=10, ge=1, le=60)
    stt_final_timeout_seconds: float = Field(default=5, ge=0.5, le=30)
    system_prompt: str = Field(default=DEFAULT_STYLE, min_length=1, repr=False)
    greeting: str = Field(
        default=(
            "Namaste. Main ek AI sahayak hoon. Kya aap Hindi ya Hinglish mein baat karna chahenge?"
        ),
        min_length=1,
    )
    opt_out_reply: str = Field(
        default="Ji, main ab yeh baatcheet band kar raha hoon. Dhanyavaad.", min_length=1
    )
    opt_out_phrases: tuple[str, ...] = (
        "stop",
        "exit",
        "goodbye",
        "bye",
        "opt out",
        "do not call",
        "don't call",
        "band karo",
        "call mat karo",
        "बंद करो",
        "कॉल मत करो",
        "बात नहीं करनी",
    )
    max_session_seconds: float = Field(default=600, ge=1, le=3600)
    provider_timeout_seconds: float = Field(default=20, ge=1, le=120)
    connection_timeout_seconds: float = Field(default=20, ge=1, le=60)
    cleanup_timeout_seconds: float = Field(default=10, ge=1, le=30)
    idle_timeout_seconds: float = Field(default=90, ge=5, le=600)
    opt_out_grace_seconds: float = Field(default=8, ge=1, le=30)
    vad_confidence: float = Field(default=0.7, ge=0.1, le=1)
    vad_start_seconds: float = Field(default=0.2, ge=0.05, le=1)
    vad_stop_seconds: float = Field(default=0.4, ge=0.1, le=2)
    turn_silence_seconds: float = Field(default=0.6, ge=0, le=3)
    host: str = "127.0.0.1"
    port: int = Field(default=7860, ge=1024, le=65535)
    max_sessions: int = Field(default=4, ge=1, le=16)

    # Aliases preserve conventional provider env vars while supporting explicit injection in tests.
    model_config = Settings.model_config | {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def select_credentials(cls, data):
        data = dict(data)
        # A conventional provider key is used only for its own adapter. Explicit role keys win.
        for role, provider, conventional in (
            ("stt", "sarvam", "SARVAM_API_KEY"),
            ("stt", "sarvam-realtime", "SARVAM_API_KEY"),
            ("stt", "openai", "OPENAI_API_KEY"),
            ("llm", "openai", "OPENAI_API_KEY"),
            ("llm", "sarvam", "SARVAM_API_KEY"),
            ("tts", "sarvam", "SARVAM_API_KEY"),
            ("tts", "sarvam-v4-flash", "SARVAM_API_KEY"),
        ):
            selected = data.get(f"{role}_provider", cls.model_fields[f"{role}_provider"].default)
            secret = data.get(conventional)
            if selected == provider and f"{role}_api_key" not in data and secret is not None:
                data[f"{role}_api_key"] = secret
        return data

    @model_validator(mode="after")
    def require_tts_credentials(self):
        if self.collection_start_hour >= self.collection_end_hour:
            raise ValueError("invalid collection calling window")
        if (self.agent_type == "soft-collection") != (self.collection_case is not None):
            raise ValueError("collection case is required only for collection agents")
        if self.tts_provider != "google" and self.tts_api_key is None:
            raise ValueError("selected TTS provider requires a role credential")
        return self

    @field_validator("sarvam_llm_base_url", "sarvam_tts_websocket_url")
    @classmethod
    def trusted_sarvam_endpoint(cls, value: str, info: ValidationInfo) -> str:
        # Credentials must never be redirected to arbitrary hosts or URLs with userinfo.
        expected = {
            "sarvam_llm_base_url": "https://api.sarvam.ai/v1",
            "sarvam_tts_websocket_url": "wss://api.sarvam.ai/text-to-speech/ws",
        }
        if value != expected[info.field_name]:
            raise ValueError("unsupported Sarvam endpoint")
        return value

    @field_validator("assistant_male_name", "assistant_female_name")
    @classmethod
    def safe_assistant_name(cls, value: str) -> str:
        if not value.replace(" ", "").isalpha():
            raise ValueError("assistant name must contain letters only")
        return value

    @property
    def assistant_name(self):
        return self.assistant_male_name if self.persona == "male" else self.assistant_female_name

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in ("127.0.0.1", "localhost"):
            raise ValueError("local browser transport requires loopback binding")
        return value

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        if value not in ("hi", "hi-IN", "en", "en-IN"):
            raise ValueError("supported language must be Hindi or Indian English")
        return value

    @field_validator("opt_out_phrases")
    @classmethod
    def nonempty_phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not phrase.strip() for phrase in value):
            raise ValueError("opt-out phrases must not be empty")
        return value

    @field_validator("stt_api_key", "llm_api_key", "tts_api_key", "google_application_credentials")
    @classmethod
    def real_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        secret = value.get_secret_value()
        if not secret.strip() or secret.startswith("<"):
            raise ValueError("credential is missing or a placeholder")
        return value
