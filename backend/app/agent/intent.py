"""Intent routing (FR6): decide how a query should be answered.

Three destinations:

* `TOOLS`     — the ReAct loop, because a tool the agent has could answer this.
* `RETRIEVAL` — knowledge-base lookup. **Not built yet**; Phase 3 replaces the stub.
* `DIRECT`    — chat/greeting/opinion, answered from the prompt with no tool call.

Routing is rule-based rather than a model call. A model round-trip costs 3-8 seconds on
this hardware, which is a large fraction of the NFR3 budget, and spending it just to
choose a branch is poor value when keywords do the job. Phase 4 can measure whether a
learned router is worth the latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Route(str, Enum):
    TOOLS = "tools"
    RETRIEVAL = "retrieval"
    DIRECT = "direct"


@dataclass(frozen=True)
class RoutingDecision:
    route: Route
    reason: str
    # Tool keys the wording points at; the loop still decides what to actually call.
    suggested_tools: list[str]


_ARITHMETIC = re.compile(r"\d\s*[-+*/^]\s*\d|\b\d+%\s|\bhow much\b|\btotal\b|\bcalculate\b")
_CODE = re.compile(r"\bpython\b|\bscript\b|\bcode\b|\brun this\b|\bsimulate\b")
_FILE = re.compile(r"\bfile\b|\bdocument\b|\bhandbook\b|\bpolicy\b|\bletter\b|\bnewsletter\b|\.(md|txt|csv|pdf)\b")
_SEARCH = re.compile(r"\bsearch\b|\bfind\b|\blook up\b|\bwhere (?:can|do) i\b|\bwhat does .* say\b")

# Questions about the school's own records. Retrieval owns these from Phase 3; until then
# they route to DIRECT with a placeholder, never to a tool that cannot answer them.
_SCHOOL_RECORDS = re.compile(
    r"\battendance\b|\babsen\w*\b|\bfee[s]?\b|\bpay(?:ment|ing|able)?\b|\binvoice\b|"
    # "how much do I owe" names no domain word at all, but is unambiguously a fee question.
    r"\bowe[sd]?\b|\bowing\b|\bbalance\b|\boutstanding\b|\barrears\b|\bbill\b|"
    r"\bgrade[s]?\b|\bresult[s]?\b|\bexam[s]?\b|\bmark[s]?\b|\bscore[s]?\b|"
    r"\bhomework\b|\bassignment\b|\btimetable\b|\bschedule\b|\blesson[s]?\b|"
    r"\bmy child\b|\bmy son\b|\bmy daughter\b|\bhow (?:is|are) (?:my|he|she) .*doing\b"
)

_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon)|bye)\b", re.I)


def route_query(question: str, available_tools: list[str]) -> RoutingDecision:
    text = question.lower().strip()
    available = set(available_tools)

    if _GREETING.match(text) and len(text.split()) <= 4:
        return RoutingDecision(Route.DIRECT, "greeting or pleasantry", [])

    # School records are retrieval's job, and retrieval does not exist yet. Check this
    # before the tool patterns so "how much do I owe in fees" is not sent to the
    # calculator, which has no idea what the fees are.
    if _SCHOOL_RECORDS.search(text):
        return RoutingDecision(
            Route.RETRIEVAL, "asks about school records held in the knowledge base", []
        )

    suggested: list[str] = []
    if "code_runner" in available and _CODE.search(text):
        suggested.append("code_runner")
    if "calculator" in available and _ARITHMETIC.search(text):
        suggested.append("calculator")
    if "file_reader" in available and _FILE.search(text):
        suggested.append("file_reader")
    if "search" in available and (_SEARCH.search(text) or _FILE.search(text)):
        suggested.append("search")

    if suggested:
        return RoutingDecision(Route.TOOLS, f"wording suggests {', '.join(suggested)}", suggested)

    # An agent with tools gets the ReAct loop anyway — the model is a better judge than
    # these patterns, and it can still answer directly with final_answer on step one.
    if available:
        return RoutingDecision(Route.TOOLS, "agent has tools; letting the model decide", [])

    return RoutingDecision(Route.DIRECT, "no tools configured for this agent", [])


# --------------------------------------------------------------------------------------
# Phase 3a: the retrieval branch is implemented
# --------------------------------------------------------------------------------------
#
# `Route.RETRIEVAL` is handled by `agent/graph_retrieval.py`, which selects a SPARQL
# template, runs it through `graph/retrieval.py` with the caller's role scope injected,
# and phrases the rows. The Phase 2 placeholder that used to live here is gone.
#
# Routing still happens here and still runs before any model call, so a question about
# fees never reaches the calculator.
