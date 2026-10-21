from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService

from techfi_voice.integrations.timing import CorrelatedLLMMixin


class ConfiguredOpenAILLMService(CorrelatedLLMMixin, OpenAILLMService):
    """Use the supported client factory hook for bounded HTTP reads and no silent retries."""

    def __init__(self, *, timeout_seconds: float, **kwargs):
        self._timeout_seconds = timeout_seconds
        super().__init__(**kwargs)

    def create_client(self, api_key=None, base_url=None, **kwargs):
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self._timeout_seconds,
            max_retries=0,
        )

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            await self._client.close()


class ConfiguredOpenAISTTService(OpenAISTTService):
    """Whisper transcribes VAD-delimited utterances; this is not a realtime STT socket."""

    def __init__(self, *, timeout_seconds: float, **kwargs):
        super().__init__(http_client=DefaultAsyncHttpxClient(timeout=timeout_seconds), **kwargs)

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            await self._client.close()
