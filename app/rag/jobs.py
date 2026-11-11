"""Réponses en tâche de fond : elles continuent même si le navigateur se déconnecte,
et on peut s'y raccrocher (rechargement de page, réseau coupé)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .chat import Assistant
from .db import DB

log = logging.getLogger("rag.jobs")


@dataclass
class Job:
    message_id: str
    events: list[dict] = field(default_factory=list)  # en ajout seul
    finished: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    created: float = field(default_factory=time.time)
    task: asyncio.Task | None = None

    def push(self, ev: dict) -> None:
        self.events.append(ev)
        self.changed.set()


class Jobs:
    def __init__(self, db: DB, assistant: Assistant):
        self.db, self.assistant = db, assistant
        self.jobs: dict[str, Job] = {}
        # Une seule génération à la fois : sur CPU, Ollama traite de toute façon une requête à la fois
        self.slot = asyncio.Semaphore(1)

    def start(self, conversation_id: str, question: str, mode: str) -> str:
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in self.db.messages(conversation_id)
            if m["status"] == "done" and m["content"]
        ]
        self.db.add_message(conversation_id, "user", question, meta={"mode": mode})
        mid = self.db.add_message(conversation_id, "assistant", "", status="pending")
        job = self.jobs[mid] = Job(mid)
        job.task = asyncio.create_task(self._run(job, question, history, mode))
        self._gc()
        return mid

    async def run_once(self, question: str, mode: str = "auto", history: list[dict] | None = None) -> dict:
        """Réponse complète sans conversation enregistrée (API /api/ask, n8n, terminal)."""
        final: dict[str, Any] = {"type": "error", "text": "Pas de réponse"}
        async with self.slot:
            async for ev in self.assistant.answer(question, history or [], mode):
                if ev["type"] in {"done", "error"}:
                    final = ev
        return final

    async def _run(self, job: Job, question: str, history: list[dict], mode: str) -> None:
        text = ""
        final: dict[str, Any] | None = None
        try:
            if self.slot.locked():
                job.push({"type": "status", "text": "Une autre réponse est en cours, je patiente…"})
            async with self.slot:
                async for ev in self.assistant.answer(question, history, mode):
                    if ev["type"] == "token":
                        text += ev["text"]
                    elif ev["type"] == "reset":
                        text = ""
                    elif ev["type"] in {"done", "error"}:
                        final = ev
                    job.push(ev)
        except asyncio.CancelledError:
            final = {"type": "error", "text": "⏹ Réponse arrêtée."}
            job.push(final)
        except Exception as e:  # noqa: BLE001
            log.exception("Tâche de réponse")
            final = {"type": "error", "text": f"⚠️ Erreur inattendue : {e}"}
            job.push(final)
        finally:
            if final is None:
                final = {"type": "error", "text": "⚠️ Réponse interrompue."}
                job.push(final)
            if final["type"] == "done":
                self.db.update_message(job.message_id, final["content"], "done", final.get("meta"))
            else:
                content = (text.rstrip() + "\n\n" if text.strip() else "") + final["text"]
                self.db.update_message(job.message_id, content, "error", {"route": "error"})
            job.finished = True
            job.changed.set()

    def cancel(self, message_id: str) -> bool:
        job = self.jobs.get(message_id)
        if job and job.task and not job.finished:
            job.task.cancel()
            return True
        return False

    async def follow(self, message_id: str) -> AsyncIterator[dict]:
        """Rejoue les événements déjà produits puis suit les nouveaux (avec des « ping » pour garder la connexion)."""
        job = self.jobs.get(message_id)
        if job is None:
            return
        i = 0
        while True:
            while i < len(job.events):
                yield job.events[i]
                i += 1
            if job.finished:
                return
            job.changed.clear()
            if i < len(job.events):
                continue
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield {"type": "ping"}

    def _gc(self) -> None:
        old = [k for k, j in self.jobs.items() if j.finished and time.time() - j.created > 3600]
        for k in old:
            del self.jobs[k]
