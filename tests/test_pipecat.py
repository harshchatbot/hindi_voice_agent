import asyncio

from loguru import logger
from test_foundations import voice_settings

logger.remove()


def test_real_provider_constructors():
    from techfi_voice.integrations.providers import create_services

    async def construct():
        services = create_services(voice_settings())
        assert services.stt is not services.llm
        assert services.llm is not services.tts

    asyncio.run(construct())


def test_sessions_have_isolated_context_and_processors():
    from pipecat.processors.frame_processor import FrameProcessor
    from techfi_voice.integrations.providers import Services
    from techfi_voice.session import build_session

    class Transport:
        def input(self):
            return FrameProcessor()

        def output(self):
            return FrameProcessor()

    async def construct():
        created = []

        def factory(settings):
            services = Services(FrameProcessor(), FrameProcessor(), FrameProcessor())
            created.append(services)
            return services

        settings = voice_settings()
        one = build_session(settings, Transport(), factory)
        two = build_session(settings, Transport(), factory)
        assert one.scope.session_id != two.scope.session_id
        assert one.context is not two.context
        one.context.add_message({"role": "user", "content": "private-first-session"})
        assert len(one.context.messages) == 1
        assert len(two.context.messages) == 0
        assert created[0].stt is not created[1].stt
        assert one.task is not two.task

    asyncio.run(construct())


def test_current_runner_lifecycle_without_audio_or_network():
    from pipecat.frames.frames import EndFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    class Passthrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    async def run():
        finished = asyncio.Event()
        worker = PipelineWorker(Pipeline([Passthrough()]), enable_rtvi=False)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)

        @worker.event_handler("on_pipeline_started")
        async def finish(worker, frame):
            await worker.queue_frame(EndFrame())

        @worker.event_handler("on_pipeline_finished")
        async def ended(worker, frame):
            finished.set()

        await asyncio.wait_for(runner.run(), timeout=5)
        assert finished.is_set()
        assert worker.has_finished()

    asyncio.run(run())


def test_provider_configuration_errors_are_typed():
    import pytest
    from techfi_shared.errors import ConfigurationError
    from techfi_voice.integrations.providers import create_services

    with pytest.raises(ConfigurationError):
        create_services(voice_settings(stt_model="unsupported-model"))
