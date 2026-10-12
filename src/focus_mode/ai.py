"""Optional AI helpers for any OpenAI-compatible chat API.

Works with OpenAI, OpenRouter, Groq, Together, LM Studio, Ollama
(http://localhost:11434/v1) and anything else that speaks /chat/completions.
Uses only the standard library. Calls are blocking: run them off the UI thread.
"""

from __future__ import annotations

import ast
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from focus_mode.db import PRIORITIES
from focus_mode.messages import Message

SYSTEM_JSON = (
    "You are a precise assistant. Reply with a single valid JSON object only: no prose, no explanation, "
    "no reasoning, no code fences. Use double quotes, no trailing commas. Start with { and end with }."
)
JSON_FAILED = "The model's reply wasn't valid JSON. Try again or pick a stronger model."


class AIError(Exception):
    """A problem the user can read and act on."""

    def __init__(self, message: str = "", code: int | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.code = code  # HTTP status, when the server sent one
        self.raw = raw  # the model's last raw reply, when JSON parsing failed


def _is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class AIClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = timeout
        self._json_mode = True  # try response_format=json_object; switched off if the server refuses it

    @property
    def configured(self) -> bool:
        if not (self.base_url and self.model):
            return False
        return bool(self.api_key) or _is_local(self.base_url)

    def endpoint(self) -> str:
        url = self.base_url.rstrip("/")
        return url if url.endswith("/chat/completions") else url + "/chat/completions"

    def chat(self, system: str, user: str, temperature: float = 0.7, max_tokens: int = 700) -> str:
        msg = self.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return clean_reply(_content_text(msg))

    def chat_json(self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 700):
        """Ask for JSON and return the parsed object.

        Copes with what real models do: reasoning blocks, code fences, chatter, trailing commas,
        single quotes and replies cut off by the token limit. Uses the server's JSON mode when it
        has one, and retries once with a stricter instruction before giving up.
        """
        budget = max(max_tokens * 2, 1200)  # reasoning models spend tokens thinking before they answer
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        text = ""
        for attempt in range(2):
            msg = self._complete_json(messages, temperature if attempt == 0 else 0.0, budget)
            text = _reply_text(msg)
            try:
                return extract_json(text)
            except AIError:
                pass
            messages = [
                *messages[:2],
                {"role": "assistant", "content": text[:1500] or "(no answer)"},
                {
                    "role": "user",
                    "content": "That was not valid JSON. Reply again with ONLY the JSON object: "
                    "no explanation, no reasoning, no code fences.",
                },
            ]
            budget = min(budget * 2, 4000)
        raise AIError(JSON_FAILED, raw=text)

    def _complete_json(self, messages: list[dict], temperature: float, max_tokens: int) -> dict:
        if self._json_mode:
            try:
                return self.complete(
                    messages, temperature=temperature, max_tokens=max_tokens, response_format={"type": "json_object"}
                )
            except AIError as exc:
                if exc.code not in (400, 415, 422):
                    raise
                self._json_mode = False  # this server doesn't take response_format; remember that
        return self.complete(messages, temperature=temperature, max_tokens=max_tokens)

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 700,
        response_format: dict | None = None,
    ) -> dict:
        """One chat-completions call. Returns the assistant message (may contain tool_calls)."""
        if not self.configured:
            raise AIError("AI is not set up. Add a base URL, API key and model in Settings.")
        payload: dict = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "focus-mode/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.endpoint(), data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise AIError(_http_message(exc, tools_used=bool(tools)), code=exc.code) from None
        except urllib.error.URLError as exc:
            raise AIError(f"Can't reach the AI server: {exc.reason}") from None
        except (TimeoutError, OSError):
            raise AIError("The AI server took too long to answer.") from None
        except json.JSONDecodeError:
            raise AIError("The server replied with something that isn't JSON. Check the base URL.") from None
        try:
            return dict(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError):
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict) and err.get("message"):
                raise AIError(str(err["message"])[:300]) from None
            raise AIError("Unexpected reply format. Is this an OpenAI-compatible URL?") from None


def _http_message(exc: urllib.error.HTTPError, tools_used: bool = False) -> str:
    detail = ""
    try:
        data = json.loads(exc.read().decode("utf-8", "replace"))
        err = data.get("error", data)
        detail = err.get("message", "") if isinstance(err, dict) else str(err)
    except Exception:
        pass
    hints = {401: "API key rejected (401).", 403: "Access denied (403).", 404: "Not found (404): check the base URL and model name.", 429: "Rate limited (429): try again soon."}
    base = hints.get(exc.code, f"Server error ({exc.code}).")
    if tools_used and ("tool" in detail.lower() or exc.code in (400, 404)) and "key" not in detail.lower():
        base = "This model may not support tool calling (needed for the agent). Try another model, e.g. one marked 'tools' on OpenRouter."
    return f"{base} {detail}".strip()[:300]


