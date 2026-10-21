import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from techfi_shared.contracts import CallOutcome, CallResult, SessionScope
from techfi_shared.logging import Event

from techfi_voice.config import VoiceSettings
from techfi_voice.integrations.providers import ServiceFactory, session_profile

if TYPE_CHECKING:
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.transports.base_transport import BaseTransport

    from techfi_voice.collection import CollectionWorkflow
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
    collection: "CollectionWorkflow | None" = field(default=None, repr=False)
    calling_window_remaining: Callable[[], float] | None = field(default=None, repr=False)
    closing: bool = False
    greeting: str | None = field(default=None, repr=False)

    def result(self) -> CallResult:
        if self.collection:
            from techfi_voice.collection import CollectionCallResult

            return CollectionCallResult(
                scope=self.scope, outcome=self.outcome, collection=self.collection.result()
            )
        return CallResult(scope=self.scope, outcome=self.outcome)


def build_session(
    settings: VoiceSettings,
    transport: "BaseTransport",
    factory: ServiceFactory,
    *,
    collection_policy=None,
) -> VoiceSession:
    """Production admission wrapper; native development never calls this wrapper."""
    workflow = None
    if settings.agent_type == "soft-collection":
        from techfi_voice.collection import CollectionPolicyError, CollectionWorkflow

        if collection_policy is None or not collection_policy.within_hours():
            raise CollectionPolicyError()
        workflow = CollectionWorkflow(
            settings.collection_case,
            settings.assistant_name,
            settings.persona,
            settings.opt_out_phrases,
            today=lambda: collection_policy.now().date(),
        )
    return _assemble_session(
        settings,
        transport,
        factory,
        workflow=workflow,
        collection_allowed=collection_policy.within_hours if workflow else None,
        calling_window_remaining=collection_policy.remaining_seconds if workflow else None,
    )


def _assemble_session(
    settings: VoiceSettings,
    transport: "BaseTransport",
    factory: ServiceFactory,
    *,
    workflow=None,
    collection_allowed: Callable[[], bool] | None = None,
    calling_window_remaining: Callable[[], float] | None = None,
) -> VoiceSession:
    """Internal pipeline assembly. Admission belongs to the invoking entry point."""
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
    from pipecat.services.stt_service import SegmentedSTTService
    from pipecat.turns.user_start import VADUserTurnStartStrategy
    from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from techfi_voice.latency import LatencyObserver
    from techfi_voice.monitor import SessionMonitor
    from techfi_voice.policy import OptOutProcessor

    services = factory(settings)
    # Segmented HTTP STT also queues delayed results; it needs the same fence
    # before opt-out/context processing, not just the realtime adapter.
    fenced = (
        workflow is not None
        or services.require_finalized_transcripts
        or isinstance(services.stt, SegmentedSTTService)
    )
    context = LLMContext()
    scope = SessionScope(tenant_id=settings.tenant_id)
    logging.getLogger("techfi").info(
        Event.SESSION_CONFIGURATION,
        extra={
            "session_id": scope.session_id,
            "transcript_fence": fenced,
            **session_profile(settings),
        },
    )
    clock = SystemClock()
    stop_strategy = SpeechTimeoutUserTurnStopStrategy(
        user_speech_timeout=settings.turn_silence_seconds,
    )
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
            **({"empty_user_turn": None} if fenced else {}),
            # The final-transcript fence must expire before Pipecat's generic
            # no-progress fallback can close a turn and accept delayed text.
            user_turn_stop_timeout=(
                settings.stt_final_timeout_seconds + settings.turn_silence_seconds + 1
                if fenced
                else 5
            ),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[stop_strategy],
            ),
        ),
    )

    async def opt_out(text):
        if workflow:
            from techfi_voice.policy import requests_do_not_call

            workflow.opt_out(requests_do_not_call(text))
        session.opted_out = True
        session.outcome = CallOutcome.OPTED_OUT
        session.opt_out_requested.set()
        await session.task.queue_frame(TTSSpeakFrame(text=settings.opt_out_reply))

    async def fail():
        session.failed = True
        session.outcome = CallOutcome.FAILED
        session.stop.set()

    async def bot_stopped():
        if session.opted_out or session.closing:
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
        require_finalized_transcripts=fenced,
        on_stt_timeout=stt_timeout if fenced else None,
        external_stt_deadline=fenced,
    )
    output = transport.output()
    latency = LatencyObserver(
        scope.session_id,
        services.stt,
        services.llm,
        services.tts,
        output,
        clock.get_time,
        require_finalized_transcripts=fenced,
    )
    latency.bind(aggregators.user(), stop_strategy)
    # Retain the event hook for older adapters. Unknown request ownership is
    # withheld by the bound observer; raw text is never inspected or retained.
    from pipecat.services.tts_service import TTSService

    if isinstance(services.tts, TTSService) and not hasattr(services.tts, "turn_timing"):

        @services.tts.event_handler("on_tts_request")
        async def tts_request(service: Any, context_id: str, text: str) -> None:
            latency.tts_request(context_id)

    transcript_gate = []
    if fenced:
        from techfi_voice.realtime_stt import FinalTranscriptGate

        transcript_gate.append(
            FinalTranscriptGate(
                scope.session_id,
                final_timeout=settings.stt_final_timeout_seconds,
                fail=fail,
                timing=latency,
            )
        )

    collection_before, collection_after = [], []
    if workflow:
        from techfi_voice.collection_pipeline import (
            CollectionResponseProcessor,
            CollectionTurnProcessor,
        )

        async def closing():
            session.closing = True
            session.opt_out_requested.set()  # Reuse bounded closing-audio grace period.

        def allowed():
            if collection_allowed is not None and not collection_allowed():
                session.stop.set()
                return False
            return not session.stop.is_set() and not session.opted_out

        collection_before.append(CollectionTurnProcessor(workflow, allowed, closing))
        collection_after.append(CollectionResponseProcessor(workflow, allowed, closing))

    pipeline = Pipeline(
        [
            transport.input(),
            services.stt,
            *transcript_gate,
            OptOutProcessor(settings.opt_out_phrases, opt_out),
            aggregators.user(),
            *collection_before,
            services.llm,
            *collection_after,
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
    session = VoiceSession(scope, task, context, monitor=monitor, collection=workflow)
    if workflow:
        session.greeting = workflow.greeting()
        session.calling_window_remaining = calling_window_remaining

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
