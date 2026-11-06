"""Asking Claude. It gets the rules and the catalog, and writes one query; the server checks and runs it, and tells Claude only how many rows came back,
or what the error was. Rows never reach Claude. A question costs at most MAX_CALLS requests."""

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal

import anthropic

from . import prompt, running, sqlcheck

MODEL = "claude-opus-5-5"
MAX_CALLS = 3
MAX_TOKENS = 16000
EFFORT = "high"            # a wrong query looks like a right one, so this isn't the place to save
FALLBACK_BETA = "server-side-fallback-2026-07-01"
TIMEOUT_SECONDS = 180
# per million tokens: input, output, cache read, cache write
PRICES = {"claude-opus-5-5": (Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5"))}
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sql", "title", "explanation", "clarification"],
                 "properties": {"sql": {"type": "string", "description": "One SELECT query, or empty when asking for clarification"},
                                "title": {"type": "string", "description": "A few words naming the result"},
                                "explanation": {"type": "string", "description": "What the query does, in plain words"},
                                "clarification": {"type": "string", "description": "A short question back, only when sql is empty"}}}


class NotConfigured(RuntimeError):
    """What the administrator has to do."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost(self, model: str) -> Decimal:
        rates = PRICES.get(model, PRICES[MODEL])        # an estimate for any other model: better than letting it pass the budget unseen
        counts = (self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens)
        return sum((Decimal(n) * rate for n, rate in zip(counts, rates)), Decimal(0)) / Decimal(1_000_000)


@dataclass
class Answer:
    status: str = "failed"     # answered, clarify, refused, failed
    title: str = ""
    explanation: str = ""
    clarification: str = ""
    problem: str = ""
    sql: str = ""
    sources: list = field(default_factory=list)
    total: int | None = None
    result: running.Result | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = MODEL
    seconds: float = 0


def model_name() -> str:
    return os.environ.get("ASSISTANT_MODEL") or MODEL


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"))


def client():
    if not is_configured():
        raise NotConfigured("The assistant has no Claude API key yet. An administrator can add a line ANTHROPIC_API_KEY=... to .local/database.env, then restart the web app.")
    return anthropic.Anthropic(timeout=TIMEOUT_SECONDS)


def _call(api, system: list, messages: list):
    """A request Claude declines is tried again on another model by the API itself (fallbacks), rather than leaving the person with nothing."""
    return api.beta.messages.create(model=model_name(), max_tokens=MAX_TOKENS, system=system, messages=messages, betas=[FALLBACK_BETA], fallbacks="default", thinking={"type": "adaptive"},
                                    output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}})


def _text_of(response) -> str:
    return next((b.text for b in response.content if b.type == "text"), "")


def _feedback(answer_text: str) -> dict:
    return {"role": "user", "content": answer_text}


def ask(question: str, user, today: str) -> Answer:
    started = time.monotonic()
    answer = Answer(model=model_name())
    try:
        _ask(question, user, today, answer)
    except anthropic.AuthenticationError:
        answer.problem = "The Claude API key was refused. An administrator can check ANTHROPIC_API_KEY in .local/database.env."
    except anthropic.PermissionDeniedError:
        answer.problem = "The Claude API key doesn't have access to this model."
    except anthropic.RateLimitError:
        answer.problem = "Claude is busy or the account's limit was reached. Try again in a minute."
    except anthropic.BadRequestError as e:
        answer.problem = f"Claude refused the request: {str(e.message)[:200]}"
    except anthropic.APIStatusError as e:
        answer.problem = f"Claude answered with an error ({e.status_code}). Try again in a minute."
    except anthropic.APIConnectionError:
        answer.problem = "Can't reach Claude. Is this computer online?"
    except RuntimeError as e:          # no database login, no key
        answer.problem = str(e)
    if answer.status == "answered":    # a try at improving an empty answer went wrong: the answer still stands
        answer.problem = ""
    answer.seconds = round(time.monotonic() - started, 2)
    return answer


def _ask(question: str, user, today: str, answer: Answer) -> None:
    datasets = prompt.usable_datasets(user)
    if not datasets:
        answer.problem = "You don't have the right to see any dataset the assistant can use."
        return
    allowed = prompt.allowed_map(datasets)
    system = [{"type": "text", "text": prompt.RULES}, {"type": "text", "text": prompt.describe(datasets), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": f"Today is {today}.\n\nQuestion: {question}"}]
    api = client()
    for _ in range(MAX_CALLS):
        response = _call(api, system, messages)
        answer.usage.add(response.usage)
        retrying_empty = answer.status == "answered"
        if response.stop_reason == "refusal":
            if not retrying_empty:
                answer.status, answer.problem = "refused", "Claude declined to answer that."
            return
        if response.stop_reason == "max_tokens":
            answer.problem = "The answer was too long. Ask for something simpler."
            return
        try:
            reply = json.loads(_text_of(response))
        except ValueError:
            answer.problem = "Claude's answer couldn't be read. Try asking again."
            return
        if not (reply.get("sql") or "").strip():
            if not retrying_empty:
                answer.status, answer.clarification = "clarify", str(reply.get("clarification") or "Could you say a bit more?")
            return
        messages.append({"role": "assistant", "content": response.content})        # whole, thinking included: the API requires it unchanged
        try:
            checked = sqlcheck.check(reply["sql"], allowed)
            result = running.run(checked.sql)
        except sqlcheck.SqlRefused as e:
            answer.problem = f"The query wasn't allowed: {e}"
            messages.append(_feedback(f"The server refused that query: {e}\nWrite it again within the rules."))
            continue
        except running.QueryFailed as e:
            answer.problem = f"The query failed: {e}"
            messages.append(_feedback(f"PostgreSQL said: {e}\nFix the query."))
            continue
        answer.title, answer.explanation = str(reply.get("title") or "")[:120], str(reply.get("explanation") or "")
        answer.sql, answer.sources, answer.result, answer.total = checked.sql, checked.sources, result, result.total
        answer.status, answer.problem = "answered", ""
        if result.total or retrying_empty:
            return
        messages.append(_feedback("That returned 0 rows. If a condition is probably too strict (spelling, capitals, a value that's stored differently), write it again. If 0 rows is likely the true answer, send the same query."))
