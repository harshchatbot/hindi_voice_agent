import os
from dotenv import load_dotenv
from groq import Groq

# Load environment variables from .env
load_dotenv()

# Read key
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise ValueError("🚨 ERROR: GROQ_API_KEY not found in .env file")

client = Groq(api_key=api_key)

def chat_with_llm(text):
    completion = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": 
             "You are a friendly Indian AI voice agent. "
             "Speak in Hinglish (Hindi + English). "
             "Keep responses short and natural."
            },
            {"role": "user", "content": text}
        ]
    )
    return completion.choices[0].message.content
