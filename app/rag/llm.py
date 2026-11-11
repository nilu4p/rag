"""Client Ollama (API HTTP) : embeddings et chat en streaming."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from .config import settings

log = logging.getLogger("rag.llm")

THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


class LLMError(RuntimeError):
    pass


class Ollama:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=900.0),
        )
        self._caps: dict[str, set[str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- Informations ----------
    async def tags(self) -> list[dict[str, Any]]:
        r = await self._client.get("/api/tags", timeout=10)
        r.raise_for_status()
        return r.json().get("models", [])

    async def loaded(self) -> list[dict[str, Any]]:
        r = await self._client.get("/api/ps", timeout=10)
        r.raise_for_status()
        return r.json().get("models", [])

    async def has_model(self, name: str) -> bool:
        names = {m.get("name", "") for m in await self.tags()}
        return name in names or f"{name}:latest" in names

    async def capabilities(self, model: str) -> set[str]:
        if model not in self._caps:
            try:
                r = await self._client.post("/api/show", json={"model": model}, timeout=30)
                r.raise_for_status()
                self._caps[model] = set(r.json().get("capabilities") or [])
            except httpx.HTTPError as e:
                log.warning("Capacités de %s inconnues : %s", model, e)
                return set()
        return self._caps[model]

    async def _think_param(self, model: str) -> Any:
        """Valeur du paramètre `think` adaptée au modèle (None = ne pas l'envoyer)."""
        caps = await self.capabilities(model)
        if "thinking" not in caps:
            return None
        level = settings.think.lower()
        if "gpt-oss" in model:
            # gpt-oss ne sait pas désactiver la réflexion : on garde le niveau le plus bas possible
            return level if level in {"low", "medium", "high"} else "low"
        if level in {"off", "0", "false", "non", "no", ""}:
            return False
        return True

    # ---------- Embeddings ----------
    async def embed(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            part = texts[i : i + batch]
            try:
                r = await self._client.post(
                    "/api/embed",
                    json={
                        "model": settings.embed_model,
                        "input": part,
                        "truncate": True,
                        "keep_alive": settings.keep_alive,
                    },
                )
            except httpx.HTTPError as e:
                raise LLMError(f"Ollama injoignable ({self.base_url}) : {e}") from e
            if r.status_code != 200:
                raise LLMError(f"Embeddings impossibles ({r.status_code}) : {r.text[:300]}")
            vecs = r.json().get("embeddings") or []
            if len(vecs) != len(part):
                raise LLMError("Réponse d'embeddings incomplète")
            out.extend(vecs)
        return out

    # ---------- Chat ----------
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        fmt: Any = None,
        temperature: float | None = None,
        num_predict: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Produit des couples (type, texte) : type = "thinking" ou "content"."""
        model = model or settings.chat_model
        options: dict[str, Any] = {
            "num_ctx": settings.num_ctx,
            "temperature": settings.temperature if temperature is None else temperature,
        }
        if num_predict:
            options["num_predict"] = num_predict
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options,
            "keep_alive": settings.keep_alive,
        }
        think = await self._think_param(model)
        if think is not None:
            body["think"] = think
        if fmt is not None:
            body["format"] = fmt
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:400]
                    raise LLMError(f"Le modèle {model} a refusé la requête ({r.status_code}) : {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise LLMError(str(data["error"]))
                    msg = data.get("message") or {}
                    if msg.get("thinking"):
                        yield "thinking", msg["thinking"]
                    if msg.get("content"):
                        yield "content", msg["content"]
                    if data.get("done"):
                        break
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama injoignable ou trop lent ({self.base_url}) : {e!r}") from e

    async def chat(self, messages: list[dict[str, str]], **kw: Any) -> str:
        parts: list[str] = []
        async for kind, text in self.chat_stream(messages, **kw):
            if kind == "content":
                parts.append(text)
        return strip_think("".join(parts))


def strip_think(text: str) -> str:
    text = THINK_RE.sub("", text)
    # Balise <think> non fermée (réponse coupée) : on enlève tout ce qui la suit
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()
