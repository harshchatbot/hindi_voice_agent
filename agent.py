import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise ValueError("🚨 ERROR: GROQ_API_KEY not found in .env file")

client = Groq(api_key=api_key)

# --------- MEMORY ---------
conversation_memory = []

# --------- MAIN AGENT FN ---------
def chat_with_llm(user_text):

    global conversation_memory

    # Keep last 6 exchanges only (to avoid long tokens)
    if len(conversation_memory) > 12:
        conversation_memory = conversation_memory[-12:]

    conversation_memory.append({"role": "user", "content": user_text})

    system_prompt = """
You are an Indian AI Voice Agent speaking in Hinglish (Hindi + English).
Your job is to act like a polite call center executive.

--- TONE ---
• Very polite
• Human-like
• Short sentences
• Simple Hindi + English mix
• No complex words
• Sounds like talking on a call

--- YOUR GOALS ---
1. Greet the user nicely
2. Understand their need
3. Collect lead details:
   - Name
   - Phone number
   - City
   - Requirement / interest
4. Ask questions ONE-BY-ONE
5. Confirm details
6. Be friendly and conversational
7. At the end, summarize the lead

--- VERY IMPORTANT ---
• Never give long paragraphs
• Speak naturally like a caller
• Ask 1 question at a time
• If user asks “who are you”, explain politely
• If user abuses, reply professionally
"""

    messages = [{"role": "system", "content": system_prompt}] + conversation_memory

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages
    ).choices[0].message.content

    # Save assistant reply to memory
    conversation_memory.append({"role": "assistant", "content": response})

    return response

# --------- CALL SUMMARY FN ---------
def get_lead_summary():
    """
    Provides a summary of collected lead details from memory.
    """
    summary_prompt = """
Extract the following details from the conversation:
- Name
- Phone
- City
- Requirement / interest

Return it in JSON format ONLY.
If missing, put null.
"""
    messages = [
        {"role": "system", "content": summary_prompt},
        {"role": "user", "content": str(conversation_memory)}
    ]

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages
    ).choices[0].message.content

    return response
