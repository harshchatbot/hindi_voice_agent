"""Content-free turn timing using Pipecat's monotonic pipeline clock and supported events."""

import logging
from collections.abc import Callable
from uuid import UUID

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from techfi_shared.logging import LatencyName, LatencySample


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
        self._last_vad_ids: tuple[int, int | None] = (-1, None)
        self._requests: dict[str, int] = {}
        self.reset()

    def reset(self):
        self.speech_end: int | None = None
        self.transcript_end: int | None = None
        self.llm_start: int | None = None
        self._emitted: set[LatencyName] = set()

    def emit(self, metric: LatencyName, start: int | None, end: int | None):
        if start is None or end is None or end < start or metric in self._emitted:
            return
        self._emitted.add(metric)
        logging.getLogger("techfi").info(
            LatencySample(self.session_id, self.turn_index, metric, (end - start) / 1_000_000)
        )

    def emit_stt(self):
        self.emit(LatencyName.STT_COMPLETION, self.speech_end, self.transcript_end)

    def tts_request(self, context_id: str):
        # Event handlers and frame observers have separate queues. Capture the monotonic
        # request time independently, then associate it when the matching audio is observed.
        self._requests.setdefault(context_id, self.clock())
        if len(self._requests) > 32:
            self._requests.pop(next(iter(self._requests)))

    async def on_push_frame(self, data: FramePushed):
        frame, now = data.frame, data.timestamp
        if isinstance(
            frame, (InterruptionFrame, CancelFrame, EndFrame, VADUserStartedSpeakingFrame)
        ):
            self.emit_stt()
            self.reset()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            # VAD broadcasts sibling frames; they represent the same speech boundary.
            if frame.id in self._last_vad_ids:
                return
            self.emit_stt()
            transcript_end = self.transcript_end
            self.reset()
            self.transcript_end = transcript_end
            self._last_vad_ids = (frame.id, frame.broadcast_sibling_id)
            self.turn_index += 1
            # Ignore the frame's wall-clock timestamp. Subtract configured silence duration
            # from its monotonic observation to estimate the physical end of speech.
            self.speech_end = now - int(max(0, frame.stop_secs) * 1_000_000_000)
        elif (
            isinstance(frame, TranscriptionFrame)
            and data.source is self.stt
            and (not self.require_finalized_transcripts or frame.finalized)
        ):
            # Not InterimTranscriptionFrame. Some services emit multiple completed segments;
            # keep the last one used by the LLM rather than declaring the first complete.
            if self.llm_start is None:
                self.transcript_end = now
        elif isinstance(frame, LLMFullResponseStartFrame) and data.source is self.llm:
            if self.speech_end is not None and self.llm_start is None:
                self.emit_stt()
                self.llm_start = now
        elif isinstance(frame, LLMTextFrame) and data.source is self.llm:
            self.emit(LatencyName.LLM_FIRST_TOKEN, self.llm_start, now)
        elif isinstance(frame, TTSAudioRawFrame) and data.source is self.tts:
            request = self._requests.pop(frame.context_id, None)
            if request is not None and self.llm_start is not None and request >= self.llm_start:
                self.emit(LatencyName.TTS_FIRST_AUDIO, request, now)
                self.emit(LatencyName.SPEECH_TO_AUDIO, self.speech_end, now)
        elif isinstance(frame, BotStartedSpeakingFrame) and data.source is self.output:
            if LatencyName.TTS_FIRST_AUDIO in self._emitted:
                self.emit(LatencyName.SPEECH_TO_OUTPUT, self.speech_end, now)

    async def cleanup(self):
        self.emit_stt()
        self.reset()
        self._requests.clear()
        await super().cleanup()
