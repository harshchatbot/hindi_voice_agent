import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from techfi_shared.errors import ConfigurationError, ProviderError

from techfi_voice.config import VoiceSettings
from techfi_voice.prompt import conversational_prompt

if TYPE_CHECKING:
    from pipecat.processors.frame_processor import FrameProcessor


@dataclass(frozen=True)
class Services:
    stt: "FrameProcessor"
    llm: "FrameProcessor"
    tts: "FrameProcessor"
    require_finalized_transcripts: bool = False


class ServiceFactory(Protocol):
    def __call__(self, settings: VoiceSettings) -> Services: ...


def playground_tts_settings(settings: VoiceSettings, provider: str) -> VoiceSettings:
    """Resolve a browser choice to trusted server configuration, never client voice IDs."""
    presets = {
        "sarvam": (settings.playground_sarvam_model, settings.playground_sarvam_voice_id),
        "google": (settings.playground_google_model, settings.playground_google_voice_id),
    }
    if provider not in presets:
        raise ConfigurationError()
    model, voice = presets[provider]
    values = settings.model_dump()
    values.update(tts_provider=provider, tts_model=model, tts_voice_id=voice)
    # A role key is reusable only for the provider it was configured for.
    if provider != settings.tts_provider:
        values["tts_api_key"] = settings.sarvam_api_key if provider == "sarvam" else None
    selected = VoiceSettings(
        _env_file=None,
        **values,
        SARVAM_API_KEY=settings.sarvam_api_key,
        google_application_credentials=settings.google_application_credentials,
    )
    validate_provider_settings(selected)
    return selected


def validate_provider_settings(settings: VoiceSettings) -> None:
    """Validate provider-specific capabilities without creating clients or opening sockets."""
    if (
        settings.stt_provider not in ("openai", "sarvam", "sarvam-realtime", "deepgram")
        or settings.llm_provider not in ("openai", "groq")
        or settings.tts_provider not in ("sarvam", "cartesia", "google")
    ):
        raise ConfigurationError()
    if settings.stt_provider == "openai":
        if settings.stt_model != "whisper-1" or settings.stt_mode != "transcribe":
            raise ConfigurationError()
    if settings.stt_provider == "sarvam-realtime":
        if settings.stt_model != "saaras:v4":
            raise ConfigurationError()
        if settings.stt_mode not in ("transcribe", "codemix", "verbatim", "translit"):
            raise ConfigurationError()
    if settings.stt_provider == "sarvam":
        if settings.stt_model not in ("saaras:v3", "saaras:v4"):
            raise ConfigurationError()
        if settings.stt_mode not in ("transcribe", "codemix", "verbatim", "translit"):
            raise ConfigurationError()
    if settings.tts_provider == "sarvam":
        from pipecat.services.sarvam.tts import SarvamTTSSpeakerV3

        if settings.tts_model != "bulbul:v3":
            raise ConfigurationError()
        if settings.tts_voice_id not in {speaker.value for speaker in SarvamTTSSpeakerV3}:
            raise ConfigurationError()
    if settings.tts_provider == "google":
        language = {"hi": "hi-IN", "en": "en-IN"}.get(settings.language, settings.language)
        if settings.tts_model != "chirp3-hd" or not re.fullmatch(
            re.escape(language) + r"-Chirp3-HD-[A-Za-z]+", settings.tts_voice_id
        ):
            raise ConfigurationError()


