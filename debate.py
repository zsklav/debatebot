"""
Day 3: Multi-turn debates.

The big trick: LLMs don't actually have memory. To make the bot "remember"
what you said earlier, you keep a list of every message in the conversation
and resend the full list on each API call. The model re-reads the whole
history and replies to your latest message.

This is the foundation of every chat AI ever built. ChatGPT, Claude.ai,
Cursor, Copilot Chat — all of them are doing this exact dance under the hood.
"""

from groq import Groq
from personas import PERSONAS

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class Debate:
    """A single multi-turn debate session with one persona.

    The whole class is essentially a wrapper around `self.messages` — a list
    of {"role": ..., "content": ...} dicts that grows by 2 with every turn
    (one user message + one assistant reply).
    """

    def __init__(
        self, client: Groq, persona_key: str, model: str = DEFAULT_MODEL
    ) -> None:
        if persona_key not in PERSONAS:
            raise ValueError(
                f"Unknown persona '{persona_key}'. "
                f"Available: {list(PERSONAS.keys())}"
            )
        self.client = client
        self.persona_key = persona_key
        self.persona = PERSONAS[persona_key]
        self.model = model
        # The system prompt is the first message; it's set once and never
        # removed. Every API call sends this whole list.
        self.messages: list[dict] = [
            {"role": "system", "content": self.persona["system_prompt"]}
        ]
        # Cumulative token counter — useful for cost/scaling intuition.
        self.total_tokens = 0

    def respond(self, user_message: str) -> str:
        """Append a user turn, send the full history, store the reply."""
        self.messages.append({"role": "user", "content": user_message})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            temperature=self.persona["temperature"],
        )
        reply = response.choices[0].message.content
        self.messages.append({"role": "assistant", "content": reply})
        self.total_tokens += response.usage.total_tokens
        return reply

    def turn_count(self) -> int:
        """Number of completed user-assistant exchanges."""
        # subtract the system message, divide remaining by 2
        return (len(self.messages) - 1) // 2

    def transcript(self) -> str:
        """Pretty-printed conversation history. Skips the system prompt."""
        lines = []
        for m in self.messages[1:]:
            who = "YOU" if m["role"] == "user" else self.persona["name"].upper()
            lines.append(f"{who}: {m['content']}")
        return "\n\n".join(lines)
