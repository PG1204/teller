"""The provider seam: ``Decider`` turns (observation, previous tool result) into one tool call.

Three implementations:

* ``GeminiDecider``     — google-genai SDK, function calling forced to exactly one call per turn.
* ``AnthropicDecider``  — official Anthropic SDK, manual tool-use loop with strict tools.
* ``ScriptedDecider``   — replays a recorded cassette (offline demos, tests, graders without keys).

Each decider keeps its *own* provider-native history (Gemini needs thought signatures echoed
back verbatim; Anthropic needs tool_use/tool_result pairing), trims old screenshots itself, and
exposes a provider-neutral ``transcript()`` for evidence. Nothing outside this module knows which
provider is in use; switching is a CLI flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from teller.discovery.tools import ToolSpec

log = logging.getLogger("teller.discovery.llm")


@dataclass
class Decision:
    tool: str
    args: dict[str, Any]
    text: str | None = None  # any prose the model emitted alongside (should be rare)
    stop_reason: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


@dataclass
class ToolFeedback:
    """What the loop tells the model about its previous call."""

    tool: str
    ok: bool
    text: str


class Decider(Protocol):
    name: str
    model: str

    def start(self, system_prompt: str, tools: list[ToolSpec], first_user_text: str) -> None: ...
    def decide(
        self, observation_text: str, screenshot_jpeg: bytes | None, feedback: ToolFeedback | None
    ) -> Decision: ...
    def usage(self) -> dict[str, Any]: ...
    def transcript(self) -> list[dict[str, Any]]: ...


class DeciderError(RuntimeError):
    pass


def obs_hash(observation_text: str) -> str:
    return hashlib.sha256(observation_text.encode()).hexdigest()[:16]


def _retry_delay_from(err: Exception) -> float | None:
    """Honour the server's RetryInfo ('Please retry in 59.3s' / retryDelay: '59s') when present."""
    import re

    text = str(err)
    m = re.search(r"retry in ([0-9.]+)s", text) or re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?([0-9.]+)s", text)
    if m:
        return float(m.group(1)) + 1.0
    return None


# ---------------------------------------------------------------------------------------------
# Provider-neutral transcript kept by every decider (images replaced by file refs by the caller)
# ---------------------------------------------------------------------------------------------


class _Transcript:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.thinking_tokens = 0
        self.cached_tokens = 0
        self.wall_ms = 0

    def add(self, **turn: Any) -> None:
        self.turns.append(turn)

    def usage(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "cached_input_tokens": self.cached_tokens,
            "model_wall_ms": self.wall_ms,
        }


# ---------------------------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------------------------