_REASONING_TAG = r"(?:think|thinking|thought|reasoning)"
_SMART_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def _content_text(msg: dict) -> str:
    """The assistant text, whether the server sends a string or a list of content parts."""
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "").strip()


def _reply_text(msg: dict) -> str:
    """Like _content_text, but falls back to the reasoning field some servers use when `content` is empty."""
    return _content_text(msg) or str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip()


def clean_reply(text: str) -> str:
    """Drop <think>...</think> style reasoning blocks that some models put inside the reply."""
    text = re.sub(rf"<({_REASONING_TAG})>.*?</\1>", "", text or "", flags=re.S | re.I)
    # Some templates only emit the closing tag: everything before it was reasoning.
    closers = list(re.finditer(rf"</{_REASONING_TAG}>", text, re.I))
    if closers:
        text = text[closers[-1].end() :]
    # An opening tag that never closes means the model ran out of tokens while thinking.
    opener = re.search(rf"<{_REASONING_TAG}>", text, re.I)
    if opener:
        text = text[: opener.start()]
    return text.strip()


def _scan(text: str, start: int):
    """Walk the bracketed value that begins at `start`.

    Returns (end, stack, in_string): `end` is the index just past the matching closer, or None if the
    text finished (or the brackets didn't match) first; `stack` holds the closers still owed.
    """
    stack: list[str] = []
    in_str, quote, esc = False, "", False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str, quote = True, ch
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None, [], False  # mismatched: not salvageable from here
            stack.pop()
            if not stack:
                return i + 1, [], False
    return None, stack, in_str


def _loads_lenient(raw: str):
    """json.loads that forgives trailing commas, smart quotes and Python-style literals."""
    nocomma = re.sub(r",\s*([}\]])", r"\1", raw)
    straight = nocomma.translate(_SMART_QUOTES)
    for candidate in (raw, nocomma, straight):
        try:
            return json.loads(candidate)
        except ValueError:
            pass
    pythonic = re.sub(r"\bnull\b", "None", re.sub(r"\bfalse\b", "False", re.sub(r"\btrue\b", "True", straight)))
    for candidate in (straight, pythonic):  # single-quoted keys/strings, True/None
        try:
            return ast.literal_eval(candidate)
        except Exception:
            pass
    return None


def _salvage(fragment: str):
    """Recover what we can from a reply that was cut off mid-way: close it after the last complete item."""
    cuts = [m.end() for m in re.finditer(r"[}\]]", fragment)]
    for cut in reversed(cuts[-80:]):
        head = fragment[:cut]
        end, stack, in_str = _scan(head, 0)
        if end is None and stack and not in_str:
            value = _loads_lenient(head + "".join(reversed(stack)))
            if isinstance(value, (dict, list)) and value:
                return value
    return None


def _find_json(pool: str):
    pool = pool[:40000]
    whole = _loads_lenient(pool.strip())
    if isinstance(whole, (dict, list)):
        return whole
    tried = 0
    for i, ch in enumerate(pool):
        if ch not in "{[":
            continue
        tried += 1
        if tried > 60:
            break
        end, stack, _in_str = _scan(pool, i)
        if end is not None:
            value = _loads_lenient(pool[i:end])
            if isinstance(value, (dict, list)):
                return value
        elif stack:  # ran off the end: probably truncated
            value = _salvage(pool[i:])
            if value is not None:
                return value
    return None


def extract_json(text: str):
    """Pull a JSON object/array out of a model reply, tolerating reasoning blocks, fences, chatter,
    trailing commas, single quotes and truncation."""
    text = clean_reply(text)
    pools = [m.group(1) for m in re.finditer(r"```[\w-]*\s*(.*?)```", text, re.S)]
    if text.count("```") % 2:  # an opening fence that never closed (cut off)
        pools.append(text.rsplit("```", 1)[1].split("\n", 1)[-1])
    pools.append(text)
    for pool in pools:
        value = _find_json(pool)
        if value is not None:
            return value
    raise AIError(JSON_FAILED, raw=text)


def _messages_from_text(text: str, count: int) -> list[Message]:
    """Last resort for the speech bubble: pull lines out of a reply that isn't parseable JSON."""
    text = clean_reply(text)
    found: list[tuple[str, str]] = re.findall(
        r'"headline"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"body"\s*:\s*"((?:[^"\\]|\\.)*)"', text
    )
    if not found:
        for line in text.splitlines():
            line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip().strip("\"'\u201c\u201d")
            m = re.match(r"^([A-Z][A-Z' !?-]{0,17}[.!?])\s+(.{3,})$", line)
            if m:
                found.append((m.group(1), m.group(2).strip().strip("\"'\u201c\u201d")))
    out: list[Message] = []
    for head, body in found:
        if head.strip() and body.strip():
            out.append(Message(head.strip().upper()[:18], body.strip()[:90]))
    return out[:count]


