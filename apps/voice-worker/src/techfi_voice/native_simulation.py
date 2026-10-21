"""Explicitly started native development simulation; no inbound network transport."""

import argparse
import asyncio
import os
import sys
from datetime import date
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from techfi_voice.collection import CollectionWorkflow, DemoCase
from techfi_voice.config import VoiceSettings

FIXTURES = MappingProxyType(
    {
        "fictional-emi-001": DemoCase(
            fictional=True,
            bank_name="Demo Prerna Bank",
            customer_name="Demo Asha Verma",
            emi_amount="4500.00",
            due_date="2026-10-25",
            loan_type="personal",
            payment_status="due",
            payment_methods=("official_app", "bank_branch"),
        )
    }
)


class NativeSettings(BaseSettings):
    """Only opt-in and Sarvam credentials are read from the environment/file."""

    model_config = SettingsConfigDict(
        env_prefix="TECHFI_NATIVE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    environment: Literal["development", "production"] = "production"
    sarvam_api_key: SecretStr = Field(validation_alias="SARVAM_API_KEY", min_length=1, repr=False)


class _PipelineSettings(VoiceSettings):
    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, **kwargs):
        # Native configuration must never inherit TECHFI_* record, prompt, endpoint or policy data.
        return (init_settings,)


def pipeline_settings(config: NativeSettings, persona: Literal["male", "female"]) -> VoiceSettings:
    if config.environment != "development" or persona not in {"male", "female"}:
        raise ValueError("native development authorization required")
    return _PipelineSettings(
        _env_file=None,
        environment="development",
        tenant_id=uuid4(),
        agent_type="soft-collection",
        collection_case=FIXTURES["fictional-emi-001"],
        persona=persona,
        stt_provider="sarvam-realtime",
        stt_model="saaras:v4",
        llm_provider="sarvam",
        llm_model="sarvam-105b-conversations",
        tts_provider="sarvam",
        tts_model="bulbul:v3",
        tts_voice_id="shubh" if persona == "male" else "ritu",
        stt_api_key=config.sarvam_api_key,
        llm_api_key=config.sarvam_api_key,
        tts_api_key=config.sarvam_api_key,
    )


def require_local_console() -> None:
    # Refuse supported remote shells and noninteractive deployment. Not device/OS attestation.
    if (
        sys.platform != "darwin"
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))
    ):
        raise ValueError("native simulation requires an interactive local macOS console")


def build_native_session(settings, transport, factory):
    from techfi_voice.session import _assemble_session

    # One immutable fixture, one fresh workflow. No production admission or stores are constructed.
    if (
        not isinstance(settings, _PipelineSettings)
        or settings.environment != "development"
        or settings.collection_case != FIXTURES["fictional-emi-001"]
    ):
        raise ValueError("native fictional fixture required")
    workflow = CollectionWorkflow(
        FIXTURES["fictional-emi-001"],
        settings.assistant_name,
        settings.persona,
        settings.opt_out_phrases,
        # Fixed fictional scenario calendar, independent of hours.
        today=lambda: date(2026, 10, 19),
    )
    return _assemble_session(settings, transport, factory, workflow=workflow)


def native_transport():
    # Optional dependency imported only by this executable, never by production startup.
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

    class OwnedLocalAudioTransport(LocalAudioTransport):
        def close(self):
            # Pipecat 1.12 cleans streams but exposes no owner cleanup for its PyAudio instance.
            # Keep this pinned-version ownership detail confined to the native adapter.
            self._pyaudio.terminate()

    return OwnedLocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            audio_in_channels=1,
            audio_out_channels=1,
        )
    )


async def run_native(settings, transport_factory=None, service_factory=None):
    from pipecat.frames.frames import TTSSpeakFrame

    from techfi_voice.integrations.providers import create_services
    from techfi_voice.runtime import run_session

    transport = (transport_factory or native_transport)()
    try:
        session = build_native_session(settings, transport, service_factory or create_services)

        @session.task.event_handler("on_pipeline_started")
        async def greet(worker, frame):
            await worker.queue_frame(TTSSpeakFrame(text=session.greeting))

        try:
            return await run_session(session, settings)
        except asyncio.CancelledError:
            # run_session has already marked cancellation and cleared context/pending proposals.
            return session.result()
    finally:
        # run_session owns bounded worker/stream teardown before PortAudio termination.
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Native fictional collection simulation (macOS)")
    parser.add_argument("--development", action="store_true", required=True)
    parser.add_argument("--fictional-data-only", action="store_true", required=True)
    parser.add_argument("--persona", choices=("male", "female"), default="male")
    parser.add_argument("--check-config", action="store_true", help="No audio or provider clients")
    args = parser.parse_args()
    from loguru import logger as dependency_logger
    from techfi_shared.logging import configure_logging

    dependency_logger.remove()
    logger = configure_logging()
    try:
        require_local_console()
        config = NativeSettings()
        settings = pipeline_settings(config, args.persona)
        from techfi_voice.integrations.providers import validate_provider_settings

        validate_provider_settings(settings)
        print("SIMULATION — FICTIONAL DATA. Cloud audio processing; Ctrl-C to end.", flush=True)
        if args.check_config:
            print("Native simulation configuration valid; audio and providers not started.")
            return
        result = asyncio.run(run_native(settings))
        # Explicit local report only. No facts, transcript, credentials or complete settings dump.
        print(result.collection.model_dump_json())
        if result.outcome.value == "failed":
            raise SystemExit(1)
    except KeyboardInterrupt:
        print("Fictional simulation stopped.")
    except Exception:
        # Third-party errors may contain transcripts/credentials/device data. Never print them.
        logger.error("native_simulation_failed")
        print("Check local development authorization, Sarvam configuration and native audio setup.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