class GeminiDecider:
    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        thinking_level: str = "HIGH",
        keep_images: int = 2,
        max_retries: int = 6,
        requests_per_minute: float | None = None,
    ):
        from google import genai

        self.model = model or os.environ.get("TELLER_GEMINI_MODEL", "gemini-3.8-flash")
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise DeciderError("GEMINI_API_KEY is not set (put it in .env or the environment)")
        self._client = genai.Client(api_key=key)
        self._thinking_level = thinking_level
        self._keep_images = keep_images
        self._max_retries = max_retries
        self._contents: list[Any] = []
        self._config: Any = None
        self._tools: list[ToolSpec] = []
        self._t = _Transcript()
        self._pending_call_name: str | None = None
        self._extra_calls: list[str] = []  # parallel calls we answered with 'ignored'
        # free tier is 5 requests/minute on Flash: pace calls instead of tripping 429s
        rpm = requests_per_minute or float(os.environ.get("TELLER_GEMINI_RPM", "5"))
        self._min_interval = (60.0 / rpm) + 0.5 if rpm > 0 else 0.0
        self._last_call = 0.0

    def start(self, system_prompt: str, tools: list[ToolSpec], first_user_text: str) -> None:
        from google.genai import types

        self._tools = tools
        decls = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.json_schema()
            )
            for t in tools
        ]
        thinking = (
            types.ThinkingConfig(thinking_level=self._thinking_level)
            if self.model.startswith("gemini-3")
            else None  # 2.5-era models take thinking_budget; their default is fine here
        )
        self._config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=decls)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            thinking_config=thinking,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.0,
            max_output_tokens=4096,
        )
        self._contents = [types.Content(role="user", parts=[types.Part.from_text(text=first_user_text)])]
        self._t.add(role="user", text=first_user_text)

    def _trim_images(self) -> None:
        """Keep only the most recent N screenshots as image bytes; stub the rest as text."""
        from google.genai import types

        seen = 0
        for content in reversed(self._contents):
            if content.role != "user":
                continue
            new_parts = []
            for part in content.parts or []:
                is_img = part.inline_data is not None
                if is_img:
                    seen += 1
                    if seen > self._keep_images:
                        new_parts.append(types.Part.from_text(text="(earlier screenshot omitted)"))
                        continue
                new_parts.append(part)
            content.parts = new_parts

    def decide(
        self, observation_text: str, screenshot_jpeg: bytes | None, feedback: ToolFeedback | None
    ) -> Decision:
        from google.genai import errors, types

        parts: list[Any] = []
        if feedback is not None and self._pending_call_name:
            parts.append(
                types.Part.from_function_response(
                    name=self._pending_call_name,
                    response={"ok": feedback.ok, "result": feedback.text},
                )
            )
            for extra in self._extra_calls:  # every emitted call must get a response
                parts.append(types.Part.from_function_response(
                    name=extra, response={"ok": False, "result": "ignored: exactly one tool call per turn; only the first was executed"},
                ))
        elif feedback is not None:
            parts.append(types.Part.from_text(text=f"SYSTEM NOTE: {feedback.text}"))
        self._extra_calls = []
        parts.append(types.Part.from_text(text=observation_text))
        if screenshot_jpeg:
            parts.append(types.Part.from_bytes(data=screenshot_jpeg, mime_type="image/jpeg"))
        self._contents.append(types.Content(role="user", parts=parts))
        self._trim_images()
        self._t.add(
            role="user",
            feedback=feedback.__dict__ if feedback else None,
            observation=observation_text,
            screenshot=bool(screenshot_jpeg),
            observation_hash=obs_hash(observation_text),
        )

        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            t0 = time.monotonic()
            try:
                resp = self._client.models.generate_content(
                    model=self.model, contents=self._contents, config=self._config
                )
                self._last_call = time.monotonic()
                break
            except errors.APIError as e:  # 429 / 5xx: back off; 4xx other: raise
                last_err = e
                self._last_call = time.monotonic()
                code = getattr(e, "code", None)
                if code == 429 and ("PerDay" in str(e) or "per day" in str(e).lower()):
                    raise DeciderError(f"gemini daily quota exhausted for {self.model}: {e}") from e
                if code in (429, 500, 502, 503, 504) and attempt < self._max_retries - 1:
                    delay = _retry_delay_from(e) or (5 * (attempt + 1))
                    if code == 429:
                        delay = max(delay, 30)
                    delay = min(delay, 120)
                    log.warning("gemini %s; retrying in %ss", code, delay)
                    time.sleep(delay)
                    continue
                raise DeciderError(f"gemini error: {e}") from e
        else:  # pragma: no cover
            raise DeciderError(f"gemini error: {last_err}")
        latency = int((time.monotonic() - t0) * 1000)

        self._t.calls += 1
        self._t.wall_ms += latency
        um = resp.usage_metadata
        usage: dict[str, Any] = {}
        if um is not None:
            self._t.input_tokens += um.prompt_token_count or 0
            self._t.output_tokens += um.candidates_token_count or 0
            self._t.thinking_tokens += um.thoughts_token_count or 0
            self._t.cached_tokens += um.cached_content_token_count or 0
            usage = {
                "prompt_tokens": um.prompt_token_count,
                "output_tokens": um.candidates_token_count,
                "thinking_tokens": um.thoughts_token_count,
            }

        if not resp.candidates:
            fb = getattr(resp, "prompt_feedback", None)
            raise DeciderError(f"gemini returned no candidates (blocked? {fb})")
        cand = resp.candidates[0]
        content = cand.content
        finish = str(getattr(cand, "finish_reason", "") or "")
        if content is None or not content.parts:
            # SAFETY / MAX_TOKENS / empty: nothing to echo back; report as a non-tool turn
            self._pending_call_name = None
            self._t.add(role="model", text=None, finish_reason=finish, usage=usage)
            tool = "__refusal__" if "SAFETY" in finish.upper() or "BLOCK" in finish.upper() else "__no_tool__"
            return Decision(tool=tool, args={}, stop_reason=finish or "empty", raw_usage=usage, latency_ms=latency)
        # echo the model turn back verbatim (thought signatures included) on the next call
        self._contents.append(content)

        text_bits: list[str] = []
        call: Any = None
        for part in content.parts or []:
            if part.function_call is not None:
                if call is None:
                    call = part.function_call
                else:
                    self._extra_calls.append(part.function_call.name)
            elif part.text and not getattr(part, "thought", False):
                text_bits.append(part.text)
        if call is None:
            self._pending_call_name = None
            self._t.add(role="model", text="\n".join(text_bits), finish_reason=finish, usage=usage)
            return Decision(
                tool="__no_tool__", args={}, text="\n".join(text_bits) or None,
                stop_reason=finish or "no_tool", raw_usage=usage, latency_ms=latency,
            )
        self._pending_call_name = call.name
        args = dict(call.args or {})
        self._t.add(
            role="model", tool=call.name, args=args, text="\n".join(text_bits) or None,
            finish_reason=finish, usage=usage, latency_ms=latency,
        )
        return Decision(
            tool=call.name, args=args, text="\n".join(text_bits) or None,
            stop_reason=finish, raw_usage=usage, latency_ms=latency,
        )

    def usage(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, **self._t.usage()}

    def transcript(self) -> list[dict[str, Any]]:
        return self._t.turns


