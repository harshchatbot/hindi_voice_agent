import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from techfi_shared.errors import ConfigurationError, ProviderError

from techfi_voice.config import VoiceSettings
from techfi_voice.prompt import agent_prompt

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


def session_profile(settings: VoiceSettings) -> dict[str, str]:
    """Allowlisted configuration diagnostics; no credential or arbitrary config serialization."""
    realtime = settings.stt_provider == "sarvam-realtime"
    return {
        "stt_profile": {
            "sarvam-realtime": "sarvam_realtime_v4",
            "sarvam": "sarvam_legacy",
            "openai": "openai_whisper",
        }.get(settings.stt_provider, "other"),
        "llm_profile": (
            "openai_gpt_4_1_mini"
            if settings.llm_provider == "openai" and settings.llm_model == "gpt-4.1-mini"
            else "sarvam_105b_conversations"
            if settings.llm_provider == "sarvam"
            else "other"
        ),
        "tts_profile": {
            "sarvam": "sarvam_bulbul_v3",
            "sarvam-v4-flash": "sarvam_bulbul_v4_flash",
            "google": "google_chirp3_hd",
        }.get(settings.tts_provider, "other"),
        "stream_type": settings.stt_stream_type if realtime else "not_applicable",
        "endpointing": "manual" if realtime else "not_applicable",
    }


def playground_tts_settings(settings: VoiceSettings, provider: str) -> VoiceSettings:
    """Resolve a browser choice to trusted server configuration, never client voice IDs."""
    presets = {
        "sarvam": (settings.playground_sarvam_model, settings.playground_sarvam_voice_id),
        "google": (settings.playground_google_model, settings.playground_google_voice_id),
        "sarvam-v4-flash": (
            settings.playground_sarvam_v4_model,
            settings.playground_sarvam_v4_voice_id,
        ),
    }
    if provider not in presets:
        raise ConfigurationError()
    model, voice = presets[provider]
    values = settings.model_dump()
    values.update(tts_provider=provider, tts_model=model, tts_voice_id=voice)
    # A role key is reusable only for the provider it was configured for.
    sarvam_family = {"sarvam", "sarvam-v4-flash"}
    same_account = provider in sarvam_family and settings.tts_provider in sarvam_family
    if provider != settings.tts_provider and not same_account:
        values["tts_api_key"] = (
            settings.sarvam_api_key if provider in ("sarvam", "sarvam-v4-flash") else None
        )
    selected = VoiceSettings(
        _env_file=None,
        **values,
        SARVAM_API_KEY=settings.sarvam_api_key,
        OPENAI_API_KEY=settings.openai_api_key,
        google_application_credentials=settings.google_application_credentials,
    )
    validate_provider_settings(selected)
    return selected


def playground_settings(
    settings: VoiceSettings, voice_provider: str, llm_provider: str, persona: str = "male"
) -> VoiceSettings:
    """Freeze both allowlisted choices per session; never reuse a different provider's role key."""
    models = {
        "openai": settings.playground_openai_model,
        "sarvam": settings.playground_sarvam_llm_model,
    }
    if llm_provider not in models:
        raise ConfigurationError()
    if persona not in ("male", "female"):
        raise ConfigurationError()
    # Resolve LLM first: TTS helper subsequently validates the complete snapshot.
    values = settings.model_dump()
    values.update(llm_provider=llm_provider, llm_model=models[llm_provider], persona=persona)
    if llm_provider != settings.llm_provider:
        key = settings.sarvam_api_key if llm_provider == "sarvam" else settings.openai_api_key
        if key is None:
            raise ConfigurationError()
        values["llm_api_key"] = key
    selected = VoiceSettings(
        _env_file=None,
        **values,
        SARVAM_API_KEY=settings.sarvam_api_key,
        OPENAI_API_KEY=settings.openai_api_key,
        google_application_credentials=settings.google_application_credentials,
    )
    if persona == "female":
        updates = {
            "playground_sarvam_voice_id": settings.playground_sarvam_female_voice_id,
            "playground_sarvam_v4_voice_id": settings.playground_sarvam_v4_female_voice_id,
            "playground_google_voice_id": settings.playground_google_female_voice_id,
        }
        selected = selected.model_copy(update=updates)
    selected = playground_tts_settings(selected, voice_provider)
    approved = {
        ("sarvam", "male"): {
            "shubh",
            "aditya",
            "rahul",
            "rohan",
            "amit",
            "dev",
            "ratan",
            "varun",
            "manan",
            "sumit",
            "kabir",
            "aayan",
            "ashutosh",
            "advait",
            "anand",
            "tarun",
            "sunny",
            "mani",
            "gokul",
            "vijay",
            "mohit",
            "rehan",
            "soham",
        },
        ("sarvam", "female"): {
            "ritu",
            "priya",
            "neha",
            "pooja",
            "simran",
            "kavya",
            "ishita",
            "shreya",
            "roopa",
            "tanya",
            "shruti",
            "suhani",
            "kavitha",
            "rupali",
        },
        ("sarvam-v4-flash", "male"): {"shubh_hi_customer", "shubh_hi_ecomm", "shubh_enhi_banking"},
        ("sarvam-v4-flash", "female"): {"ritu_hi_customer"},
        ("google", "male"): {"hi-IN-Chirp3-HD-Achird"},
        ("google", "female"): {"hi-IN-Chirp3-HD-Kore"},
    }
    if selected.tts_voice_id not in approved.get((voice_provider, persona), set()):
        raise ConfigurationError()
    return selected