# -- features ---------------------------------------------------------------
def check_connection(client: AIClient) -> str:
    reply = client.chat("You are a connection test.", "Reply with the single word: OK", temperature=0, max_tokens=8)
    return reply[:40] or "OK"


def suggest_priority(client: AIClient, title: str, due: str | None = None) -> dict:
    prompt = (
        "Rate this to-do item for a student/professional's focus planner.\n"
        f"Task: {title!r}\nDue: {due or 'unspecified'}\n"
        'Return {"priority": "high|medium|low", "minutes": <realistic focus minutes, integer>, '
        '"reason": "<max 12 words>"}'
    )
    data = client.chat_json(SYSTEM_JSON, prompt, temperature=0.2, max_tokens=150)
    if not isinstance(data, dict):
        raise AIError("Unexpected answer from the model.")
    priority = str(data.get("priority", "medium")).lower()
    try:
        minutes = int(data.get("minutes", 0))
    except (TypeError, ValueError):
        minutes = 0
    return {
        "priority": priority if priority in PRIORITIES else "medium",
        "minutes": max(0, min(minutes, 600)),
        "reason": str(data.get("reason", ""))[:120],
    }


def prioritize_tasks(client: AIClient, tasks: list[dict]) -> dict[int, tuple[str, str]]:
    """tasks: [{id, title, due, start, end}] -> {id: (priority, reason)}"""
    prompt = (
        "Assign a priority to each task using urgency (due dates) and importance. "
        "Be decisive; not everything is high.\n"
        f"Tasks: {json.dumps(tasks, ensure_ascii=False)}\n"
        'Return {"priorities": [{"id": <int>, "priority": "high|medium|low", "reason": "<max 10 words>"}]}'
    )
    data = client.chat_json(SYSTEM_JSON, prompt, temperature=0.2, max_tokens=900)
    items = data.get("priorities", []) if isinstance(data, dict) else data
    valid = {t["id"] for t in tasks}
    out: dict[int, tuple[str, str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            tid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        pr = str(item.get("priority", "")).lower()
        if tid in valid and pr in PRIORITIES:
            out[tid] = (pr, str(item.get("reason", ""))[:100])
    if not out:
        raise AIError("The model didn't return usable priorities.")
    return out


def _message_items(data) -> list:
    """The list of {headline, body} objects, wherever the model put it."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("messages"), list):
            return data["messages"]
        if data.get("headline") and data.get("body"):  # a single bare object
            return [data]
        for value in data.values():  # some other key, e.g. "lines" or "quotes"
            if isinstance(value, list):
                return value
    return []


def generate_messages(client: AIClient, context: str, count: int = 8) -> list[Message]:
    situation = {
        "idle": "the user is about to start a focus session",
        "focus": "the user is in the middle of a focus session and may be tempted to check their phone",
        "break": "the user is on a short break between focus sessions",
        "done": "the user just finished a focus session",
    }.get(context, "the user is working")
    prompt = (
        f"You are a stern but caring dark-knight guardian helping someone avoid distraction. "
        f"Situation: {situation}.\nWrite {count} different short lines. "
        'Return {"messages": [{"headline": "<1-2 words, CAPS, ends with period>", "body": "<max 8 words>"}]}'
    )
    try:
        data = client.chat_json(SYSTEM_JSON, prompt, temperature=0.9, max_tokens=500)
    except AIError as exc:
        salvaged = _messages_from_text(exc.raw, count) if exc.raw else []
        if salvaged:
            return salvaged
        raise
    items = _message_items(data)
    out: list[Message] = []
    for item in items:
        if isinstance(item, dict) and item.get("headline") and item.get("body"):
            out.append(Message(str(item["headline"]).upper()[:18], str(item["body"])[:90]))
    if not out:
        raise AIError("The model didn't return any messages.")
    return out[:count]


def write_insight(client: AIClient, snapshot: dict) -> str:
    prompt = (
        "These are a person's focus-timer statistics:\n"
        f"{json.dumps(snapshot)}\n"
        "Write a short coaching insight in plain sentences (max 90 words, no lists, no markdown): "
        "what the pattern shows, one thing they do well, and one concrete next step. "
        "If there is little data, say so and encourage a first session."
    )
    return client.chat(
        "You are a practical productivity coach. Be specific, warm and brief.",
        prompt,
        temperature=0.6,
        max_tokens=300,
    )
