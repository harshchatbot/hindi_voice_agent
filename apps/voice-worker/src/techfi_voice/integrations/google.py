"""Google streaming TTS: ADC file support and session-owned gRPC cleanup."""

from google.auth import load_credentials_from_file
from google.cloud import texttospeech_v1
from pipecat.services.google.tts import GoogleTTSService

from techfi_voice.integrations.timing import TimedTTSMixin


class ConfiguredGoogleTTSService(TimedTTSMixin, GoogleTTSService):
    def _create_client(self, credentials, credentials_path):
        # Pydantic reads .env without changing os.environ. Load its optional ADC path
        # explicitly, supporting service accounts and other Google ADC file types.
        if credentials_path:
            creds, _ = load_credentials_from_file(
                credentials_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            return texttospeech_v1.TextToSpeechAsyncClient(credentials=creds)
        return super()._create_client(credentials, credentials_path)

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            await self._client.transport.close()