def validate_provider_settings(settings: VoiceSettings) -> None:
    """Validate provider-specific capabilities without creating clients or opening sockets."""
    if (
        settings.stt_provider not in ("openai", "sarvam", "sarvam-realtime", "deepgram")
        or settings.llm_provider not in ("openai", "groq", "sarvam")
        or settings.tts_provider not in ("sarvam", "sarvam-v4-flash", "cartesia", "google")
    ):
        raise ConfigurationError()
    if settings.llm_provider == "sarvam" and settings.llm_model != "sarvam-105b-conversations":
        raise ConfigurationError()
    if settings.tts_provider == "sarvam-v4-flash":
        if (
            settings.tts_model != "bulbul:v4-flash"
            or settings.tts_voice_id
            not in {"shubh_hi_customer", "shubh_hi_ecomm", "shubh_enhi_banking", "ritu_hi_customer"}
            or settings.language not in {"hi", "hi-IN"}
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
        from techfi_voice.integrations.sarvam import ConfiguredSarvamRealtimeSTTService

        return ConfiguredSarvamRealtimeSTTService(
            api_key=settings.stt_api_key.get_secret_value(),
            endpointing="manual",
            sample_rate=16000,
            keepalive_timeout=settings.stt_keepalive_seconds,
            settings=ConfiguredSarvamRealtimeSTTService.Settings(
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
    if settings.llm_provider == "sarvam":
        from techfi_voice.integrations.sarvam_experimental import SarvamConversationLLMService

        return SarvamConversationLLMService(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.sarvam_llm_base_url,
            timeout_seconds=settings.provider_timeout_seconds,
            settings=SarvamConversationLLMService.Settings(
                model=settings.llm_model,
                system_instruction=agent_prompt(settings),
            ),
        )
    if settings.llm_provider == "openai":
        from techfi_voice.integrations.openai import ConfiguredOpenAILLMService

        return ConfiguredOpenAILLMService(
            api_key=settings.llm_api_key.get_secret_value(),
            timeout_seconds=settings.provider_timeout_seconds,
            settings=ConfiguredOpenAILLMService.Settings(
                model=settings.llm_model,
                system_instruction=agent_prompt(settings),
            ),
        )
    from pipecat.services.groq.llm import GroqLLMService

    return GroqLLMService(
        api_key=settings.llm_api_key.get_secret_value(),
        settings=GroqLLMService.Settings(
            model=settings.llm_model,
            system_instruction=agent_prompt(settings),
        ),
    )


def _tts(settings: VoiceSettings):
    from pipecat.transcriptions.language import Language

    language = Language(settings.language)
    if settings.tts_provider == "sarvam-v4-flash":
        from techfi_voice.integrations.sarvam_experimental import SarvamV4TTSService

        return SarvamV4TTSService(
            api_key=settings.tts_api_key.get_secret_value(),
            url=settings.sarvam_tts_websocket_url,
            voice=settings.tts_voice_id,
            language="hi-IN",
        )
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
        from techfi_voice.integrations.sarvam import ConfiguredSarvamTTSService

        return ConfiguredSarvamTTSService(
            api_key=settings.tts_api_key.get_secret_value(),
            sample_rate=24000,
            reconnect_on_error=False,
            settings=ConfiguredSarvamTTSService.Settings(
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
