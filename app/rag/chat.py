"""Orchestration d'une réponse : commandes, aiguillage, recherche, génération, vérifications.

`Assistant.answer()` produit un flux d'événements (dict) :
  status   {"text"}                  étape en cours (recherche, lecture des pages…)
  thinking {}                        le modèle réfléchit (gpt-oss)
  token    {"text"}                  morceau de réponse
  reset    {}                        efface le texte affiché (bascule vers internet)
  done     {"content", "meta"}       réponse finale vérifiée
  error    {"text"}
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from typing import Any, AsyncIterator

from . import websearch
from .config import settings
from .db import DB
from .llm import LLMError, Ollama, strip_think
from .postprocess import fix_citations, is_not_found, strip_sentinel, unverified_numbers
from .prompts import (
    CHAT_SYSTEM,
    NOT_FOUND,
    PARTIAL_SUMMARY_SYSTEM,
    ROUTER_SCHEMA,
    ROUTER_SYSTEM,
    SUMMARY_SYSTEM,
    docs_messages,
    today,
    web_messages,
)
from .qdrant import Qdrant, QdrantError
from .retrieval import Passage, Retriever

log = logging.getLogger("rag.chat")

Event = dict[str, Any]

HELP = """**Commandes disponibles**

| Commande | Effet |
|---|---|
| `/docs` | Liste des documents indexés |
| `/resume <nom>` | Résumé d'un document entier (ex. `/resume cv`) |
| `/doc <question>` | Cherche uniquement dans tes documents (jamais sur internet) |
| `/web <question>` | Cherche directement sur internet |
| `/chat <message>` | Répond sans chercher (rédaction, traduction, calcul…) |
| `/aide` | Cette aide |

