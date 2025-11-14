from groq import Groq

client = Groq(api_key="YOUR_GROQ_API_KEY")

def chat_with_llm(text):
    completion = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": 
             "You are a friendly Indian AI voice agent. "
             "Speak in Hinglish and keep responses short and natural."
            },
            {"role": "user", "content": text}
        ]
    )
    return completion.choices[0].message["content"]
