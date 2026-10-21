"""Content-free, monotonic turn instrumentation and bounded correlation state."""

import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from techfi_shared.logging import (
    Event,
    LatencyName,
    LatencySample,
    TurnReason,
    TurnStage,
    TurnTiming,
)

from techfi_voice.integrations.timing import TURN_KEY

_UNSET = object()


@dataclass
class Turn:
    index: int
    times: dict[TurnStage, int] = field(default_factory=dict)
    emitted: set[LatencyName] = field(default_factory=set)
    cancelled: bool = False


class LatencyObserver(BaseObserver):
    def __init__(
        self,
        session_id: UUID,
        stt,
        llm,
        tts,
        output,
        clock: Callable[[], int],
        *,
        require_finalized_transcripts: bool = False,
    ):
        super().__init__(observe_every_push=False)
        self.session_id = session_id
        self.stt, self.llm, self.tts, self.output = stt, llm, tts, output
        self.clock = clock
        self.require_finalized_transcripts = require_finalized_transcripts
        self.turn_index = 0
        self.turns: OrderedDict[int, Turn] = OrderedDict()
        self.current: Turn | None = None
        self.speaking = False
        self._bound = False
        self._bot_speaking = False
        self._stt_final_event_index = 0
        self._requests: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._last_vad_ids: tuple[int, int | None] = (-1, None)

    @property
    def transcript_end(self):
        return self.current.times.get(TurnStage.FINAL) if self.current else None

    def mark(self, stage: TurnStage, owner: int | None, now: int | None = None, *, reason=None):
        turn = self.turns.get(owner)
        if turn is None or turn.cancelled or stage in turn.times:
            return
        now = max(0, self.clock() if now is None else now)
        turn.times[stage] = now
        speech_end = turn.times.get(TurnStage.SPEECH_END)
        logging.getLogger("techfi").info(
            TurnTiming(
                self.session_id,
                turn.index,
                stage,
                now / 1_000_000,
                (now - speech_end) / 1_000_000 if speech_end is not None else None,
                reason,
            )
        )

    def emit(
        self, metric: LatencyName, start: int | None, end: int | None, owner: int | None = None
    ):
        turn = self.turns.get(owner) if owner is not None else self.current
        if (
            turn is None
            or turn.cancelled
            or start is None
            or end is None
            or end < start
            or metric in turn.emitted
        ):
            return
        turn.emitted.add(metric)
        logging.getLogger("techfi").info(
            LatencySample(self.session_id, turn.index, metric, (end - start) / 1_000_000)
        )

    def emit_stt(self, turn: Turn | None = None):
        turn = turn or self.current
        if turn:
            self.emit(
                LatencyName.STT_COMPLETION,
                turn.times.get(TurnStage.SPEECH_END),
                turn.times.get(TurnStage.FINAL),
                turn.index,
            )

    def cancel(self, now: int, reason=TurnReason.INTERRUPTION):
        for turn in self.turns.values():
            if not turn.cancelled:
                self.emit_stt(turn)
                complete = (
                    TurnStage.LLM_END in turn.times
                    and TurnStage.TTS_AUDIO in turn.times
                    and reason in (TurnReason.NEXT_INPUT, TurnReason.SESSION_END)
                )
                self.mark(
                    TurnStage.WINDOW_CLOSED if complete else TurnStage.CANCELLED,
                    turn.index,
                    now,
                    reason=reason,
                )
                turn.cancelled = True

    def speech_started(self, frame, now):
        if self.speaking:
            return
        if self.current is None:
            reason = TurnReason.NEW_INPUT
        elif TurnStage.FINAL not in self.current.times:
            reason = TurnReason.RESUMED_BEFORE_FINAL
        elif self._bot_speaking:
            reason = TurnReason.SERVER_BARGE_IN
        elif (
            TurnStage.LLM_REQUEST in self.current.times
            and TurnStage.LLM_END not in self.current.times
        ):
            reason = TurnReason.INFERENCE_INTERRUPTED
        elif TurnStage.TTS_AUDIO in self.current.times:
            reason = TurnReason.NEXT_INPUT
        else:
            reason = TurnReason.NEW_INPUT
        self.cancel(now, reason)
        self.turn_index += 1
        self.current = Turn(self.turn_index)
        self.turns[self.turn_index] = self.current
        while len(self.turns) > 32:
            self.turns.popitem(last=False)
        self.speaking = True
        frame.metadata[TURN_KEY] = self.turn_index

    def speech_stopped(self, frame, now):
        if frame.id in self._last_vad_ids:
            return
        self._last_vad_ids = (frame.id, frame.broadcast_sibling_id)
        if self.current is None:
            self.speech_started(frame, now)
        elif not self.speaking:
            return  # Canonical local VAD emits separate, unpaired directional frames.
        self.speaking = False
        frame.metadata[TURN_KEY] = self.current.index
        self.mark(
            TurnStage.SPEECH_END,
            self.current.index,
            now - int(max(0, frame.stop_secs) * 1_000_000_000),
        )
        self.mark(TurnStage.VAD_STOP, self.current.index, now)

    def stt_frame(self, frame, now=None):
        if self.current is None or self.current.cancelled:
            return
        owner = self.current.index
        if isinstance(frame, InterimTranscriptionFrame):
            self.mark(TurnStage.INTERIM, owner, now)
        elif isinstance(frame, TranscriptionFrame) and (
            not self.require_finalized_transcripts or frame.finalized
        ):
            frame.metadata[TURN_KEY] = owner
            # Keep last completed segment for the legacy adapters.
            if not self.require_finalized_transcripts and TurnStage.FINAL in self.current.times:
                self.current.times.pop(TurnStage.FINAL)
            self.mark(TurnStage.FINAL, owner, now)

    def tts_request(self, context_id: str, owner=_UNSET):
        if owner is _UNSET:
            # Legacy standalone observers can use the current window. Bound
            # session hooks require explicit ownership, including delayed callbacks.
            owner = self.current.index if self.current and not self._bound else None
        if owner not in self.turns:
            return
        self._requests.setdefault(context_id, (owner, self.clock()))
        if len(self._requests) > 32:
            self._requests.popitem(last=False)
        self.mark(TurnStage.TTS_REQUEST, owner)

    def audio(self, frame, now):
        request = self._requests.get(frame.context_id)
        if not request:
            return
        owner, requested = request
        frame.metadata[TURN_KEY] = owner
        turn = self.turns.get(owner)
        if turn is None:
            return
        self.mark(TurnStage.TTS_AUDIO, owner, now)
        if TurnStage.LLM_START in turn.times:
            self.emit(LatencyName.TTS_FIRST_AUDIO, requested, now, owner)
            self.emit(LatencyName.SPEECH_TO_AUDIO, turn.times.get(TurnStage.SPEECH_END), now, owner)

    def bind(self, user, strategy):
        """Synchronous public push events avoid observer-queue ownership races."""
        self._bound = True

        @user.event_handler("on_before_push_frame")
        async def user_frame(processor, frame):
            now = self.clock()
            if isinstance(frame, VADUserStartedSpeakingFrame):
                self.speech_started(frame, now)
            elif isinstance(frame, VADUserStoppedSpeakingFrame):
                self.speech_stopped(frame, now)
            elif isinstance(frame, LLMContextFrame) and self.current and not self.current.cancelled:
                frame.metadata[TURN_KEY] = self.current.index
                self.mark(TurnStage.LLM_REQUEST, self.current.index, now)
            elif isinstance(frame, (CancelFrame, EndFrame)):
                self.cancel(
                    now,
                    TurnReason.PIPELINE_CANCEL
                    if isinstance(frame, CancelFrame)
                    else TurnReason.SESSION_END,
                )
            elif isinstance(frame, InterruptionFrame):
                # VAD start already cancelled the previous response; keep the new input turn.
                for turn in self.turns.values():
                    if turn is not self.current or TurnStage.LLM_REQUEST in turn.times:
                        if not turn.cancelled:
                            self.mark(
                                TurnStage.CANCELLED, turn.index, now, reason=TurnReason.INTERRUPTION
                            )
                            turn.cancelled = True

        @strategy.event_handler("on_user_turn_inference_triggered")
        async def strategy_complete(strategy, *args):
            if self.current:
                self.mark(TurnStage.TURN_COMPLETE, self.current.index)

        @self.stt.event_handler("on_before_push_frame")
        async def stt_frame(processor, frame):
            if isinstance(frame, TranscriptionFrame) and frame.finalized:
                self._stt_final_event_index += 1
                # Raw provider availability has no assumed turn owner. Gate
                # acceptance is a separate event with verified boundary state.
                logging.getLogger("techfi").info(
                    Event.STT_FINAL_RECEIVED,
                    extra={
                        "session_id": self.session_id,
                        "final_event_index": self._stt_final_event_index,
                    },
                )
            # Realtime frames are timed only after the gate accepts ownership.
            # Legacy/Whisper adapters have no gate and retain segment timing.
            if not self.require_finalized_transcripts and isinstance(
                frame, (TranscriptionFrame, InterimTranscriptionFrame)
            ):
                self.stt_frame(frame)

        @self.llm.event_handler("on_before_push_frame")
        async def llm_frame(processor, frame):
            context = getattr(processor, "timing_turn", None)
            owner = context.get() if context is not None else None
            frame.metadata[TURN_KEY] = owner
            if isinstance(frame, LLMFullResponseStartFrame):
                if owner in self.turns:
                    self.emit_stt(self.turns[owner])
                self.mark(TurnStage.LLM_START, owner)
            elif isinstance(frame, LLMTextFrame):
                self.mark(TurnStage.LLM_TEXT, owner)
                turn = self.turns.get(owner)
                if turn:
                    self.emit(
                        LatencyName.LLM_FIRST_TOKEN,
                        turn.times.get(TurnStage.LLM_START),
                        self.clock(),
                        owner,
                    )
            elif isinstance(frame, LLMFullResponseEndFrame):
                self.mark(TurnStage.LLM_END, owner)

        @self.tts.event_handler("on_before_push_frame")
        async def tts_frame(processor, frame):
            if isinstance(frame, TTSAudioRawFrame):
                self.audio(frame, self.clock())

        if hasattr(self.tts, "turn_timing"):
            self.tts.turn_timing = self
        if hasattr(self.stt, "turn_timing"):
            self.stt.turn_timing = self

        @self.output.event_handler("on_before_push_frame")
        async def output_frame(processor, frame):
            # Physical server transport state only; not browser audibility.
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False

    async def on_process_frame(self, data):
        # These observations use immutable frame ownership, not the current turn.
        owner = data.frame.metadata.get(TURN_KEY)
        if data.processor is self.stt and isinstance(data.frame, VADUserStoppedSpeakingFrame):
            self.mark(TurnStage.ENDPOINT, owner, data.timestamp)
        elif data.processor is self.output and isinstance(data.frame, TTSAudioRawFrame):
            self.mark(TurnStage.TRANSPORT, owner, data.timestamp)

    async def on_push_frame(self, data: FramePushed):
        if self._bound:
            # BotStartedSpeaking is a server proxy; rechunking loses context IDs.
            # Do not guess its turn or equate it with audible browser playback.
            return
        frame, now = data.frame, data.timestamp
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self.speech_started(frame, now)
        elif isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
            self.cancel(now)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.speech_stopped(frame, now)
        elif (
            isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
            and data.source is self.stt
        ):
            self.stt_frame(frame, now)
        elif isinstance(frame, LLMFullResponseStartFrame) and data.source is self.llm:
            if self.current and not self.current.cancelled:
                self.emit_stt()
                self.mark(TurnStage.LLM_START, self.current.index, now)
        elif isinstance(frame, LLMTextFrame) and data.source is self.llm:
            if self.current:
                self.mark(TurnStage.LLM_TEXT, self.current.index, now)
                self.emit(
                    LatencyName.LLM_FIRST_TOKEN, self.current.times.get(TurnStage.LLM_START), now
                )
        elif isinstance(frame, TTSAudioRawFrame) and data.source is self.tts:
            self.audio(frame, now)
        elif isinstance(frame, BotStartedSpeakingFrame) and data.source is self.output:
            if self.current and LatencyName.TTS_FIRST_AUDIO in self.current.emitted:
                self.emit(
                    LatencyName.SPEECH_TO_OUTPUT, self.current.times.get(TurnStage.SPEECH_END), now
                )

    async def cleanup(self):
        self.emit_stt()
        self.turns.clear()
        self.current = None
        self._requests.clear()
        await super().cleanup()
