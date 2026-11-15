"""
DebateBot — interactive CLI.

Modes:
  1-3. Multi-turn debate with a chosen persona. Loops until you type /exit.
       Type /transcript to dump the full debate so far.
       Type /tokens to see cumulative tokens used.
  4.   Compare all three personas on one claim (single-shot, side-by-side).

Run with:
    .venv/bin/python -m tools.chat
"""

import os
import sys
from dotenv import load_dotenv
from groq import Groq
from app.debate.personas import PERSONAS
from app.debate.engine import Debate
from app.debate.scorer import score_argument, format_score_report
from app.debate.find_hole import find_hole, format_hole_report

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY not found. Check your .env file.")
    sys.exit(1)

client = Groq(api_key=api_key)


def banner() -> None:
    print()
    print("DebateBot")
    print("-" * 40)
    print("Pick a mode:")
    keys = list(PERSONAS.keys())
    for i, key in enumerate(keys, 1):
        p = PERSONAS[key]
        print(f"  {i}. Debate with {p['name']}")
        print(f"     ({p['tagline']})")
    print(f"  {len(keys) + 1}. Compare all three on one claim (single-shot)")
    print(f"  {len(keys) + 2}. Find the single weakest claim in a pasted argument")
    print()


def run_multiturn(persona_key: str) -> None:
    debate = Debate(client, persona_key)
    p = PERSONAS[persona_key]
    print()
    print(f"=== {p['name']} is ready. Type your claim to begin. ===")
    print("(Commands: /exit, /transcript, /tokens, /score, /findhole)")
    print()

    while True:
        try:
            user_input = input("YOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input == "/exit":
            print(f"\nDebate ended after {debate.turn_count()} turn(s).")
            break
        if user_input == "/transcript":
            print()
            print("--- TRANSCRIPT ---")
            print(debate.transcript())
            print("--- END ---\n")
            continue
        if user_input == "/tokens":
            print(f"\n(used {debate.total_tokens} tokens across "
                  f"{debate.turn_count()} turn(s) so far)\n")
            continue
        if user_input == "/score":
            if debate.turn_count() == 0:
                print("\nNo debate yet — make a claim first.\n")
                continue
            print("\n  ...scoring your argument (this takes ~2-4 seconds)...")
            try:
                result = score_argument(client, debate.transcript())
                print(format_score_report(result))
                print()
            except RuntimeError as e:
                print(f"\nScoring failed: {e}\n")
            continue
        if user_input == "/findhole":
            if debate.turn_count() == 0:
                print("\nNo debate yet — make a claim first.\n")
                continue
            print("\n  ...locating the single weakest claim...")
            try:
                weakness = find_hole(client, debate.transcript())
                print(format_hole_report(weakness))
                print()
            except RuntimeError as e:
                print(f"\nfind_hole failed: {e}\n")
            continue

        reply = debate.respond(user_input)
        print()
        print(f"{p['name'].upper()}: {reply}")
        print()


def read_multiline_argument() -> str:
    """Read a multi-line argument from the user. Ends on blank line or EOF."""
    print("Paste your argument below. Press Enter on an EMPTY line to finish:")
    print()
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "" and lines:
            break
        if line == "" and not lines:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def run_findhole_standalone() -> None:
    """Standalone mode: paste an argument, get the single weakest claim."""
    argument = read_multiline_argument()
    if not argument:
        print("Empty argument. Exiting.")
        return
    print("\n  ...locating the single weakest claim...")
    try:
        weakness = find_hole(client, argument)
        print(format_hole_report(weakness))
    except RuntimeError as e:
        print(f"\nfind_hole failed: {e}")


def run_compare(claim: str) -> None:
    for key in PERSONAS:
        d = Debate(client, key)
        reply = d.respond(claim)
        p = PERSONAS[key]
        print()
        print("=" * 72)
        print(f"  {p['name'].upper()}")
        print(f"  ({p['tagline']})")
        print("=" * 72)
        print(reply)


def main() -> None:
    banner()
    raw = input("Choice: ").strip()
    keys = list(PERSONAS.keys())
    if not raw.isdigit() or not (1 <= int(raw) <= len(keys) + 2):
        print("Invalid choice. Exiting.")
        sys.exit(1)
    choice = int(raw)

    if choice <= len(keys):
        run_multiturn(keys[choice - 1])
    elif choice == len(keys) + 1:
        print()
        claim = input("Your claim: ").strip()
        if not claim:
            print("Empty claim. Exiting.")
            sys.exit(1)
        run_compare(claim)
    else:
        print()
        run_findhole_standalone()
    print()


if __name__ == "__main__":
    main()
