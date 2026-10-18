import asyncio
import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
from groq import Groq
from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise ValueError("🚨 ERROR: GROQ_API_KEY not found in .env file")

# model = WhisperModel("small", device="cpu")
# for less cpu usage
model = WhisperModel("tiny", device="cpu")
client = Groq(api_key=api_key)

SAMPLE_RATE = 16000
CHUNK = 1024

async def stream_agent():
    print("🎧 Free Realtime Hindi/Hinglish Agent Started. Speak...")

    audio_buffer = []

    def mic_callback(indata, frames, time, status):
        audio_buffer.append(indata.copy())

    async def play_voice(file):
        audio = AudioSegment.from_mp3(file)
        play(audio)
        os.remove(file)

    with sd.InputStream(callback=mic_callback, channels=1, samplerate=SAMPLE_RATE):
        while True:
            if len(audio_buffer) > 3:
                chunk = audio_buffer.pop(0)
                audio_int16 = (chunk * 32768).astype(np.int16)

                segments, _ = model.transcribe(audio_int16)
                text = "".join([seg.text for seg in segments]).strip()

                if text:
                    print("🗣 User:", text)

                    response = client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[{"role": "user", "content": text}]
                    ).choices[0].message.content

                    print("🤖 Agent:", response)

                    tts = gTTS(text=response, lang="hi", tld="co.in")
                    outfile = "resp.mp3"
                    tts.save(outfile)

                    asyncio.create_task(play_voice(outfile))

            await asyncio.sleep(0.05)

if __name__ == "__main__":
    asyncio.run(stream_agent())
