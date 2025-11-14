import whisper

model = whisper.load_model("small")  # small = fast + good accuracy for Hindi/Hinglish

def transcribe(audio_file):
    result = model.transcribe(audio_file, fp16=False)
    return result["text"]
