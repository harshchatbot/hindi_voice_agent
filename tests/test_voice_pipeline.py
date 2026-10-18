import asyncio

from loguru import logger
from pipecat.frames.frames import BotStoppedSpeakingFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameProcessor
from techfi_shared.contracts import CallOutcome
from techfi_voice.integrations.providers import Services
from techfi_voice.runtime import run_session
from techfi_voice.session import build_session
from test_foundations import voice_settings

logger.remove()


class Forward(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class Transport:
    def input(self):
        return Forward()

    def output(self):
        return Forward()


def test_opt_out_through_running_pipecat_pipeline_with_mocked_providers():
    class MockTTS(Forward):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                await self.push_frame(BotStoppedSpeakingFrame(), direction)

    async def run():
        settings = voice_settings(max_session_seconds=3)
        session = build_session(
            settings, Transport(), lambda _: Services(Forward(), Forward(), MockTTS())
        )

        @session.task.event_handler("on_pipeline_started")
        async def started(worker, frame):
            await worker.queue_frame(TranscriptionFrame("band karo", "", ""))

        result = await run_session(session, settings)
        assert result.outcome == CallOutcome.OPTED_OUT
        assert session.opted_out
        assert session.context.messages == []

    asyncio.run(run())
