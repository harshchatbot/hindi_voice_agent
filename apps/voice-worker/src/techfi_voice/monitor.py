import asyncio
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed


class SessionMonitor(BaseObserver):
    """Watch provider progress, not content. Never retain or log transcript frames."""

    def __init__(
        self,
        timeout: float,
        fail: Callable[[], Awaitable[None]],
        bot_stopped: Callable[[], Awaitable[None]],
        *,
        require_finalized_transcripts: bool = False,
        on_stt_timeout: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(observe_every_push=False)
        self.timeout = timeout
        self.fail = fail
        self.bot_stopped = bot_stopped
        self.require_finalized_transcripts = require_finalized_transcripts
        self.on_stt_timeout = on_stt_timeout
        self._timers: dict[str, asyncio.Task] = {}

    def cancel_timer(self, stage: str):
        timer = self._timers.pop(stage, None)
        if timer:
            timer.cancel()

    def arm(self, stage: str):
        self.cancel_timer(stage)

        async def expired():
            await asyncio.sleep(self.timeout)
            self._timers.pop(stage, None)
            if stage == "stt" and self.on_stt_timeout:
                await self.on_stt_timeout()
            await self.fail()

        self._timers[stage] = asyncio.create_task(expired())

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, (CancelFrame, EndFrame, InterruptionFrame)):
            await self.clear()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.cancel_timer("stt")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.arm("stt")
        elif isinstance(frame, TranscriptionFrame) and (
            not self.require_finalized_transcripts or frame.finalized
        ):
            self.cancel_timer("stt")
        elif isinstance(frame, LLMFullResponseStartFrame):
            self.cancel_timer("stt")
            self.arm("llm")
        elif isinstance(frame, LLMTextFrame):
            self.arm("llm")
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.cancel_timer("llm")
        elif isinstance(frame, (TTSStartedFrame, TTSSpeakFrame)):
            self.arm("tts")
        elif isinstance(frame, TTSAudioRawFrame):
            self.arm("tts")
        elif isinstance(frame, TTSStoppedFrame):
            self.cancel_timer("tts")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self.bot_stopped()

    async def clear(self):
        timers = list(self._timers.values())
        self._timers.clear()
        for timer in timers:
            timer.cancel()
        await asyncio.gather(*timers, return_exceptions=True)

    async def cleanup(self):
        await self.clear()
        await super().cleanup()
