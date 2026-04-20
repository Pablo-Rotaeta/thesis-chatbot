"""
LLM Adapter Layer
"""

from abc import ABC, abstractmethod
from pyexpat.errors import messages
from typing import List, Dict, Optional
import os, httpx


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> Dict:
        return {"role": self.role, "content": self.content}


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class BaseLLMAdapter(ABC):

    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaAdapter(BaseLLMAdapter):

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self._model = model
        self.base_url = base_url

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(self, messages, system_prompt, temperature=0.7, max_tokens=512) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system_prompt}]
            + [m.to_dict() for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Gemini
# FIX A: changed default model from "gemini-2.5-flash-lite" (does not exist)
#        to "gemini-2.5-flash" which is confirmed available in your account.
# ---------------------------------------------------------------------------

class GeminiAdapter(BaseLLMAdapter):

    def __init__(self, model: str = "gemini-2.5-flash"):
        self._model = model
        self.api_key = os.getenv("GEMINI_API_KEY", "")

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(self, messages, system_prompt, temperature=0.7, max_tokens=512) -> str:
        import asyncio
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self.api_key}"
        )
        full_prompt = system_prompt + "\n\n"
        for m in messages:
            if m.role == "user":
                full_prompt += f"User: {m.content}\n"
            elif m.role == "assistant":
                full_prompt += f"Assistant: {m.content}\n"

        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        for attempt in range(4):
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, json=payload)
                if r.status_code in (503, 429) and attempt < 3:
                    wait = 2 ** attempt
                    print(f"Gemini {r.status_code}, retrying in {wait}s (attempt {attempt+1}/4)")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIAdapter(BaseLLMAdapter):

    def __init__(self, model: str = "gpt-4o-mini"):
        self._model = model
        self.api_key = os.getenv("OPENAI_API_KEY", "")

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(self, messages, system_prompt, temperature=0.7, max_tokens=512) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system_prompt}]
            + [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicAdapter(BaseLLMAdapter):

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self._model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(self, messages, system_prompt, temperature=0.7, max_tokens=2048) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [m.to_dict() for m in messages if m.role != "system"],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_adapter(provider: str, model: Optional[str] = None) -> BaseLLMAdapter:
    adapters = {
        "ollama":    lambda m: OllamaAdapter(model=m or "llama3"),
        "gemini":    lambda m: GeminiAdapter(model=m or "gemini-2.5-flash"),
        "openai":    lambda m: OpenAIAdapter(model=m or "gpt-4o-mini"),
        "anthropic": lambda m: AnthropicAdapter(model=m or "claude-haiku-4-5-20251001"),
    }
    if provider not in adapters:
        raise ValueError(f"Unknown provider '{provider}'")
    return adapters[provider](model)
