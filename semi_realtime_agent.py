import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
from groq import Groq
from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
import os
import time
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

model = WhisperModel("tiny", device="cpu")
client = Groq(api_key=api_key)

SAMPLE_RATE = 16000
BLOCK_DURATION = 1.0  # 1 second chunk recording

def record_block():
    duration = BLOCK_DURATION
    print("\n🎤 Listening...")
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
    sd.wait()
    return audio

def main():
    print("🚀 Semi-Realtime Hindi/Hinglish Agent Started")
    print("Speak... (say 'stop' to exit)")
    
    while True:
        data = record_block()
        audio = data.flatten().astype(np.int16)

        segments, _ = model.transcribe(audio)
        text = "".join([seg.text for seg in segments]).strip()

        if not text:
            continue

        print("🗣 You:", text)

        if text.lower() in ["stop", "exit", "bye"]:
            print("👋 Ending...")
            break

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": text}]
        ).choices[0].message.content

        print("🤖 Agent:", response)

        tts = gTTS(text=response, lang="hi", tld="co.in")
        tts.save("resp.mp3")
        audio_out = AudioSegment.from_mp3("resp.mp3")
        play(audio_out)
        os.remove("resp.mp3")

if __name__ == "__main__":
    main()
