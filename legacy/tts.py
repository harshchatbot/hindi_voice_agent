from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
import os
import re

def detect_language(text):
    # Check for Hindi unicode
    hindi_chars = re.findall("[\u0900-\u097F]+", text)
    return "hi" if hindi_chars else "en"

def speak(text):
    lang = detect_language(text)

    if lang == "hi":
        tts = gTTS(text=text, lang='hi', tld='co.in')
    else:
        tts = gTTS(text=text, lang='en', tld='co.in')

    tts.save("response.mp3")

    audio = AudioSegment.from_mp3("response.mp3")
    play(audio)

    os.remove("response.mp3")
