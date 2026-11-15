"""Multi-turn debates with a full saved transcript and bounded model context."""

from groq import Groq
from app.debate.personas import PERSONAS
from app.core.model_config import get_model, reasoning_options

RECENT_TURNS = 4
HISTORY_CHARS = 12_000
MAX_INPUT_CHARS = 4_000
MAX_COMPLETION_TOKENS = 768


class Debate:
    """A single multi-turn debate session with one persona.

    The whole class is essentially a wrapper around `self.messages` — a list
    of {"role": ..., "content": ...} dicts that grows by 2 with every turn
    (one user message + one assistant reply).
    """

    def __init__(
        self, client: Groq, persona_key: str, model: str | None = None
    ) -> None:
        if persona_key not in PERSONAS:
            raise ValueError(
                f"Unknown persona '{persona_key}'. "
                f"Available: {list(PERSONAS.keys())}"
            )
        self.client = client
        self.persona_key = persona_key
        self.persona = PERSONAS[persona_key]
        self.model = get_model("debate", model)
        # Persist all exchanges; select a smaller context separately for inference.
        self.messages: list[dict] = [
            {"role": "system", "content": self.persona["system_prompt"]}
        ]
        # Cumulative token counter — useful for cost/scaling intuition.
        self.total_tokens = 0

    def request_messages(self, user_message: str) -> list[dict]:
        """Keep complete recent exchanges and excerpts of the opening exchange.

        Character budgets are deterministic safeguards, not exact token counts.
        No extra model call is made to summarize history. Excerpts retain their
        original user/assistant roles; user text never becomes a system rule.
        """
        recent = []
        used = 0
        history = self.messages[1:]
        for offset in range(len(history) - 2, max(-1, len(history) - RECENT_TURNS * 2 - 1), -2):
            pair = history[offset:offset + 2]
            size = sum(len(message["content"]) for message in pair)
            if used + size > HISTORY_CHARS:
                break
            recent[0:0] = pair
            used += size
        opening = []
        if len(recent) < len(history):
            for message in history[:2]:
                content = message["content"]
                excerpt = content[:800] + (" [excerpt ends]" if len(content) > 800 else "")
                opening.append({"role": message["role"], "content": excerpt})
        system = {"role": "system", "content": self.persona["system_prompt"] +
                  "\nKeep replies under 120 words. Older exchanges may be omitted; "
                  "opening excerpts and recent exchanges are not a complete history. "
                  "Ask for clarification rather than inventing missing context."}
        return [system, *opening, *recent, {"role": "user", "content": user_message}]

    def respond(self, user_message: str) -> str:
        """Commit a complete exchange only after a usable reply arrives."""
        if not user_message.strip():
            raise ValueError("User message must not be blank")
        if len(user_message) > MAX_INPUT_CHARS:
            raise ValueError(f"Keep each debate message within {MAX_INPUT_CHARS} characters")
        user_turn = {"role": "user", "content": user_message}
        pending_messages = self.request_messages(user_message)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=pending_messages,
            temperature=self.persona["temperature"],
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            **reasoning_options(self.model),
        )
        reply = response.choices[0].message.content
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Model returned an empty debate reply")
        # Usage is optional in provider responses. Compute it before mutating state.
        tokens = response.usage.total_tokens if response.usage else 0
        self.messages.extend([user_turn, {"role": "assistant", "content": reply}])
        self.total_tokens += tokens
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
