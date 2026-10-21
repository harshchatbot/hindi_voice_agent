"""Content-free correlation through supported processor/service extension points."""

from contextvars import ContextVar

from pipecat.frames.frames import LLMContextFrame, LLMTextFrame
from techfi_shared.logging import TurnStage

TURN_KEY = "techfi_turn_index"


class CorrelatedLLMMixin:
    def __init__(self, **kwargs):
        self.timing_turn = ContextVar(f"llm-turn-{id(self)}", default=None)
        super().__init__(**kwargs)

    async def process_frame(self, frame, direction):
        if not isinstance(frame, LLMContextFrame):
            return await super().process_frame(frame, direction)
        token = self.timing_turn.set(frame.metadata.get(TURN_KEY))
        try:
            await super().process_frame(frame, direction)
        finally:
            self.timing_turn.reset(token)


class TimedTTSMixin:
    def __init__(self, **kwargs):
        self.turn_timing = None
        self._timing_turn = ContextVar(f"tts-turn-{id(self)}", default=None)
        super().__init__(**kwargs)

        async def text_ready(text, aggregation_type):
            if self.turn_timing:
                self.turn_timing.mark(TurnStage.TTS_READY, self._timing_turn.get())
            return text  # Never retain, log or alter the text.

        self.add_text_transformer(text_ready)

    async def process_frame(self, frame, direction):
        owner = frame.metadata.get(TURN_KEY)
        token = self._timing_turn.set(owner)
        try:
            if self.turn_timing and isinstance(frame, LLMTextFrame):
                self.turn_timing.mark(TurnStage.TTS_INPUT, owner)
            await super().process_frame(frame, direction)
        finally:
            self._timing_turn.reset(token)

    async def run_tts(self, text, context_id):
        # run_tts is the supported synthesis boundary. Unlike an asynchronous
        # event callback, this captures the owner and time before provider work.
        if self.turn_timing:
            self.turn_timing.tts_request(context_id, self._timing_turn.get())
        async for frame in super().run_tts(text, context_id):
            yield frame
