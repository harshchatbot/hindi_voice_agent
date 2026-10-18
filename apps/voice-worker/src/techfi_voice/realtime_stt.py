"""Final-transcript boundary for realtime STT services; no provider or conversation state."""

import logging
from uuid import UUID

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from techfi_shared.logging import Event


class FinalTranscriptGate(FrameProcessor):
    def __init__(self, session_id: UUID):
        super().__init__()
        self.session_id = session_id
        self.partial_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (VADUserStartedSpeakingFrame, InterruptionFrame)):
            self.partial_count = 0
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, InterimTranscriptionFrame):
                self.partial_count += 1
                return
            if isinstance(frame, TranscriptionFrame):
                if not frame.finalized or not frame.text.strip():
                    return
                logging.getLogger("techfi").info(
                    Event.STT_FINALIZED,
                    extra={"session_id": self.session_id, "partial_count": self.partial_count},
                )
                self.partial_count = 0
        await self.push_frame(frame, direction)
