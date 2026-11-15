"""Bounded, explicit model routes using LangChain's provider-neutral Runnable."""

from dataclasses import dataclass, field
import os
import httpx
from langchain_core.runnables import RunnableLambda

ENDPOINTS = {"groq":"https://api.groq.com/openai/v1/chat/completions",
             "openrouter":"https://openrouter.ai/api/v1/chat/completions"}


class ReviewConfigurationError(ValueError):
    pass


class ReviewProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelRoute:
    role: str
    provider: str
    model: str
    family: str
    api_key: str = field(repr=False)

    def public(self):
        return {"role":self.role, "provider":self.provider, "model":self.model, "declared_family":self.family}


def configuration():
    routes = []
    for role in ("a", "b", "judge"):
        prefix = "POST_REVIEW_" + role.upper()
        provider = os.environ.get(prefix + "_PROVIDER", "").strip().lower()
        model = os.environ.get(prefix + "_MODEL", "").strip()
        family = os.environ.get(prefix + "_FAMILY", "").strip().lower()
        key = os.environ.get(prefix + "_API_KEY") or os.environ.get("GROQ_API_KEY" if provider == "groq" else "OPENROUTER_API_KEY", "")
        if provider not in ENDPOINTS or not model or not family or not key:
            raise ReviewConfigurationError("Configure POST_REVIEW_A/B/JUDGE provider, model, family and server-side credentials")
        if provider == "openrouter" and (model.endswith(":free") or model in {"openrouter/auto", "auto"}):
            raise ReviewConfigurationError("Use explicit versioned model routes; automatic/free routing is not supported")
        routes.append(ModelRoute(role, provider, model, family, key))
    if len({r.family for r in routes}) != 3 or len({r.model.casefold() for r in routes}) != 3:
        raise ReviewConfigurationError("Declare three distinct model IDs and families; no silent single-model fallback")
    return routes


class ModelAdapter:
    def __init__(self, route, transport=None):
        self.route, self.transport = route, transport
        self.requests = 0
        self.tokens = None
        self.runnable = RunnableLambda(self._request)

    def _request(self, messages):
        if self.requests:
            raise ReviewProviderError("Per-role request budget exhausted")
        self.requests += 1
        body = {"model":self.route.model, "messages":messages, "temperature":0,
                "max_tokens":1800, "response_format":{"type":"json_object"}}
        if self.route.model.startswith("openai/gpt-oss-"):
            body["reasoning_effort"] = "low"
        if self.route.provider == "openrouter":
            body["provider"] = {"allow_fallbacks":False, "require_parameters":True}
        try:
            with httpx.Client(timeout=httpx.Timeout(35, connect=8), transport=self.transport, follow_redirects=False) as client:
                response = client.post(ENDPOINTS[self.route.provider], headers={"Authorization":f"Bearer {self.route.api_key}"}, json=body)
                response.raise_for_status()
                if len(response.content) > 131072:
                    raise ReviewProviderError("Provider response exceeds budget")
                data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop" or not isinstance(choice["message"]["content"], str):
                raise ReviewProviderError("Provider returned incomplete output")
            usage = data.get("usage") or {}
            self.tokens = max(0, int(usage["total_tokens"])) if usage.get("total_tokens") is not None else None
            return choice["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ReviewProviderError("Model request failed; no retry or provider fallback was made") from error

    def invoke(self, messages):
        return self.runnable.invoke(messages)
