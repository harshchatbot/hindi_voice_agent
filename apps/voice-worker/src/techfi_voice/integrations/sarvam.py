from contextvars import ContextVar

from pipecat.frames.frames import VADUserStoppedSpeakingFrame
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from techfi_shared.logging import TurnStage

from techfi_voice.integrations.timing import TURN_KEY, TimedTTSMixin


class ConfiguredSarvamTTSService(TimedTTSMixin, SarvamTTSService):
    """Native Sarvam TTS with content-free synthesis timing."""


class ConfiguredSarvamRealtimeSTTService(SarvamRealtimeSTTService):
    """Pinned 1.12.0 send hook: distinguish a handoff from a completed socket send."""

    def __init__(self, **kwargs):
        self.turn_timing = None
        self._endpoint_turn = ContextVar(f"sarvam-endpoint-{id(self)}", default=None)
        super().__init__(**kwargs)

    async def process_frame(self, frame, direction):
        if not isinstance(frame, VADUserStoppedSpeakingFrame):
            return await super().process_frame(frame, direction)
        token = self._endpoint_turn.set(frame.metadata.get(TURN_KEY))
        try:
            await super().process_frame(frame, direction)
        finally:
            self._endpoint_turn.reset(token)

    async def _send_json(self, payload):
        # Native service still owns tail flushing, 50ms chunks and speech_end.
        sent = await super()._send_json(payload)
        if payload.get("event") == "speech_end":
            if sent and self.turn_timing:
                self.turn_timing.mark(TurnStage.ENDPOINT_SENT, self._endpoint_turn.get())
            elif not sent:
                await self.push_error("STT endpoint delivery failed")
        return sent
