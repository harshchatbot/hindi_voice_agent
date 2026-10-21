"""Experimental Sarvam contracts, verified against Pipecat 1.12.0.

No global registry patches or v3 speaker/config fallback. Keep the pinned socket
lifecycle (audio contexts, final events, interruption and teardown) in Pipecat.
"""

import json
from contextlib import suppress

from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.services.tts_service import InterruptibleTTSService

from techfi_voice.integrations.openai import ConfiguredOpenAILLMService
from techfi_voice.integrations.timing import TimedTTSMixin


class SarvamConversationLLMService(ConfiguredOpenAILLMService):
    """Sarvam's documented OpenAI-compatible V1 SSE endpoint; content only."""

    def build_chat_completion_params(self, params_from_context):
        params = super().build_chat_completion_params(params_from_context)
        # Do not send OpenAI-only stream_options, service_tier or completion-token
        # parameters. Explicit null disables default thinking for spoken dialogue.
        return {
            "model": params["model"],
            "messages": params["messages"],
            "stream": True,
            "reasoning_effort": None,
        }


class _SarvamV4SocketService(SarvamTTSService):
    """Version-scoped config shim: native 1.12.0 constructor rejects v4.

    Initialize its supported base directly, then reuse native setup, receive,
    flush, ping, final-event and close/reconnect-on-interruption implementations.
    No v3 defaults or model registry are inherited into the wire configuration.
    """

    def __init__(self, *, api_key: str, url: str, voice: str, language: str):
        settings = self.Settings(model="bulbul:v4-flash", voice=voice, language=language)
        InterruptibleTTSService.__init__(
            self,
            settings=settings,
            sample_rate=24000,
            push_text_frames=True,
            pause_frame_processing=True,
            push_stop_frames=True,
            push_start_frame=True,
            reconnect_on_error=False,
        )
        self._api_key = api_key
        self._websocket_url = f"{url}?model=bulbul:v4-flash&send_completion_event=true"
        self._speech_sample_rate = "24000"
        self._receive_task = None
        self._keepalive_task = None

    async def _send_config(self):
        # v4 uses language_code and persona IDs. temperature is ignored by v4;
        # omit it, along with all v3-specific defaults. LINEAR16 is documented
        # for WebSocket streaming and is raw mono PCM, not a WAV container.
        socket = self._get_websocket()
        try:
            await socket.send(
                json.dumps(
                    {
                        "type": "config",
                        "data": {
                            "model": "bulbul:v4-flash",
                            "language_code": self._settings.language,
                            "speaker": self._settings.voice,
                            "speech_sample_rate": 24000,
                            "output_audio_codec": "linear16",
                            "enable_preprocessing": True,
                        },
                    }
                )
            )
        except BaseException:
            # Native connect clears its socket reference after config failure.
            # Close here first so failure/cancellation cannot orphan a connection.
            with suppress(Exception):
                await socket.close()
            raise


class SarvamV4TTSService(TimedTTSMixin, _SarvamV4SocketService):
    """Native streaming and content-free timing around the experimental v4 config."""
