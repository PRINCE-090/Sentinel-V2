"""
budget_advisor.adapters
=========================
Concrete asynchronous LLM backend adapters: Groq, OpenAI, and Ollama.

Each adapter implements the `LLMBackend` structural protocol defined
in `engine.py` (an `async def complete(system_prompt, user_query, history) -> str`
method).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .schemas import ChatMessage, MessageRole

logger = logging.getLogger("budget_advisor.adapters")

DEFAULT_GROQ_BASE_URL = None
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class GroqLLMAdapter:
    """
    Asynchronous LLMBackend implementation backed by Groq's chat completions API.
    Uses AsyncGroq for non-blocking execution under high concurrency.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "llama-3.3-70b-versatile",
        base_url: Optional[str] = None,
        timeout_seconds: float = 20.0,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> None:
        try:
            from groq import AsyncGroq
        except ImportError as exc:
            raise ImportError(
                "The 'groq' package is required to use GroqLLMAdapter. "
                "Install it with: pip install groq"
            ) from exc

        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

        client_kwargs = {"timeout": timeout_seconds}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = AsyncGroq(**client_kwargs)

    async def complete(
        self,
        system_prompt: str,
        user_query: str,
        history: List[ChatMessage],
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = "assistant" if msg.role == MessageRole.ASSISTANT else "user"
            if msg.role == MessageRole.SYSTEM:
                role = "user"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": user_query})

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        choice = response.choices[0]
        content = choice.message.content
        if not content or not content.strip():
            raise ValueError("Groq API returned an empty completion.")
        return content.strip()


class OpenAILLMAdapter:
    """
    Asynchronous LLMBackend implementation backed by OpenAI's chat completions API.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        timeout_seconds: float = 20.0,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required to use OpenAILLMAdapter. "
                "Install it with: pip install openai"
            ) from exc

        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

        client_kwargs = {"timeout": timeout_seconds}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**client_kwargs)

    async def complete(
        self,
        system_prompt: str,
        user_query: str,
        history: List[ChatMessage],
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = "assistant" if msg.role == MessageRole.ASSISTANT else "user"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": user_query})

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        choice = response.choices[0]
        content = choice.message.content
        if not content or not content.strip():
            raise ValueError("OpenAI API returned an empty completion.")
        return content.strip()


class OllamaLLMAdapter:
    """
    Asynchronous LLMBackend implementation backed by a local Ollama server.
    """

    def __init__(
        self,
        model: str = "llama3.1",
        base_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "The 'httpx' package is required to use OllamaLLMAdapter. "
                "Install it with: pip install httpx"
            ) from exc

        self._model = model
        self._base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._httpx = httpx

    async def complete(
        self,
        system_prompt: str,
        user_query: str,
        history: List[ChatMessage],
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = "assistant" if msg.role == MessageRole.ASSISTANT else "user"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": user_query})

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
            },
        }

        url = f"{self._base_url}/api/chat"
        async with self._httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            if not content or not content.strip():
                raise ValueError("Ollama server returned an empty completion.")
            return content.strip()
