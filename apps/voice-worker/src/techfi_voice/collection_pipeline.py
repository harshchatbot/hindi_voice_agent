"""A closed response vocabulary around the existing streamed LLM.

LLM content is NEVER spoken directly for collections. Rules own all facts,
identity decisions and outcomes; labels only choose post-verification templates.
"""

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from techfi_voice.collection import CollectionState
from techfi_voice.integrations.timing import TURN_KEY


class CollectionTurnProcessor(FrameProcessor):
    def __init__(self, workflow, allowed, closing=None):
        super().__init__()
        self.workflow, self.allowed, self.closing = workflow, allowed, closing

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
            self.workflow.cancel_pending()
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            if not self.allowed() or self.workflow.state == CollectionState.CLOSING:
                return
            messages = frame.context.get_messages()
            text = next(
                (
                    m.get("content")
                    for m in reversed(messages)
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                None,
            )
            owner = frame.metadata.get(TURN_KEY)
            if not isinstance(text, str) or not self.workflow.begin_turn(owner, text):
                return
            if self.workflow.state == CollectionState.CLOSING and self.closing:
                await self.closing()
            # Known rule-controlled interactions do not need customer text at the LLM.
            request = text if self.workflow.approved_reply is None else "rule-controlled response"
            safe = LLMContextFrame(LLMContext([{"role": "user", "content": request}]))
            safe.metadata.update(frame.metadata)
            await self.push_frame(safe, direction)
            return
        await self.push_frame(frame, direction)


class CollectionResponseProcessor(FrameProcessor):
    def __init__(self, workflow, allowed, closing):
        super().__init__()
        self.workflow, self.allowed, self.closing = workflow, allowed, closing
        self._owner = None
        self._label = ""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        owner = frame.metadata.get(TURN_KEY)
        if isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
            self._owner, self._label = None, ""
        elif isinstance(frame, LLMFullResponseStartFrame):
            if owner is None or owner != self.workflow.pending_owner:
                return
            self._owner, self._label = owner, ""
        elif isinstance(frame, TextFrame):
            if isinstance(frame, LLMTextFrame) and owner is not None and owner == self._owner:
                # Invalid/oversized output stays invalid, never a spoken response.
                combined = self._label + frame.text
                self._label = combined if len(combined) <= 64 else "#invalid#"
            return
        elif isinstance(frame, LLMFullResponseEndFrame):
            if owner is None or owner != self._owner or not self.allowed():
                return
            self._owner = None
            reply = self.workflow.finish_turn(owner, self._label)
            self._label = ""
            if reply is None:
                return
            approved = LLMTextFrame(reply)
            approved.metadata.update(frame.metadata)
            await self.push_frame(approved, direction)
            if self.workflow.state == CollectionState.CLOSING:
                await self.closing()
        await self.push_frame(frame, direction)