Sans commande, l'assistant choisit seul : **tes documents** pour ce qui te concerne, **internet** pour l'actualité, ou **la conversation directe** pour rédiger, traduire, expliquer.
Le sélecteur en bas de page force un mode pour toutes les questions."""

LLM_DOWN = (
    "⚠️ Le modèle de langage n'a pas pu répondre : {err}\n\n"
    "Vérifie qu'Ollama tourne sur le PC (`make doctor`) et qu'il reste assez de mémoire libre."
)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


class Assistant:
    def __init__(self, db: DB, llm: Ollama, store: Qdrant, retriever: Retriever):
        self.db, self.llm, self.store, self.retriever = db, llm, store, retriever

    # ================================================================ entrée
    async def answer(self, question: str, history: list[dict], mode: str = "auto") -> AsyncIterator[Event]:
        t0 = time.time()
        q = question.strip()
        meta: dict[str, Any] = {"route": None, "query": q}
        try:
            cmd, _, rest = q.partition(" ")
            cmd = cmd.lower()
            if cmd in {"/aide", "/help", "/?"}:
                yield self._done(HELP, meta | {"route": "command"}, t0)
                return
            if cmd == "/docs":
                yield self._done(self._list_docs(), meta | {"route": "command"}, t0)
                return
            if cmd in {"/resume", "/résumé", "/resumer", "/résumer"}:
                async for ev in self._summary(rest.strip(), meta | {"route": "summary"}, t0):
                    yield ev
                return
            if cmd in {"/web", "/doc", "/chat"}:
                if not rest.strip():
                    yield self._done(f"Ajoute ta question après la commande, par exemple : `{cmd} …`", meta | {"route": "command"}, t0)
                    return
                mode, q = {"/web": "web", "/doc": "docs", "/chat": "chat"}[cmd], rest.strip()
            elif cmd.startswith("/") and len(cmd) > 1 and " " not in cmd:
                yield self._done(f"Commande inconnue : `{cmd}`.\n\n" + HELP, meta | {"route": "command"}, t0)
                return

            route, query = mode if mode in {"docs", "web", "chat"} else "auto", q
            forced = route != "auto"
            if route == "auto":
                if settings.query_router:
                    yield {"type": "status", "text": "Je comprends ta demande…"}
                    route, query = await self._route(q, history)
                else:
                    route = "docs"
            elif history and route in {"docs", "web"} and settings.query_router:
                # Mode forcé : on rend quand même la question autonome (« et là-bas ? »)
                yield {"type": "status", "text": "Je comprends ta demande…"}
                _, query = await self._route(q, history)
            if route == "web" and not settings.web_enabled:
                route = "docs"
            meta.update(route=route, query=query)
            log.info("Question %r → %s (%r)", q[:80], route, query[:80])

            if route == "chat":
                async for ev in self._chat(q, history, meta, t0):
                    yield ev
            elif route == "web":
                async for ev in self._web(query, meta, t0, fallback_docs=not forced):
                    yield ev
            else:
                async for ev in self._docs(query, meta, t0, allow_web=not forced and settings.web_enabled):
                    yield ev
        except LLMError as e:
            log.error("LLM : %s", e)
            yield {"type": "error", "text": LLM_DOWN.format(err=e)}
        except QdrantError as e:
            log.error("Qdrant : %s", e)
            yield {"type": "error", "text": f"⚠️ La base de recherche (Qdrant) ne répond pas : {e}"}
        except Exception as e:  # noqa: BLE001
            log.exception("Erreur inattendue")
            yield {"type": "error", "text": f"⚠️ Erreur inattendue : {type(e).__name__} : {e}"}

    # ================================================================ aiguillage
    async def _route(self, q: str, history: list[dict]) -> tuple[str, str]:
        names = [f.rsplit("/", 1)[-1] for f, row in self.db.files().items() if row["status"] == "ok"]
        docs = ", ".join(names[:60]) or "(aucun document indexé)"
        msgs: list[dict] = [{"role": "system", "content": ROUTER_SYSTEM.format(docs=docs)}]
        convo = []
        for m in history[-2 * settings.memory_turns :]:
            who = "Utilisateur" if m["role"] == "user" else "Assistant"
            convo.append(f"{who} : {m['content'][:700]}")
        user = (("Conversation précédente :\n" + "\n".join(convo) + "\n\n") if convo else "") + f"Dernière demande : {q}"
        msgs.append({"role": "user", "content": user})
        try:
            raw = await self.llm.chat(msgs, fmt=ROUTER_SCHEMA, temperature=0)
            data = _parse_json(raw)
            route = data.get("route") if data.get("route") in {"docs", "web", "chat"} else "docs"
            query = str(data.get("query") or "").strip() or q
            return route, query
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - aiguillage raté : on cherche dans les documents
            log.warning("Aiguillage impossible (%s) : documents par défaut", e)
            return "docs", q

    # ================================================================ documents
    async def _docs(self, query: str, meta: dict, t0: float, allow_web: bool) -> AsyncIterator[Event]:
        yield {"type": "status", "text": "Je cherche dans tes documents…"}
        try:
            passages = await self.retriever.search(query)
        except QdrantError as e:
            if allow_web:
                log.warning("Recherche documents impossible (%s) : bascule internet", e)
                passages = []
            else:
                raise
        if not passages:
            if allow_web:
                meta["fallback"] = "Rien de pertinent dans tes documents"
                async for ev in self._web(query, meta, t0, fallback_docs=False):
                    yield ev
                return
            yield self._done(
                "Je n'ai trouvé aucun passage pertinent dans tes documents pour cette question.\n\n"
                "- Vérifie que le document est bien indexé (`/docs`).\n"
                "- Reformule avec les mots exacts du document, ou essaie `/web` pour internet.",
                meta | {"route": "docs", "sources": []},
                t0,
            )
            return

        msgs, suspicious = docs_messages(query, passages)
        yield {"type": "status", "text": f"Je rédige à partir de {len(passages)} extrait(s)…"}
        answer = ""
        async for ev in self._generate(msgs):
            if ev["type"] == "final":
                answer = ev["text"]
            else:
                yield ev

        if is_not_found(answer):
            if allow_web:
                yield {"type": "reset"}
                meta["fallback"] = "L'information n'est pas dans tes documents"
                async for ev in self._web(query, meta, t0, fallback_docs=False):
                    yield ev
                return
            answer = "Tes documents ne contiennent pas cette information (extraits consultés ci-dessous)."
        answer = strip_sentinel(answer)
        answer, cited, removed = fix_citations(answer, len(passages))
        warnings = []
        if removed:
            warnings.append(f"{removed} référence(s) vers un extrait inexistant retirée(s).")
        if suspicious:
            warnings.append("Instruction suspecte ignorée dans : " + ", ".join(sorted(suspicious)))
        corpus = "\n".join(p.text + " " + p.name for p in passages)
        meta.update(
            route="docs",
            sources=[_doc_source(i, p, i in cited) for i, p in enumerate(passages, start=1)],
            unverified=unverified_numbers(answer, corpus, query + " " + today()),
            warnings=warnings,
        )
        yield self._done(answer, meta, t0)

    # ================================================================ internet
    async def _web(self, query: str, meta: dict, t0: float, fallback_docs: bool) -> AsyncIterator[Event]:
        yield {"type": "status", "text": "Je cherche sur internet…"}
        try:
            results = await websearch.search(query)
        except websearch.WebError as e:
            log.warning("Recherche web : %s", e)
            results = []
            meta["warnings"] = [f"Recherche internet indisponible : {e}"]
        if not results:
            if fallback_docs:
                async for ev in self._docs(query, meta, t0, allow_web=False):
                    yield ev
                return
            prefix = (meta.get("fallback") + ", et ") if meta.get("fallback") else ""
            yield self._done(
                f"{prefix}je n'ai rien trouvé sur internet pour « {query} ».".capitalize(),
                meta | {"route": "web", "sources": []},
                t0,
            )
            return
        yield {"type": "status", "text": f"Je lis {min(len(results), settings.web_results)} page(s) web…"}
        pages = await websearch.read_pages(results, query)
        msgs = web_messages(query, pages)
        yield {"type": "status", "text": "Je rédige la réponse…"}
        answer = ""
        async for ev in self._generate(msgs):
            if ev["type"] == "final":
                answer = ev["text"]
            else:
                yield ev
        if is_not_found(answer):
            answer = "Les pages consultées sur internet ne permettent pas de répondre (liens ci-dessous)."
        answer = strip_sentinel(answer)
        answer, cited, _ = fix_citations(answer, len(pages))
        warnings = list(meta.get("warnings") or [])
        bad = [p.url for p in pages if p.suspicious]
        if bad:
            warnings.append("Instruction suspecte ignorée sur : " + ", ".join(bad))
        meta.update(
            route="web",
            sources=[
                {"n": i, "title": p.title, "url": p.url, "cited": i in cited}
                for i, p in enumerate(pages, start=1)
            ],
            warnings=warnings,
        )
        yield self._done(answer, meta, t0)

    # ================================================================ conversation directe
    async def _chat(self, q: str, history: list[dict], meta: dict, t0: float) -> AsyncIterator[Event]:
        msgs: list[dict] = [{"role": "system", "content": CHAT_SYSTEM.format(date=today())}]
        for m in history[-2 * settings.memory_turns :]:
            msgs.append({"role": m["role"], "content": m["content"][:4000]})
        msgs.append({"role": "user", "content": q})
        answer = ""
        async for ev in self._generate(msgs, sentinel=False):
            if ev["type"] == "final":
                answer = ev["text"]
            else:
                yield ev
        yield self._done(answer, meta | {"route": "chat", "sources": []}, t0)

    # ================================================================ /resume
    async def _summary(self, name: str, meta: dict, t0: float) -> AsyncIterator[Event]:
        files = {k: v for k, v in self.db.files().items() if v["status"] == "ok"}
        if not name:
            yield self._done("Précise le document : `/resume <nom>` (ex. `/resume cv`). Liste : `/docs`.", meta, t0)
            return
        target = _fold(name)
        matches = [k for k in files if target in _fold(k)]
        if not matches:
            words = target.split()
            matches = [k for k in files if all(w in _fold(k) for w in words)]
        if not matches:
            yield self._done(f"Aucun document indexé ne correspond à « {name} ». Liste : `/docs`.", meta, t0)
            return
        if len(matches) > 1:
            exact = [k for k in matches if _fold(k.rsplit("/", 1)[-1].rsplit(".", 1)[0]) == target]
            if len(exact) == 1:
                matches = exact
            else:
                lst = "\n".join(f"- `{k}`" for k in matches[:15])
                yield self._done(f"Plusieurs documents correspondent à « {name} » :\n{lst}\n\nPrécise le nom.", meta, t0)
                return
        path = matches[0]
        yield {"type": "status", "text": f"Je lis {path}…"}
        chunks = await self.store.scroll_path(path)
        text = _join_chunks(chunks)
        budget = int(settings.num_ctx * 2.6) - 3000  # ~2,6 caractères par token en français, marge pour la réponse
        if len(text) > budget:
            parts = [text[i : i + budget] for i in range(0, len(text), budget)][:8]
            notes = []
            for i, part in enumerate(parts, start=1):
                yield {"type": "status", "text": f"Je lis la partie {i}/{len(parts)} de {path}…"}
                notes.append(
                    await self.llm.chat(
                        [
                            {"role": "system", "content": PARTIAL_SUMMARY_SYSTEM},
                            {"role": "user", "content": f"Document : {path} (partie {i}/{len(parts)})\n\n<document>\n{part}\n</document>"},
                        ]
                    )
                )
            text = "\n\n".join(f"Notes sur la partie {i} :\n{n}" for i, n in enumerate(notes, start=1))
        yield {"type": "status", "text": "Je rédige le résumé…"}
        msgs = [
            {"role": "system", "content": SUMMARY_SYSTEM.format(date=today())},
            {"role": "user", "content": f"Document : {path}\n\n<document>\n{text}\n</document>\n\nRésume ce document."},
        ]
        answer = ""
        async for ev in self._generate(msgs, sentinel=False):
            if ev["type"] == "final":
                answer = ev["text"]
            else:
                yield ev
        row = files[path]
        meta["sources"] = [{"n": 1, "name": path.rsplit("/", 1)[-1], "path": path, "page": None, "label": "", "cited": True,
                            "excerpt": f"{row['chunks']} morceaux, {row['pages']} page(s)"}]
        yield self._done(answer, meta, t0)

    # ================================================================ /docs
    def _list_docs(self) -> str:
        files = self.db.files()
        if not files:
            return "Aucun document indexé pour l'instant. Dépose des fichiers dans l'onglet **Documents** (ou dans le dossier `documents/`)."
        ok = [(k, v) for k, v in files.items() if v["status"] == "ok"]
        bad = [(k, v) for k, v in files.items() if v["status"] != "ok"]
        lines = [f"**{len(ok)} document(s) indexé(s)** ({sum(v['chunks'] for _, v in ok)} morceaux)\n",
                 "| Document | Morceaux | Lecture |", "|---|---|---|"]
        for k, v in ok:
            lines.append(f"| {k} | {v['chunks']} | {v['method'] or ''} |")
        if bad:
            lines.append(f"\n**{len(bad)} fichier(s) non indexé(s)**\n")
            for k, v in bad:
                lines.append(f"- `{k}` : {v['error']}")
        return "\n".join(lines)

    # ================================================================ génération
    async def _generate(self, msgs: list[dict], sentinel: bool = True) -> AsyncIterator[Event]:
        """Diffuse la réponse ; retient le début tant qu'il peut s'agir de NON_TROUVE."""
        buf = ""
        released = 0
        decided = not sentinel
        in_think = False
        last_think = 0.0
        async for kind, text in self.llm.chat_stream(msgs):
            if kind == "thinking":
                if time.time() - last_think > 1.5:
                    last_think = time.time()
                    yield {"type": "thinking"}
                continue
            buf += text
            # Balises <think> écrites dans le texte (certains modèles) : masquées
            if "<think>" in buf[released:] and not in_think:
                in_think = True
            if in_think:
                if "</think>" in buf:
                    buf = strip_think(buf)
                    in_think = False
                    released = 0
                else:
                    if time.time() - last_think > 1.5:
                        last_think = time.time()
                        yield {"type": "thinking"}
                    continue
            if not decided:
                head = buf.lstrip().lstrip("*_`'\"")
                if not head:
                    continue
                if NOT_FOUND.startswith(head[: len(NOT_FOUND)]):
                    if len(head) >= len(NOT_FOUND):
                        break  # c'est la sentinelle : inutile de laisser le modèle continuer
                    continue
                decided = True
            if len(buf) > released:
                yield {"type": "token", "text": buf[released:]}
                released = len(buf)
        yield {"type": "final", "text": strip_think(buf)}

    @staticmethod
    def _done(content: str, meta: dict, t0: float) -> Event:
        meta = dict(meta)
        meta["seconds"] = round(time.time() - t0, 1)
        return {"type": "done", "content": content.strip(), "meta": meta}


def _doc_source(n: int, p: Passage, cited: bool) -> dict:
    return {
        "n": n,
        "name": p.name,
        "path": p.path,
        "page": p.page,
        "label": p.label,
        "heading": p.heading,
        "score": round(p.score, 3),
        "cited": cited,
        "excerpt": p.text[:600],
    }


def _join_chunks(chunks: list[dict]) -> str:
    """Recolle les morceaux d'un document en retirant les chevauchements."""
    out = ""
    page = None
    for c in chunks:
        t = c.get("text", "")
        if c.get("page") != page and c.get("page"):
            page = c.get("page")
            out += f"\n\n[{c.get('label') or f'page {page}'}]\n"
        # retire le début déjà présent (chevauchement)
        for k in range(min(len(t), 400), 20, -1):
            if out.endswith(t[:k]):
                t = t[k:]
                break
        out += ("\n\n" if out and not out.endswith("\n") else "") + t
    return out.strip()


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            return json.loads(m.group(0))
        raise
