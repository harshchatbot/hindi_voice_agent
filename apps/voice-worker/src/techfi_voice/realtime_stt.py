"""Final-transcript boundary for realtime STT services; no provider or conversation state."""

import asyncio
import logging
from uuid import UUID

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from techfi_shared.logging import Event, TurnStage


class FinalTranscriptGate(FrameProcessor):
    def __init__(self, session_id: UUID, *, final_timeout=None, fail=None, timing=None):
        super().__init__()
        self.session_id = session_id
        self.partial_count = 0
        self.final_timeout, self.fail, self.timing = final_timeout, fail, timing
        self._pending_endpoints = 0
        self._buffered_finals = []
        self._buffered_text = set()
        self._speaking = False
        self._closed = False
        self._deadline = None

    def cancel_deadline(self):
        if self._deadline:
            self._deadline.cancel()
            self._deadline = None

    async def terminate(self, event, boundary_reason=None):
        if self._closed:
            return
        self._closed = True
        self.cancel_deadline()
        self._pending_endpoints = 0
        self._buffered_finals.clear()
        self._buffered_text.clear()
        logging.getLogger("techfi").warning(
            event,
            extra={
                "session_id": self.session_id,
                "boundary_reason": boundary_reason,
            },
        )
        if self.fail:
            await self.fail()

    async def expire(self):
        await asyncio.sleep(self.final_timeout)
        self._deadline = None
        if self.timing and self.timing.current:
            self.timing.mark(TurnStage.FINAL_TIMEOUT, self.timing.current.index)
        await self.terminate(Event.STT_TIMEOUT)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (CancelFrame, EndFrame)):
            self._closed = True
            self.cancel_deadline()
            self._pending_endpoints = 0
            self._buffered_finals.clear()
            self._buffered_text.clear()
        if self._closed and isinstance(
            frame, (VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
        ):
            return
        # In the production layout this sees the upstream local-VAD copy;
        # synthetic transports may deliver equivalent VAD frames downstream.
        if self.final_timeout is not None:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                self._speaking = True
            elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._speaking:
                self._speaking = False
                self._pending_endpoints += 1
                if self._pending_endpoints == 1:
                    self.cancel_deadline()
                    self._deadline = asyncio.create_task(self.expire())
        if isinstance(frame, (VADUserStartedSpeakingFrame, InterruptionFrame)):
            self.partial_count = 0
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, InterimTranscriptionFrame):
                if self.timing and not self._closed:
                    self.timing.stt_frame(frame)
                self.partial_count += 1
                return
            if isinstance(frame, TranscriptionFrame):
                if not frame.finalized or not frame.text.strip():
                    return
                if self._closed:
                    return
                if self.final_timeout is not None:
                    if not self._pending_endpoints:
                        await self.terminate(Event.STT_AMBIGUOUS, "final_without_pending_endpoint")
                        return
                    text = frame.text.strip()
                    if text in self._buffered_text:
                        await self.terminate(Event.STT_AMBIGUOUS, "duplicate_final")
                        return
                    self._buffered_text.add(text)
                    self._buffered_finals.append(frame)
                    self._pending_endpoints -= 1
                    if self._pending_endpoints:
                        return
                    self.cancel_deadline()
                    if self._speaking:
                        return
                    frame = self._combined_final()
                if self.timing:
                    self.timing.stt_frame(frame)
                logging.getLogger("techfi").info(
                    Event.STT_FINALIZED,
                    extra={"session_id": self.session_id, "partial_count": self.partial_count},
                )
                self.partial_count = 0
        await self.push_frame(frame, direction)

    def _combined_final(self):
        frames = self._buffered_finals
        self._buffered_finals = []
        self._buffered_text.clear()
        if len(frames) == 1:
            return frames[0]
        last = frames[-1]
        combined = TranscriptionFrame(
            " ".join(part.text.strip() for part in frames),
            last.user_id,
            last.timestamp,
            last.language,
            result=last.result,
            finalized=True,
        )
        combined.metadata.update(last.metadata)
        return combined

    async def cleanup(self):
        self._closed = True
        deadline = self._deadline
        self.cancel_deadline()
        if deadline:
            await asyncio.gather(deadline, return_exceptions=True)
        await super().cleanup()
