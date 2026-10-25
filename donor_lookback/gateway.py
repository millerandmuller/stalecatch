"""The one thin module that talks to Token Factory (OpenAI-compatible). Provider, models, prices: config.py."""
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import ModelTier, Settings

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _mentions_format(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(w in msg for w in ("response_format", "json_schema", "json schema"))


def redact(text: str, limit: int = 240) -> str:
    """Strip anything that looks like a credential before an error reaches a log, event or client."""
    text = re.sub(r"\b(sk-|tvly-|nvapi-)[A-Za-z0-9_\-\.]{6,}", "[redacted]", text)
    text = re.sub(r"(?i)\b(bearer|api[_-]?key)\b[\"':= ]+[A-Za-z0-9_\-\.+/=]{8,}", "[redacted credential]", text)
    return text[:limit]


class GatewayError(RuntimeError):
    """The model call failed after retries; callers mark the run incomplete, never silently green."""


@dataclass
class CallResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    usd: float
    model: str


def usd_for(tier: ModelTier, prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * tier.usd_per_m_input + completion_tokens * tier.usd_per_m_output) / 1_000_000


def parse_json(text: str) -> dict:
    """Parse model output as JSON; tolerate <think> blocks, code fences and prose around one object."""
    cleaned = THINK_BLOCK.sub("", text or "").strip()
    cleaned = re.sub(r"^.*?</think>", "", cleaned, flags=re.DOTALL)  # an unclosed or leading reasoning block
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise GatewayError(f"Model did not return valid JSON: {cleaned[:200]!r}")


class Gateway:
    """client: anything with .chat.completions.create(...) (the openai SDK, or a test fake)."""

    def __init__(self, settings: Settings, client: Any = None,
                 recorder: Optional[Callable[..., None]] = None, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._client = client
        self._recorder = recorder
        self._sleep = sleep
        self._schema_unsupported: set[str] = set()  # tiers whose endpoint rejected response_format

    @property
    def client(self):
        if self._client is None:
            if not (self.settings.base_url and self.settings.api_key):
                raise GatewayError("Token Factory is not configured: set TOKEN_FACTORY_BASE_URL and TOKEN_FACTORY_API_KEY.")
            from openai import OpenAI  # imported lazily so tests and the memory server run without credentials
            self._client = OpenAI(base_url=self.settings.base_url, api_key=self.settings.api_key, max_retries=0,
                                timeout=self.settings.request_timeout_s)
        return self._client

    def tier(self, name: str) -> ModelTier:
        if name not in ("nano", "ultra"):
            raise ValueError(f"unknown tier {name!r}")
        return getattr(self.settings, name)

    def chat(self, tier_name: str, system: str, user: str, *, purpose: str,
             run_id: Optional[str] = None, response_format: Optional[dict] = None,
             max_tokens: int = 1500) -> CallResult:
        tier = self.tier(tier_name)
        delay = 1.0
        last_error: Optional[Exception] = None
        attempt = 0
        while attempt < self.settings.max_retries:
            attempt += 1
            fmt = None if tier_name in self._schema_unsupported else response_format
            started = time.monotonic()
            try:
                kwargs = dict(model=tier.model_id, max_tokens=max_tokens, temperature=0.0,
                              messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
                if fmt:
                    kwargs["response_format"] = fmt
                if not tier.thinking:
                    kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below, re-raised as GatewayError
                last_error = exc
                status = getattr(exc, "status_code", None)
                if status == 400 and fmt and _mentions_format(exc):
                    self._schema_unsupported.add(tier_name)  # endpoint rejects response_format: prompt + lenient parse
                    attempt -= 1  # the fallback does not use up a retry
                    continue
                retryable = not isinstance(status, int) or status in (408, 409, 429) or status >= 500
                if not retryable or attempt == self.settings.max_retries:
                    self._record(run_id, tier, purpose, 0, 0, int((time.monotonic() - started) * 1000), ok=False)
                    raise GatewayError(f"{tier.name} call failed ({purpose}): {redact(str(exc))}") from exc
                self._sleep(delay)
                delay *= 2
                continue
            latency = int((time.monotonic() - started) * 1000)
            usage = getattr(resp, "usage", None)
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            self._record(run_id, tier, purpose, pt, ct, latency, ok=True)
            return CallResult(text=THINK_BLOCK.sub("", text).strip(), prompt_tokens=pt, completion_tokens=ct,
                              latency_ms=latency, usd=usd_for(tier, pt, ct), model=tier.model_id)
        raise GatewayError(f"{tier.name} call failed ({purpose}): {last_error}")

    def chat_tools(self, tier_name: str, messages: list, tools: list, *, purpose: str, run_id: Optional[str] = None,
                   max_tokens: int = 800):
        """One tool-calling turn. Returns the assistant message (content and/or tool_calls). Usage is logged like any call."""
        tier = self.tier(tier_name)
        started = time.monotonic()
        try:
            extra = {} if tier.thinking else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
            resp = self.client.chat.completions.create(model=tier.model_id, messages=messages, tools=tools,
                                                       max_tokens=max_tokens, temperature=0.0, **extra)
        except Exception as exc:  # noqa: BLE001
            self._record(run_id, tier, purpose, 0, 0, int((time.monotonic() - started) * 1000), ok=False)
            raise GatewayError(f"{tier.name} call failed ({purpose}): {redact(str(exc))}") from exc
        usage = getattr(resp, "usage", None)
        self._record(run_id, tier, purpose, int(getattr(usage, "prompt_tokens", 0) or 0),
                     int(getattr(usage, "completion_tokens", 0) or 0), int((time.monotonic() - started) * 1000), ok=True)
        if not resp.choices:
            raise GatewayError(f"{tier.name} returned no choices ({purpose})")
        return resp.choices[0].message

    def chat_json(self, tier_name: str, system: str, user: str, *, purpose: str, run_id: Optional[str] = None,
                  schema: Optional[dict] = None, max_tokens: int = 1500) -> dict:
        fmt = ({"type": "json_schema", "json_schema": {"name": purpose, "schema": schema, "strict": True}}
               if schema else {"type": "json_object"})
        for attempt in (1, 2):
            res = self.chat(tier_name, system + "\nReturn ONLY one JSON object, no prose.", user, purpose=purpose,
                            run_id=run_id, response_format=fmt, max_tokens=max_tokens)
            try:
                return parse_json(res.text)
            except GatewayError:
                if attempt == 2:
                    raise
                max_tokens *= 2  # a reasoning model that ran out of room before the JSON gets one retry with twice the room

    def _record(self, run_id, tier: ModelTier, purpose: str, pt: int, ct: int, latency_ms: int, ok: bool) -> None:
        if self._recorder:
            self._recorder(run_id=run_id, tier=tier.name, model=tier.model_id, purpose=purpose,
                           prompt_tokens=pt, completion_tokens=ct, latency_ms=latency_ms,
                           usd=usd_for(tier, pt, ct), ok=ok)
