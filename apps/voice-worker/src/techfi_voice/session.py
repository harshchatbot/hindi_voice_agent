import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from techfi_shared.contracts import CallOutcome, CallResult, SessionScope
from techfi_shared.logging import Event

from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import ServiceFactory

if TYPE_CHECKING:
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.transports.base_transport import BaseTransport

    from techfi_voice.monitor import SessionMonitor


@dataclass
class VoiceSession:
    scope: SessionScope
    task: "PipelineWorker"
    context: "LLMContext"
    failed: bool = False
    opted_out: bool = False
    outcome: CallOutcome = CallOutcome.COMPLETED
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    opt_out_requested: asyncio.Event = field(default_factory=asyncio.Event)
    monitor: "SessionMonitor | None" = None

    def result(self) -> CallResult:
        return CallResult(scope=self.scope, outcome=self.outcome)


def build_session(
    settings: VoiceSettings, transport: "BaseTransport", factory: ServiceFactory
) -> VoiceSession:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.clocks.system_clock import SystemClock
    from pipecat.frames.frames import TTSSpeakFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.turns.user_start import VADUserTurnStartStrategy
    from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from techfi_voice.latency import LatencyObserver
    from techfi_voice.monitor import SessionMonitor
    from techfi_voice.policy import OptOutProcessor

    services = factory(settings)
    context = LLMContext()
    scope = SessionScope(tenant_id=settings.tenant_id)
    clock = SystemClock()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=settings.vad_confidence,
                    start_secs=settings.vad_start_seconds,
                    stop_secs=settings.vad_stop_seconds,
                )
            ),
            **({"empty_user_turn": None} if services.require_finalized_transcripts else {}),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[
                    SpeechTimeoutUserTurnStopStrategy(
                        user_speech_timeout=settings.turn_silence_seconds,
                    )
                ],
            ),
        ),
    )

    async def opt_out():
        session.opted_out = True
        session.outcome = CallOutcome.OPTED_OUT
        session.opt_out_requested.set()
        await session.task.queue_frame(TTSSpeakFrame(text=settings.opt_out_reply))

    async def fail():
        session.failed = True
        session.outcome = CallOutcome.FAILED
        session.stop.set()

    async def bot_stopped():
        if session.opted_out:
            session.stop.set()

    async def stt_timeout():
        logging.getLogger("techfi").warning(
            Event.STT_TIMEOUT, extra={"session_id": scope.session_id}
        )

    if services.require_finalized_transcripts:
        connection_state: bool | None = None

        @services.stt.event_handler("on_connected")
        async def stt_connected(service: Any) -> None:
            nonlocal connection_state
            if connection_state is not True:
                logging.getLogger("techfi").info(
                    Event.STT_CONNECTED, extra={"session_id": scope.session_id}
                )
            connection_state = True

        @services.stt.event_handler("on_disconnected")
        async def stt_disconnected(service: Any) -> None:
            nonlocal connection_state
            if connection_state is not False:
                logging.getLogger("techfi").info(
                    Event.STT_DISCONNECTED, extra={"session_id": scope.session_id}
                )
            connection_state = False

        @services.stt.event_handler("on_connection_error")
        async def stt_connection_error(service: Any, *args: Any) -> None:
            # Provider error arguments may contain credentials or transcripts. Ignore them.
            if not session.stop.is_set():
                logging.getLogger("techfi").error(
                    Event.STT_CONNECTION_FAILED, extra={"session_id": scope.session_id}
                )
                await fail()

        @services.stt.event_handler("on_usable_changed")
        async def stt_usable_changed(service: Any, usable: bool) -> None:
            if not usable:
                await stt_connection_error(service)

    monitor = SessionMonitor(
        settings.provider_timeout_seconds,
        fail,
        bot_stopped,
        require_finalized_transcripts=services.require_finalized_transcripts,
        on_stt_timeout=stt_timeout if services.require_finalized_transcripts else None,
    )
    output = transport.output()
    latency = LatencyObserver(
        scope.session_id,
        services.stt,
        services.llm,
        services.tts,
        output,
        clock.get_time,
        require_finalized_transcripts=services.require_finalized_transcripts,
    )
    # Supported TTS event: ignore the text argument entirely; never retain or log it.
    from pipecat.services.tts_service import TTSService

    if isinstance(services.tts, TTSService):

        @services.tts.event_handler("on_tts_request")
        async def tts_request(service: Any, context_id: str, text: str) -> None:
            latency.tts_request(context_id)

    transcript_gate = []
    if services.require_finalized_transcripts:
        from techfi_voice.realtime_stt import FinalTranscriptGate

        transcript_gate.append(FinalTranscriptGate(scope.session_id))

    pipeline = Pipeline(
        [
            transport.input(),
            services.stt,
            *transcript_gate,
            OptOutProcessor(settings.opt_out_phrases, opt_out),
            aggregators.user(),
            services.llm,
            services.tts,
            output,
            aggregators.assistant(),
        ]
    )
    task = PipelineWorker(
        pipeline,
        enable_rtvi=False,
        enable_turn_tracking=False,
        enable_tracing=False,
        observers=[monitor, latency],
        clock=clock,
        idle_timeout_secs=settings.idle_timeout_seconds,
        setup_timeout_secs=settings.connection_timeout_seconds,
        start_timeout_secs=settings.connection_timeout_seconds,
        cancel_timeout_secs=settings.cleanup_timeout_seconds,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=False,
            enable_usage_metrics=False,
        ),
    )
    session = VoiceSession(scope, task, context, monitor=monitor)

    @task.event_handler("on_pipeline_error")
    async def pipeline_error(worker: Any, frame: Any) -> None:
        await fail()

    @task.event_handler("on_setup_timeout")
    async def setup_timeout(worker: Any, *args: Any) -> None:
        await fail()

    @task.event_handler("on_pipeline_timeout")
    async def pipeline_timeout(worker: Any, *args: Any) -> None:
        await fail()

    @task.event_handler("on_idle_timeout")
    async def idle_timeout(worker: Any, *args: Any) -> None:
        session.outcome = CallOutcome.TIMED_OUT
        session.stop.set()

    return session
