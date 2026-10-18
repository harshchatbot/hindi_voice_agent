import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
from stt import transcribe
from agent import chat_with_llm
from tts import speak

SAMPLE_RATE = 16000

def record_audio(duration=4):
    print("\n🎤 Listening...")
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
    sd.wait()
    wav.write("input.wav", SAMPLE_RATE, audio)
    return "input.wav"

def main():
    print("🚀 Free Hindi + Hinglish AI Voice Agent Started (M1 Mac Optimized)")
    print("Speak… (say 'stop' or 'exit' to quit)\n")

    while True:
        audio_file = record_audio()

        text = transcribe(audio_file)
        print("🗣 You:", text)

        if text.strip().lower() in ["stop", "exit", "bye"]:
            print("👋 Ending agent…")
            break

        response = chat_with_llm(text)
        print("🤖 Agent:", response)

        speak(response)

if __name__ == "__main__":
    main()
