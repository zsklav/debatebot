"""
Day 1: First contact with an LLM.

Goal: send one claim to Llama 3 70B (running on Groq's servers) and have it
disagree. This is the simplest possible thing that proves the entire stack
works end-to-end. Once this runs, we know:
  - the API key works
  - we can talk to a real LLM
  - we can shape its behavior with a system prompt

Run with:
    .venv/bin/python hello_groq.py
"""

import os
import sys
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY not found in environment.")
    print("Make sure you have a .env file with GROQ_API_KEY=gsk_... in it.")
    sys.exit(1)

client = Groq(api_key=api_key)

my_claim = "Remote work is more productive than working in an office."

response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[
        {
            "role": "system",
            "content": (
                "You are a sharp, articulate skeptic. The user will state a "
                "claim. Disagree with it firmly. Pick the strongest "
                "counter-argument you can think of. Be direct, not rude. "
                "3-4 sentences max."
            ),
        },
        {"role": "user", "content": my_claim},
    ],
    temperature=0.7,
)

print()
print("YOU SAID:")
print(f"  {my_claim}")
print()
print("BOT FIRES BACK:")
print(f"  {response.choices[0].message.content}")
print()
print(
    f"(used {response.usage.total_tokens} tokens — "
    f"{response.usage.prompt_tokens} in, "
    f"{response.usage.completion_tokens} out)"
)