def _stt(settings: VoiceSettings):
    from pipecat.transcriptions.language import Language

    language = Language(settings.language)
    if settings.stt_provider == "openai":
        from techfi_voice.integrations.openai import ConfiguredOpenAISTTService

        return ConfiguredOpenAISTTService(
            api_key=settings.stt_api_key.get_secret_value(),
            timeout_seconds=settings.provider_timeout_seconds,
            settings=ConfiguredOpenAISTTService.Settings(
                model=settings.stt_model,
                language=language,
            ),
        )
    if settings.stt_provider == "sarvam-realtime":
        from pipecat.services.sarvam.stt import SarvamRealtimeSTTService

        return SarvamRealtimeSTTService(
            api_key=settings.stt_api_key.get_secret_value(),
            endpointing="manual",
            sample_rate=16000,
            keepalive_timeout=settings.stt_keepalive_seconds,
            settings=SarvamRealtimeSTTService.Settings(
                model=settings.stt_model,
                language_code={"hi": "hi-IN", "en": "en-IN"}.get(
                    settings.language, settings.language
                ),
                stream_type=settings.stt_stream_type,
                mode=settings.stt_mode,
            ),
        )
    if settings.stt_provider == "sarvam":
        from pipecat.services.sarvam.stt import SarvamSTTService

        return SarvamSTTService(
            api_key=settings.stt_api_key.get_secret_value(),
            mode=settings.stt_mode,
            sample_rate=16000,
            settings=SarvamSTTService.Settings(
                model=settings.stt_model,
                language=language,
                vad_signals=False,
            ),
        )
    from pipecat.services.deepgram.stt import DeepgramSTTService

    return DeepgramSTTService(
        api_key=settings.stt_api_key.get_secret_value(),
        mip_opt_out=True,
        settings=DeepgramSTTService.Settings(model=settings.stt_model, language=language),
    )


def _llm(settings: VoiceSettings):
    if settings.llm_provider == "openai":
        from techfi_voice.integrations.openai import ConfiguredOpenAILLMService

        return ConfiguredOpenAILLMService(
            api_key=settings.llm_api_key.get_secret_value(),
            timeout_seconds=settings.provider_timeout_seconds,
            settings=ConfiguredOpenAILLMService.Settings(
                model=settings.llm_model,
                system_instruction=conversational_prompt(settings.system_prompt),
            ),
        )
    from pipecat.services.groq.llm import GroqLLMService

    return GroqLLMService(
        api_key=settings.llm_api_key.get_secret_value(),
        settings=GroqLLMService.Settings(
            model=settings.llm_model,
            system_instruction=conversational_prompt(settings.system_prompt),
        ),
    )


def _tts(settings: VoiceSettings):
    from pipecat.transcriptions.language import Language

    language = Language(settings.language)
    if settings.tts_provider == "google":
        from techfi_voice.integrations.google import ConfiguredGoogleTTSService

        return ConfiguredGoogleTTSService(
            credentials_path=(
                settings.google_application_credentials.get_secret_value()
                if settings.google_application_credentials
                else None
            ),
            sample_rate=24000,
            settings=ConfiguredGoogleTTSService.Settings(
                voice=settings.tts_voice_id,
                language=Language(
                    {"hi": "hi-IN", "en": "en-IN"}.get(settings.language, settings.language)
                ),
            ),
        )
    if settings.tts_provider == "sarvam":
        from pipecat.services.sarvam.tts import SarvamTTSService

        return SarvamTTSService(
            api_key=settings.tts_api_key.get_secret_value(),
            sample_rate=24000,
            reconnect_on_error=False,
            settings=SarvamTTSService.Settings(
                model=settings.tts_model,
                voice=settings.tts_voice_id,
                language=language,
            ),
        )
    from pipecat.services.cartesia.tts import CartesiaTTSService

    return CartesiaTTSService(
        api_key=settings.tts_api_key.get_secret_value(),
        settings=CartesiaTTSService.Settings(
            model=settings.tts_model,
            voice=settings.tts_voice_id,
            language=language,
        ),
    )


def create_services(settings: VoiceSettings) -> Services:
    validate_provider_settings(settings)
    try:
        return Services(
            stt=_stt(settings),
            llm=_llm(settings),
            tts=_tts(settings),
            require_finalized_transcripts=settings.stt_provider == "sarvam-realtime",
        )
    except ValueError:
        raise ConfigurationError() from None
    except Exception:
        raise ProviderError() from None