# ---------------------------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------------------------


class AnthropicDecider:
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        effort: str = "high",
        keep_images: int = 2,
        max_tokens: int = 16000,
        fallbacks: bool = True,
    ):
        import anthropic

        self.model = model or os.environ.get("TELLER_ANTHROPIC_MODEL", "claude-opus-5")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise DeciderError("ANTHROPIC_API_KEY is not set (put it in .env or the environment)")
        self._client = anthropic.Anthropic(api_key=key, max_retries=3)
        self._effort = effort
        self._keep_images = keep_images
        self._max_tokens = max_tokens
        self._fallbacks = fallbacks
        self._system = ""
        self._tools: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_use_id: str | None = None
        self._t = _Transcript()

    def start(self, system_prompt: str, tools: list[ToolSpec], first_user_text: str) -> None:
        self._system = system_prompt
        self._tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.json_schema(),
                "strict": True,
            }
            for t in tools
        ]
        self._messages = [{"role": "user", "content": [{"type": "text", "text": first_user_text}]}]
        self._t.add(role="user", text=first_user_text)

    def _trim_images(self) -> None:
        seen = 0
        stub = {"type": "text", "text": "(earlier screenshot omitted)"}
        for msg in reversed(self._messages):
            if msg["role"] != "user" or not isinstance(msg["content"], list):
                continue
            for j, block in enumerate(msg["content"]):
                if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                    inner = block["content"]
                    for i, b in enumerate(inner):
                        if b.get("type") == "image":
                            seen += 1
                            if seen > self._keep_images:
                                inner[i] = dict(stub)
                elif block.get("type") == "image":
                    seen += 1
                    if seen > self._keep_images:
                        msg["content"][j] = dict(stub)

    def decide(
        self, observation_text: str, screenshot_jpeg: bytes | None, feedback: ToolFeedback | None
    ) -> Decision:
        import anthropic

        obs_blocks: list[dict[str, Any]] = [{"type": "text", "text": observation_text}]
        if screenshot_jpeg:
            obs_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(screenshot_jpeg).decode(),
                    },
                }
            )
        content: list[dict[str, Any]]
        if feedback is not None and self._pending_tool_use_id:
            content = [
                {
                    "type": "tool_result",
                    "tool_use_id": self._pending_tool_use_id,
                    "is_error": not feedback.ok,
                    "content": [{"type": "text", "text": feedback.text}, *obs_blocks],
                }
            ]
        elif feedback is not None:
            content = [{"type": "text", "text": f"SYSTEM NOTE: {feedback.text}"}, *obs_blocks]
        else:
            content = obs_blocks
        self._messages.append({"role": "user", "content": content})
        self._trim_images()
        self._t.add(
            role="user", feedback=feedback.__dict__ if feedback else None,
            observation=observation_text, screenshot=bool(screenshot_jpeg),
            observation_hash=obs_hash(observation_text),
        )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            "tools": self._tools,
            "messages": self._messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }
        if self.model.startswith("claude-fable"):
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        else:
            kwargs["tool_choice"] = {"type": "any", "disable_parallel_tool_use": True}
        t0 = time.monotonic()
        try:
            if self._fallbacks:
                resp = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            raise DeciderError(f"anthropic error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise DeciderError(f"anthropic connection error: {e}") from e
        latency = int((time.monotonic() - t0) * 1000)
        self._t.calls += 1
        self._t.wall_ms += latency
        u = resp.usage
        self._t.input_tokens += getattr(u, "input_tokens", 0) or 0
        self._t.output_tokens += getattr(u, "output_tokens", 0) or 0
        self._t.cached_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        usage = {
            "input_tokens": getattr(u, "input_tokens", None),
            "output_tokens": getattr(u, "output_tokens", None),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
        }
        # echo the assistant turn (thinking blocks included) verbatim
        self._messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

        if resp.stop_reason == "refusal":
            self._pending_tool_use_id = None
            self._t.add(role="model", refusal=True, stop_reason="refusal", usage=usage)
            return Decision(tool="__refusal__", args={}, stop_reason="refusal", raw_usage=usage, latency_ms=latency)
        text_bits: list[str] = []
        tool_use: Any = None
        for block in resp.content:
            if block.type == "tool_use" and tool_use is None:
                tool_use = block
            elif block.type == "text":
                text_bits.append(block.text)
        if tool_use is None:
            self._pending_tool_use_id = None
            self._t.add(role="model", text="\n".join(text_bits), stop_reason=resp.stop_reason, usage=usage)
            return Decision(
                tool="__no_tool__", args={}, text="\n".join(text_bits) or None,
                stop_reason=resp.stop_reason, raw_usage=usage, latency_ms=latency,
            )
        self._pending_tool_use_id = tool_use.id
        args = dict(tool_use.input or {})
        self._t.add(
            role="model", tool=tool_use.name, args=args, text="\n".join(text_bits) or None,
            stop_reason=resp.stop_reason, usage=usage, latency_ms=latency,
        )
        return Decision(
            tool=tool_use.name, args=args, text="\n".join(text_bits) or None,
            stop_reason=resp.stop_reason, raw_usage=usage, latency_ms=latency,
        )

    def usage(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, **self._t.usage()}

    def transcript(self) -> list[dict[str, Any]]:
        return self._t.turns


# ---------------------------------------------------------------------------------------------
# Scripted / cassette
# ---------------------------------------------------------------------------------------------


class CassetteMismatch(DeciderError):
    pass


class ScriptedDecider:
    """Replays recorded decisions. ``strict`` compares each observation's hash with the recording
    and fails loudly on divergence, so an offline demo cannot silently drift from reality."""

    name = "scripted"

    def __init__(self, cassette: dict[str, Any], *, strict: bool = False):
        self.model = cassette.get("model", "cassette")
        self._turns: list[dict[str, Any]] = cassette["turns"]
        self._i = 0
        self._strict = strict
        self._t = _Transcript()

    @classmethod
    def from_file(cls, path: str, *, strict: bool = False) -> ScriptedDecider:
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh), strict=strict)

    def start(self, system_prompt: str, tools: list[ToolSpec], first_user_text: str) -> None:
        self._t.add(role="user", text=first_user_text)

    def decide(
        self, observation_text: str, screenshot_jpeg: bytes | None, feedback: ToolFeedback | None
    ) -> Decision:
        if self._i >= len(self._turns):
            raise DeciderError("cassette exhausted")
        turn = self._turns[self._i]
        self._i += 1
        h = obs_hash(observation_text)
        recorded = turn.get("observation_hash")
        if self._strict and recorded and recorded != h:
            raise CassetteMismatch(
                f"turn {self._i}: observation differs from the recording "
                f"({h} != {recorded}); the surface changed — re-record the cassette"
            )
        self._t.calls += 1
        self._t.add(role="user", observation=observation_text, observation_hash=h, screenshot=bool(screenshot_jpeg))
        self._t.add(role="model", tool=turn["tool"], args=turn["args"], replayed=True)
        return Decision(tool=turn["tool"], args=dict(turn["args"]), stop_reason="cassette")

    def usage(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, **self._t.usage()}

    def transcript(self) -> list[dict[str, Any]]:
        return self._t.turns


def cassette_from_transcript(provider: str, model: str, transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Pair each model tool call with the observation hash it responded to."""
    turns: list[dict[str, Any]] = []
    last_hash: str | None = None
    for t in transcript:
        if t.get("role") == "user" and t.get("observation_hash"):
            last_hash = t["observation_hash"]
        elif t.get("role") == "model" and t.get("tool"):
            turns.append({"observation_hash": last_hash, "tool": t["tool"], "args": t["args"]})
    return {"provider": provider, "model": model, "turns": turns}


def make_decider(provider: str, model: str | None = None, **kw: Any) -> Decider:
    provider = provider.lower()
    if provider == "gemini":
        return GeminiDecider(model, **kw)
    if provider == "anthropic":
        return AnthropicDecider(model, **kw)
    raise DeciderError(f"unknown provider {provider!r} (gemini | anthropic)")
