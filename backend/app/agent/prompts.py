"""Prompt construction.

The wizard collects plain English from staff; this module is the only place that turns it
into something a model reads. Keeping it separate means the ReAct format can be retuned
for a new model without touching the loop.

The format is text-based ReAct with a strict JSON action block rather than Ollama's native
tool-calling: 3-4B models are unreliable at emitting well-formed tool calls, but they are
reasonably good at copying a JSON shape shown to them in the prompt, and a text protocol
lets us retry and repair when they get it wrong.
"""

from __future__ import annotations

from .tools.base import ToolRegistry

ACTION_SCHEMA = '{"action": "<tool name>", "input": "<what to pass it>"}'
FINAL_SCHEMA = '{"action": "final_answer", "input": "<your answer to the user>"}'


def build_system_prompt(
    *,
    agent_name: str,
    role_instructions: str,
    role: str,
    user_name: str,
    preferences: list[str] | None = None,
) -> str:
    """The agent's identity and who it is talking to."""
    lines = [f"You are {agent_name}, an assistant used inside a school."]
    if role_instructions.strip():
        lines += ["", "Your job, as set by school staff:", role_instructions.strip()]

    lines += [
        "",
        f"You are currently talking to {user_name}, whose role is: {role}.",
        "Answer only what they ask. Keep answers short and in plain English — no jargon.",
        "If you do not know something, say so plainly rather than guessing.",
    ]

    if preferences:
        lines += ["", f"Standing preferences for {role}s:"]
        lines += [f"- {preference}" for preference in preferences]

    return "\n".join(lines)


def build_react_prompt(
    *,
    registry: ToolRegistry,
    question: str,
    history: list[tuple[str, str]],
    scratchpad: str,
    suggested_tools: list[str] | None = None,
) -> str:
    """The reason/act/observe prompt, rebuilt each iteration with the running scratchpad.

    `suggested_tools` carries the intent router's decision (FR6) into the prompt. Without
    it a 3B model asked "the trip costs 35 pounds for 3 children, what is the total?"
    answers "35" straight from `final_answer` instead of calling the calculator.
    """
    sections: list[str] = []

    if history:
        turns = "\n".join(
            f"{'User' if sender == 'user' else 'You'}: {text}" for sender, text in history
        )
        sections += ["Earlier in this conversation:", turns, ""]

    sections += [
        "You can use these tools:",
        registry.describe_all(),
        "",
        "Work in steps. Each step, reply with exactly ONE JSON object and nothing else.",
        "",
        "To use a tool:",
        ACTION_SCHEMA,
        "",
        "When you can answer, or if no tool helps:",
        FINAL_SCHEMA,
        "",
        "Rules:",
        "- Output ONE JSON object. No markdown, no code fences, no text around it.",
        '- "input" must always be a string.',
        "- Do not invent tool names. Use only the tools listed above.",
        "- After a tool result comes back, either use another tool or give final_answer.",
        "- If a tool keeps failing, give a final_answer explaining what you could not do.",
        "- If a tool can produce the answer, use it. Do not work it out yourself and do "
        "not put an unevaluated expression in final_answer.",
        "",
        f"Question: {question}",
    ]

    if suggested_tools:
        named = " or ".join(f"`{key}`" for key in suggested_tools)
        sections += [
            "",
            f"The wording of this question suggests {named} will help. "
            "Use it first unless you can already answer from the steps below.",
        ]

    if scratchpad:
        sections += ["", "Steps so far:", scratchpad]

    sections += ["", "Your next JSON object:"]
    return "\n".join(sections)


def build_repair_prompt(bad_output: str) -> str:
    """Sent after unparseable output. Deliberately terse — the model already has context."""
    return (
        "That was not valid JSON.\n\n"
        f"You replied:\n{bad_output[:500]}\n\n"
        "Reply again with ONE JSON object and absolutely nothing else. Either:\n"
        f"{ACTION_SCHEMA}\n"
        "or:\n"
        f"{FINAL_SCHEMA}\n\n"
        "Your JSON object:"
    )


def build_direct_prompt(
    *, question: str, history: list[tuple[str, str]], context: str = ""
) -> str:
    """No-tools path: answer straight from the system prompt and conversation."""
    sections: list[str] = []
    if history:
        turns = "\n".join(
            f"{'User' if sender == 'user' else 'You'}: {text}" for sender, text in history
        )
        sections += ["Earlier in this conversation:", turns, ""]
    if context:
        sections += ["Background you may use:", context, ""]
    sections += [f"User: {question}", "", "Reply to the user directly and briefly."]
    return "\n".join(sections)
